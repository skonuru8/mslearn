import type { FormEvent } from "react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  DomainProfileResponse,
  IngestResponse,
  SourceRow,
  SynthesizeResponse,
} from "../api/types";
import { ErrorBanner, Loading } from "../components/Status";

export function CorpusView() {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [domainProfile, setDomainProfile] = useState("technical");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ref, setRef] = useState("");
  const [role, setRole] = useState("spine");
  const [sourceType, setSourceType] = useState("");
  const [local, setLocal] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rows, profile] = await Promise.all([
        api<SourceRow[]>("/api/corpus/sources"),
        api<DomainProfileResponse>("/api/corpus/settings/domain-profile"),
      ]);
      setSources(rows);
      setDomainProfile(profile.profile);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load corpus");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      const result = await api<IngestResponse>("/api/corpus/sources", {
        method: "POST",
        body: JSON.stringify({
          ref,
          role,
          source_type: sourceType || null,
          local,
        }),
      });
      const rows = await api<SourceRow[]>("/api/corpus/sources");
      setSources(rows);
      const row = rows.find((item) => item.source_id === result.source_id);
      if (row) {
        setSources([row, ...rows.filter((item) => item.source_id !== result.source_id)]);
      }
      setRef("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
    }
  }

  async function setStatus(sourceId: string, action: "pause" | "resume") {
    try {
      await api(`/api/corpus/sources/${encodeURIComponent(sourceId)}/${action}`, {
        method: "POST",
      });
      await load();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    }
  }

  async function onDomainProfileChange(value: string) {
    try {
      const result = await api<DomainProfileResponse>("/api/corpus/settings/domain-profile", {
        method: "POST",
        body: JSON.stringify({ profile: value }),
      });
      setDomainProfile(result.profile);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid domain profile");
    }
  }

  async function onSynthesize() {
    try {
      await api<SynthesizeResponse>("/api/corpus/synthesize", { method: "POST" });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Synthesis enqueue failed");
    }
  }

  if (loading) {
    return <Loading />;
  }

  return (
    <section className="panel">
      <h1>Corpus</h1>
      <ErrorBanner message={error} />

      <form className="form-grid" onSubmit={(event) => void onSubmit(event)}>
        <label>
          Source ref (path or URL)
          <input value={ref} onChange={(event) => setRef(event.target.value)} required />
        </label>
        <label>
          Role
          <select value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="spine">spine</option>
            <option value="supplement">supplement</option>
          </select>
        </label>
        <label>
          Source type (optional)
          <select value={sourceType} onChange={(event) => setSourceType(event.target.value)}>
            <option value="">auto</option>
            <option value="pdf">pdf</option>
            <option value="epub">epub</option>
            <option value="blog">blog</option>
            <option value="youtube">youtube</option>
            <option value="audio">audio</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={local} onChange={(event) => setLocal(event.target.checked)} />
          Run ingest locally (eager Celery)
        </label>
        <button type="submit" className="primary">
          Add source
        </button>
      </form>

      <p style={{ marginTop: "1.5rem" }}>
        <label>
          Domain profile{" "}
          <select
            value={domainProfile}
            onChange={(event) => void onDomainProfileChange(event.target.value)}
          >
            <option value="technical">technical</option>
            <option value="interpretive">interpretive</option>
          </select>
        </label>{" "}
        <button type="button" onClick={() => void onSynthesize()}>
          Run synthesis
        </button>
      </p>

      <table style={{ marginTop: "1rem" }}>
        <thead>
          <tr>
            <th>Ref</th>
            <th>Role</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((row) => (
            <tr key={row.source_id}>
              <td>{row.ref}</td>
              <td>{row.role}</td>
              <td>{row.status}</td>
              <td>
                {row.done_chunks + row.failed_chunks}/{row.total_chunks}
                {row.failed_chunks > 0 ? ` (${row.failed_chunks} failed)` : ""}
              </td>
              <td>
                {row.status === "paused" ? (
                  <button type="button" onClick={() => void setStatus(row.source_id, "resume")}>
                    Resume
                  </button>
                ) : (
                  <button type="button" onClick={() => void setStatus(row.source_id, "pause")}>
                    Pause
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
