def replay_run(run_id: str, db):
    return [
        {
            "step_id": e.step_id,
            "event_id": e.event_id,
            "event_type": e.event_type,
            "component": e.component,
            "status": e.status,
            "input": e.input,
            "output": e.output,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in db.get_events(run_id)
    ]
