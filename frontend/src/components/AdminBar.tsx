import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ExportResponse, ProfileInfo, StatusResponse } from "../api/types";
import { ErrorBanner } from "./Status";

const STATUS_POLL_MS = 30_000;

export function AdminBar() {
  const [profiles, setProfiles] = useState<ProfileInfo | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api<StatusResponse>("/api/status"));
    } catch {
      setStatus(null);
    }
  }, []);

  const refreshProfiles = useCallback(async () => {
    try {
      setProfiles(await api<ProfileInfo>("/api/admin/profiles"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load profiles");
    }
  }, []);

  useEffect(() => {
    void refreshProfiles();
    void refreshStatus();

    let timer: number | undefined;
    const start = () => {
      if (timer === undefined) {
        timer = window.setInterval(() => void refreshStatus(), STATUS_POLL_MS);
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
        void refreshStatus();
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
  }, [refreshProfiles, refreshStatus]);

  async function onProfileChange(name: string) {
    try {
      await api(`/api/admin/profiles/${encodeURIComponent(name)}`, { method: "POST" });
      await refreshProfiles();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Profile switch failed");
    }
  }

  async function onExport() {
    setBusy(true);
    setExportMsg(null);
    try {
      const result = await api<ExportResponse>("/api/exports", {
        method: "POST",
        body: JSON.stringify({ kinds: ["markdown", "anki", "graph"] }),
      });
      const lines = Object.entries(result.files).flatMap(([kind, paths]) =>
        paths.map((path) => `${kind}: ${path}`),
      );
      setExportMsg(`Exported to ${result.root}\n${lines.join("\n")}`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="admin-bar" aria-label="Advanced settings">
        <strong>Advanced</strong>
        <span
          className={`worker-chip ${status?.worker ? "online" : "offline"}`}
          title="Background jobs (ingestion, synthesis) need a Celery worker process running alongside the API. See README.md (scripts/dev_up.sh / make run)."
        >
          {status?.worker
            ? "Background worker running"
            : "Worker offline — sources won't process, synthesis won't run"}
        </span>
        {status?.dead_letter_count ? (
          <span
            className="worker-chip offline"
            title="Some queued jobs came from an older version of the app and no worker will ever pick them up. Restart the app with the latest code; if this doesn't clear, contact support."
          >
            {status.dead_letter_count} background{" "}
            {status.dead_letter_count === 1 ? "job is" : "jobs are"} stuck — restart the app with
            the latest code
          </span>
        ) : null}
        <label>
          Profile
          <select
            value={profiles?.active ?? ""}
            disabled={!profiles}
            onChange={(event) => void onProfileChange(event.target.value)}
          >
            {(profiles?.available ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <span>
          Spend: ${status?.spend.total_cost_usd.toFixed(4) ?? "—"} · {status?.spend.total_calls ?? 0}{" "}
          calls
        </span>
        <button type="button" onClick={() => void refreshStatus()}>
          Refresh
        </button>
        <button type="button" className="primary" disabled={busy} onClick={() => void onExport()}>
          Export all
        </button>
        <a href="/api/admin/tunables" target="_blank" rel="noreferrer">
          Tunables API
        </a>
      </div>
      <ErrorBanner message={error} />
      {exportMsg ? <div className="toast">{exportMsg}</div> : null}
    </div>
  );
}
