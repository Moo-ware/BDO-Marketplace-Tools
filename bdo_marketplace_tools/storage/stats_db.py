"""SQLite-backed local stats store (``data/stats.db``).

Owns three small tables:

- ``lifetime_stats``: the lifetime purchase totals previously kept in
  ``local_stats.json``. The first read seeds row 1 from that legacy JSON file
  when it exists, then the JSON file is never written again.
- ``outfit_events``: one timestamped row per detected availability episode
  start and per successful purchase. Rows are only written when something was
  actually newly found or bought, so nothing here sits on the empty-poll hot
  path.
- ``daily_coverage``: per-local-day successful scan counts, so trend charts
  can distinguish "no outfits appeared" from "the monitor was not running".

Timestamps are stored as unix epoch seconds and bucketed to local time in the
query helpers, matching how the user experiences days and hours.

Schema changes are tracked with SQLite's ``PRAGMA user_version``. Version 1 is
the first SQLite stats schema; unversioned prototype databases are promoted in
place without rewriting existing rows.
"""

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, time as dt_time, timedelta

from bdo_marketplace_tools.storage.paths import LOCAL_STATS_PATH, STATS_DB_PATH


DETECTION_EVENT = "detection"
PURCHASE_EVENT = "purchase"
SCHEMA_VERSION = 1

DEFAULT_LIFETIME_STATS = {
    "successful_purchases": 0,
    "silver_spent": 0,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifetime_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    successful_purchases INTEGER NOT NULL DEFAULT 0,
    silver_spent INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS outfit_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('detection', 'purchase')),
    occurred_at INTEGER NOT NULL,
    item_id TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    silver INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outfit_events_type_time
    ON outfit_events (event_type, occurred_at);
