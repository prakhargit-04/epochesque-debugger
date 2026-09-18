# EPOCHESQUE 2.0 — SCOREBOARD

All claims are empirically verified by automated unit & integration tests.

| Claim | Status | Proof |
|---|---|---|
| Every run is traced | Done | tests/unit/test_review1_spec.py:33 (`test_step_ids_strictly_increasing_and_unique`), :273 (`test_review1_e2e_full_flow_sqlite_persisted`) |
| Diagnosis is evidence-constrained | Done | tests/unit/test_phase8_adversarial.py:34 (`test_fake_citation`), :53 (`test_cross_run_citation`), :88 (`test_out_of_window_citation`), :258 (`test_malformed_diagnosis_json`) |
| LLM cannot set confidence | Done | tests/unit/test_phase8_adversarial.py:124 (`test_llm_claims_high_confidence_overridden`), :164 (`test_invariant_i3_catches_corrupted_diagnosis`) |
| Recovery is bounded | Done | tests/unit/test_phase8_adversarial.py:196 (`test_recovery_action_manipulation_rejected`), tests/unit/test_review1_spec.py:162 (`test_recovery_eligibility_gates`) |
| Recovery is verified by actual result | Done | tests/unit/test_review1_spec.py:173 (`test_recovery_execution_and_invariants`), :273 (`test_review1_e2e_full_flow_sqlite_persisted`) |
| Replay has zero side effects | Done | tests/unit/test_phase8_adversarial.py:347 (`test_replay_with_unavailable_provider_and_executor`), tests/unit/test_review1_spec.py:258 (`test_replay_makes_zero_new_events`) |
| 20-turn context survives | Done | tests/unit/test_context_and_harness.py:30 (`test_context_manager_truncation_preserves_pinned_turns`), tests/unit/test_phase8_adversarial.py:380 (`test_context_budget_boundary`) |
| Cost is tracked | Done | tests/unit/test_replay_and_metrics.py:30 (`test_cost_calculation`), tests/unit/test_review1_spec.py:273 (`test_review1_e2e_full_flow_sqlite_persisted`) |
