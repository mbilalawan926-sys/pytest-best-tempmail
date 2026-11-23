"""pytest fixtures for real disposable inboxes.

Design notes, because each one is a decision rather than a default:

**It wraps the SDK, it does not reimplement it.** Every HTTP call goes through
`best_tempmail.TempMail`, so this plugin inherits the fixed `wait_for_message`
(which looks at mail already delivered, and honours the caller's timeout across
as many requests as it takes) instead of carrying a second copy of that logic
that could drift. That is also why the dependency is pinned at `>=2.0.0`: 1.x
had both of those bugs, and a fixture built on it would quietly inherit them.

**The inbox fixture is bound to the client.** A fixture that handed back a bare
address, leaving every call as `client.wait_for_code(inbox.address, ...)`, would
be a thin excuse for a plugin. `inbox.wait_for_code()` is the whole point.

**Nothing is skipped by default.** A test run that silently passes because no
key was configured is worse than one that fails, because it reports success
while testing nothing. `--btm-skip-without-key` exists for contributors, and
the help text says not to use it in CI.
"""

from __future__ import annotations

import os
import warnings
from typing import Any, Dict, List, Optional

import pytest

from best_tempmail import (
    Inbox,
    LinksResult,
    Message,
    MessageList,
    MessageSummary,
    OtpResult,
    PaymentRequiredError,
    RateLimitError,
    TempMail,
)

DEFAULT_BASE_URL = "https://api.best-tempmail.com/v1"
DEFAULT_TIMEOUT = 30

# How many inboxes the keyless free tier allows per IP per day. Used only to
# write a useful error message; the server is the authority.
FREE_TIER_DAILY_INBOXES = 3


# --------------------------------------------------------------------------- #
# options
# --------------------------------------------------------------------------- #

def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("best-tempmail", "disposable email inboxes")

    group.addoption(
        "--btm-api-key",
        default=None,
        metavar="KEY",
        help=(
            "API key. Prefer the BTM_API_KEY environment variable: a key on the "
            "command line is visible to anything that can list processes, and "
            "CI systems echo the command they ran."
        ),
    )
    group.addoption("--btm-base-url", default=None, metavar="URL",
                    help="API base URL. For pointing tests at a mock.")
    group.addoption("--btm-domain", default=None, metavar="DOMAIN",
                    help="Domain to create inboxes on. Defaults to the server's choice.")
    group.addoption("--btm-lifetime", default=None, type=int, metavar="MINUTES",
                    help="Inbox lifetime in minutes. Capped by your plan.")
    group.addoption("--btm-timeout", default=None, type=int, metavar="SECONDS",
                    help=f"Default wait timeout. Defaults to {DEFAULT_TIMEOUT}.")
    group.addoption(
        "--btm-keep-on-failure",
        action="store_true",
        default=False,
        help=(
            "Do not delete the inbox when a test fails, so its messages can be "
            "inspected. The inbox still expires on its own."
        ),
    )
    group.addoption(
        "--btm-skip-without-key",
        action="store_true",
        default=False,
        help=(
            "Skip tests that need an inbox when no API key is configured. For "
            "contributors without a key. NEVER use this in CI: a suite that "
            "skips silently reports success while testing nothing."
        ),
    )

    parser.addini("btm_api_key", "API key. Prefer BTM_API_KEY; an ini file gets committed.", default=None)
    parser.addini("btm_base_url", "API base URL.", default=None)
    parser.addini("btm_domain", "Domain to create inboxes on.", default=None)
    parser.addini("btm_lifetime_minutes", "Inbox lifetime in minutes.", default=None)
    parser.addini("btm_timeout", "Default wait timeout in seconds.", default=None)


def _setting(config: pytest.Config, cli: str, ini: str, env: str) -> Optional[str]:
    """Command line, then environment, then ini file.

    The command line wins because passing it is an explicit act. The ini file
    loses because it is the one source that is committed to a repository.
    """
    value = config.getoption(cli)
    if value is not None:
        return str(value)
    value = os.environ.get(env)
    if value:
        return value
    value = config.getini(ini)
    if value:
        return str(value)
    return None


# --------------------------------------------------------------------------- #
# the bound inbox
# --------------------------------------------------------------------------- #

