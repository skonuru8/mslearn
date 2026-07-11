import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { EvalMetric, EvalReport, PendingEvolutionRun } from "../api/types";
import { ErrorBanner, Loading } from "../components/Status";

// Plain-language names for the metric keys the server reports, so the page
// doesn't face the user with raw dotted identifiers.
const METRIC_LABELS: Record<string, string> = {
  "extraction.precision": "Extraction precision",
  "extraction.recall": "Extraction recall",
  "grounding.false_accept": "Grounding false-accept rate",
  "clustering.f1": "Clustering F1",
  "tension.accuracy": "Tension accuracy",
  "schema.validity": "Schema validity",
  "provenance.violations": "Memory provenance violations",
};

function labelFor(metric: string): string {
  return METRIC_LABELS[metric] ?? metric;
}

function formatValue(value: number): string {
  return value.toFixed(2);
}

function formatTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function shadowValue(shadow: Record<string, unknown> | null, metric: string): string {
  if (!shadow) {
    return "—";
  }
  const value = shadow[metric];
  return typeof value === "number" ? String(value) : "—";
}

function PendingProposals({
  pending,
  error,
  actingOn,
  onApprove,
  onReject,
}: {
  pending: PendingEvolutionRun[];
  error: string | null;
  actingOn: number | null;
  onApprove: (runId: number) => void;
  onReject: (runId: number) => void;
}) {
  if (pending.length === 0) {
    return null;
  }
  return (
    <div className="pending-evolution">
      <h2>Pending prompt changes</h2>
      <p className="pending-evolution-hint">
        These prompt rewrites passed shadow-eval but wait for your approval before taking effect.
      </p>
      <ErrorBanner message={error} />
      {pending.map((row) => (
        <div key={row.run_id} className="pending-evolution-row">
          <p className="pending-target">
            Target metric: <code>{row.proposal.targets_metric}</code>
          </p>
          <p className="pending-why">{row.why}</p>
          <p className="pending-shadow">
            <span>Before: {shadowValue(row.shadow_before, row.proposal.targets_metric)}</span>
            {" → "}
            <span>After: {shadowValue(row.shadow_after, row.proposal.targets_metric)}</span>
          </p>
          {row.proposal.new_prompt ? <pre className="pending-diff">{row.proposal.new_prompt}</pre> : null}
          <div className="pending-actions">
            <button
              type="button"
              disabled={actingOn === row.run_id}
              onClick={() => onApprove(row.run_id)}
            >
              Approve
            </button>
            <button
              type="button"
              disabled={actingOn === row.run_id}
              onClick={() => onReject(row.run_id)}
            >
              Reject
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function EvalsView() {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [pending, setPending] = useState<PendingEvolutionRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [reportResult, pendingResult] = await Promise.all([
        api<EvalReport>("/api/evals/report"),
        api<PendingEvolutionRun[]>("/api/evals/pending"),
      ]);
      setReport(reportResult);
      setPending(pendingResult);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load eval report");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function refreshPending() {
    try {
      setPending(await api<PendingEvolutionRun[]>("/api/evals/pending"));
      setPendingError(null);
    } catch (err) {
      setPendingError(err instanceof Error ? err.message : "Failed to load pending changes");
    }
  }

  async function approve(runId: number) {
    setActingOn(runId);
    try {
      await api(`/api/evals/pending/${runId}/approve`, { method: "POST" });
      await refreshPending();
    } catch (err) {
      setPendingError(err instanceof Error ? err.message : "Could not approve this change");
    } finally {
      setActingOn(null);
    }
  }

  async function reject(runId: number) {
    setActingOn(runId);
    try {
      await api(`/api/evals/pending/${runId}/reject`, { method: "POST" });
      await refreshPending();
    } catch (err) {
      setPendingError(err instanceof Error ? err.message : "Could not reject this change");
    } finally {
      setActingOn(null);
    }
  }

  if (loading) {
    return <Loading />;
  }

  if (error) {
    return (
      <section className="panel">
        <h1>Evals</h1>
        <ErrorBanner message={error} />
      </section>
    );
  }

  const pendingSection = (
    <PendingProposals
      pending={pending}
      error={pendingError}
      actingOn={actingOn}
      onApprove={(runId) => void approve(runId)}
      onReject={(runId) => void reject(runId)}
    />
  );

  if (!report || report.run === null) {
    return (
      <section className="panel">
        <h1>Evals</h1>
        <p className="empty-state">
          No eval run yet. Run <code>python -m mslearn.evals.run</code> to populate this.
        </p>
        {pendingSection}
      </section>
    );
  }

  const { run, metrics } = report;

  return (
    <section className="panel">
      <h1>Evals</h1>
      <p className="eval-summary">
        Last run: {formatTimestamp(run.ts)} · {run.kind}
        {run.git_sha ? ` · ${run.git_sha}` : ""} ·{" "}
        <span className={run.passed ? "eval-pass" : "eval-fail"}>
          {run.passed ? "all gates passed" : "some gates failed"}
        </span>
      </p>
      {metrics.length === 0 ? (
        <p className="empty-state">This run recorded no metrics.</p>
      ) : (
        <table className="eval-table">
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col">Value</th>
              <th scope="col">Gate</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric: EvalMetric) => (
              <tr key={metric.metric}>
                <td>{labelFor(metric.metric)}</td>
                <td>{formatValue(metric.value)}</td>
                <td>{metric.gate === null ? "—" : formatValue(metric.gate)}</td>
                <td className={metric.passed ? "eval-pass" : "eval-fail"}>
                  {metric.passed ? "Pass" : "Fail"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {pendingSection}
    </section>
  );
}
