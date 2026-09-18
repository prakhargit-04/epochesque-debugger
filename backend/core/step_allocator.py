import sqlite3


def next_step_id(conn: sqlite3.Connection, run_id: str) -> int:
    # Transaction-guarded allocator. Caller must use an open transaction.
    row = conn.execute("SELECT next_step FROM step_counters WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        step_id = 1
        conn.execute("INSERT INTO step_counters(run_id, next_step) VALUES (?, ?)", (run_id, 2))
        return step_id
    step_id = int(row[0])
    conn.execute("UPDATE step_counters SET next_step = ? WHERE run_id = ?", (step_id + 1, run_id))
    return step_id