class BoundInbox:
    """An inbox with the client already attached.

    Methods are proxied explicitly rather than through ``__getattr__`` so that a
    typo raises AttributeError at once, signatures survive introspection, and
    editors can complete them.
    """

    def __init__(self, client: TempMail, inbox: Inbox, default_timeout: int) -> None:
        self._client = client
        self._inbox = inbox
        self._default_timeout = default_timeout

    # -- what it is ---------------------------------------------------------- #

    @property
    def address(self) -> str:
        return self._inbox.address

    @property
    def inbox(self) -> Inbox:
        """The underlying SDK model: expires_at, lifetime_minutes, note."""
        return self._inbox

    @property
    def client(self) -> TempMail:
        """The shared client, for anything not proxied here."""
        return self._client

    def __str__(self) -> str:
        return self.address

    def __repr__(self) -> str:
        # Shown by pytest when a test fails with this in scope, so it carries
        # the address and nothing else. The client is deliberately not included
        # because its repr could grow to include configuration.
        return f"<BoundInbox {self.address}>"

    def _t(self, timeout: Optional[int]) -> int:
        return self._default_timeout if timeout is None else timeout

    # -- waiting ------------------------------------------------------------- #

    def wait_for_message(self, timeout: Optional[int] = None, **kw: Any) -> Optional[MessageSummary]:
        return self._client.wait_for_message(self.address, self._t(timeout), **kw)

    def wait_for_otp(self, timeout: Optional[int] = None, **kw: Any) -> Optional[OtpResult]:
        return self._client.wait_for_otp(self.address, self._t(timeout), **kw)

    def wait_for_code(self, timeout: Optional[int] = None, **kw: Any) -> str:
        return self._client.wait_for_code(self.address, self._t(timeout), **kw)

    def wait_for_links(self, timeout: Optional[int] = None, **kw: Any) -> Optional[LinksResult]:
        return self._client.wait_for_links(self.address, self._t(timeout), **kw)

    def wait_for_action_url(self, timeout: Optional[int] = None, **kw: Any) -> str:
        return self._client.wait_for_action_url(self.address, self._t(timeout), **kw)

    # -- reading ------------------------------------------------------------- #

    def messages(self, limit: int = 100, **kw: Any) -> List[MessageSummary]:
        return self._client.get_messages(self.address, limit, **kw)

    def list_messages(self, **kw: Any) -> MessageList:
        """Like ``messages()`` but carries has_more, scanned and
        scan_limit_reached, which is what makes an empty result interpretable."""
        return self._client.list_messages(self.address, **kw)

    def message(self, message_id: str) -> Message:
        return self._client.get_message(self.address, message_id)

    def links(self, message_id: str) -> LinksResult:
        return self._client.get_links(self.address, message_id)

    def attachment(self, message_id: str, index: int) -> Any:
        return self._client.download_attachment(self.address, message_id, index)

    # -- lifecycle ----------------------------------------------------------- #

    def delete(self) -> bool:
        """Delete it early. Teardown tolerates an already-deleted inbox."""
        return self._client.delete_inbox(self.address)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def tempmail_config(pytestconfig: pytest.Config) -> Dict[str, Any]:
    """Resolved settings. Session-scoped, and it never returns the key."""
    key = _setting(pytestconfig, "btm_api_key", "btm_api_key", "BTM_API_KEY")

    if key and not pytestconfig.getoption("btm_api_key") and not os.environ.get("BTM_API_KEY"):
        # It came from the ini file, which lives in version control.
        warnings.warn(
            "best-tempmail: the API key was read from an ini file. Those are "
            "committed to source control. Use the BTM_API_KEY environment "
            "variable instead.",
            UserWarning,
            stacklevel=1,
        )

    timeout = _setting(pytestconfig, "btm_timeout", "btm_timeout", "BTM_TIMEOUT")
    lifetime = _setting(pytestconfig, "btm_lifetime", "btm_lifetime_minutes", "BTM_LIFETIME_MINUTES")

    return {
        "has_key": bool(key),
        "base_url": _setting(pytestconfig, "btm_base_url", "btm_base_url", "BTM_BASE_URL") or DEFAULT_BASE_URL,
        "domain": _setting(pytestconfig, "btm_domain", "btm_domain", "BTM_DOMAIN"),
        "lifetime_minutes": int(lifetime) if lifetime else None,
        "timeout": int(timeout) if timeout else DEFAULT_TIMEOUT,
        "keep_on_failure": bool(pytestconfig.getoption("btm_keep_on_failure")),
        "skip_without_key": bool(pytestconfig.getoption("btm_skip_without_key")),
        # Not in this dict: the key itself. This dict is a fixture value, so it
        # can be printed by a failing test or dumped by --setup-show.
        "_key": key,
    }


