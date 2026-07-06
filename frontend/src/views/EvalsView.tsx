import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { EvalMetric, EvalReport } from "../api/types";
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

export function EvalsView() {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await api<EvalReport>("/api/evals/report"));
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

  if (!report || report.run === null) {
    return (
      <section className="panel">
        <h1>Evals</h1>
        <p className="empty-state">
          No eval run yet. Run <code>python -m mslearn.evals.run</code> to populate this.
        </p>
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
    </section>
  );
}
