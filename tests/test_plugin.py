"""Tests for pytest-best-tempmail.

Each one runs a real sub-pytest, so what is asserted is pytest's actual
behaviour: collection, fixture ordering, teardown, and the hook.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #

def test_fixtures_appear_with_no_registration(pytester):
    """`pip install` must be enough.

    This one runs in a SUBPROCESS on purpose. Every other test here uses
    `-p pytest_best_tempmail` and an in-process run, which inherits the plugin
    already loaded in this process, so none of them would notice a broken
    `pytest11` entry point. A real subprocess has to discover it the way a
    user's pytest does.
    """
    pytester.makepyfile(
        """
        def test_it(tempmail_config):
            assert tempmail_config["timeout"] == 30
            assert tempmail_config["base_url"].startswith("https://api.best-tempmail.com")
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)


def test_the_options_are_registered(pytester):
    result = pytester.runpytest_subprocess("--help")
    result.stdout.fnmatch_lines(["*disposable email inboxes*"])


# --------------------------------------------------------------------------- #
# the regression test this plugin exists to keep passing
# --------------------------------------------------------------------------- #

def test_wait_finds_mail_that_already_arrived(run, mock):
    """The mock's /wait NEVER reports a message, so finding one proves the
    inbox was read rather than only long-polled.

    This is the bug that shipped in the GitHub Action and in version 1.x of the
    Python SDK: create the inbox, trigger the signup, and the email often wins
    the race against the next line of code. A client that only long-polls then
    burns the whole timeout and reports nothing while the message sits unread.

    Deleting `include_existing` handling from the SDK, or rebuilding the wait
    inside this plugin on top of /wait alone, makes this test fail. It was
    checked that way.
    """
    mock.set(seed_messages=1)
    result = run(
        """
        def test_it(inbox):
            msg = inbox.wait_for_message(timeout=3)
            assert msg is not None, "did not see mail that was already delivered"
            assert msg.id == "msg1"
        """
    )
    result.assert_outcomes(passed=1)


def test_the_mock_really_does_refuse_to_answer_wait(run, mock):
    """Guards the test above.

    If the mock ever started reporting messages from `/wait`, the test above
    would keep passing for the wrong reason and would no longer be testing
    anything. Asking for the old long-poll-only behaviour must find nothing,
    even with a message sitting in the inbox.

    Verified the other way too: forcing `include_existing=False` inside the
    plugin makes the test above fail, which is the condition that makes it a
    regression test rather than decoration.
    """
    mock.set(seed_messages=1)
    result = run(
        """
        def test_it(inbox):
            assert inbox.wait_for_message(timeout=2, include_existing=False) is None
            assert inbox.wait_for_message(timeout=2) is not None
        """
    )
    result.assert_outcomes(passed=1)
    assert mock.state["wait_calls"] > 0, "the long poll was never even attempted"


def test_wait_for_code_returns_the_code(run, mock):
    mock.set(seed_messages=1)
    result = run(
        """
        def test_it(inbox):
            assert inbox.wait_for_code(timeout=3) == "889231"
        """
    )
    result.assert_outcomes(passed=1)


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #

def test_inbox_is_created_and_deleted(run, mock):
    result = run(
        """
        def test_it(inbox):
            assert "@" in inbox.address
        """
    )
    result.assert_outcomes(passed=1)
    state = mock.state
    assert len(state["created"]) == 1
    assert state["deleted"] == state["created"], "teardown did not delete the inbox"


def test_a_failing_test_still_has_its_inbox_deleted(run, mock):
    """Cleanup must not depend on the test passing. An inbox left behind on
    every failure is how a suite silently eats its own quota."""
    result = run(
        """
        def test_it(inbox):
            assert inbox.address
            assert False, "deliberate"
        """
    )
    result.assert_outcomes(failed=1)
    state = mock.state
    assert len(state["created"]) == 1
    assert state["deleted"] == state["created"]


def test_keep_on_failure_leaves_the_inbox_alone(run, mock):
    result = run(
        """
        def test_it(inbox):
            assert False, "deliberate"
        """,
        "--btm-keep-on-failure",
    )
    result.assert_outcomes(failed=1)
    state = mock.state
    assert len(state["created"]) == 1
    assert state["deleted"] == [], "inbox was deleted despite --btm-keep-on-failure"
    result.stdout.fnmatch_lines(["*kept*for inspection*"])


def test_keep_on_failure_still_deletes_when_the_test_passes(run, mock):
    """The flag is about failures. A passing test should not leak an inbox."""
    result = run(
        """
        def test_it(inbox):
            assert inbox.address
        """,
        "--btm-keep-on-failure",
    )
    result.assert_outcomes(passed=1)
    assert mock.state["deleted"] == mock.state["created"]


def test_deleting_early_does_not_break_teardown(run, mock):
    """`delete_inbox` is idempotent in the SDK precisely so this works."""
    result = run(
        """
        def test_it(inbox):
            assert inbox.delete() is True
        """
    )
    result.assert_outcomes(passed=1)
    assert len(mock.state["deleted"]) == 1


def test_inbox_factory_cleans_up_every_inbox(run, mock):
    result = run(
        """
        def test_it(inbox_factory):
            a, b, c = inbox_factory(), inbox_factory(), inbox_factory()
            assert len({a.address, b.address, c.address}) == 3
        """
    )
    result.assert_outcomes(passed=1)
    state = mock.state
    assert len(state["created"]) == 3
    assert sorted(state["deleted"]) == sorted(state["created"])


