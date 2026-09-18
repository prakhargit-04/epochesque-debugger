import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function buildOfflineDemo(goal, runId) {
  const code = "numbers = [5, 2, 9, 1]\nprint(sorted(numbers, reverse=True))";
  const failedCode = "numbers = [5, 2, 9, 1]\nprint(sorted(numbers, reverse=True)";
  const event = (step_id, event_type, component, status, extra = {}) => ({
    event_id: `offline-${runId}-${step_id}`,
    step_id, event_type, component, status, turn_id: 1, ...extra,
  });
  const events = [
    event(1, 'llm_call', 'gemini_adapter (demo)', 'success', { input: { goal, model: 'offline-demo-model' }, output: { code }, usage: { input_tokens: 42, output_tokens: 18, total_tokens: 60 }, latency_ms: 94 }),
    event(2, 'agent_decision', 'agent_controller', 'success', { input: { llm_event_id: `offline-${runId}-1` }, output: { decision: 'execute_generated_code', code } }),
    event(3, 'tool_call', 'constrained_executor', 'failed', { input: { code: failedCode }, output: {}, error: { type: 'SyntaxError', message: "'(' was never closed — controlled failure injected at the tool boundary" }, latency_ms: 7 }),
    event(4, 'diagnosis', 'diagnosis_engine', 'success', { input: { failure_event_id: `offline-${runId}-3` }, output: { failure_type: 'SyntaxError', confidence: 'high', recovery_action_suggested: 'correct_tool_input', root_cause: 'The injected tool input is missing its closing parenthesis.', claims: [{ text: 'The failed tool execution reports an unclosed parenthesis.', evidence_event_ids: [`offline-${runId}-3`] }] } }),
    event(5, 'recovery', 'recovery_engine', 'success', { input: { diagnosis_event_id: `offline-${runId}-4`, failed_input: { code: failedCode } }, output: { attempt: 1, action: 'correct_tool_input', retry_step_id: 6, modified_input: { code } } }),
    event(6, 'tool_call', 'constrained_executor', 'success', { input: { code }, output: { stdout: '[9, 5, 2, 1]\n' }, latency_ms: 8 }),
  ];
  return { run: { run_id: runId, goal, status: 'recovered', total_tokens: 60, total_cost_usd: 0, event_count: events.length, failure_count: 1, recovery_attempts: 1, recovery_success_rate: 1.0 }, events };
}

function isNetworkError(err) {
  return err instanceof TypeError || (err.message && err.message.toLowerCase().includes('fetch'));
}

