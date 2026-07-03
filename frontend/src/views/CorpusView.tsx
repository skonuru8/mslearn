import type { FormEvent } from "react";
import { Fragment, useCallback, useEffect, useState } from "react";
import { api, uploadSource } from "../api/client";
import type {
  DomainProfileResponse,
  FailureGroup,
  IngestResponse,
  RetryFailedResponse,
  SourceRow,
  SynthesisStatusResponse,
  SynthesizeResponse,
} from "../api/types";
import { ErrorBanner, Loading } from "../components/Status";

function isActiveSource(row: SourceRow): boolean {
  return row.status === "running" || row.status === "chunking";
}

function progressFraction(row: SourceRow): number {
  if (row.total_chunks <= 0) {
    return 0;
  }
  return (row.done_chunks + row.failed_chunks + row.rejected_chunks) / row.total_chunks;
}

function progressLabel(row: SourceRow): string {
  const done = row.done_chunks + row.failed_chunks + row.rejected_chunks;
  if (row.status === "chunking") {
    return "Preparing…";
  }
  if (row.status === "running") {
    let label = `Reading… ${done} of ${row.total_chunks} sections`;
    if (row.failed_chunks > 0) {
      label += ` · ${row.failed_chunks} problems`;
    }
    return label;
  }
  return `${done}/${row.total_chunks}`;
}

function formatSynthesisAgo(ts: number): string {
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (minutes < 1) {
    return "just now";
  }
  if (minutes === 1) {
    return "1 min ago";
  }
  return `${minutes} min ago`;
}

