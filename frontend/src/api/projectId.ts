const STORAGE_KEY = "mslearn.activeProject";

let activeProjectId =
  (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) || "default";

export function getActiveProjectId(): string {
  return activeProjectId;
}

export function setActiveProjectId(projectId: string): void {
  activeProjectId = projectId;
  localStorage.setItem(STORAGE_KEY, projectId);
}

export function projectHeaders(): HeadersInit {
  return { "X-Project-Id": activeProjectId };
}
