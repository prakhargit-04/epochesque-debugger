# EPOCHESQUE 2.0 — LOCKED MASTER SPEC
### Final, consolidated. This supersedes all prior drafts. Nothing else is in scope.

**One sentence:** An evidence-backed AI execution debugger. It traces every step of an agent run, diagnoses failures only from cited trace evidence, validates those citations deterministically, and proves recovery worked using the actual retry result — never a self-reported claim.

**The chain that must survive a hostile demo:**
```
REAL USER GOAL
  → REAL ANTHROPIC CALL
  → REAL AGENT DECISION
  → REAL TOOL INPUT
  → CONTROLLED FAILURE INJECTION (at the execution boundary, never replacing the LLM/decision path)
  → FAILED TRACE EVENT
  → DETERMINISTIC EVIDENCE WINDOW
  → LLM DIAGNOSIS
  → CITATION VALIDATOR
  → APPLICATION-COMPUTED CONFIDENCE
  → RECOVERY ACTION ALLOWLIST
  → MODIFIED RETRY
  → ACTUAL TOOL RESULT (read, never written-back-and-trusted)
  → SUCCESS / BLOCKED
  → METRICS
  → REPLAY
```
If you are ever unsure what to work on next, work on this chain.

---

## 0. TECH STACK (frozen)

