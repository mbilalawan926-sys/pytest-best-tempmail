"""pytest fixtures for real disposable email inboxes.

Installing this package is enough: pytest discovers the fixtures through the
``pytest11`` entry point, so nothing needs importing.

    def test_signup(inbox, page):
        page.fill("#email", inbox.address)
        page.click("#submit")
        page.fill("#code", inbox.wait_for_code(subject="verify"))

`BoundInbox` is exported for type annotations in your own fixtures.
"""

from .plugin import BoundInbox

__version__ = "1.0.0"
__all__ = ["BoundInbox"]
