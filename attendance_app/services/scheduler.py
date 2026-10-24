from __future__ import annotations

import threading
from datetime import datetime

from ..db import bool_setting, connect, get_settings
from ..utils import is_school_day, parse_time_on_date
from .attendance_service import close_period, get_closure, list_periods
from .messaging_service import resolve_channel_settings
from .sms_service import probe_gateway


class AbsenceScheduler(threading.Thread):
    """Marks absences once a period's absent-cutoff time is reached.

    It never sends SMS automatically: messages must be approved by an operator
    from the absences screen or the SMS queue.
    """

    def __init__(self, app) -> None:
        super().__init__(name="absence-sweeper", daemon=True)
        self.app = app
        self._stop_event = threading.Event()
        self.interval = int(app.config.get("SCHEDULER_INTERVAL", 25))

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._stop_event.wait(3.0)
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - keep the sweeper alive
                self.app.logger.exception("Absence sweeper tick failed")
            self._stop_event.wait(max(5, self.interval))

    def tick(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        closed: list[dict] = []
        with self.app.app_context():
            conn = connect(self.app.config["DB_PATH"])
            try:
                settings = get_settings(conn)
                if not bool_setting(settings, "auto_sweep", True):
                    return closed
                if not is_school_day(now.date(), settings.get("school_weekdays", "0,1,2,3,4")):
                    return closed
                today = now.date().isoformat()
                for period in list_periods(conn, include_inactive=False):
                    cutoff = parse_time_on_date(now.date(), period["absent_cutoff_time"])
                    if now < cutoff:
                        continue
                    if get_closure(conn, period["id"], today):
                        continue
                    result = close_period(
                        conn,
                        period["id"],
                        today,
                        closed_by="SYSTEM",
                        note="إغلاق تلقائي بعد بلوغ حد الغياب",
                    )
                    closed.append(
                        {
                            "period_id": period["id"],
                            "period_name": period["period_name"],
                            "inserted": result["inserted"],
                            "date": today,
                        }
                    )
                    self.app.logger.info(
                        "Auto-closed period %s for %s; %s absences inserted",
                        period["period_name"], today, result["inserted"],
                    )
            finally:
                conn.close()
        return closed


class GatewayHeartbeat(threading.Thread):
    """Keeps the phone gateway aware that the desktop app is online.

    The Android app shows a connection indicator based on the last request it
    received from SmartAttendance, so idle desktops must still send a heartbeat.
    """

    def __init__(self, app, interval: int = 25) -> None:
        super().__init__(name="gateway-heartbeat", daemon=True)
        self.app = app
        self._stop_event = threading.Event()
        self.interval = max(10, interval)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._stop_event.wait(8.0)
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - keep the heartbeat alive
                self.app.logger.debug("Gateway heartbeat failed", exc_info=True)
            self._stop_event.wait(self.interval)

    def tick(self) -> None:
        with self.app.app_context():
            conn = connect(self.app.config["DB_PATH"])
            try:
                settings = get_settings(conn)
                if settings.get("sms_gateway", "local") != "local":
                    return
                resolved = resolve_channel_settings(settings, "mobile")
                url = (resolved.get("sms_api_url") or "").strip()
                if not url:
                    return
                probe_gateway(
                    url,
                    resolved.get("sms_api_key", ""),
                    force=True,
                    timeout=3,
                )
            finally:
                conn.close()


def start_scheduler(app) -> AbsenceScheduler | None:
    if app.config.get("DISABLE_SCHEDULER") or app.config.get("TESTING"):
        return None
    scheduler = AbsenceScheduler(app)
    scheduler.start()
    app.extensions["absence_scheduler"] = scheduler
    from .update_service import UpdateScheduler

    updater = UpdateScheduler(app)
    updater.start()
    app.extensions["update_scheduler"] = updater
    heartbeat = GatewayHeartbeat(app)
    heartbeat.start()
    app.extensions["gateway_heartbeat"] = heartbeat
    return scheduler