| Layer | Choice |
|---|---|
| Backend | Python 3.11 + FastAPI |
| DB | SQLite (`epochesque.db`) |
| Data layer | Raw `sqlite3` + light wrapper, no ORM |
| LLM | Gemini API via `google-genai` SDK, behind an `LLMAdapter` interface — demo model `gemini-3.5-flash` (Free Tier: input/output free of charge, rate-limited, not for EU/EEA/UK/CH, free-tier usage trains Google's models) |
| Sandboxed exec | `subprocess` + timeout + output cap + AST import/builtin blocklist — call this a **"constrained execution environment,"** never "sandbox" |
| Frontend | React + Vite + Tailwind |
| Testing | `pytest` |
| Schema validation | Pydantic v2 for every contract — this IS the contract-conformance layer, no separate contract-test subsystem |

No: multi-agent architecture, RAG, vector DB, GitHub/VS Code integration, voice, unnecessary auth, Evidence Graph UI, unbounded code execution, Docker/VM sandboxing.

**All limits live in `backend/config.py`. No module hardcodes a number.**

```
LLM_PROVIDER=gemini
GOOGLE_API_KEY=
LLM_MODEL=gemini-3.5-flash
DATABASE_PATH=./epochesque.db
MAX_EXECUTION_TIME=10
MAX_OUTPUT_SIZE=51200
MAX_TRACE_PAYLOAD=102400
MAX_RETRIES=2            # application-level recovery attempts
MAX_LLM_RETRIES=1        # LLM infra-failure retries — a DISTINCT counter, never conflated with MAX_RETRIES
MAX_EVENTS_PER_RUN=500
EVIDENCE_WINDOW_SIZE=6
EVIDENCE_PARENT_HOP_LIMIT=5
CONTEXT_BUDGET=8000
CONTEXT_TRIGGER_RATIO=0.8
LATEST_TURNS_PRESERVED=3
```

---

## 1. SOURCE OF TRUTH RULES

- `trace_events` = historical execution truth. **Trace events are never mutated after write.** A recovery event's outcome is not written back onto itself — it is derived at read-time from the actual `tool_call` event at its `retry_step_id`.
- `AgentState` = current state snapshot, captured immediately after the step it names.
- Frontend = visualization only. Never authoritative for tokens, cost, failure count, recovery count, confidence, or event ordering.
- LLM = proposer only. Never authoritative for: confidence, evidence validity, recovery eligibility, recovery action selection, or whether recovery succeeded.
- Actual execution event = sole authority on whether a retry succeeded.

---

## 2. FROZEN CONTRACTS

### 2.1 TraceEvent
```python
class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

class ErrorInfo(BaseModel):
    type: str
    message: str

class TraceEvent(BaseModel):
    run_id: str
    event_id: str                      # evt_<uuid>
    parent_event_id: Optional[str] = None
    step_id: int                       # MUST come from core.step_allocator.next_step_id — never local
    turn_id: int
    timestamp: datetime
    event_type: Literal["llm_call","tool_call","context_update",
                         "agent_decision","diagnosis","recovery"]
    component: str
    status: Literal["success","failed","running","skipped"]
    input: dict = {}
    output: dict = {}
    error: Optional[ErrorInfo] = None
    usage: Usage = Usage()
    latency_ms: int = 0
    cost_usd: float = 0.0
    metadata: dict = {}
    truncated: bool = False            # true if input/output exceeded MAX_TRACE_PAYLOAD and was cut
```

### 2.2 AgentState
```python
class WorkingMemory(BaseModel):
    variables: dict = {}
    facts: dict = {}
    flags: dict = {}
    last_error: Optional[dict] = None

class AgentState(BaseModel):
    run_id: str
    step_id: int                       # snapshot taken immediately AFTER this step's event persisted
    goal: str
    messages: list = []
    context_tokens_estimated: int      # NEVER conflated with usage.total_tokens (LLM-reported)
    context_budget: int
    token_count_method: str = "tiktoken_cl100k"
    available_tools: list = []
    working_memory: WorkingMemory = WorkingMemory()   # LLM NEVER mutates this directly — controller/tool code only
    pending_action: Optional[dict] = None
    status: Literal["idle","thinking","blocked","recovering","done"]
```

### 2.3 Diagnosis
```python
class Claim(BaseModel):
    text: str
    evidence_event_ids: list[str]

class Diagnosis(BaseModel):
    failure_event_id: str
    tier: Literal["deterministic","llm"]
    failure_type: str
    root_cause: str
    cause_type: Literal["direct_observation","supported_inference","unsupported_speculation"]
    claims: list[Claim]
    confidence: Literal["high","medium","low","insufficient"]   # SET ONLY by confidence_gate.py
    recovery_action_suggested: Optional[str] = None   # MUST be validated against ALLOWED_RECOVERY_ACTIONS before dispatch
    resolved: Optional[bool] = None
```

### 2.4 RecoveryAttempt
```python
class RecoveryAttempt(BaseModel):
    failed_step: int
    diagnosis_event_id: str    # references the DIAGNOSIS event, never failure_event_id
    attempt: int                # 1 or 2, never 3
    action: str                 # MUST be in ALLOWED_RECOVERY_ACTIONS
    modified_input: dict        # MUST materially differ from failed input after normalization — I6
    retry_step_id: Optional[int] = None
    result: Literal["pending","success","failed"] = "pending"   # derived, never manually overwritten after write
```

```python
ALLOWED_RECOVERY_ACTIONS = {"regenerate_code", "retry_tool", "correct_tool_input"}
```

---

## 3. DATABASE SCHEMA

Three tables + one counter table. Diagnosis and recovery are stored **as trace events**, not separate tables.

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,   -- running | busy | completed | blocked | recovered | failed
    created_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE trace_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_event_id TEXT,
    step_id INTEGER NOT NULL,
    turn_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    component TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT, output_json TEXT, error_json TEXT, usage_json TEXT,
    latency_ms INTEGER, cost_usd REAL, metadata_json TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE UNIQUE INDEX uq_trace_run_step ON trace_events(run_id, step_id);  -- I1 enforced at DB level, not just in code
CREATE INDEX idx_trace_run_step ON trace_events(run_id, step_id);

CREATE TABLE agent_states (
    run_id TEXT NOT NULL, step_id INTEGER NOT NULL, state_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, step_id)
);