@pytest.fixture(scope="session")
def tempmail(tempmail_config: Dict[str, Any]) -> Any:
    """The shared client. One HTTP session for the whole run."""
    client = TempMail(
        api_key=tempmail_config["_key"],
        base_url=tempmail_config["base_url"],
    )
    try:
        yield client
    finally:
        client.close()


def _require_key(config: Dict[str, Any]) -> None:
    if config["has_key"]:
        return
    if config["skip_without_key"]:
        pytest.skip("no best-tempmail API key configured (--btm-skip-without-key)")
    # Keyless is allowed: the free tier needs no key. Nothing to do.


def _create(client: TempMail, config: Dict[str, Any]) -> Inbox:
    try:
        return client.create_inbox(
            domain=config["domain"],
            lifetime_minutes=config["lifetime_minutes"],
        )
    except (RateLimitError, PaymentRequiredError) as exc:
        if not config["has_key"]:
            pytest.fail(
                "Could not create an inbox on the keyless free tier: "
                f"{exc}\n\n"
                f"The free tier allows {FREE_TIER_DAILY_INBOXES} inboxes per day "
                "per IP address, which a test suite exhausts immediately. Set "
                "BTM_API_KEY to a paid key.\n"
                "Keys: https://best-tempmail.com/api/pricing",
                pytrace=False,
            )
        raise


@pytest.fixture
def tempmail_inbox(request: pytest.FixtureRequest, tempmail: TempMail,
                   tempmail_config: Dict[str, Any]) -> Any:
    """A fresh inbox for one test, deleted afterwards.

    Deletion is best-effort on purpose. `delete_inbox` is idempotent in the SDK
    and a cleanup failure is not a test failure: nothing is more annoying than a
    green test turned red by tidying up after itself. The inbox expires on its
    own regardless.
    """
    _require_key(tempmail_config)
    inbox = _create(tempmail, tempmail_config)
    bound = BoundInbox(tempmail, inbox, tempmail_config["timeout"])

    try:
        yield bound
    finally:
        failed = getattr(request.node, "_btm_rep_call", None)
        failed = bool(failed and failed.failed)

        if failed and tempmail_config["keep_on_failure"]:
            print(f"\n[best-tempmail] kept {inbox.address} for inspection (test failed)")
        else:
            try:
                tempmail.delete_inbox(inbox.address)
            except Exception as exc:  # noqa: BLE001 - cleanup must not fail a test
                print(f"\n[best-tempmail] could not delete {inbox.address}: {exc}")


@pytest.fixture
def inbox(tempmail_inbox: BoundInbox) -> BoundInbox:
    """Short alias for `tempmail_inbox`.

    Override it in your own conftest if the name clashes with something.
    """
    return tempmail_inbox


@pytest.fixture
def inbox_factory(request: pytest.FixtureRequest, tempmail: TempMail,
                  tempmail_config: Dict[str, Any]) -> Any:
    """Make several inboxes in one test. All are cleaned up together.

    Useful for testing invitations, transfers, or anything with two parties.

        def test_invite(inbox_factory):
            inviter, invitee = inbox_factory(), inbox_factory()
    """
    _require_key(tempmail_config)
    created: List[BoundInbox] = []

    def make() -> BoundInbox:
        inbox = _create(tempmail, tempmail_config)
        bound = BoundInbox(tempmail, inbox, tempmail_config["timeout"])
        created.append(bound)
        return bound

    try:
        yield make
    finally:
        failed = getattr(request.node, "_btm_rep_call", None)
        failed = bool(failed and failed.failed)

        for bound in created:
            if failed and tempmail_config["keep_on_failure"]:
                print(f"\n[best-tempmail] kept {bound.address} for inspection (test failed)")
                continue
            try:
                tempmail.delete_inbox(bound.address)
            except Exception as exc:  # noqa: BLE001
                print(f"\n[best-tempmail] could not delete {bound.address}: {exc}")


# --------------------------------------------------------------------------- #
# test outcome, so --btm-keep-on-failure can know
# --------------------------------------------------------------------------- #

@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    """Record each phase's result on the item.

    A fixture's teardown cannot otherwise see whether the test passed. The
    attribute is prefixed so it cannot collide with the same recipe in a user's
    own conftest, which is a common one to copy in.
    """
    report = yield
    setattr(item, f"_btm_rep_{report.when}", report)
    return report
