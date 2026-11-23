"""Harness for the plugin's own tests.

Every test runs a real sub-pytest through the ``pytester`` fixture, against the
mock in mock_server.py. Testing a pytest plugin any other way means asserting on
fixture functions in isolation, which does not prove that pytest collects them,
registers the hook, or runs teardown in the right order. Those are precisely the
things that break.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_server  # noqa: E402

pytest_plugins = ["pytester"]


@pytest.fixture(scope="session")
def mock_url():
    server = mock_server.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


class Mock:
    def __init__(self, base):
        self.root = base[: -len("/v1")]

    def _post(self, payload):
        req = urllib.request.Request(
            f"{self.root}/__control",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def reset(self, **kw):
        self._post({"reset": True})
        if kw:
            self._post(kw)

    def set(self, **kw):
        self._post(kw)

    @property
    def state(self):
        with urllib.request.urlopen(f"{self.root}/__state", timeout=5) as r:
            return json.load(r)


@pytest.fixture
def mock(mock_url):
    m = Mock(mock_url)
    m.reset()
    return m


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """Strip the real environment.

    pytester runs the sub-pytest in this same process, so a BTM_API_KEY in the
    developer's shell would silently change what these tests exercise. The
    keyless tests in particular would pass for the wrong reason.
    """
    for var in ("BTM_API_KEY", "BTM_BASE_URL", "BTM_DOMAIN",
                "BTM_LIFETIME_MINUTES", "BTM_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def run(pytester, mock_url):
    """Run a sub-pytest with the plugin loaded and pointed at the mock."""

    def go(source: str, *args: str, key: str = "TESTKEY"):
        pytester.makepyfile(source)
        argv = ["-p", "pytest_best_tempmail", f"--btm-base-url={mock_url}", "-q"]
        if key is not None:
            argv.append(f"--btm-api-key={key}")
        argv.extend(args)
        return pytester.runpytest(*argv)

    return go
