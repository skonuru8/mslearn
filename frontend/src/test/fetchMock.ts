import { vi } from "vitest";

type FetchHandler = (url: string, init?: RequestInit) => unknown | Promise<unknown>;

export function installFetchMock(handlers: Record<string, FetchHandler>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.startsWith("http") ? new URL(url).pathname : url;
    const key = Object.keys(handlers)
      .sort((a, b) => b.length - a.length)
      .find((pattern) => path === pattern || path.startsWith(pattern));
    if (!key) {
      throw new Error(`unexpected fetch: ${path}`);
    }
    const body = await handlers[key](path, init);
    if (body && typeof body === "object" && "ok" in body) {
      return body;
    }
    return { ok: true, json: async () => body };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const defaultProjects = [{ project_id: "default", name: "Default", created_ts: 1 }];

export function corpusHandlers(sources: unknown[] = [], extra: Record<string, FetchHandler> = {}) {
  return {
    "/api/projects": () => defaultProjects,
    "/api/corpus/sources": (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return extra.postSource?.(path, init) ?? { source_id: "s-new" };
      }
      return sources;
    },
    "/api/corpus/settings/domain-profile": (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return extra.postDomainProfile?.(path, init) ?? { profile: "technical" };
      }
      return { profile: "technical" };
    },
    "/api/corpus/synthesis/status": () => ({ last_run: null }),
    ...extra,
  } satisfies Record<string, FetchHandler>;
}
