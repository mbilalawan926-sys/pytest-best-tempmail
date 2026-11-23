# pytest-best-tempmail

Real disposable inboxes as pytest fixtures. Receive actual email in your tests and read the verification code out of it.

```bash
pip install pytest-best-tempmail
```

Nothing to import and nothing to register. pytest finds the fixtures through its entry point.

## Quick start

```python
def test_signup_sends_a_verification_code(inbox, page):
    page.goto("/signup")
    page.fill("#email", inbox.address)
    page.click("#submit")

    page.fill("#code", inbox.wait_for_code(subject="verify"))
    page.click("#verify")

    assert page.is_visible("#welcome")
```

The inbox is created before the test and deleted after it, whether the test passes or fails.

No API key is needed to try it. The free tier is keyless, though it allows only 3 inboxes per day per IP address, which a test suite exhausts immediately. For real use:

```bash
export BTM_API_KEY=btm_sk_live_...
```

## Why a fixture rather than just the SDK

Because `inbox.wait_for_code()` is the whole point. Without it every call carries the address around:

```python
# without this plugin
client = TempMail(api_key=os.environ["BTM_API_KEY"])
inbox = client.create_inbox()
try:
    code = client.wait_for_code(inbox.address, subject="verify")
finally:
    client.delete_inbox(inbox.address)
```

The fixture does the creating, the binding and the cleaning up, and shares one HTTP session across the whole run.

## The fixtures

| Fixture | Scope | What it gives you |
| --- | --- | --- |
| `inbox` | function | A fresh inbox, deleted after the test |
| `tempmail_inbox` | function | The same thing under its full name, in case `inbox` clashes |
| `inbox_factory` | function | Call it for each inbox you need; all are cleaned up together |
| `tempmail` | session | The underlying `TempMail` client |
| `tempmail_config` | session | Resolved settings, for your own fixtures |

### `inbox`

```python
inbox.address                 # "a7f3k2@martaz.site"
str(inbox)                    # the same, so f-strings just work

inbox.wait_for_message(subject="verify")   # MessageSummary or None
inbox.wait_for_code(subject="verify")      # str, raises if there is no code
inbox.wait_for_otp()                       # OtpResult, with candidates
inbox.wait_for_action_url()                # str, the link to click
inbox.wait_for_links()                     # LinksResult, links by type

inbox.messages()                           # list, newest first
inbox.list_messages(q="ORD-123")           # with paging and search metadata
inbox.message(message_id)                  # the full message
inbox.links(message_id)
inbox.attachment(message_id, index)

inbox.delete()                             # early, if you want
inbox.inbox                                # the raw SDK model: expires_at, note
inbox.client                               # the shared client
```

Every waiting method takes `timeout` plus `subject`, `from_` and `q`.

**Pass `subject` or `from_` whenever you can.** Without a filter the first message to arrive wins, and in a real signup flow a welcome email often beats the verification email by a second. An unfiltered wait then hands you the wrong message and the test fails a long way from the cause.

### `inbox_factory`

```python
def test_invitation(inbox_factory):
    inviter = inbox_factory()
    invitee = inbox_factory()

    send_invite(from_=inviter.address, to=invitee.address)
    assert "invited you" in invitee.wait_for_message().subject
```

### Your own fixtures

```python
import pytest
from pytest_best_tempmail import BoundInbox

@pytest.fixture
def verified_user(inbox: BoundInbox, api):
    api.register(inbox.address)
    api.verify(inbox.address, inbox.wait_for_code())
    return inbox.address
```

## Configuration

Settings resolve in this order: **command line, then environment, then ini file.**

| Setting | Command line | Environment | ini |
| --- | --- | --- | --- |
| API key | `--btm-api-key` | `BTM_API_KEY` | `btm_api_key` |
| Inbox domain | `--btm-domain` | `BTM_DOMAIN` | `btm_domain` |
| Inbox lifetime (minutes) | `--btm-lifetime` | `BTM_LIFETIME_MINUTES` | `btm_lifetime_minutes` |
| Default wait timeout | `--btm-timeout` | `BTM_TIMEOUT` | `btm_timeout` |
| API base URL | `--btm-base-url` | `BTM_BASE_URL` | `btm_base_url` |

**Use the environment variable for the key.** A key on the command line is visible to anything that can list processes, and CI systems echo the command they ran. A key in an ini file gets committed to your repository, so the plugin warns if it finds one there.

```ini
[pytest]
btm_domain = martaz.site
btm_timeout = 60
```

### `--btm-keep-on-failure`

Keeps the inbox when a test fails, so you can look at what actually arrived instead of re-running and hoping. Passing tests still clean up. The inbox expires on its own either way.

```bash
pytest --btm-keep-on-failure
```

### `--btm-skip-without-key`

Skips inbox tests when no key is configured, for contributors who don't have one.

**Never use this in CI.** A suite that skips reports success while testing nothing, which is worse than failing. That is why it is off by default and why there is no ini option for it.

## Timeouts

`wait_for_*` honours the timeout you give it in full, across as many requests as it takes. The server holds a single long poll for up to 55 seconds; that is the server's limit, not yours.

Setting a filter changes the strategy: the plugin's underlying client polls the message listing instead of long-polling, because a long poll cannot filter and would return the first message to arrive whether or not it is the one you asked for.

## Cleanup

Deletion is best-effort on purpose. `delete_inbox` is idempotent, and a cleanup failure prints a note rather than failing the test. Nothing is more irritating than a green test turned red by tidying up after itself.

Deleting an inbox yourself inside a test is fine. Teardown tolerates it.

## Requirements

Python 3.9+, pytest 8+, and `best-tempmail` 2.0.0 or newer.

That lower bound is deliberate. In 1.x, `wait_for_message` could not see mail that had already been delivered, and it silently clamped your timeout to a single 55-second request. In automated testing the first of those is the normal case, not an edge case: you create the inbox, trigger the signup, and the email frequently wins the race against your next line of code. A fixture built on 1.x would inherit both bugs, and the symptom would look like "the email never arrived".

## Plans

| | Free | Founders / Developer | Pro |
| --- | :-: | :-: | :-: |
| Requests/hour | 150 | 2,000 | 5,000 |
| Inbox creation | 3/day per IP | unlimited | unlimited |
| OTP and link extraction | no | yes | yes |
| Attachment downloads | no | no | yes |
| Commercial use | no | yes | yes |

[Pricing](https://best-tempmail.com/api/pricing) · [API docs](https://best-tempmail.com/api)

## License

MIT
