import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.config import DATABASE_PATH, MAX_TRACE_PAYLOAD
from backend.core.step_allocator import next_step_id
from backend.models.trace_event import TraceEvent


def _truncate(value: dict):
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw.encode()) <= MAX_TRACE_PAYLOAD:
        return value, False
    preview = raw[: MAX_TRACE_PAYLOAD // 2]
    return {"truncated": True, "preview": preview, "original_size_bytes": len(raw.encode())}, True


def _row_to_event(row) -> TraceEvent:
    return TraceEvent(
        run_id=row["run_id"], event_id=row["event_id"], parent_event_id=row["parent_event_id"],
        step_id=row["step_id"], turn_id=row["turn_id"], timestamp=row["timestamp"],
        event_type=row["event_type"], component=row["component"], status=row["status"],
        input=json.loads(row["input_json"] or "{}"), output=json.loads(row["output_json"] or "{}"),
        error=json.loads(row["error_json"]) if row["error_json"] else None,
        usage=json.loads(row["usage_json"] or "{}"), latency_ms=row["latency_ms"] or 0,
        cost_usd=row["cost_usd"] or 0.0, metadata=json.loads(row["metadata_json"] or "{}"),
        truncated=bool(row["truncated"]),
    )


class DB:
    def __init__(self, path: str = DATABASE_PATH):
        self.path = path

    @contextmanager
    def conn(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self, schema_path: str = "schema.sql"):
        with self.conn() as conn, open(schema_path, encoding="utf-8") as f:
            conn.executescript(f.read())

    def create_run(self, goal: str) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as conn:
            conn.execute("INSERT INTO runs(run_id, goal, status, created_at) VALUES (?, ?, ?, ?)", (run_id, goal, "running", now))
            conn.execute("INSERT INTO step_counters(run_id, next_step) VALUES (?, 1)", (run_id,))
        return run_id

    def get_run(self, run_id: str):
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_runs(self):
        with self.conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()]

    def update_run_status(self, run_id: str, status: str, ended: bool = False):
        with self.conn() as conn:
            if ended:
                conn.execute("UPDATE runs SET status=?, ended_at=? WHERE run_id=?", (status, datetime.now(timezone.utc).isoformat(), run_id))
            else:
                conn.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))

    def try_lock_run(self, run_id: str) -> bool:
        with self.conn() as conn:
            cur = conn.execute("UPDATE runs SET status = 'busy' WHERE run_id = ? AND status != 'busy'", (run_id,))
            return cur.rowcount > 0

    def next_step_id(self, run_id: str):
        with self.conn() as conn:
            return next_step_id(conn, run_id)

    def record_event(self, event: TraceEvent):
        input_value, input_truncated = _truncate(event.input)
        output_value, output_truncated = _truncate(event.output)
        truncated = bool(event.truncated or input_truncated or output_truncated)
        if truncated:
            event = event.model_copy(update={"input": input_value, "output": output_value, "truncated": True})
        with self.conn() as conn:
            conn.execute(
                """INSERT INTO trace_events
                (event_id, run_id, parent_event_id, step_id, turn_id, timestamp, event_type,
                 component, status, input_json, output_json, error_json, usage_json, latency_ms,
                 cost_usd, metadata_json, truncated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.event_id, event.run_id, event.parent_event_id, event.step_id,
                    event.turn_id, event.timestamp.isoformat(), event.event_type, event.component,
                    event.status, json.dumps(event.input), json.dumps(event.output),
                    event.error.model_dump_json() if event.error else None, event.usage.model_dump_json(),
                    event.latency_ms, event.cost_usd, json.dumps(event.metadata), int(event.truncated),
                ),
            )

    def get_events(self, run_id: str, limit: int = 500, offset: int = 0):
        with self.conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trace_events WHERE run_id=? ORDER BY step_id LIMIT ? OFFSET ?",
                (run_id, min(limit, 500), max(offset, 0)),
            ).fetchall()
        return [_row_to_event(r) for r in rows]

    def get_event(self, event_id: str):
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM trace_events WHERE event_id=?", (event_id,)).fetchone()
            return _row_to_event(row) if row else None

    def get_event_by_step(self, run_id: str, step_id: int):
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM trace_events WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone()
            return _row_to_event(row) if row else None

    def save_state(self, run_id: str, step_id: int, state: dict):
        with self.conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_states(run_id, step_id, state_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, step_id, json.dumps(state), datetime.now(timezone.utc).isoformat()),
            )

    def get_state(self, run_id: str, step_id: int):
        with self.conn() as conn:
            row = conn.execute("SELECT state_json FROM agent_states WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone()
            return json.loads(row["state_json"]) if row else None
