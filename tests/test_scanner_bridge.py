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
        self.pin = "1234"
        self.pair_key = ""
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

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                payload = {}
            phone.requests.append(
                {"path": parsed.path, "method": "POST", "pin": payload.get("pin")}
            )
            if parsed.path.rstrip("/") != "/pair":
                self._send(404, {"ok": False})
                return
            if phone.pin and payload.get("pin") != phone.pin:
                self._send(401, {"ok": False})
                return
            self._send(
                200,
                {
                    "ok": True,
                    "apiKey": phone.pair_key or phone.api_key,
                    "version": phone.version,
                    "port": phone.port,
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


def _serve(fake: FakePhone) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def phone():
    fake = FakePhone()
    server = _serve(fake)
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


@pytest.fixture()
def make_phone():
    servers: list[ThreadingHTTPServer] = []

    def _make() -> FakePhone:
        fake = FakePhone()
        servers.append(_serve(fake))
        return fake

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


def _settings(primary: FakePhone, extra: list[FakePhone]) -> dict[str, str]:
    phones = [
        {"id": bridge.phone_id(item.url), "name": f"Gate {index + 2}", "url": item.url, "key": item.api_key}
        for index, item in enumerate(extra)
    ]
    return {
        "sms_mobile_url": primary.url,
        "sms_mobile_key": primary.api_key,
        "scanner_phones": bridge.dump_phones(phones),
    }


def test_poll_all_merges_phones_with_independent_cursors(phone, make_phone):
    second = make_phone()
    settings = _settings(phone, [second])

    phone.add("A1")
    second.add("B1")
    handshake = bridge.poll_all(settings)
    assert handshake["state"] == "ok"
    assert handshake["scans"] == []
    assert {row["state"] for row in handshake["phones"]} == {"ok"}

    phone.add("A2")
    second.add("B2")
    result = bridge.poll_all(settings)
    pairs = {(scan["phone_id"], scan["code"]) for scan in result["scans"]}
    assert pairs == {
        (bridge.phone_id(phone.url), "A2"),
        (bridge.phone_id(second.url), "B2"),
    }
    assert sum(1 for scan in result["scans"] if scan["primary"]) == 1
    assert bridge.poll_all(settings)["scans"] == []

    second.add("B3")
    only_second = bridge.poll_all(settings)["scans"]
    assert [scan["code"] for scan in only_second] == ["B3"]
    assert only_second[0]["phone"] == "Gate 2"


def test_poll_all_reports_partial_when_one_phone_fails(phone, make_phone):
    broken = make_phone()
    broken.status = 500
    settings = _settings(phone, [broken])

    result = bridge.poll_all(settings)
    assert result["state"] == "partial"
    assert "1 من 2" in result["detail"]
    rows = {row["primary"]: row for row in result["phones"]}
    assert rows[True]["state"] == "ok"
    assert rows[False]["state"] == "error"


def test_probe_phones_keeps_cursors(phone, make_phone):
    second = make_phone()
    settings = _settings(phone, [second])

    info = bridge.probe_phones(settings)
    assert info["configured"] is True
    assert info["primary"]["url"] == phone.url
    assert [row["name"] for row in info["phones"]] == ["Gate 2"]
    assert all(row["state"] == "ok" for row in [info["primary"], *info["phones"]])
    assert all(request["query"] == {"resync": "1"} for request in phone.requests + second.requests)

    assert bridge.poll_all(settings)["handshake"] is True


def test_scan_targets_do_not_duplicate_primary(phone):
    settings = {
        "sms_mobile_url": phone.url,
        "sms_mobile_key": phone.api_key,
        "scanner_phones": bridge.dump_phones(
            [{"url": phone.url, "key": "duplicate", "name": "Duplicate"}]
        ),
    }
    targets = bridge.scan_targets(settings)
    assert len(targets) == 1
    assert targets[0]["primary"] is True
    assert targets[0]["key"] == phone.api_key


def test_phone_registry_register_update_limit_remove():
    settings = {"scanner_phones": ""}
    raw = bridge.register_phone(settings, name="Gate 2", url="192.168.1.21:8080", key="first-key")
    phones = bridge.parse_phones(raw)
    assert len(phones) == 1
    assert phones[0]["name"] == "Gate 2"
    assert phones[0]["url"] == "http://192.168.1.21:8080/send"

    settings["scanner_phones"] = raw
    raw = bridge.register_phone(settings, name="", url="http://192.168.1.21:8080", key="second-key")
    phones = bridge.parse_phones(raw)
    assert len(phones) == 1
    assert phones[0]["key"] == "second-key"
    assert phones[0]["name"] == "Gate 2"

    for index in range(bridge.MAX_PHONES - 1):
        settings["scanner_phones"] = bridge.register_phone(
            settings, name="", url=f"192.168.1.{30 + index}:8080", key="k"
        )
    assert len(bridge.additional_phones(settings)) == bridge.MAX_PHONES
    with pytest.raises(ValueError, match="لا يمكن إضافة"):
        bridge.register_phone(settings, name="", url="192.168.1.99:8080", key="k")

    target_id = phones[0]["id"]
    raw, removed = bridge.remove_phone(settings, target_id)
    assert removed is True
    assert all(item["id"] != target_id for item in bridge.parse_phones(raw))
    assert bridge.remove_phone(settings, "missing")[1] is False


def test_parse_phones_caps_and_drops_invalid_entries():
    raw = json.dumps(
        [
            {"url": f"192.168.2.{index}:8080", "key": "k", "name": "n"}
            for index in range(12)
        ]
        + [{"url": "", "key": "k"}, "junk"]
    )
    assert len(bridge.parse_phones(raw)) == bridge.MAX_PHONES
    assert bridge.parse_phones("not json") == []


def test_scanner_phone_api_pair_and_remove(client, csrf, phone, make_phone):
    second = make_phone()
    second.api_key = "paired-key"
    second.pair_key = "paired-key"

    configured = client.put(
        "/api/settings",
        json={"sms_mobile_url": phone.url, "sms_mobile_key": phone.api_key},
        headers=csrf,
    )
    assert configured.status_code == 200, configured.get_json()

    listed = client.get("/api/scanner/phones").get_json()
    assert listed["primary"]["url"] == phone.url
    assert listed["phones"] == []
    assert listed["max"] == bridge.MAX_PHONES

    paired = client.post(
        "/api/scanner/phones/pair",
        json={"url": second.url, "pin": "1234", "name": "Gate 2"},
        headers=csrf,
    )
    assert paired.status_code == 200, paired.get_json()
    body = paired.get_json()
    assert body["message"] == "أُضيف هاتف المسح بنجاح."
    assert body["phones"][0]["name"] == "Gate 2"
    assert "key" not in body["phones"][0]

    duplicate = client.post(
        "/api/scanner/phones/pair",
        json={"url": phone.url, "pin": "1234"},
        headers=csrf,
    )
    assert duplicate.status_code == 400
    assert "الرئيسية" in duplicate.get_json()["error"]["message"]

    listed = client.get("/api/scanner/phones").get_json()
    assert len(listed["phones"]) == 1
    assert listed["phones"][0]["name"] == "Gate 2"

    target = listed["phones"][0]["id"]
    removed = client.delete(f"/api/scanner/phones/{target}", headers=csrf)
    assert removed.status_code == 200
    assert removed.get_json()["message"] == "تمت إزالة هاتف المسح."
    assert client.delete(f"/api/scanner/phones/{target}", headers=csrf).status_code == 404


def test_scanner_phone_api_validates_url(client, csrf):
    response = client.post(
        "/api/scanner/phones/pair",
        json={"url": "", "pin": "1234"},
        headers=csrf,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "أدخل رابط بوابة الهاتف الظاهر في التطبيق."
