"""The plugin against the real API.

    BTM_API_KEY=btm_sk_live_... pytest examples/ -q

Everything in tests/ runs against a mock, which means it agrees with whatever
the author assumed. That is not a hypothetical weakness: the GitHub Action for
this API passed 21 mocked tests and then failed on the first real email, and the
attachment field names in these SDKs were guessed wrong until a real message was
inspected. During development of this plugin, the mock answered a delete with
`{"deleted": true}` while the API uses `success`.

WHAT THIS CANNOT COVER, stated rather than left to be discovered: nothing here
receives an email. Without sending mail to the inbox, OTP extraction, link
extraction and the already-arrived branch of the wait are not exercised against
real mail. Those are covered in tests/ against captured fixtures.

Cost: a handful of requests and about five inboxes of quota.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BTM_API_KEY"),
    reason="BTM_API_KEY is not set, so there is nothing to check against",
)


def test_an_inbox_is_real(inbox):
    assert "@" in inbox.address
    assert inbox.inbox.expires_at
    print(f"\n  {inbox.address} expires {inbox.inbox.expires_at}")


def test_a_new_inbox_is_conclusively_empty(inbox):
    page = inbox.list_messages()
    assert len(page) == 0
    # scan_limit_reached is what makes an empty result interpretable, and it
    # was missing from the published docs entirely, so confirm production
    # really sends it.
    assert page.scan_limit_reached is False
    assert page.conclusively_empty is True


def test_the_timeout_is_honoured_in_full(inbox):
    """The server caps one long poll at 55 seconds. A 70-second wait must span
    several requests rather than giving up at the server's limit, which is the
    bug that shipped in version 1.x of the SDK."""
    started = time.monotonic()
    assert inbox.wait_for_message(timeout=70) is None
    elapsed = time.monotonic() - started
    assert elapsed >= 60, f"gave up after {elapsed:.1f}s on a 70s timeout"
    print(f"\n  waited {elapsed:.1f}s across multiple long polls, then reported nothing")


def test_deleting_twice_is_safe(inbox):
    assert inbox.delete() is True
    assert inbox.delete() is False  # and teardown will try a third time


def test_the_factory_makes_distinct_inboxes(inbox_factory):
    a, b = inbox_factory(), inbox_factory()
    assert a.address != b.address


def test_the_plan_is_what_we_think_it_is(tempmail):
    usage = tempmail.get_usage()
    assert usage.paid, f"expected a paid plan, got {usage.plan}"
    assert usage.hourly.limit > 0
    # Never print the key. Four characters is enough to tell keys apart, which
    # is exactly why the API returns only that.
    print(f"\n  plan {usage.plan}, key ...{usage.key.last_four}, "
          f"{usage.hourly.remaining} of {usage.hourly.limit} left this hour")