CREATE TABLE step_counters (
    run_id TEXT PRIMARY KEY, next_step INTEGER NOT NULL
);
```

---

## 4. STEP ID ALLOCATION — CRITICAL

`step_id` must be strictly increasing, unique per run, assigned by **one** centralized, transaction-guarded allocator:

```python
next_step_id(run_id)  # backend/core/step_allocator.py
```

Forbidden: `len(events) + 1`, `failure_event.step_id + 1`, `step_id = 0` with a "caller should overwrite" comment, or any module inventing its own numbering. The DB's `UNIQUE(run_id, step_id)` index is a second, independent enforcement layer in case a future bug bypasses the allocator.

---

## 5. API CONTRACT

```
POST   /runs                          → {goal} → {run_id}
GET    /runs
GET    /runs/{run_id}                 → run detail + backend-computed metrics
GET    /runs/{run_id}/events?limit=100&offset=0
GET    /runs/{run_id}/state/{step_id}
POST   /runs/{run_id}/execute         → advances one step; 409 RUN_BUSY if a step is already executing for this run
POST   /runs/{run_id}/challenge       → {goal, injection_type?}
```
Error shape:
```json
{"error": {"code": "...", "message": "...", "details": {}}}
```
`400 / 404 / 409 / 422 / 500`.

**Concurrency:** a run has an implicit lock — while a step is executing, `run.status = "busy"`; a second `/execute` call during that window returns `409 RUN_BUSY` rather than racing the step allocator or interleaving events.

**Frontend never computes.** `GET /runs/{run_id}` returns pre-computed `total_tokens`, `total_cost_usd`, `event_count`, `failure_count`, `recovery_attempts`, `recovery_success_rate` (null if 0 attempts, never 0%).

---

## 6. THE SIX INVARIANTS

One function, `assert_invariants(run_id, db)`, called once per externally-visible state transition (end of `/execute`, end of `/challenge`) — **never recursively inside the DB layer itself**.

- **I1** — step_id strictly increasing, no duplicates (also enforced at the DB layer independently)
- **I2** — replay makes zero external calls (proven in tests via mock assertion, not a runtime assert)
- **I3** — invalid citations → confidence is never `high` or `medium`
- **I4** — `insufficient` or `low` confidence → no recovery event follows referencing that diagnosis
- **I5** — recovery attempts ≤ 2
- **I6** — retry input materially differs from the failed input after normalization (stripping only non-behavioral fields like `timestamp`/`attempt_id`) — a metadata-only or comment-only change does **not** satisfy this

In dev/test mode, violations raise. In prod mode, violations are logged as warnings, never silently dropped, never crash the live demo.

---

## 7. EVIDENCE WINDOW

Built once, **before** diagnosis, and fixed for the rest of that diagnosis pass:

1. the failed event itself
2. `parent_event_id` chain walked backward, hard-capped at `EVIDENCE_PARENT_HOP_LIMIT`
3. the originating `agent_decision` for that step
4. the `AgentState` immediately before the failure
5. up to `EVIDENCE_WINDOW_SIZE` bounded preceding same-turn events

Event IDs are **sorted deterministically** before being sent to the LLM — never rely on Python `set` ordering. The allowed set is frozen before the LLM call; do not re-query the full database after the LLM responds.

---

## 8. CITATION VALIDATOR + CONFIDENCE GATE

```python
def validate_citations(claims, allowed_event_ids) -> bool:
    # True only if every cited event_id in every claim is in allowed_event_ids
    ...

def has_citations(claims) -> bool: ...

def compute_confidence(tier, citations_valid, has_citations, failure_type_matches_evidence) -> str:
    """THE APPLICATION DECIDES CONFIDENCE. Any "confidence" field the LLM
    produced is discarded and never read downstream."""
    if tier == "deterministic":
        return "high"
    if not has_citations:
        return "insufficient"
    if not citations_valid:
        return "low"
    if not failure_type_matches_evidence:
        # citation is IN the window, but the cited event's actual error.type
        # does not match the claim's stated failure_type — a cheap structural
        # check, not semantic proof, that catches "valid citation, nonsense claim"
        return "insufficient"
    return "medium"  # never auto-promoted to high

def can_auto_recover(confidence, failure_type, action, recoverable_types, allowed_actions) -> bool:
    if confidence not in ("high", "medium"): return False
    if failure_type not in recoverable_types: return False
    if action not in allowed_actions: return False
    return True