def test_each_test_gets_its_own_inbox(run, mock):
    result = run(
        """
        seen = []

        def test_one(inbox):
            seen.append(inbox.address)

        def test_two(inbox):
            seen.append(inbox.address)
            assert len(set(seen)) == 2
        """
    )
    result.assert_outcomes(passed=2)
    assert len(mock.state["created"]) == 2


# --------------------------------------------------------------------------- #
# the bound object
# --------------------------------------------------------------------------- #

def test_repr_shows_the_address_and_no_key(run, mock):
    """pytest prints locals when a test fails, so the repr matters."""
    result = run(
        """
        def test_it(inbox):
            assert repr(inbox) == f"<BoundInbox {inbox.address}>"
            assert str(inbox) == inbox.address
            assert "TESTKEY" not in repr(inbox)
        """
    )
    result.assert_outcomes(passed=1)


def test_raw_inbox_and_client_are_reachable(run, mock):
    result = run(
        """
        def test_it(inbox, tempmail):
            assert inbox.inbox.expires_at
            assert inbox.inbox.lifetime_minutes == 120
            assert inbox.client is tempmail
        """
    )
    result.assert_outcomes(passed=1)


def test_list_messages_carries_the_search_metadata(run, mock):
    mock.set(seed_messages=2)
    result = run(
        """
        def test_it(inbox):
            page = inbox.list_messages()
            assert len(page) == 2
            assert page.scan_limit_reached is False
            assert len(inbox.messages()) == 2
        """
    )
    result.assert_outcomes(passed=1)


def test_a_typo_on_the_inbox_fails_loudly(run, mock):
    """Methods are proxied explicitly, not through __getattr__, so a misspelled
    one is an AttributeError rather than a silent None."""
    result = run(
        """
        import pytest

        def test_it(inbox):
            with pytest.raises(AttributeError):
                inbox.wait_for_cod
        """
    )
    result.assert_outcomes(passed=1)


# --------------------------------------------------------------------------- #
# options
# --------------------------------------------------------------------------- #

def test_domain_and_lifetime_reach_the_server(run, mock):
    result = run(
        """
        def test_it(inbox):
            assert inbox.address.endswith("@simtim.site")
        """,
        "--btm-domain=simtim.site",
        "--btm-lifetime=45",
    )
    result.assert_outcomes(passed=1)
    assert mock.state["requested"][0] == {"domain": "simtim.site", "lifetime_minutes": 45}


def test_default_timeout_option_is_applied(run, mock):
    """With no mail and /wait always timing out, a 1-second default must make
    the wait return promptly rather than sitting for the built-in 30."""
    result = run(
        """
        import time

        def test_it(inbox):
            started = time.monotonic()
            assert inbox.wait_for_message() is None
            assert time.monotonic() - started < 8
        """,
        "--btm-timeout=1",
    )
    result.assert_outcomes(passed=1)


def test_a_key_from_an_ini_file_warns(pytester, mock_url, mock):
    """An ini file is the one source of configuration that gets committed."""
    pytester.makeini(
        """
        [pytest]
        btm_api_key = INIKEY
        """
    )
    pytester.makepyfile(
        """
        def test_it(inbox):
            assert inbox.address
        """
    )
    result = pytester.runpytest("-p", "pytest_best_tempmail", f"--btm-base-url={mock_url}", "-q")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*committed to source control*"])


# --------------------------------------------------------------------------- #
# no key
# --------------------------------------------------------------------------- #

def test_without_a_key_tests_still_run(run, mock):
    """The free tier needs no key, so the default must be to run. A plugin that
    skipped here would report success while testing nothing."""
    result = run(
        """
        def test_it(inbox):
            assert inbox.address
        """,
        key=None,
    )
    result.assert_outcomes(passed=1)
    assert len(mock.state["created"]) == 1


def test_skip_without_key_is_opt_in(run, mock):
    result = run(
        """
        def test_it(inbox):
            assert inbox.address
        """,
        "--btm-skip-without-key",
        key=None,
    )
    result.assert_outcomes(skipped=1)
    assert mock.state["created"] == []


def test_the_free_tier_cap_is_explained(run, mock):
    """A raw 429 from a quota the user did not know existed is a bad error."""
    mock.set(create_status=429)
    result = run(
        """
        def test_it(inbox):
            assert inbox.address
        """,
        key=None,
    )
    result.assert_outcomes(errors=1)
    # Both phrases land on one line, so check the whole output rather than
    # fnmatch_lines, which wants each pattern on a line of its own and in order.
    out = result.stdout.str()
    assert "inboxes per day" in out
    assert "BTM_API_KEY" in out
    assert "pricing" in out


def test_a_paid_quota_error_is_not_dressed_up(run, mock):
    """With a key, the SDK's own error should surface rather than being
    replaced by free-tier advice that does not apply."""
    mock.set(create_status=429)
    result = run(
        """
        def test_it(inbox):
            assert inbox.address
        """
    )
    result.assert_outcomes(errors=1)
    assert "inboxes per day" not in result.stdout.str()


def test_paid_only_features_fail_clearly_without_a_key(run, mock):
    mock.set(seed_messages=1)
    result = run(
        """
        import pytest
        from best_tempmail import PaymentRequiredError

        def test_it(inbox):
            with pytest.raises(PaymentRequiredError):
                inbox.wait_for_otp(timeout=3)
        """,
        key=None,
    )
    result.assert_outcomes(passed=1)
