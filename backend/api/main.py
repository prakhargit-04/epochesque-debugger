from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agent.controller import AgentController
from backend.config import DEBUG
from backend.db import DB
from backend.failure_injection import inject_failure_at_boundary
from backend.replay.replay_engine import replay_run

app = FastAPI(title="Epochesque 2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

db = DB()
try:
    db.init_schema()
except FileNotFoundError:
    pass

controller = AgentController(db)


class CreateRunRequest(BaseModel):
    goal: str


class ChallengeRequest(BaseModel):
    goal: str
    injection_type: str | None = "generated_code_failure"


@app.post("/runs")
def create_run(req: CreateRunRequest):
    return {"run_id": db.create_run(req.goal)}


@app.get("/runs")
def list_runs():
    return db.list_runs()


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "RUN_NOT_FOUND")
    events = db.get_events(run_id)
    recoveries = [e for e in events if e.event_type == "recovery"]
    successful = [r for r in recoveries if replay_run(run_id, db) and any(e.step_id == r.output.get("retry_step_id") and e.status == "success" for e in events)]
    return {
        **run,
        "total_tokens": sum(e.usage.total_tokens for e in events),
        "total_cost_usd": round(sum(e.cost_usd for e in events), 8),
        "event_count": len(events),
        "failure_count": sum(e.status == "failed" for e in events),
        "recovery_attempts": len(recoveries),
        "recovery_success_rate": (len(successful) / len(recoveries)) if recoveries else None,
    }


@app.get("/runs/{run_id}/events")
def get_events(run_id: str, limit: int = 100, offset: int = 0):
    if not db.get_run(run_id):
        raise HTTPException(404, "RUN_NOT_FOUND")
    return db.get_events(run_id, limit, offset)


@app.get("/runs/{run_id}/state/{step_id}")
def get_state(run_id: str, step_id: int):
    state = db.get_state(run_id, step_id)
    if state is None:
        raise HTTPException(404, "STATE_NOT_FOUND")
    return state


@app.post("/runs/{run_id}/execute")
def execute(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "RUN_NOT_FOUND")
    try:
        events = controller.execute(run_id)
    except RuntimeError as exc:
        if str(exc) == "RUN_BUSY":
            raise HTTPException(409, "RUN_BUSY") from exc
        raise
    return {"events": events}


# Note: Per spec §5, /challenge is the primary endpoint for failure injection testing.
# /execute is reserved for standard run executions without failure injection.
@app.post("/runs/{run_id}/challenge")
def challenge(run_id: str, req: ChallengeRequest):
    if not db.get_run(run_id):
        raise HTTPException(404, "RUN_NOT_FOUND")
    return {"events": controller.execute(run_id, injection=req.injection_type)}


@app.get("/runs/{run_id}/replay")
def replay(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "RUN_NOT_FOUND")
    return replay_run(run_id, db)