```

**Malformed diagnosis JSON from the LLM (missing fields / invalid JSON) is caught explicitly** — it must never crash the run. It becomes `confidence = "insufficient"`, `run.status = "blocked"`, no recovery attempted.

### 8.1 Required adversarial tests (hand-written before any implementation)
- **C1** valid citation → accepted
- **C2** out-of-window citation → `low` (not `insufficient`)
- **C3** empty citations → `insufficient`
- **C4** LLM self-reports `"confidence": "high"` → application overrides to the correct computed value
- **C5** *(new)* valid citation, but cited event's `error.type` doesn't match claimed `failure_type` → `insufficient`, not `medium`
- **C6** *(new)* malformed/unparseable diagnosis JSON → `insufficient`, `run.status = "blocked"`, no exception raised

---

## 9. RECOVERY ENGINE

```python
def attempt_recovery(run_id, failure_event, diagnosis, attempt_number, db):
    assert attempt_number <= 2
    if not can_auto_recover(diagnosis.confidence, diagnosis.failure_type,
                             diagnosis.recovery_action_suggested,
                             RECOVERABLE_TYPES, ALLOWED_RECOVERY_ACTIONS):
        mark_run_blocked(run_id, db)
        return None

    modified = generate_modified_action(failure_event, diagnosis)  # real behavioral change — I6, no comment-only fallback
    recovery_event = build_recovery_event(run_id, failure_event, diagnosis, attempt_number, modified)
    db.record_event(recovery_event)  # written once; never mutated afterward

    retry_step_id = db.next_step_id(run_id)
    retry_result = execute_tool(modified)               # ACTUAL execution
    retry_event = build_tool_event(run_id, retry_result, retry_step_id)
    db.record_event(retry_event)

    if retry_event.status == "failed":
        if attempt_number < 2:
            # a NEW diagnosis against the NEW failure — never reuse the prior diagnosis
            new_diagnosis = diagnose(run_id, retry_event, db.get_events(run_id), db)
            db.record_event(build_diagnosis_event(run_id, new_diagnosis))
            return attempt_recovery(run_id, retry_event, new_diagnosis, attempt_number + 1, db)
        db.update_run_status(run_id, "blocked")
    else:
        db.update_run_status(run_id, "recovered", ended=True)

    return retry_event
```

**Recovery resolution is derived, not stored-and-trusted:**
```python
def is_recovery_resolved(recovery_event, db) -> Optional[bool]:
    retry_step_id = recovery_event.output.get("retry_step_id")
    if retry_step_id is None:
        return None
    retry_event = db.get_event_by_step(recovery_event.run_id, retry_step_id)
    return retry_event.status == "success" if retry_event else None
```

---

## 10. FAILURE INJECTION — INTEGRITY BOUNDARY

**This is the most important integrity rule in the whole spec.** Injection must occur *after* a real LLM call and a real agent decision — never in place of them.

```
Correct:
  USER GOAL → REAL LLM CALL → REAL AGENT DECISION → REAL TOOL INPUT
    → [injection layer modifies/forces the execution outcome at the tool boundary]
    → FAILURE

Forbidden:
  USER GOAL → [injected_code silently replaces LLM output] → TOOL FAIL
```

The trace must show a genuine `llm_call` and `agent_decision` event preceding the failure — a judge inspecting the trace must see real model output, not a hardcoded stand-in.

```python
INJECTION_TYPES = {"tool_failure", "malformed_tool_response", "generated_code_failure"}

def inject_failure_at_boundary(injection_type: str, real_tool_input: dict) -> dict:
    # mutates/forces the outcome of the REAL tool input, does not replace the LLM step
    ...
```

Fixed menu of exactly 3. No arbitrary chaos engine.

---

## 11. CONTEXT MANAGER

Runs immediately before **every** LLM call. Hard postcondition: `context_tokens_estimated < CONTEXT_BUDGET` before any call proceeds — if not satisfiable, `run.status = "blocked"` and no LLM call happens.

```
if context_tokens_estimated < CONTEXT_BUDGET * CONTEXT_TRIGGER_RATIO:
    proceed unchanged
else:
    pinned = [turn 1] + [turns flagged requirement_capture]
    latest_N = last LATEST_TURNS_PRESERVED completed turns (never the currently-open turn)
    eligible = completed turns not in pinned, not in latest_N
    trim lowest-information eligible turns first
    if still over budget: summarize remaining eligible turns
        {summary, facts, decisions, requirements, unresolved}
    if still over budget: run.status = "blocked"; do not call LLM
    record a context_update event (via db.next_step_id(), never step_id=0) with before/after token counts
