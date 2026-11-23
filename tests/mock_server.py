"""A stand-in for the API, built around one deliberate refusal.

**`/wait` never reports a message.** It always answers `200 {"timed_out": true}`.

That is the whole point of this mock. The long-poll endpoint signals mail
*arriving*, so it cannot see a message that landed before the call. A client
that only long-polls therefore never finds mail that is already sitting in the
inbox, which in automated testing is the normal case rather than the edge case.
The GitHub Action for this API shipped with 21 passing tests and failed on the
first real email for exactly that reason, because its mock answered `/wait`
whenever asked and so could not express the difference.

With this mock, any test that finds a pre-seeded message proves the inbox was
actually read. Nothing else can produce that result.

It also carries a `/__control` endpoint so a test can make inbox creation fail
the way the free tier's daily cap fails, and a `/__state` endpoint so a test can
assert that teardown really deleted what it created.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

DOMAINS = ["martaz.site", "simtim.site", "linsal.site"]

# Reset between tests through /__control.
STATE = {
    "created": [],        # addresses, in order
    "deleted": [],        # addresses, in order
    "requested": [],      # the body of each create, to check options are passed
    "create_status": 200,  # set to 429 or 402 to make creation fail
    "seed_messages": 0,    # messages waiting in each new inbox
    "wait_calls": 0,
}

_counter = {"n": 0}
_lock = threading.Lock()


def _seeded(n):
    return [
        {
            "id": f"msg{i + 1}",
            "from": '"Verify" <no-reply@example.com>',
            "subject": f"Your code is {100000 + i}",
            "date": datetime.now(timezone.utc).isoformat(),
            "has_attachments": False,
        }
        for i in range(n)
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence
        pass

    # -- plumbing ----------------------------------------------------------- #

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("x-ratelimit-limit", "2000")
        self.send_header("x-ratelimit-remaining", "1999")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _keyed(self):
        return bool(self.headers.get("x-api-key"))

    # -- routes ------------------------------------------------------------- #

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()

        if path == "/__control":
            with _lock:
                if body.get("reset"):
                    STATE.update(created=[], deleted=[], requested=[],
                                 create_status=200, seed_messages=0, wait_calls=0)
                for k in ("create_status", "seed_messages"):
                    if k in body:
                        STATE[k] = body[k]
            return self._send(200, {"ok": True})

        if path == "/v1/inboxes":
            with _lock:
                STATE["requested"].append(body)
                status = STATE["create_status"]
            if status != 200:
                return self._send(status, {
                    "error": "quota_exceeded",
                    "message": "Daily inbox limit reached for this IP address.",
                })
            with _lock:
                _counter["n"] += 1
                n = _counter["n"]
            domain = body.get("domain") or DOMAINS[0]
            address = f"{body.get('username') or f'test{n:04d}'}@{domain}"
            with _lock:
                STATE["created"].append(address)
            now = datetime.now(timezone.utc)
            asked = body.get("lifetime_minutes")
            given = min(asked, 120) if asked else 120
            payload = {
                "address": address,
                "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "expires_at": (now + timedelta(minutes=given)).strftime("%Y-%m-%d %H:%M:%S"),
                "lifetime_minutes": given,
            }
            if asked and asked > given:
                payload["note"] = f"Clamped to {given} minutes by your plan."
            return self._send(200, payload)

        self._send(404, {"error": "not_found", "message": path})

    def do_DELETE(self):
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["v1", "inboxes"]:
            address = unquote(parts[2])
            with _lock:
                known = address in STATE["created"]
                already = address in STATE["deleted"]
                if known and not already:
                    STATE["deleted"].append(address)
            if not known or already:
                return self._send(404, {"error": "not_found", "message": "No such inbox"})
            # The field is "success". Checked against the SDK rather than
            # guessed: it does bool(response.get("success")), so a mock
            # answering {"deleted": true} makes delete_inbox return False and
            # look like a cleanup failure.
            return self._send(200, {"success": True})
        self._send(404, {"error": "not_found", "message": path})

    def do_GET(self):
        u = urlparse(self.path)
        path, parts = u.path, [p for p in u.path.split("/") if p]

        if path == "/__state":
            with _lock:
                return self._send(200, dict(STATE))

        if path == "/v1/health":
            return self._send(200, {"status": "ok", "version": "mock"})
        if path == "/v1/domains":
            return self._send(200, {"domains": DOMAINS})
        if path == "/v1/usage":
            return self._send(200, {
                "plan": "developer" if self._keyed() else "free",
                "paid": self._keyed(),
                "hourly": {"limit": 2000, "remaining": 1999, "used": 1, "resets_at": 0},
                "key": {"last_four": "1234"} if self._keyed() else None,
            })

        # /v1/inboxes/{address}/wait  -> never reports anything. See the docstring.
        if len(parts) == 4 and parts[3] == "wait":
            with _lock:
                STATE["wait_calls"] += 1
            return self._send(200, {"timed_out": True})

        # /v1/inboxes/{address}/messages
        if len(parts) == 4 and parts[3] == "messages":
            address = unquote(parts[2])
            with _lock:
                live = address in STATE["created"] and address not in STATE["deleted"]
                msgs = _seeded(STATE["seed_messages"])
            if not live:
                return self._send(404, {"error": "not_found", "message": "No such inbox"})
            # parse_qs, not a substring test: "limit=100" contains "limit=1".
            query = parse_qs(u.query or "")
            limit = int(query.get("limit", [50])[0])
            page = msgs[:limit]
            return self._send(200, {
                "address": address,
                "messages": page,
                "limit": limit,
                "offset": int(query.get("offset", [0])[0]),
                "returned": len(page),
                "has_more": len(page) < len(msgs),
                "scanned": len(msgs),
                "scan_limit_reached": False,
            })

        # /v1/inboxes/{address}/messages/{id}/otp
        if len(parts) == 6 and parts[5] == "otp":
            if not self._keyed():
                return self._send(402, {
                    "error": "payment_required",
                    "message": "OTP extraction requires a paid plan.",
                    "plan": "Free",
                })
            return self._send(200, {
                "message_id": unquote(parts[4]),
                "code": "889231",
                "confidence": "high",
                "candidates": [],
            })

        self._send(404, {"error": "not_found", "message": path})


def start(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