CREATE TABLE IF NOT EXISTS daily_coverage (
    day TEXT PRIMARY KEY,
    scans INTEGER NOT NULL DEFAULT 0
);
"""


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    _migrate(connection)
    return connection


def _database_version(connection):
    row = connection.execute("PRAGMA user_version").fetchone()
    try:
        return int(row[0])
    except (TypeError, ValueError, IndexError):
        return 0


def _set_database_version(connection, version):
    connection.execute(f"PRAGMA user_version = {int(version)}")


def _migrate(connection):
    version = _database_version(connection)
    connection.executescript(_SCHEMA)
    if version < SCHEMA_VERSION:
        _set_database_version(connection, SCHEMA_VERSION)
    connection.commit()


def _read_legacy_lifetime_json(path):
    if path is None:
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_lifetime_stats(path=STATS_DB_PATH, legacy_json_path=LOCAL_STATS_PATH):
    """Load lifetime totals, seeding once from the retired JSON stats file."""
    with closing(_connect(path)) as connection:
        row = connection.execute(
            "SELECT successful_purchases, silver_spent FROM lifetime_stats WHERE id = 1"
        ).fetchone()
        if row is not None:
            return {"successful_purchases": _safe_int(row[0]), "silver_spent": _safe_int(row[1])}

        legacy = _read_legacy_lifetime_json(legacy_json_path)
        seeded = {
            "successful_purchases": _safe_int(legacy.get("successful_purchases")),
            "silver_spent": _safe_int(legacy.get("silver_spent")),
        }
        connection.execute(
            "INSERT INTO lifetime_stats (id, successful_purchases, silver_spent, updated_at)"
            " VALUES (1, ?, ?, ?)",
            (seeded["successful_purchases"], seeded["silver_spent"], _now_iso()),
        )
        connection.commit()
        return seeded


def save_lifetime_stats(successful_purchases, silver_spent, path=STATS_DB_PATH):
    with closing(_connect(path)) as connection:
        connection.execute(
            "INSERT INTO lifetime_stats (id, successful_purchases, silver_spent, updated_at)"
            " VALUES (1, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " successful_purchases = excluded.successful_purchases,"
            " silver_spent = excluded.silver_spent,"
            " updated_at = excluded.updated_at",
            (_safe_int(successful_purchases), _safe_int(silver_spent), _now_iso()),
        )
        connection.commit()


def _insert_events(rows, path):
    if not rows:
        return
    with closing(_connect(path)) as connection:
        connection.executemany(
            "INSERT INTO outfit_events (event_type, occurred_at, item_id, quantity, silver)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()


def record_detection_events(buy_list, at=None, path=STATS_DB_PATH):
    """Record one detection row per newly started availability episode.

    Callers are responsible for filtering repeated sightings of an already
    active item. Rows keep the normal monitor shape: ``[item_id, stock, price]``.
    """
    occurred_at = int(at if at is not None else datetime.now().timestamp())
    rows = []
    for item_id, stock, _price in buy_list:
        quantity = _safe_int(stock)
        if quantity <= 0:
            continue
        rows.append((DETECTION_EVENT, occurred_at, str(item_id), quantity, 0))
    _insert_events(rows, path)


def record_purchase_events(purchase_records, at=None, path=STATS_DB_PATH):
    """Record one purchase row per successful purchase record from ``APIHandler.buy_item``."""
    occurred_at = int(at if at is not None else datetime.now().timestamp())
    rows = []
    for record in purchase_records:
        count = _safe_int(record.get("count", 1))
        if count <= 0:
            continue
        silver = _safe_int(record.get("price")) * count
        rows.append((PURCHASE_EVENT, occurred_at, str(record.get("item_id", "")), count, silver))
    _insert_events(rows, path)


def add_daily_coverage(scan_counts, path=STATS_DB_PATH):
    """Accumulate ``{iso_day: scans}`` batches into the per-day coverage table."""
    rows = [(day, _safe_int(count)) for day, count in scan_counts.items() if _safe_int(count) > 0]
    if not rows:
        return
    with closing(_connect(path)) as connection:
        connection.executemany(
            "INSERT INTO daily_coverage (day, scans) VALUES (?, ?)"
            " ON CONFLICT(day) DO UPDATE SET scans = scans + excluded.scans",
            rows,
        )
        connection.commit()


def load_trends(days, path=STATS_DB_PATH, today=None):
    """One-shot trends snapshot over the last ``days`` local days (today inclusive).

    Returns ``{"daily": [...], "weekday": {...}, "hourly": [[...]]}``:

    - ``daily``: oldest-first dicts ``{"day", "detected", "purchased", "scans"}``
    - ``weekday``: ``{"detected": [7], "purchased": [7]}`` indexed Monday..Sunday
    - ``hourly``: 7x24 weekday-by-hour matrix of detected quantities
    """
    today = today or date.today()
    start_day = today - timedelta(days=days - 1)
    start_epoch = int(datetime.combine(start_day, dt_time.min).timestamp())

    daily = {
        start_day + timedelta(days=offset): {"detected": 0, "purchased": 0, "scans": 0}
        for offset in range(days)
    }
    weekday = {"detected": [0] * 7, "purchased": [0] * 7}
    hourly = [[0] * 24 for _ in range(7)]

    with closing(_connect(path)) as connection:
        events = connection.execute(
            "SELECT event_type, occurred_at, quantity FROM outfit_events"
            " WHERE occurred_at >= ? ORDER BY occurred_at",
            (start_epoch,),
        ).fetchall()
        coverage_rows = connection.execute(
            "SELECT day, scans FROM daily_coverage WHERE day >= ?",
            (start_day.isoformat(),),
        ).fetchall()

    for event_type, occurred_at, quantity in events:
        moment = datetime.fromtimestamp(occurred_at)
        bucket = daily.get(moment.date())
        if bucket is None:
            continue
        quantity = _safe_int(quantity)
        if event_type == DETECTION_EVENT:
            bucket["detected"] += quantity
            weekday["detected"][moment.weekday()] += quantity
            hourly[moment.weekday()][moment.hour] += quantity
        elif event_type == PURCHASE_EVENT:
            bucket["purchased"] += quantity
            weekday["purchased"][moment.weekday()] += quantity

    for day_text, scans in coverage_rows:
        try:
            day = date.fromisoformat(day_text)
        except (TypeError, ValueError):
            continue
        bucket = daily.get(day)
        if bucket is not None:
            bucket["scans"] = _safe_int(scans)

    return {
        "daily": [{"day": day, **daily[day]} for day in sorted(daily)],
        "weekday": weekday,
        "hourly": hourly,
    }
