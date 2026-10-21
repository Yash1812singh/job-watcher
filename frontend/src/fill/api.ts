/** Fill with AI API: per-user accounts, profile, resume and extension keys. */

const TOKEN_KEY = "fill-with-ai-token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: stays logged in for this tab only */
  }
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`/api/fill${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof Blob) ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : body.detail?.[0]?.msg?.replace(/^Value error, /, "") ?? msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export interface User { id: number; email: string; name: string }

export interface Entry { [k: string]: string }

export interface ProfileData {
  personal?: Entry;
  work?: Entry;
  eligibility?: Entry;
  eeo?: Entry;
  sourcing?: Entry;
  summary?: string;
  skills?: string;
  cover_letter?: string;
  education?: Entry[];
  history?: Entry[];
  languages?: Entry[];
  certifications?: Entry[];
  answers?: { question: string; answer: string }[];
}

export interface ProfileResponse {
  data: ProfileData;
  resume: { name: string; size: number; updated_at: string } | null;
  updated_at: string | null;
  gaps: string[];
}

export interface Key { id: string; label: string; created_at: string; last_used_at: string | null }

export interface Job {
  id: number;
  title: string;
  url: string;
  apply_url: string;
  location: string | null;
  department: string | null;
  fresh_at: string;
  tech_category: string | null;
  company: string;
  application: "opened" | "submitted" | null;
}

export interface Application {
  id: number;
  job_id: number | null;
  url: string;
  title: string;
  company: string;
  status: "opened" | "submitted";
  created_at: string;
  updated_at: string;
}