```

No semantic classifier, no embeddings, no LLM-based importance scoring — explicitly out of scope.

`context_tokens_estimated` (tiktoken-based estimate) is tracked **separately** from `usage.total_tokens` (Anthropic-reported, exact) — the two numbers are never presented as interchangeable.

---

## 12. THE 20-TURN HARNESS — COST-BOUNDED DESIGN

**Turns 1–19 are constructed as real trace events representing conversation/tool state — no LLM round-trip needed to prove context accounting.** Turn 20 is the one genuine Anthropic call.

```
Turn 1   → goal: "process this list of numbers and save results as CSV"          [constructed event]
Turn 2   → noise                                                                  [constructed event]
Turn 3   → "IMPORTANT REQUIREMENT: output must be sorted descending"              [constructed, requirement_capture=true]
Turn 4   → noise                                                                  [constructed event]
Turn 5   → tool call (succeeds)                                                   [constructed event]
Turn 6   → noise                                                                  [constructed event]
Turn 7   → injected failure (NameError, via §10's boundary rule)                  [real execution]
Turn 8   → recovery (real, via §9)                                                [real execution]
Turns 9–19 → filler generating REAL context pressure (long enough to exceed budget) [constructed events]
Turn 20  → "what was the sort order I asked for?"                                 [REAL LLM call, context-managed]
```

Run twice — context manager ON vs. naive oldest-truncation OFF — and **actually re-run the turn-20 probe through the agent, inspecting the real response** for a correct reference to the turn-3 requirement. `requirement_preserved = context_enabled` (a tautology) is explicitly forbidden — the result must come from inspecting real state/response content.

---

## 13. LLM RELIABILITY

Behind an `LLMAdapter` interface:
```python
class LLMAdapter(Protocol):
    def call(self, messages, system, max_tokens) -> dict: ...

class GeminiAdapter(LLMAdapter): ...      # real calls — ACTUAL DEMO, gemini-3.5-flash on the Free Tier
class AnthropicAdapter(LLMAdapter): ...   # optional, same interface, no code elsewhere changes if swapped in
class MockLLMAdapter(LLMAdapter): ...     # tests only
```
The architecture does not depend on any single provider — only the adapter boundary matters. Every `llm_call` trace event still records model, input, output, input/output/total tokens, latency, and cost regardless of provider.

**Free Tier caveats to design around, not ignore:** the free tier has real rate limits (requests/minute, requests/day, tokens/minute) that are tighter than paid tiers — $0 cost does not mean unlimited throughput, so `MAX_LLM_RETRIES` and backoff matter more here, not less. Free-tier usage is used by Google to improve its products (paid tier is not) — acceptable for a public hackathon demo with no sensitive data, but note this explicitly in the README rather than silently. The free tier is unavailable to requests originating from the EU/EEA/UK/Switzerland — irrelevant for most hackathons but worth a one-line note if the team is there.

**Cost tracking on the free tier:** `PRICING_TABLE["gemini-3.5-flash"]` records `input_per_1k = 0`, `output_per_1k = 0` for Free Tier use, while `usage.input_tokens` / `usage.output_tokens` / `usage.total_tokens` are still captured accurately from the real API response — the metrics dashboard shows real token counts with `$0.00` cost, not fabricated non-zero pricing.

Infra failures (timeout, rate limit — more likely to bite on the free tier's tighter limits, malformed provider response) are retried once via `MAX_LLM_RETRIES` with deterministic backoff, recorded as a failed `llm_call` event, and **never routed through the diagnosis/recovery pipeline** — that pipeline is for tool/execution failures, not provider outages. If the retry also fails: `run.status = "failed"`.

Before a live demo: a real health-check call verifying token accounting and diagnosis end-to-end, including confirming the free-tier rate limit has headroom for the rehearsal + the actual judge run. **Never silently fall back from the real adapter to the mock during a judge demo.**

---

## 14. CONSTRAINED EXECUTION ENVIRONMENT (never "sandbox")

`subprocess.run(..., timeout=MAX_EXECUTION_TIME)` plus:
- AST-based blocklist on dangerous imports/builtins (`os`, `subprocess`, `socket`, `eval`, `exec`, unrestricted `open`)
- output capped at `MAX_OUTPUT_SIZE`
- temporary, isolated working directory
- sanitized environment variables

Documented everywhere — code comments, README, demo script, judge Q&A — as a **"constrained execution environment,"** never as "sandboxed" or "secure." No Docker/VM in 24 hours; don't claim isolation stronger than what's implemented.

---

## 15. REPLAY

Pure reconstruction from stored trace events. **Zero external calls** — no LLM, no execution, no network, no new trace events, no new cost. Proven by mocking the LLM adapter and executor and asserting zero invocations, **seeded against a real, non-empty trace** (not an empty DB).

---

## 16. TEST SUITE (expanded from the original "six files, stop there")

1. step_id strictly increasing, no duplicates (allocator + DB-level)
2. duplicate step_id fails I1
3. C1–C6 (§8.1, including the two new adversarial cases)
4. I3–I6 pass/fail cases, including the metadata-only-change-does-not-satisfy-I6 case
5. recovery ≤ 2 attempts, `run.status = "blocked"` on exhaustion
6. failed retry triggers a genuinely new diagnosis, not reuse of the original
7. recovery references `diagnosis_event_id`, not `failure_event_id`
8. recovery outcome is derived from the linked tool_call event, never read from a mutated field
9. replay invokes zero external services against a real seeded trace
10. context manager runs before every LLM call; hard postcondition enforced
11. open turn is never trimmed/summarized
12. 20-turn harness retains the real turn-3 requirement, verified from an actual turn-20 response
13. LLM infra failure traced separately from diagnosis/recovery; retried at most once
14. cost computed only from `PRICING_TABLE` + token usage
15. concurrent `/execute` calls on the same run → second returns `409 RUN_BUSY`
16. recovery action outside `ALLOWED_RECOVERY_ACTIONS` is rejected, not dispatched
17. injection produces a real preceding `llm_call` + `agent_decision` in the trace, never replaces them
18. full E2E flow, inspecting persisted SQLite rows directly — order, unique step_ids, correct linkage, real statuses

---

## 17. HARD RULES

1. No new features after feature-freeze. None.
2. No module hardcodes a limit — everything from `config.py`.
3. Frontend computes nothing — renders backend-computed fields only.
4. Confidence is never read directly from LLM output.
5. Recovery action is never dispatched outside `ALLOWED_RECOVERY_ACTIONS`.
6. Trace events are never mutated after write; derived state is computed at read-time.
7. A failed retry always gets a fresh diagnosis — never a reused one.
8. Failure injection always occurs after a real LLM call and real agent decision.
9. "Sandbox," "secure," and "recovered" are never claimed beyond what is actually implemented and verified.
10. If a checkpoint gate times out, apply the documented downgrade — don't improvise one under pressure.
11. SCOREBOARD.md numbers are real numbers from real runs, always.
12. If unsure whether something is in scope, it's not.

---

## 18. BUILD ORDER

```
PHASE 1  Repo + SQLite + Pydantic models + centralized step allocator + trace store         → TEST   [DONE — 17/17 passing]
PHASE 2  Agent + LLMAdapter(Anthropic/Mock) + constrained execution env + run lock           → H4 TEST
PHASE 3  Evidence window + citation validator + confidence gate (incl. C5/C6)                → C1–C6 TEST
PHASE 4  Recovery (allowlisted, derived outcome, fresh-diagnosis-on-retry-failure) + linkage  → E2E TEST
PHASE 5  Context manager + cost-bounded 20-turn harness (1 real LLM call, not 20)             → CONTEXT TEST
PHASE 6  Replay + metrics + centralized pricing                                              → REPLAY/METRICS TEST
PHASE 7  React debugger UI + API integration + CORS                                          → FULL DEMO
PHASE 8  Judge Challenge with correct injection boundary + full adversarial attack sequence   → HARD-MODE TEST
```

### Phase 8 hard-mode attack sequence (replaces the vaguer "trust attack" checklist)
```
fake citation · cross-run citation · out-of-window citation · empty citation
LLM claims HIGH confidence · recovery action manipulation · duplicate execution request
malformed diagnosis JSON · retry produces a different failure type
replay while provider/executor is unavailable · context pressure at exactly the budget boundary
```
If it survives all of these, the architecture is trustworthy. Fix concrete integrity boundaries — then stop planning and build.
