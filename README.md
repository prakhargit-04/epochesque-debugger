# EPOCHESQUE 2.0 — Demo-First Foundation

Evidence-Backed AI Execution Debugger for Track 1 — The Glass Box Problem.

## Goal

This repository is intentionally structured so the team can build a convincing hackathon demo first, then harden the same architecture into the final submission.

The product proof chain is:

`REAL USER GOAL → REAL LLM CALL → REAL AGENT DECISION → REAL TOOL INPUT → CONTROLLED FAILURE → TRACE → EVIDENCE → DIAGNOSIS → VALIDATION → CONFIDENCE → RECOVERY → ACTUAL RESULT → METRICS → REPLAY`

## Stack

- Python 3.11 + FastAPI
- SQLite + raw sqlite3
- Gemini through an LLMAdapter
- React + Vite + Tailwind
- pytest

## Demo-first rule

Build the smallest end-to-end vertical slice before adding depth:

1. create run
2. real Gemini call
3. agent decision
4. constrained tool execution
5. trace event capture
6. injected failure at the tool boundary
7. deterministic diagnosis/citation validation
8. recovery + actual retry result
9. trace UI
10. replay + metrics

Do not add Evidence Graph, multi-agent, RAG, GitHub integration, or other out-of-scope features.

## Run

Backend:

```bash
python -m venv .venv
# activate the venv
pip install -r requirements.txt
cp .env.example .env
uvicorn backend.api.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Tests:

```bash
pytest -q
```

See `CLAUDE.md` for the implementation rules and sequence.