function App() {
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runDetails, setRunDetails] = useState(null);
  const [events, setEvents] = useState([]);
  const [isReplayMode, setIsReplayMode] = useState(false);
  const [replayEvents, setReplayEvents] = useState(null);
  const [goal, setGoal] = useState('Process a list of numbers and print the result.');
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('metrics'); // 'metrics' | 'timeline' | 'diagnosis' | 'recovery'
  const [offlineRuns, setOfflineRuns] = useState([]);
  const [backendError, setBackendError] = useState(null);

  function showOfflineDemo(runId, demoGoal = goal) {
    const demo = buildOfflineDemo(demoGoal, runId);
    setRuns((current) => [demo.run, ...current.filter((run) => run.run_id !== runId)]);
    setOfflineRuns((current) => [demo, ...current.filter((item) => item.run.run_id !== runId)]);
    setRunDetails(demo.run);
    setEvents(demo.events);
    setSelectedRunId(runId);
    setIsReplayMode(false);
    setReplayEvents(null);
    setBackendError('Backend offline. Showing local demonstration trace.');
  }

  async function refreshRuns() {
    setBackendError(null);
    try {
      const res = await fetch(`${API}/runs`);
      if (res.ok) {
        const data = await res.json();
        setRuns(data);
      } else {
        setBackendError(`Backend API returned error: HTTP ${res.status}`);
      }
    } catch (err) {
      if (isNetworkError(err)) {
        showOfflineDemo(`offline-demo-${Date.now()}`);
      } else {
        setBackendError(`Failed to load runs: ${err.message}`);
      }
    }
  }

  async function loadRunData(runId) {
    if (!runId) return;
    setBackendError(null);
    try {
      const [rRes, eRes] = await Promise.all([
        fetch(`${API}/runs/${runId}`),
        fetch(`${API}/runs/${runId}/events`),
      ]);
      if (rRes.ok) {
        setRunDetails(await rRes.json());
      } else {
        setBackendError(`Failed to load run detail: HTTP ${rRes.status}`);
      }
      if (eRes.ok) {
        setEvents(await eRes.json());
      }
    } catch (err) {
      if (isNetworkError(err)) {
        showOfflineDemo(runId);
      } else {
        setBackendError(`Network error loading run data: ${err.message}`);
      }
    }
  }

  async function handleCreateRun() {
    setLoading(true);
    setBackendError(null);
    try {
      const res = await fetch(`${API}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setBackendError(errData.detail || `Create run failed with HTTP ${res.status}`);
        return;
      }
      const data = await res.json();
      setSelectedRunId(data.run_id);
      setIsReplayMode(false);
      await refreshRuns();
    } catch (err) {
      if (isNetworkError(err)) {
        showOfflineDemo(`offline-${Date.now()}`);
      } else {
        setBackendError(`Create run failed: ${err.message}`);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleExecuteRun() {
    if (!selectedRunId) return;
    setLoading(true);
    setBackendError(null);
    try {
      const res = await fetch(`${API}/runs/${selectedRunId}/execute`, { method: 'POST' });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setBackendError(errData.detail || `Execution failed with HTTP ${res.status}`);
        return;
      }
      await loadRunData(selectedRunId);
      await refreshRuns();
    } catch (err) {
      if (isNetworkError(err)) {
        showOfflineDemo(selectedRunId);
      } else {
        setBackendError(`Execute error: ${err.message}`);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleRunDemoFailure() {
    setLoading(true);
    setBackendError(null);
    try {
      const createRes = await fetch(`${API}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: 'Process a list of numbers and print the result.' }),
      });
      if (!createRes.ok) {
        setBackendError(`Failed to create demo run: HTTP ${createRes.status}`);
        return;
      }
      const { run_id } = await createRes.json();
      setSelectedRunId(run_id);
      setIsReplayMode(false);

      const challengeRes = await fetch(`${API}/runs/${run_id}/challenge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          goal: 'Process a list of numbers and print the result.',
          injection_type: 'generated_code_failure',
        }),
      });
      if (!challengeRes.ok) {
        const errData = await challengeRes.json().catch(() => ({}));
        setBackendError(errData.detail || `Challenge execution failed: HTTP ${challengeRes.status}`);
        return;
      }

      await loadRunData(run_id);
      await refreshRuns();
    } catch (err) {
      if (isNetworkError(err)) {
        showOfflineDemo(`offline-${Date.now()}`, 'Process a list of numbers and print the result.');
      } else {
        setBackendError(`Demo failure execution error: ${err.message}`);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleReplay() {
    if (!selectedRunId) return;
    setLoading(true);
    setBackendError(null);
    try {
      const res = await fetch(`${API}/runs/${selectedRunId}/replay`);
      if (res.ok) {
        const replayData = await res.json();
        setReplayEvents(replayData);
        setIsReplayMode(true);
      } else {
        setBackendError(`Replay API returned HTTP ${res.status}`);
      }
    } catch (err) {
      if (isNetworkError(err)) {
        const offline = offlineRuns.find((item) => item.run.run_id === selectedRunId);
        if (offline) {
          setReplayEvents(offline.events);
          setIsReplayMode(true);
        }
      } else {
        setBackendError(`Replay error: ${err.message}`);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refreshRuns();
  }, []);

  useEffect(() => {
    if (selectedRunId) {
      setIsReplayMode(false);
      setReplayEvents(null);
      const offline = offlineRuns.find((item) => item.run.run_id === selectedRunId);
      if (offline) {
        setRunDetails(offline.run);
        setEvents(offline.events);
      } else {
        loadRunData(selectedRunId);
      }
    }
  }, [selectedRunId, offlineRuns]);

  const displayedEvents = isReplayMode && replayEvents ? replayEvents : events;
  const diagnosisEvents = displayedEvents.filter((e) => e.event_type === 'diagnosis');
  const recoveryEvents = displayedEvents.filter((e) => e.event_type === 'recovery');

  return (
    <div className="app">
      {/* Sidebar Controls */}
      <aside className="sidebar">
        <div className="brand-header">
          <div className="brand-badge">GLASS BOX OBSERVABILITY</div>
          <h1 className="brand-title">EPOCHESQUE 2.0</h1>
          <p className="brand-sub">Evidence-Backed AI Execution & Recovery Console</p>
        </div>

        <div className="section-card">
          <label className="input-label">User Execution Goal</label>
          <textarea
            className="goal-input"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Describe execution task..."
            rows={3}
          />

          <div className="action-buttons">
            <button className="btn btn-demo" onClick={handleRunDemoFailure} disabled={loading}>
              ⚡ Run Demo Failure
            </button>
            <button className="btn btn-secondary" onClick={handleCreateRun} disabled={loading}>
              + Create Blank Run
            </button>
          </div>
        </div>

        <div className="runs-section">
          <div className="runs-header">
            <h3>Recent Executions</h3>
            <span className="runs-count">{runs.length}</span>
          </div>

          <div className="runs-list">
            {runs.map((r) => (
              <div
                key={r.run_id}
                className={`run-item ${selectedRunId === r.run_id ? 'active' : ''}`}
                onClick={() => setSelectedRunId(r.run_id)}
              >
                <div className="run-id-row">
                  <span className="run-mono">{r.run_id.slice(0, 14)}...</span>
                  <span className={`status-badge status-${r.status}`}>{r.status}</span>
                </div>
                <div className="run-goal-preview">{r.goal}</div>
              </div>
            ))}
          </div>
        </div>
      </aside>

      {/* Main Debugger View */}
      <main className="main-content">
        {backendError && (
          <div className="error-notice-bar" style={{ padding: '12px 20px', backgroundColor: 'rgba(239,68,68,0.15)', borderBottom: '1px solid rgba(239,68,68,0.3)', color: '#f87171', fontWeight: 600 }}>
            ⚠️ {backendError}
          </div>
        )}

        {selectedRunId ? (
          <>
            {/* Top Bar Header */}
            <header className="topbar">
              <div className="run-info">
                <div className="eyebrow">ACTIVE EXECUTION RUN</div>
                <h2 className="run-title">{selectedRunId}</h2>
                <div className="run-meta">Goal: "{runDetails?.goal}"</div>
              </div>

              <div className="topbar-actions">
                {runDetails?.status === 'created' || runDetails?.status === 'running' ? (
                  <button className="btn btn-primary" onClick={handleExecuteRun} disabled={loading}>
                    ▶ Execute Step
                  </button>
                ) : null}

                <button
                  className={`btn ${isReplayMode ? 'btn-active-replay' : 'btn-secondary'}`}
                  onClick={handleReplay}
                  disabled={loading}
                >
                  🔄 {isReplayMode ? 'Replay Active' : 'Replay Trace'}
                </button>
              </div>
            </header>

            {/* Spec Section Views Navigation Tabs (§2-§9) */}
            <nav style={{ display: 'flex', gap: '8px', padding: '12px 24px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              <button
                style={{ padding: '8px 16px', borderRadius: '6px', border: 'none', background: activeTab === 'metrics' ? '#3b82f6' : 'rgba(255,255,255,0.05)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                onClick={() => setActiveTab('metrics')}
              >
                📊 Overview & Metrics (§5)
              </button>
              <button
                style={{ padding: '8px 16px', borderRadius: '6px', border: 'none', background: activeTab === 'timeline' ? '#3b82f6' : 'rgba(255,255,255,0.05)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                onClick={() => setActiveTab('timeline')}
              >
                📜 Trace Timeline (§2, §4)
              </button>
              <button
                style={{ padding: '8px 16px', borderRadius: '6px', border: 'none', background: activeTab === 'diagnosis' ? '#3b82f6' : 'rgba(255,255,255,0.05)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                onClick={() => setActiveTab('diagnosis')}
              >
                🔍 Evidence & Diagnosis (§7, §8)
              </button>
              <button
                style={{ padding: '8px 16px', borderRadius: '6px', border: 'none', background: activeTab === 'recovery' ? '#3b82f6' : 'rgba(255,255,255,0.05)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                onClick={() => setActiveTab('recovery')}
              >
                🛠️ Recovery Engine (§9)
              </button>
            </nav>

            {/* Replay Notice */}
            {isReplayMode && (
              <div className="replay-notice-bar">
                <span>🛡️ REPLAY MODE: Historical Trace Reconstruction · Zero Gemini/Execution Calls</span>
              </div>
            )}

            {/* Section 1: Overview & Metrics View (§5) */}
            {activeTab === 'metrics' && runDetails && (
              <section className="metrics-banner" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px', padding: '24px' }}>
                <div className="metric-card">
                  <span className="metric-label">RUN STATUS</span>
                  <span className={`metric-value status-${runDetails.status}`}>
                    {runDetails.status.toUpperCase()}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">TOTAL TOKENS (PRE-COMPUTED)</span>
                  <span className="metric-value">{runDetails.total_tokens?.toLocaleString() || 0}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">TOTAL COST USD (PRICING TABLE)</span>
                  <span className="metric-value">${runDetails.total_cost_usd?.toFixed(6) || '0.000000'}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">TRACE EVENT COUNT</span>
                  <span className="metric-value">{runDetails.event_count || 0}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">FAILURE COUNT</span>
                  <span className="metric-value alert-red">{runDetails.failure_count || 0}</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">RECOVERY SUCCESS RATE</span>
                  <span className="metric-value alert-green">
                    {runDetails.recovery_success_rate !== null && runDetails.recovery_success_rate !== undefined
                      ? `${(runDetails.recovery_success_rate * 100).toFixed(0)}%`
                      : 'N/A (0 Attempts)'}
                  </span>
                </div>
              </section>
            )}

            {/* Section 2: Trace Event Timeline View (§2, §4) */}
            {activeTab === 'timeline' && (
              <section className="timeline-container">
                <h3 className="section-title">Trace Event Sequence ({displayedEvents.length} Events)</h3>
                <div className="timeline">
                  {displayedEvents.map((evt) => (
                    <div key={evt.event_id} className={`event-card event-type-${evt.event_type} event-status-${evt.status}`}>
                      <div className="event-header">
                        <div className="event-step">STEP #{evt.step_id}</div>
                        <div className="event-type">{evt.event_type.toUpperCase()}</div>
                        <div className="event-component">{evt.component}</div>
                        <div className={`status-pill status-${evt.status}`}>{evt.status}</div>
                        <div className="event-id">{evt.event_id}</div>
                      </div>

                      {evt.event_type === 'llm_call' && (
                        <div className="event-body">
                          <div className="field-group">
                            <span className="field-label">Model:</span>
                            <span className="field-val">{evt.input?.model || 'gemini-3.5-flash'}</span>
                            <span className="field-label ml-4">Tokens:</span>
                            <span className="field-val">{evt.usage?.total_tokens || 0} ({evt.usage?.input_tokens} in / {evt.usage?.output_tokens} out)</span>
                            <span className="field-label ml-4">Latency:</span>
                            <span className="field-val">{evt.latency_ms || 0}ms</span>
                          </div>
                          {evt.output?.code && (
                            <div className="code-block">
                              <div className="code-header">Generated LLM Action Output:</div>
                              <pre><code>{evt.output.code}</code></pre>
                            </div>
                          )}
                        </div>
                      )}

                      {evt.event_type === 'agent_decision' && (
                        <div className="event-body">
                          <div className="field-group">
                            <span className="field-label">Decision:</span>
                            <span className="field-val">{evt.output?.decision}</span>
                            <span className="field-label ml-4">LLM Event Ref:</span>
                            <span className="field-val mono">{evt.input?.llm_event_id}</span>
                          </div>
                        </div>
                      )}

                      {evt.event_type === 'tool_call' && (
                        <div className="event-body">
                          {evt.input?.code && (
                            <div className="code-block">
                              <div className="code-header">Tool Execution Input Code:</div>
                              <pre><code>{evt.input.code}</code></pre>
                            </div>
                          )}
                          {evt.error && (
                            <div className="error-box">
                              <div className="error-title">❌ EXECUTION FAILURE: {evt.error.type}</div>
                              <div className="error-msg">{evt.error.message}</div>
                            </div>
                          )}
                          {evt.output?.stdout && (
                            <div className="output-box">
                              <div className="output-title">Console Stdout:</div>
                              <pre>{evt.output.stdout}</pre>
                            </div>
                          )}
                        </div>
                      )}

                      {evt.event_type === 'diagnosis' && (
                        <div className="event-body diagnosis-body">
                          <div className="diagnosis-grid">
                            <div>
                              <span className="field-label">Failure Type:</span>
                              <span className="field-val highlight-red">{evt.output?.failure_type}</span>
                            </div>
                            <div>
                              <span className="field-label">Confidence Gate Level:</span>
                              <span className={`confidence-badge conf-${evt.output?.confidence}`}>
                                {evt.output?.confidence?.toUpperCase()}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}

                      {evt.event_type === 'recovery' && (
                        <div className="event-body recovery-body">
                          <div className="field-group">
                            <span className="field-label">Attempt:</span>
                            <span className="field-val">#{evt.output?.attempt}</span>
                            <span className="field-label ml-4">Action:</span>
                            <span className="field-val highlight-cyan">{evt.output?.action}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Section 3: Evidence & Diagnosis View (§7, §8) */}
            {activeTab === 'diagnosis' && (
              <section className="timeline-container" style={{ padding: '24px' }}>
                <h3 className="section-title">Evidence-Backed Diagnosis Engine ({diagnosisEvents.length} Diagnosis Events)</h3>
                {diagnosisEvents.length === 0 ? (
                  <p style={{ color: '#94a3b8' }}>No failure diagnosis events recorded for this run.</p>
                ) : (
                  diagnosisEvents.map((evt) => (
                    <div key={evt.event_id} className="event-card event-type-diagnosis" style={{ marginBottom: '16px' }}>
                      <div className="event-header">
                        <div className="event-step">STEP #{evt.step_id}</div>
                        <div className="event-type">DIAGNOSIS</div>
                        <div className="event-component">{evt.component}</div>
                        <div className="event-id">{evt.event_id}</div>
                      </div>
                      <div className="event-body diagnosis-body">
                        <div className="diagnosis-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '12px' }}>
                          <div>
                            <span className="field-label">Failure Type:</span>
                            <span className="field-val highlight-red">{evt.output?.failure_type}</span>
                          </div>
                          <div>
                            <span className="field-label">Confidence Gate:</span>
                            <span className={`confidence-badge conf-${evt.output?.confidence}`}>
                              {evt.output?.confidence?.toUpperCase()}
                            </span>
                          </div>
                          <div>
                            <span className="field-label">Suggested Action:</span>
                            <span className="field-val highlight-cyan">{evt.output?.recovery_action_suggested || 'none'}</span>
                          </div>
                        </div>

                        <div className="root-cause-box" style={{ marginBottom: '12px' }}>
                          <strong>Root Cause Diagnosis:</strong> {evt.output?.root_cause}
                        </div>

                        {evt.output?.claims && evt.output.claims.length > 0 && (
                          <div className="claims-box">
                            <span className="field-label">Evidence Claims & Cited Event IDs:</span>
                            {evt.output.claims.map((claim, idx) => (
                              <div key={idx} className="claim-item" style={{ marginTop: '6px' }}>
                                <span className="claim-text">"{claim.text}"</span>
                                <span className="cited-ids" style={{ marginLeft: '12px' }}>
                                  Cited Event IDs: {claim.evidence_event_ids?.map((id) => <span key={id} className="cite-badge">{id}</span>)}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </section>
            )}

            {/* Section 4: Recovery Engine View (§9) */}
            {activeTab === 'recovery' && (
              <section className="timeline-container" style={{ padding: '24px' }}>
                <h3 className="section-title">Allowlisted Recovery Engine ({recoveryEvents.length} Recovery Attempts)</h3>
                {recoveryEvents.length === 0 ? (
                  <p style={{ color: '#94a3b8' }}>No recovery attempts executed for this run.</p>
                ) : (
                  recoveryEvents.map((evt) => (
                    <div key={evt.event_id} className="event-card event-type-recovery" style={{ marginBottom: '16px' }}>
                      <div className="event-header">
                        <div className="event-step">STEP #{evt.step_id}</div>
                        <div className="event-type">RECOVERY</div>
                        <div className="event-component">{evt.component}</div>
                        <div className="event-id">{evt.event_id}</div>
                      </div>
                      <div className="event-body recovery-body">
                        <div className="field-group" style={{ marginBottom: '12px' }}>
                          <span className="field-label">Attempt:</span>
                          <span className="field-val">#{evt.output?.attempt}</span>
                          <span className="field-label ml-4" style={{ marginLeft: '16px' }}>Action:</span>
                          <span className="field-val highlight-cyan">{evt.output?.action}</span>
                          <span className="field-label ml-4" style={{ marginLeft: '16px' }}>Linked Retry Step:</span>
                          <span className="field-val mono highlight-green">Step #{evt.output?.retry_step_id}</span>
                        </div>

                        {evt.output?.modified_input?.code && (
                          <div className="code-block recovery-code">
                            <div className="code-header">Corrected Modified Retry Input (I6 Behavioral Difference):</div>
                            <pre><code>{evt.output.modified_input.code}</code></pre>
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </section>
            )}
          </>
        ) : (
          <div className="empty-state">
            <h2>Select an Execution Run or Click "Run Demo Failure"</h2>
            <p>EPOCHESQUE 2.0 provides glass-box observability into AI failure diagnosis and recovery.</p>
          </div>
        )}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
