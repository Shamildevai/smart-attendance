from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from attendance_app.services import scanner_bridge_service as bridge


class FakePhone:
    def __init__(self):
        self.version = "1.1.0"
        self.api_key = "phone-key"
        self.last_seq = 0
        self.items: list[dict] = []
        self.status = 0
        self.requests: list[dict] = []
        self.host = "127.0.0.1"
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/send"

    def add(self, code: str) -> int:
        self.last_seq += 1
        self.items.append({"seq": self.last_seq, "code": code, "at": 1700000000000})
        return self.last_seq


def _handler(phone: FakePhone):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            phone.requests.append(
                {"path": parsed.path, "query": query, "key": self.headers.get("X-Api-Key")}
            )
            if parsed.path.rstrip("/") != "/scans":
                self._send(404, {"ok": False})
                return
            if phone.status:
                self._send(phone.status, {"ok": False, "error": "forced"})
                return
            if phone.api_key and self.headers.get("X-Api-Key") != phone.api_key:
                self._send(401, {"ok": False, "error": "Invalid or missing API key"})
                return
            if query.get("resync") in ("1", "true"):
                scans = []
            else:
                after = int(query.get("after") or 0)
                scans = [item for item in phone.items if item["seq"] > after]
            self._send(
                200,
                {
                    "ok": True,
                    "version": phone.version,
                    "lastSeq": phone.last_seq,
                    "scans": scans,
                },
            )

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            pass

    return Handler


@pytest.fixture()
def phone():
    fake = FakePhone()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bridge.reset()
    yield fake
    server.shutdown()
    server.server_close()
    bridge.reset()


def test_handshake_skips_previous_scans(phone):
    phone.add("1001")
    phone.add("1002")

    result = bridge.poll(phone.url, phone.api_key)
    assert result["state"] == "ok"
    assert result["handshake"] is True
    assert result["scans"] == []
    assert result["last_seq"] == 2
    assert phone.requests[-1]["query"] == {"resync": "1"}

    noise = bridge.poll(phone.url, phone.api_key)
    assert noise["handshake"] is False
    assert noise["scans"] == []
    assert phone.requests[-1]["query"] == {"after": "2"}


def test_new_scans_are_delivered_once(phone):
    bridge.poll(phone.url, phone.api_key)
    phone.add("2001")
    phone.add("2002")

    first = bridge.poll(phone.url, phone.api_key)
    assert [scan["code"] for scan in first["scans"]] == ["2001", "2002"]
    assert first["pending"] == 0

    second = bridge.poll(phone.url, phone.api_key)
    assert second["scans"] == []

    phone.add("2003")
    third = bridge.poll(phone.url, phone.api_key)
    assert [scan["code"] for scan in third["scans"]] == ["2003"]


def test_phone_restart_resyncs_sequence(phone):
    bridge.poll(phone.url, phone.api_key)
    phone.add("3001")
    phone.add("3002")
    assert len(bridge.poll(phone.url, phone.api_key)["scans"]) == 2

    phone.items.clear()
    phone.last_seq = 0
    phone.add("3003")

    restarted = bridge.poll(phone.url, phone.api_key)
    assert restarted["state"] == "resynced"
    assert restarted["scans"] == []

    resumed = bridge.poll(phone.url, phone.api_key)
    assert [scan["code"] for scan in resumed["scans"]] == ["3003"]


def test_unauthorized_and_outdated_and_offline(phone):
    phone.api_key = "other-key"
    assert bridge.poll(phone.url, "wrong-key")["state"] == "unauthorized"

    bridge.reset()
    phone.status = 404
    assert bridge.poll(phone.url, phone.api_key)["state"] == "outdated"

    bridge.reset()
    phone.status = 500
    assert bridge.poll(phone.url, phone.api_key)["state"] == "error"

    bridge.reset()
    phone.status = 0
    phone.version = "1.0.1"
    bridge.poll(phone.url, phone.api_key)
    phone.add("4001")
    assert bridge.poll(phone.url, phone.api_key)["state"] == "outdated"

    assert bridge.poll("http://127.0.0.1:1/send", "k")["state"] == "offline"
    assert bridge.poll("", "k")["state"] == "not_configured"


def test_poll_api_uses_configured_gateway(client, csrf, phone):
    response = client.put(
        "/api/settings",
        json={"sms_mobile_url": phone.url, "sms_mobile_key": phone.api_key},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()

    bridge.reset()
    phone.add("5001")

    handshake = client.get("/api/scanner/bridge/poll")
    assert handshake.status_code == 200
    assert handshake.get_json()["handshake"] is True

    phone.add("5002")
    polled = client.get("/api/scanner/bridge/poll").get_json()
    assert polled["ok"] is True
    assert polled["state"] == "ok"
    assert [scan["code"] for scan in polled["scans"]] == ["5002"]
    assert phone.requests[-1]["key"] == phone.api_key
