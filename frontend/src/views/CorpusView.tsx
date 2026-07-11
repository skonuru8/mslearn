import type { FormEvent } from "react";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
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
import { useProject } from "../context/ProjectContext";
import { ErrorBanner, Loading } from "../components/Status";
import {
  detectSourceTypeFromUrl,
  formatSynthesisProgress,
  sourceStatusLabel,
  translateError,
} from "../utils/userMessages";

type AddTab = "file" | "link";

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

function roleLabel(role: string): string {
  return role === "spine" ? "Main course" : "Extra reading";
}

export function CorpusView() {
  const { projectId } = useProject();
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [domainProfile, setDomainProfile] = useState("technical");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorTechnical, setErrorTechnical] = useState<string | null>(null);
  const [addTab, setAddTab] = useState<AddTab>("file");
  const [linkRef, setLinkRef] = useState("");
  const [isMainCourse, setIsMainCourse] = useState(true);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [mainSourceIndex, setMainSourceIndex] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadIndex, setUploadIndex] = useState<{ current: number; total: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [expandedSource, setExpandedSource] = useState<string | null>(null);
  const [failures, setFailures] = useState<Record<string, FailureGroup[]>>({});
  const [synthMsg, setSynthMsg] = useState<string | null>(null);
  const [synthesisStatus, setSynthesisStatus] = useState<SynthesisStatusResponse | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  const role = isMainCourse ? "spine" : "supplement";
  const hasSpine = sources.some((row) => row.role === "spine");
  // Once the user manually toggles the main-course checkbox we stop
  // re-deriving its default, so we don't fight their choice mid-session.
  const spineTouched = useRef(false);

  function setUserError(message: string | null, technical: string | null = null) {
    setError(message);
    setErrorTechnical(technical);
  }

  function captureError(err: unknown, fallback: string) {
    const raw = err instanceof Error ? err.message : fallback;
    const translated = translateError(raw);
    setUserError(translated.message, translated.technical);
  }

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
      setUserError(null);
    } catch (err) {
      captureError(err, "Failed to load your materials");
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
  }, [load, refreshSynthesisStatus, projectId]);

  // Default the main-course checkbox OFF once a spine source already exists,
  // so a second upload doesn't silently join the main course. Only while the
  // user hasn't manually toggled it this session.
  useEffect(() => {
    if (!spineTouched.current) {
      setIsMainCourse(!hasSpine);
    }
  }, [hasSpine]);

  useEffect(() => {
    if (!synthesisStatus?.running_since) {
      return;
    }
    const poll = () => {
      void refreshSynthesisStatus();
    };
    let timer: number | undefined;
    const start = () => {
      if (timer === undefined) {
        timer = window.setInterval(poll, 15_000);
      }
    };
    const stop = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        poll();
        start();
      } else {
        stop();
      }
    };
    if (document.visibilityState === "visible") {
      start();
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [synthesisStatus?.running_since, refreshSynthesisStatus]);

  useEffect(() => {
    if (!sources.some(isActiveSource)) {
      return;
    }
    const poll = () => {
      void refreshSources().catch(() => {
        /* keep polling */
      });
    };
    let timer: number | undefined;
    const start = () => {
      if (timer === undefined) {
        timer = window.setInterval(poll, 5000);
      }
    };
    const stop = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        poll();
        start();
      } else {
        stop();
      }
    };
    if (document.visibilityState === "visible") {
      start();
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [sources, refreshSources]);

  function onFilesChosen(files: FileList | null) {
    const arr = files ? Array.from(files) : [];
    setUploadFiles(arr);
    setMainSourceIndex(arr.length ? 0 : null);
    setUserError(null);
  }

  async function onAddLink(event: FormEvent) {
    event.preventDefault();
    try {
      const sourceType = detectSourceTypeFromUrl(linkRef);
      const result = await api<IngestResponse>("/api/corpus/sources", {
        method: "POST",
        body: JSON.stringify({
          ref: linkRef.trim(),
          role,
          source_type: sourceType,
          local: false,
        }),
      });
      const rows = await refreshSources();
      const row = rows.find((item) => item.source_id === result.source_id);
      if (row) {
        setSources([row, ...rows.filter((item) => item.source_id !== result.source_id)]);
      }
      setLinkRef("");
      spineTouched.current = false;
      setUserError(null);
    } catch (err) {
      captureError(err, "Could not add that link");
    }
  }

  function roleFor(i: number): string {
    return uploadFiles.length > 1
      ? i === mainSourceIndex
        ? "spine"
        : "supplement"
      : isMainCourse
        ? "spine"
        : "supplement";
  }

  async function onUpload(event: FormEvent) {
    event.preventDefault();
    if (uploadFiles.length === 0) {
      setUserError("Choose a file first");
      return;
    }
    setUploading(true);
    let completed = 0;
    setUploadIndex({ current: 0, total: uploadFiles.length });
    // Upload concurrently — the backend/worker already fans out chunk
    // processing in parallel, so there's no benefit to a client-side queue.
    const results = await Promise.allSettled(
      uploadFiles.map((file, i) =>
        uploadSource(file, roleFor(i), false).then((r) => {
          completed += 1;
          setUploadIndex({ current: completed, total: uploadFiles.length });
          return r;
        }),
      ),
    );
    const failed = uploadFiles
      .filter((_, i) => results[i]!.status === "rejected")
      .map((f) => f.name);
    setUploadFiles([]);
    setMainSourceIndex(null);
    setUploadIndex(null);
    spineTouched.current = false;
    await refreshSources();
    if (failed.length > 0) {
      captureError(new Error(`These files failed to upload: ${failed.join(", ")}`), "Upload failed");
    } else {
      setUserError(null);
    }
    setUploading(false);
  }

  async function setStatus(sourceId: string, action: "pause" | "resume") {
    try {
      await api(`/api/corpus/sources/${encodeURIComponent(sourceId)}/${action}`, {
        method: "POST",
      });
      await refreshSources();
      setUserError(null);
    } catch (err) {
      captureError(err, `${action} failed`);
    }
  }

  async function onDomainProfileChange(value: string) {
    try {
      const result = await api<DomainProfileResponse>("/api/corpus/settings/domain-profile", {
        method: "POST",
        body: JSON.stringify({ profile: value }),
      });
      setDomainProfile(result.profile);
      setUserError(null);
    } catch (err) {
      captureError(err, "Could not save project setting");
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
      setUserError(null);
    } catch (err) {
      captureError(err, "Failed to load problem details");
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
      setUserError(null);
    } catch (err) {
      captureError(err, "Retry failed");
    }
  }

  async function onDelete(sourceId: string) {
    const ok = window.confirm(
      "Remove this material? Everything the app learned from it will be removed from your course too.",
    );
    if (!ok) {
      return;
    }
    try {
      await api(`/api/corpus/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
      setExpandedSource(null);
      await refreshSources();
      setUserError(null);
    } catch (err) {
      captureError(err, "Could not remove that material");
    }
  }

  async function onSynthesize() {
    setSynthMsg(null);
    try {
      const result = await api<SynthesizeResponse>("/api/corpus/synthesize", { method: "POST" });
      if (result.already_running) {
        setSynthMsg("Already building your course — hang tight.");
      } else if (!result.worker_online) {
        setSynthMsg(
          "Worker offline — synthesis was queued but nothing will process it. Start the worker (make worker or make run) and try again.",
        );
      } else {
        setSynthMsg("Synthesis queued — the background worker will process it shortly.");
      }
      setUserError(null);
    } catch (err) {
      captureError(err, "Synthesis could not start");
    }
  }

  if (loading) {
    return <Loading />;
  }

  const lastRun = synthesisStatus?.last_run;

  return (
    <section className="panel">
      <h1>My materials</h1>
      <ErrorBanner message={error} />
      {error && errorTechnical && errorTechnical !== error ? (
        <details className="technical-details">
          <summary>Show technical details</summary>
          <pre>{errorTechnical}</pre>
        </details>
      ) : null}

      <div className="add-material-card">
        <h2>Add learning material</h2>
        <div className="tab-row" role="tablist" aria-label="How to add material">
          <button
            type="button"
            role="tab"
            aria-selected={addTab === "file"}
            className={addTab === "file" ? "active" : undefined}
            onClick={() => setAddTab("file")}
          >
            From my computer
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={addTab === "link"}
            className={addTab === "link" ? "active" : undefined}
            onClick={() => setAddTab("link")}
          >
            From a link
          </button>
        </div>

        {uploadFiles.length > 1 ? null : (
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={isMainCourse}
              onChange={(event) => {
                spineTouched.current = true;
                setIsMainCourse(event.target.checked);
              }}
            />
            <span>
              <strong>Is this your main book or course?</strong>
              <span className="hint">
                {hasSpine
                  ? "You already have a main course — leave this off for extra reading, or turn it on to add this to the main course too."
                  : "Turn on for your primary textbook or course videos. Turn off for extra articles or side readings."}
              </span>
            </span>
          </label>
        )}

        {uploadFiles.length > 1 ? (
          <fieldset className="main-source-picker">
            <legend>Which of these is your main source? The rest become extra reading.</legend>
            {uploadFiles.map((file, i) => (
              <label key={i}>
                <input
                  type="radio"
                  name="main-source"
                  checked={i === mainSourceIndex}
                  onChange={() => setMainSourceIndex(i)}
                />
                {file.name}
              </label>
            ))}
          </fieldset>
        ) : null}

        {addTab === "file" ? (
          <form className="form-grid" onSubmit={(event) => void onUpload(event)}>
            <div
              className={`drop-zone ${dragOver ? "drag-over" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragOver(false);
                onFilesChosen(event.dataTransfer.files);
              }}
            >
              <p>Drag and drop PDFs, EPUBs, web pages, audio, or images here</p>
              <label className="browse-label">
                Browse
                <input
                  type="file"
                  multiple
                  accept=".pdf,.epub,.html,.htm,.mp3,.m4a,.wav,.flac,.ogg,.png,.jpg,.jpeg,.webp,.gif,.bmp,.heic"
                  onChange={(event) => onFilesChosen(event.target.files)}
                />
              </label>
              {uploadFiles.length === 1 ? (
                <p className="hint">Selected: {uploadFiles[0]!.name}</p>
              ) : uploadFiles.length > 1 ? (
                <p className="hint">{uploadFiles.length} files selected</p>
              ) : null}
            </div>
            {uploadIndex !== null ? (
              <div className="upload-progress">
                <progress max={uploadIndex.total} value={uploadIndex.current} />
                <span>
                  Uploading {uploadIndex.total} files… ({uploadIndex.current} of {uploadIndex.total} done)
                </span>
              </div>
            ) : null}
            <button type="submit" className="primary" disabled={uploadFiles.length === 0 || uploading}>
              {uploading
                ? "Uploading…"
                : uploadFiles.length > 1
                  ? `Add ${uploadFiles.length} files`
                  : "Add file"}
            </button>
          </form>
        ) : (
          <form className="form-grid" onSubmit={(event) => void onAddLink(event)}>
            <label>
              Paste a YouTube or article link
              <input
                value={linkRef}
                onChange={(event) => setLinkRef(event.target.value)}
                placeholder="https://…"
                required
              />
            </label>
            <button type="submit" className="primary" disabled={!linkRef.trim()}>
              Add link
            </button>
          </form>
        )}
      </div>

      <div className="settings-block">
        <button
          type="button"
          className="settings-toggle"
          aria-expanded={showSettings}
          onClick={() => setShowSettings((open) => !open)}
        >
          Project settings
        </button>
        {showSettings ? (
          <div className="settings-panel">
            <label>
              When sources disagree, treat this subject as
              <select
                value={domainProfile}
                onChange={(event) => void onDomainProfileChange(event.target.value)}
              >
                <option value="technical">Facts &amp; techniques</option>
                <option value="interpretive">Opinions &amp; interpretations</option>
              </select>
            </label>
            <p className="hint">
              <button type="button" onClick={() => void onSynthesize()}>
                Build my course from materials
              </button>
              {" — "}
              turns your reading notes into study topics.
            </p>
          </div>
        ) : null}
      </div>

      {synthMsg ? (
        <div className={`synth-notice ${synthMsg.includes("offline") ? "warn" : ""}`}>{synthMsg}</div>
      ) : null}
      {synthesisStatus?.running_since ? (
        <div className="synth-notice">
          Building your course from what was read… started{" "}
          {formatSynthesisAgo(synthesisStatus.running_since)}. {formatSynthesisProgress(synthesisStatus.progress)}{" "}
          Topics appear in My course when it finishes.
        </div>
      ) : null}
      {lastRun ? (
        <p className="hint">
          Last course update: {lastRun.processed_concepts} topics refreshed, {lastRun.curriculum_len}{" "}
          in your course ({formatSynthesisAgo(lastRun.ts)})
        </p>
      ) : null}

      <table className="materials-table">
        <thead>
          <tr>
            <th>Material</th>
            <th>Type</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sources.length === 0 ? (
            <tr>
              <td colSpan={5} className="empty-state">
                No materials yet — add a file or link above to get started.
              </td>
            </tr>
          ) : null}
          {sources.map((row) => (
            <Fragment key={row.source_id}>
              <tr>
                <td>{row.ref}</td>
                <td>{roleLabel(row.role)}</td>
                <td>
                  {sourceStatusLabel(row.status)}
                  {row.error ? (
                    <div className="hint">{translateError(row.error).message}</div>
                  ) : null}
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
                            {row.failed_chunks} problems — why?
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
                <td className="action-cell">
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
                      Retry
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void onDelete(row.source_id)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
              {expandedSource === row.source_id ? (
                <tr key={`${row.source_id}-failures`}>
                  <td colSpan={5}>
                    <ul>
                      {(failures[row.source_id] ?? []).map((group) => (
                        <li key={group.error}>
                          {group.count}× {translateError(group.error).message}
                          <details>
                            <summary>Technical details</summary>
                            {group.error}
                          </details>
                          <span className="hint"> (e.g. {group.sample_chunk_ids.join(", ")})</span>
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
      <p className="hint">
        When reading finishes, open <Link to="/curriculum">My course</Link> or{" "}
        <Link to="/chat">Ask questions</Link>.
      </p>
    </section>
  );
}
