import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ExportResponse, ProfileInfo, SpendSummary } from "../api/types";
import { ErrorBanner } from "./Status";

export function AdminBar() {
  const [profiles, setProfiles] = useState<ProfileInfo | null>(null);
  const [spend, setSpend] = useState<SpendSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshSpend = useCallback(async () => {
    try {
      setSpend(await api<SpendSummary>("/api/admin/spend?limit=100"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load spend");
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
    void refreshSpend();
    const timer = window.setInterval(() => void refreshSpend(), 30_000);
    return () => window.clearInterval(timer);
  }, [refreshProfiles, refreshSpend]);

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
      <div className="admin-bar">
        <strong>mslearn</strong>
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
          Spend: ${spend?.total_cost_usd.toFixed(4) ?? "—"} · {spend?.total_calls ?? 0} calls
        </span>
        <button type="button" onClick={() => void refreshSpend()}>
          Refresh spend
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
