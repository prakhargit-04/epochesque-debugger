CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS trace_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_event_id TEXT,
    step_id INTEGER NOT NULL,
    turn_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    component TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT,
    output_json TEXT,
    error_json TEXT,
    usage_json TEXT,
    latency_ms INTEGER,
    cost_usd REAL,
    metadata_json TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trace_run_step ON trace_events(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_trace_run_step ON trace_events(run_id, step_id);

CREATE TABLE IF NOT EXISTS agent_states (
    run_id TEXT NOT NULL,
    step_id INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, step_id)
);

CREATE TABLE IF NOT EXISTS step_counters (
    run_id TEXT PRIMARY KEY,
    next_step INTEGER NOT NULL
);
