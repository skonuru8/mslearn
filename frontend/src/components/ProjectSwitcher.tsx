import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useProject } from "../context/ProjectContext";

export function ProjectSwitcher() {
  const { projectId, projects, loading, setProjectId, createProject, deleteProject } = useProject();
  const navigate = useNavigate();
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  async function onCreate() {
    const name = newName.trim();
    if (!name) {
      return;
    }
    setBusy(true);
    try {
      await createProject(name);
      setNewName("");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (projectId === "default") {
      return;
    }
    const current = projects.find((p) => p.project_id === projectId);
    const label = current?.name ?? projectId;
    if (!window.confirm(`Delete project "${label}" and all its materials? This cannot be undone.`)) {
      return;
    }
    setBusy(true);
    try {
      await deleteProject(projectId);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="project-switcher">
      <label>
        Learning project
        <select
          value={projectId}
          disabled={loading || busy}
          onChange={(event) => {
            setProjectId(event.target.value);
            navigate("/curriculum");
          }}
          aria-label="Learning project"
        >
          {projects.map((row) => (
            <option key={row.project_id} value={row.project_id}>
              {row.name}
            </option>
          ))}
        </select>
      </label>
      <label className="new-project">
        New project
        <span className="inline-field">
          <input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="e.g. Biology 101"
            disabled={busy}
            aria-label="New project name"
          />
          <button type="button" onClick={() => void onCreate()} disabled={busy || !newName.trim()}>
            Add
          </button>
        </span>
      </label>
      {projectId !== "default" ? (
        <button type="button" className="danger" onClick={() => void onDelete()} disabled={busy}>
          Delete project
        </button>
      ) : null}
    </div>
  );
}
