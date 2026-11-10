import type { Envelope, Problem } from "./types";

export const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

const TOKEN_KEY = "fb.token";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail || problem.title);
    this.status = problem.status;
    this.code = problem.code;
    this.problem = problem;
  }
}

/** Token lives in sessionStorage (tab-scoped, cleared on close); never in localStorage or cookies. */
export const tokenStore = {
  get: (): string | null => sessionStorage.getItem(TOKEN_KEY),
  set: (t: string) => sessionStorage.setItem(TOKEN_KEY, t),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  idempotencyKey?: string;
  query?: Record<string, string | number | undefined | null>;
  signal?: AbortSignal;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const qs = opts.query
    ? "?" +
      new URLSearchParams(
        Object.entries(opts.query)
          .filter(([, v]) => v !== undefined && v !== null && v !== "")
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : "";
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/v1${path}${qs === "?" ? "" : qs}`, {
      method: opts.method ?? (body ? "POST" : "GET"),
      headers,
      body,
      signal: opts.signal,
    });
  } catch {
    throw new ApiError({ title: "Network error", status: 0, detail: "Could not reach the FactoryBridge API.", code: "NETWORK" });
  }
  if (res.status === 401 && path !== "/auth/login") {
    tokenStore.clear();
    onUnauthorized?.();
  }
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const p = (json ?? {}) as Partial<Problem>;
    throw new ApiError({
      title: p.title ?? res.statusText,
      status: res.status,
      detail: p.detail ?? res.statusText,
      code: p.code ?? `HTTP_${res.status}`,
      correlation_id: p.correlation_id ?? res.headers.get("X-Correlation-ID"),
      extra: p.extra,
    });
  }
  return json as T;
}

export async function data<T>(path: string, opts?: RequestOptions): Promise<T> {
  return (await request<Envelope<T>>(path, opts)).data;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const validation = e.problem.extra?.validation as { findings?: unknown } | undefined;
    const issues = (e.problem.extra?.issues ?? e.problem.extra?.blockers ?? e.problem.extra?.problems ?? validation?.findings) as
      | { message?: string; code?: string; field_path?: string; field?: string }[]
      | undefined;
    const tail = Array.isArray(issues) && issues.length
      ? " — " +
        issues
          .slice(0, 4)
          .map((i) => [i.field_path ?? i.field, i.code, i.message].filter(Boolean).join(": "))
          .join("; ")
      : "";
    return `${e.problem.detail}${tail}`;
  }
  return e instanceof Error ? e.message : String(e);
}
