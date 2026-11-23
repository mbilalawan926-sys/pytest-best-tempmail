# Changelog

## 1.0.0

First release.

Fixtures: `inbox` (and `tempmail_inbox` under its full name), `inbox_factory`,
`tempmail`, `tempmail_config`.

### Decisions worth recording

**It wraps the SDK rather than reimplementing it.** Every HTTP call goes through
`best_tempmail.TempMail`, so there is one client in the ecosystem rather than a
second copy of the waiting logic that can drift from it.

**`best-tempmail>=2.0.0` is a hard floor.** In 1.x, `wait_for_message` could not
see mail that had already been delivered, and it silently clamped the caller's
timeout to a single 55-second request. In automated testing the first of those
is the normal case: you create the inbox, trigger the signup, and the email
frequently wins the race against the next line of code. A fixture on 1.x would
inherit both, and the symptom reads as "the email never arrived".

**The inbox is bound to the client.** `inbox.wait_for_code()` rather than
`client.wait_for_code(inbox.address)`. Methods are proxied explicitly instead of
through `__getattr__`, so a typo raises `AttributeError` immediately and
signatures survive introspection.

**Nothing is skipped by default.** A run that passes because no key was
configured reports success while testing nothing.
`--btm-skip-without-key` is opt-in, has no ini equivalent, and its help text
says not to use it in CI.

**Cleanup is best-effort and runs whether the test passed or failed.** A failure
to tidy up prints a note instead of turning a test red.
`--btm-keep-on-failure` keeps the inbox after a failing test so what arrived can
be inspected; passing tests still clean up.

**The key never reaches a fixture value.** `tempmail_config` deliberately omits
it, because fixture values get printed by `--setup-show` and by pytest's own
output when a test fails. `BoundInbox.__repr__` is `<BoundInbox address>` for
the same reason.

**An API key found in an ini file raises a warning.** The ini file is the one
configuration source that gets committed to a repository. It is still supported,
because refusing it outright just pushes people into hardcoding the key in
`conftest.py`, which is worse.

### Verified

22 tests, each running a real sub-pytest through the `pytester` fixture, so what
is asserted is pytest's actual behaviour: collection, fixture ordering, teardown
order and the report hook. Testing the fixture functions in isolation would not
prove any of that, and those are the parts that break.

The mock's `/wait` endpoint never reports a message, so any test that finds mail
proves the inbox was really read. Forcing `include_existing=False` inside the
plugin was confirmed to make that test fail.

One bug the suite caught during development: the mock answered a delete with
`{"deleted": true}` while the SDK checks `success`, which made `delete_inbox`
return False and look like a cleanup failure. Response shapes come from the
live API, never from a guess.