export function CorpusView() {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [domainProfile, setDomainProfile] = useState("technical");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ref, setRef] = useState("");
  const [role, setRole] = useState("spine");
  const [sourceType, setSourceType] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [expandedSource, setExpandedSource] = useState<string | null>(null);
  const [failures, setFailures] = useState<Record<string, FailureGroup[]>>({});
  const [synthMsg, setSynthMsg] = useState<string | null>(null);
  const [synthesisStatus, setSynthesisStatus] = useState<SynthesisStatusResponse | null>(null);

  const refreshSources = useCallback(async () => {
    const [rows, profile] = await Promise.all([
      api<SourceRow[]>("/api/corpus/sources"),
      api<DomainProfileResponse>("/api/corpus/settings/domain-profile"),
    ]);
    setSources(rows);
    setDomainProfile(profile.profile);
    return rows;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      await refreshSources();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load corpus");
    } finally {
      setLoading(false);
    }
  }, [refreshSources]);

  const refreshSynthesisStatus = useCallback(async () => {
    try {
      setSynthesisStatus(await api<SynthesisStatusResponse>("/api/corpus/synthesis/status"));
    } catch {
      setSynthesisStatus(null);
    }
  }, []);

  useEffect(() => {
    void load();
    void refreshSynthesisStatus();
  }, [load, refreshSynthesisStatus]);

  useEffect(() => {
    if (!sources.some(isActiveSource)) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshSources().catch(() => {
        /* keep polling; transient errors surface on manual actions */
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [sources, refreshSources]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      const result = await api<IngestResponse>("/api/corpus/sources", {
        method: "POST",
        body: JSON.stringify({
          ref,
          role,
          source_type: sourceType || null,
          local: false,
        }),
      });
      const rows = await refreshSources();
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

  async function onUpload(event: FormEvent) {
    event.preventDefault();
    if (!uploadFile) {
      setError("Choose a file first");
      return;
    }
    setUploading(true);
    setUploadPercent(0);
    try {
      await uploadSource(uploadFile, role, false, (percent) => setUploadPercent(percent));
      setUploadFile(null);
      setUploadPercent(null);
      await refreshSources();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      setUploadPercent(null);
    }
  }

  async function setStatus(sourceId: string, action: "pause" | "resume") {
    try {
      await api(`/api/corpus/sources/${encodeURIComponent(sourceId)}/${action}`, {
        method: "POST",
      });
      await refreshSources();
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

  async function toggleFailures(sourceId: string) {
    if (expandedSource === sourceId) {
      setExpandedSource(null);
      return;
    }
    try {
      const groups = await api<FailureGroup[]>(
        `/api/corpus/sources/${encodeURIComponent(sourceId)}/failures`,
      );
      setFailures((prev) => ({ ...prev, [sourceId]: groups }));
      setExpandedSource(sourceId);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load failure reasons");
    }
  }

  async function onRetryFailed(sourceId: string) {
    try {
      await api<RetryFailedResponse>(
        `/api/corpus/sources/${encodeURIComponent(sourceId)}/retry-failed`,
        { method: "POST" },
      );
      setExpandedSource(null);
      await refreshSources();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retry failed");
    }
  }

  async function onSynthesize() {
    setSynthMsg(null);
    try {
      const result = await api<SynthesizeResponse>("/api/corpus/synthesize", { method: "POST" });
      if (!result.worker_online) {
        setSynthMsg(
          "Worker offline — synthesis was queued but nothing will process it. Start the worker (make worker or make run) and try again.",
        );
      } else {
        setSynthMsg("Synthesis queued — the background worker will process it shortly.");
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Synthesis enqueue failed");
    }
  }

  if (loading) {
    return <Loading />;
  }

  const lastRun = synthesisStatus?.last_run;

  return (
    <section className="panel">
      <h1>Corpus</h1>
      <ErrorBanner message={error} />

      <form className="form-grid" onSubmit={(event) => void onUpload(event)}>
        <label>
          Upload a file from this computer (pdf, epub, html, audio)
          <input
            type="file"
            accept=".pdf,.epub,.html,.htm,.mp3,.m4a,.wav,.flac,.ogg"
            onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {uploadPercent !== null ? (
          <div className="upload-progress">
            <progress max={100} value={uploadPercent} />
            <span>Uploading… {uploadPercent}%</span>
          </div>
        ) : null}
        <button type="submit" className="primary" disabled={!uploadFile || uploading}>
          {uploading ? "Uploading…" : "Upload & ingest"}
        </button>
      </form>

      <form className="form-grid" onSubmit={(event) => void onSubmit(event)}>
        <label>
          Or source ref (URL — blog post, YouTube — or server path)
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
      {synthMsg ? <div className={`synth-notice ${synthMsg.includes("offline") ? "warn" : ""}`}>{synthMsg}</div> : null}
      {lastRun ? (
        <p className="hint">
          Last synthesis: {lastRun.processed_concepts} concepts updated, curriculum length{" "}
          {lastRun.curriculum_len} ({formatSynthesisAgo(lastRun.ts)})
        </p>
      ) : null}

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
            <Fragment key={row.source_id}>
              <tr>
                <td>{row.ref}</td>
                <td>{row.role}</td>
                <td>
                  {row.status}
                  {row.error ? <div className="hint">{row.error}</div> : null}
                </td>
                <td>
                  {isActiveSource(row) ? (
                    <div className="ingest-progress">
                      <progress max={1} value={progressFraction(row)} />
                      <span>{progressLabel(row)}</span>
                    </div>
                  ) : (
                    <>
                      {row.done_chunks + row.failed_chunks + row.rejected_chunks}/{row.total_chunks}
                      {row.failed_chunks > 0 ? (
                        <>
                          {" "}
                          <button type="button" onClick={() => void toggleFailures(row.source_id)}>
                            {row.failed_chunks} failed — why?
                          </button>
                        </>
                      ) : (
                        ""
                      )}
                      {row.rejected_chunks > 0
                        ? ` (${row.rejected_chunks} had no trustworthy content)`
                        : ""}
                    </>
                  )}
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
                  {row.failed_chunks > 0 ? (
                    <button type="button" onClick={() => void onRetryFailed(row.source_id)}>
                      Retry failed
                    </button>
                  ) : null}
                </td>
              </tr>
              {expandedSource === row.source_id ? (
                <tr key={`${row.source_id}-failures`}>
                  <td colSpan={5}>
                    <ul>
                      {(failures[row.source_id] ?? []).map((group) => (
                        <li key={group.error}>
                          {group.count}× {group.error} (e.g. {group.sample_chunk_ids.join(", ")})
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ) : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </section>
  );
}
