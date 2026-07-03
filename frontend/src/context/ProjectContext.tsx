import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";
import { getActiveProjectId, setActiveProjectId } from "../api/projectId";
import type { ProjectRow } from "../api/types";

type ProjectContextValue = {
  projectId: string;
  projects: ProjectRow[];
  loading: boolean;
  error: string | null;
  setProjectId: (id: string) => void;
  refreshProjects: () => Promise<void>;
  createProject: (name: string) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
};

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projectId, setProjectIdState] = useState(getActiveProjectId);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshProjects = useCallback(async () => {
    const rows = await api<ProjectRow[]>("/api/projects");
    setProjects(rows);
    if (!rows.some((row) => row.project_id === getActiveProjectId())) {
      const fallback = rows[0]?.project_id ?? "default";
      setActiveProjectId(fallback);
      setProjectIdState(fallback);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refreshProjects();
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load projects");
      } finally {
        setLoading(false);
      }
    })();
  }, [refreshProjects]);

  const setProjectId = useCallback((id: string) => {
    setActiveProjectId(id);
    setProjectIdState(id);
  }, []);

  const createProject = useCallback(
    async (name: string) => {
      const created = await api<ProjectRow>("/api/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      await refreshProjects();
      setProjectId(created.project_id);
    },
    [refreshProjects, setProjectId],
  );

  const deleteProject = useCallback(
    async (id: string) => {
      await api(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refreshProjects();
    },
    [refreshProjects],
  );

  const value = useMemo(
    () => ({
      projectId,
      projects,
      loading,
      error,
      setProjectId,
      refreshProjects,
      createProject,
      deleteProject,
    }),
    [projectId, projects, loading, error, setProjectId, refreshProjects, createProject, deleteProject],
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) {
    throw new Error("useProject must be used within ProjectProvider");
  }
  return ctx;
}
