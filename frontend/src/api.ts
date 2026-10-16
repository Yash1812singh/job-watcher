export type SiteStatus = "pending" | "ok" | "empty" | "error";

export type Site = {
  id: number;
  url: string;
  name: string;
  interval_minutes: number;
  created_at: string;
  last_checked_at: string | null;
  last_status: SiteStatus;
  last_error: string | null; // error text, or a note about how/why nothing was found
  last_method: string | null;
  last_job_count: number | null;
  active_jobs: number; // everything currently listed on the site
  fresh_jobs: number;  // posted inside the window
  new_jobs: number;    // posted inside the window and not opened yet
};

export type Job = {
  id: number;
  site_id: number;
  site_name: string;
  title: string;
  url: string;
  location: string | null;
  department: string | null;
  posted_at: string | null;
  fresh_at: string;                    // posting time used for the window
  posted_at_source: "site" | "found";  // from the site, or when Job Watcher first saw it
  tech_category: string | null;        // e.g. "Software Engineering"; null if not a technical role
  first_seen_at: string;
  last_seen_at: string;
  is_active: 0 | 1;
  is_seen: 0 | 1;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, "")).join("; ")
      : detail ?? `Request failed (${res.status})`;
    throw new Error(message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/** One page of companies plus totals for the top bar. */
export type CompanyPage = {
  total: number;
  items: Site[];
  summary: { companies: number; fresh_jobs: number; new_jobs: number };
};

/** A heading in "What counts as technical?" and the roles under it. */
export type TechCategory = { category: string; roles: string[] };

/** Which jobs to show. Default: technical roles in India. */
export type View = { country: "india" | "all"; role: "tech" | "all" };

export const api = {
  sites: (postedWithin: number, view: View) =>
    request<Site[]>(`/api/sites?${new URLSearchParams({ posted_within: String(postedWithin), ...view })}`),
  companies: (postedWithin: number, view: View, q: string, limit: number) =>
    request<CompanyPage>(`/api/companies?${new URLSearchParams({
      posted_within: String(postedWithin), ...view, q: q.trim(), limit: String(limit),
    })}`),
  addSite: (url: string, name: string, intervalMinutes: number) =>
    request<Site>("/api/sites", {
      method: "POST",
      body: JSON.stringify({ url, name: name || null, interval_minutes: intervalMinutes }),
    }),
  updateSite: (id: number, patch: { name?: string; interval_minutes?: number }) =>
    request<Site>(`/api/sites/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteSite: (id: number) => request<void>(`/api/sites/${id}`, { method: "DELETE" }),
  scanSite: (id: number) => request<Site>(`/api/sites/${id}/scan`, { method: "POST" }),
  jobs: (postedWithin: number, view: View, siteId: number | null, q: string) => {
    const params = new URLSearchParams({ posted_within: String(postedWithin), ...view });
    if (siteId != null) params.set("site_id", String(siteId));
    if (q.trim()) params.set("q", q.trim());
    return request<Job[]>(`/api/jobs?${params}`);
  },
  markAllSeen: (siteId: number | null, view: View) =>
    request<{ updated: number }>("/api/jobs/mark-seen", {
      method: "POST",
      body: JSON.stringify({ site_id: siteId, ...view }),
    }),
  techRoles: () => request<TechCategory[]>("/api/tech-roles"),
  markSeen: (jobId: number) => request<{ updated: number }>(`/api/jobs/${jobId}/seen`, { method: "POST" }),
};
