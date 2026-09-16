"""Durable, paper-only market-session scheduler.

The scheduler deliberately does not place orders.  It calls an injected orchestration
function once for each regular US market session and supplies a stable idempotency
key.  Alpaca's clock and calendar are injected so this module is easy to test and so
holidays, shortened sessions, and daylight-saving changes come from the broker.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
DEFAULT_STATE_PATH = Path(os.getenv("MOSQUITO_SCHEDULER_FILE", "/data/mosquito-scheduler.json"))


class RetryPending(RuntimeError):
    """The session is healthy but an intraday condition is not satisfied yet."""


def _aware(value: datetime, fallback_tz=EASTERN) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=fallback_tz)
    return value


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _session_date(value) -> date:
    if isinstance(value, datetime):
        return _aware(value).astimezone(EASTERN).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _session_time(day: date, value) -> datetime:
    if isinstance(value, datetime):
        return _aware(value).astimezone(EASTERN)
    if isinstance(value, time):
        return datetime.combine(day, value, tzinfo=value.tzinfo or EASTERN).astimezone(EASTERN)
    # Alpaca calendar values have historically appeared as either HH:MM strings or
    # RFC3339 timestamps, so accept both without tying this module to an SDK version.
    text = str(value)
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00"))).astimezone(EASTERN)
    except ValueError:
        parsed = time.fromisoformat(text)
        return datetime.combine(day, parsed, tzinfo=parsed.tzinfo or EASTERN).astimezone(EASTERN)


@dataclass(frozen=True)
class MarketSession:
    day: date
    opens_at: datetime
    closes_at: datetime

    @property
    def key(self) -> str:
        return self.day.isoformat()


class JsonRunStore:
    """Atomic state store shared safely by scheduler threads in one process."""

    def __init__(self, path: Path | str = DEFAULT_STATE_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            try:
                value = json.loads(self.path.read_text())
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                return {}

    def save(self, value: dict) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".mosquito-scheduler-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(value, handle, separators=(",", ":"), sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self.path)
            finally:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass


class MarketScheduler:
    """Start one paper orchestration cycle per open market session.

    ``get_clock`` must return an Alpaca-like clock (``timestamp``, ``is_open``,
    ``next_open``). ``get_calendar`` receives ISO start/end dates. ``orchestrate``
    receives keyword arguments ``session_date``, ``idempotency_key``, and
    ``paper_only``.  Failed callbacks may be retried with the same key; a successful
    session is permanently suppressed, including after process restarts.
    """

    def __init__(
        self,
        get_clock: Callable[[], object],
        get_calendar: Callable[..., Iterable[object]],
        orchestrate: Callable[..., object],
        *,
        state_path: Path | str = DEFAULT_STATE_PATH,
        paper_only: bool = True,
        now: Callable[[], datetime] | None = None,
    ):
        if not paper_only:
            raise ValueError("MarketScheduler is hard-locked to paper-only operation")
        self.get_clock = get_clock
        self.get_calendar = get_calendar
        self.orchestrate = orchestrate
        self.store = JsonRunStore(state_path)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._tick_lock = threading.Lock()

    def _calendar(self, day: date) -> list[MarketSession]:
        start, end = day - timedelta(days=1), day + timedelta(days=1)
        try:
            rows = self.get_calendar(start=start.isoformat(), end=end.isoformat())
        except TypeError:
            rows = self.get_calendar(start.isoformat(), end.isoformat())
        sessions = []
        for row in rows or []:
            session_day = _session_date(_field(row, "date"))
            sessions.append(MarketSession(
                session_day,
                _session_time(session_day, _field(row, "open")),
                _session_time(session_day, _field(row, "close")),
            ))
        return sessions

    def tick(self) -> dict:
        """Evaluate current broker state and launch if this session is due."""
        if not self._tick_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            clock = self.get_clock()
            raw_now = _field(clock, "timestamp") or self.now()
            current = _aware(raw_now, timezone.utc).astimezone(EASTERN)
            session = next((s for s in self._calendar(current.date()) if s.day == current.date()), None)
            if session is None:
                return {"status": "closed", "reason": "non_trading_day"}
            # The calendar supplies the DST-correct opening instant.  Clock state is
            # authoritative during exceptional halts/closures.
            if current < session.opens_at:
                return {"status": "waiting", "opens_at": session.opens_at.isoformat()}
            if current > session.closes_at or not bool(_field(clock, "is_open", False)):
                return {"status": "closed", "reason": "market_not_open"}

            state = self.store.load()
            if state.get("completed_session") == session.key:
                return {"status": "already_completed", "session_date": session.key}

            key = f"mosquito-paper-open-{session.key}"
            attempt = int(state.get("attempt", 0)) + 1 if state.get("session_date") == session.key else 1
            state.update(session_date=session.key, idempotency_key=key, status="running",
                         attempt=attempt, started_at=current.isoformat(), paper_only=True)
            self.store.save(state)
            try:
                result = self.orchestrate(session_date=session.key,
                                          idempotency_key=key, paper_only=True)
            except RetryPending as exc:
                state.update(status="waiting_entry_gate", checked_at=self.now().isoformat(),
                             pending_reason=str(exc)[:160])
                state.pop("error_type", None)
                self.store.save(state)
                return {"status": "waiting_entry_gate", "session_date": session.key,
                        "idempotency_key": key}
            except Exception as exc:
                state.update(status="failed", failed_at=self.now().isoformat(),
                             error_type=type(exc).__name__)
                self.store.save(state)
                return {"status": "failed", "session_date": session.key,
                        "idempotency_key": key, "error_type": type(exc).__name__}
            state.update(status="completed", completed_session=session.key,
                         completed_at=self.now().isoformat())
            state.pop("error_type", None)
            self.store.save(state)
            return {"status": "completed", "session_date": session.key,
                    "idempotency_key": key, "result": result}
        finally:
            self._tick_lock.release()

    def run_forever(self, stop_event: threading.Event, poll_seconds: float = 15.0) -> None:
        """Poll forever; caller owns lifecycle and can stop cleanly."""
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while not stop_event.is_set():
            try:self.tick()
            except Exception as exc:
                state=self.store.load();state.update(status="connection_error",error_type=type(exc).__name__,failed_at=self.now().isoformat());self.store.save(state)
            stop_event.wait(poll_seconds)
