import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, CompanyPage, Job, Site, TechCategory, View } from "./api";

const POLL_MS = 10_000;
const CHECK_INTERVALS = [1, 2, 5, 15, 30, 60, 180, 720, 1440];
const WINDOWS = [15, 30, 60, 120, 360, 1440];
const WINDOW_KEY = "jobwatcher.window";
const COMPANY_PAGE = 50; // company cards drawn at once; the rest via "Show more" or search

const VIEW_KEY = "jobwatcher.view";
const DEFAULT_VIEW: View = { country: "india", role: "tech" };

function readView(): View {
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
    return {
      country: v?.country === "all" ? "all" : "india",
      role: v?.role === "all" ? "all" : "tech",
    };
  } catch {
    return DEFAULT_VIEW;
  }
}

/** "technical jobs in India", "technical jobs", "jobs in India" or "jobs" (singular when count is 1) */
function viewLabel(view: View, count = 2): string {
  return `${view.role === "tech" ? "technical " : ""}${count === 1 ? "job" : "jobs"}${view.country === "india" ? " in India" : ""}`;
}

function readWindow(): number {
  try {
    const v = Number(localStorage.getItem(WINDOW_KEY));
    return WINDOWS.includes(v) ? v : 30;
  } catch {
    return 30;
  }
}

function minutesSince(iso: string): number {
  return Math.max(0, (Date.now() - new Date(iso).getTime()) / 60_000);
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = minutesSince(iso);
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.floor(mins)} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function durationLabel(mins: number): string {
  if (mins < 60) return `${mins} min`;
  if (mins < 1440) return `${mins / 60} h`;
  return `${mins / 1440} day`;
}

function methodLabel(method: string | null): string {
  if (!method || method === "none") return "";
  const rendered = method.endsWith(" (rendered)");
  const base = method.replace(" (rendered)", "");
  const names: Record<string, string> = {
    greenhouse: "Greenhouse board",
    lever: "Lever board",
    ashby: "Ashby board",
    workable: "Workable board",
    smartrecruiters: "SmartRecruiters board",
    workday: "Workday careers site",
    amazon: "Amazon jobs feed",
    eightfold: "Eightfold careers site",
    "json-ld": "page job data",
    links: "job links on page",
  };
  return `via ${names[base] ?? base}${rendered ? " · browser" : ""}`;
}

function AddCompany({ onAdded }: { onAdded: (site: Site) => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [interval, setInterval] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const site = await api.addSite(url, name, interval);
      setUrl("");
      setName("");
      onAdded(site);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="add" onSubmit={submit}>
      <label htmlFor="url" className="label">Company careers page</label>
      <input
        id="url"
        type="text"
        inputMode="url"
        placeholder="https://company.com/careers"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        required
      />
      <div className="add-row">
        <input
          id="name"
          type="text"
          placeholder="Company name (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
        />
        <select id="interval" value={interval} onChange={(e) => setInterval(Number(e.target.value))} aria-label="Check every">
          {CHECK_INTERVALS.map((m) => (
            <option key={m} value={m}>check every {durationLabel(m)}</option>
          ))}
        </select>
      </div>
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Adding…" : "Watch this company"}
      </button>
      {error ? <p className="form-error">{error}</p> : null}
    </form>
  );
}

function CompanyRow({ site, windowMins, selected, scanning, onSelect, onScan, onRemove, onInterval }: {
  site: Site;
  windowMins: number;
  selected: boolean;
  scanning: boolean;
  onSelect: () => void;
  onScan: () => void;
  onRemove: () => void;
  onInterval: (mins: number) => void;
}) {
  const status = scanning || site.last_status === "pending" ? "checking" : site.last_status;
  const statusText = {
    checking: "Checking…",
    ok: `${site.active_jobs} listed`,
    empty: "No jobs found",
    error: "Couldn't read",
  }[status];

  return (
    <li className={`company ${selected ? "selected" : ""}`}>
      <button className="company-main" onClick={onSelect} aria-pressed={selected}>
        <span className="company-name">
          {site.name}
          {site.fresh_jobs > 0 ? (
            <span className={`pill ${site.new_jobs > 0 ? "new" : "fresh"}`}>
              {site.fresh_jobs} in last {durationLabel(windowMins)}
            </span>
          ) : null}
        </span>
        <span className="company-url">{site.url.replace(/^https?:\/\/(www\.)?/, "")}</span>
        <span className="company-meta">
          <span className={`dot ${status}`} aria-hidden />
          {statusText}
          <span className="sep">·</span>
          checked {ago(site.last_checked_at)}
          {site.last_method && status === "ok" ? <><span className="sep">·</span>{methodLabel(site.last_method)}</> : null}
        </span>
        {site.last_error && status !== "checking" ? (
          <span className={`company-note ${status === "error" ? "error" : ""}`}>{site.last_error}</span>
        ) : null}
      </button>
      <div className="company-actions">
        <select
          value={site.interval_minutes}
          onChange={(e) => onInterval(Number(e.target.value))}
          aria-label={`Check ${site.name} every`}
        >
          {[...new Set([...CHECK_INTERVALS, site.interval_minutes])].sort((a, b) => a - b).map((m) => (
            <option key={m} value={m}>every {durationLabel(m)}</option>
          ))}
        </select>
        <button onClick={onScan} disabled={scanning}>{scanning ? "Checking…" : "Check now"}</button>
        <button className="danger" onClick={onRemove}>Remove</button>
      </div>
    </li>
  );
}

function JobRow({ job, windowMins, onOpen }: { job: Job; windowMins: number; onOpen: () => void }) {
  const isNew = job.is_seen === 0;
  const age = minutesSince(job.fresh_at);
  const leavesIn = Math.max(1, Math.ceil(windowMins - age));
  const when = job.posted_at_source === "site" ? `posted ${ago(job.fresh_at)}` : `appeared ${ago(job.fresh_at)}`;
  return (
    <li className={`job ${isNew ? "is-new" : ""}`}>
      <div className="job-main">
        <a href={job.url} target="_blank" rel="noreferrer" onClick={onOpen} className="job-title">
          {job.title}
        </a>
        <div className="job-meta">
          {job.tech_category ? <span className="role-tag">{job.tech_category}</span> : null}
          <span className="job-company">{job.site_name}</span>
          {job.location ? <><span className="sep">·</span>{job.location}</> : null}
          {job.department ? <><span className="sep">·</span>{job.department}</> : null}
        </div>
      </div>
      <div className="job-side">
        <span className="job-when" title={new Date(job.fresh_at).toLocaleString()}>
          {isNew ? <span className="pill new">New</span> : null}
          {when}
        </span>
        <span className="job-leaves">leaves list in {leavesIn} min</span>
      </div>
    </li>
  );
}

export default function App() {
  const [sites, setSites] = useState<Site[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [windowMins, setWindowMins] = useState(readWindow);
  const [view, setView] = useState<View>(readView);
  const [techRoles, setTechRoles] = useState<TechCategory[]>([]);

  useEffect(() => {
    api.techRoles().then(setTechRoles).catch(() => setTechRoles([]));
  }, []);
  const [siteId, setSiteId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [companyQuery, setCompanyQuery] = useState("");
  const [companyLimit, setCompanyLimit] = useState(COMPANY_PAGE);
  const [companyTotal, setCompanyTotal] = useState(0);
  const [summary, setSummary] = useState<CompanyPage["summary"]>({ companies: 0, fresh_jobs: 0, new_jobs: 0 });
  // keeps "Jobs at X" working when the chosen company scrolls out of the loaded page
  const [pinnedSite, setPinnedSite] = useState<Site | null>(null);
  const [scanning, setScanning] = useState<Set<number>>(new Set());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [alerts, setAlerts] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );
  const [, setTick] = useState(0);
  const knownIds = useRef<Set<number> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [page, j] = await Promise.all([
        api.companies(windowMins, view, companyQuery, companyLimit),
        api.jobs(windowMins, view, siteId, query),
      ]);
      setSites(page.items);
      setCompanyTotal(page.total);
      setSummary(page.summary);
      setJobs(j);
      setLoadError(null);
    } catch (err) {
      setLoadError(`Can't reach the Job Watcher backend: ${(err as Error).message}`);
    } finally {
      setLoaded(true);
    }
  }, [windowMins, view, siteId, query, companyQuery, companyLimit]);

  useEffect(() => {
    const t = setTimeout(refresh, 200); // small debounce for typing in search
    const poll = window.setInterval(refresh, POLL_MS);
    return () => {
      clearTimeout(t);
      window.clearInterval(poll);
    };
  }, [refresh]);

  // Re-render every 30 s so "posted N min ago" / "leaves list in" stay current between polls.
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 30_000);
    return () => window.clearInterval(t);
  }, []);

  // Poll faster while any company is still on its first check.
  useEffect(() => {
    if (!sites.some((s) => s.last_status === "pending")) return;
    const t = setTimeout(refresh, 2500);
    return () => clearTimeout(t);
  }, [sites, refresh]);

  // Desktop alert for each job that shows up after the page has loaded.
  useEffect(() => {
    if (!loaded || loadError) return;
    const ids = new Set(jobs.map((j) => j.id));
    if (knownIds.current && alerts === "granted") {
      const arrived = jobs.filter((j) => !knownIds.current!.has(j.id) && j.is_seen === 0);
      for (const job of arrived.slice(0, 5)) {
        const n = new Notification(`New job at ${job.site_name}`, {
          body: [job.title, job.location].filter(Boolean).join(" · "),
          tag: `job-${job.id}`,
        });
        n.onclick = () => window.open(job.url, "_blank");
      }
    }
    knownIds.current = new Set([...(knownIds.current ?? []), ...ids]);
  }, [jobs, loaded, loadError, alerts]);

  const totalFresh = summary.fresh_jobs;
  const totalNew = summary.new_jobs;
  // the server sends companies with fresh jobs first, then problems to fix, then A–Z
  const shownSites = sites;
  const selectedSite = sites.find((s) => s.id === siteId) ?? pinnedSite;
  const newInView = selectedSite ? selectedSite.new_jobs : totalNew;

  useEffect(() => {
    document.title = totalNew > 0 ? `(${totalNew}) Job Watcher` : "Job Watcher";
  }, [totalNew]);

  function changeView(patch: Partial<View>) {
    const next = { ...view, ...patch };
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(next));
    } catch {
      /* remembering the choice is optional */
    }
  }

  function chooseWindow(mins: number) {
    setWindowMins(mins);
    try {
      localStorage.setItem(WINDOW_KEY, String(mins));
    } catch {
      /* remembering the choice is optional */
    }
  }

  async function enableAlerts() {
    if (typeof Notification === "undefined") return;
    setAlerts(await Notification.requestPermission());
  }

  async function scan(id: number) {
    setScanning((s) => new Set(s).add(id));
    try {
      await api.scanSite(id);
    } finally {
      setScanning((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
      refresh();
    }
  }

  async function remove(site: Site) {
    if (!window.confirm(`Stop watching ${site.name}? Its saved jobs will be removed.`)) return;
    await api.deleteSite(site.id);
    if (siteId === site.id) setSiteId(null);
    refresh();
  }

  async function openJob(job: Job) {
    if (job.is_seen) return;
    await api.markSeen(job.id);
    refresh();
  }

  async function markAllSeen() {
    await api.markAllSeen(siteId, view);
    refresh();
  }

  const emptyText =
    summary.companies === 0
      ? "Add a company's careers page on the left to start watching it."
      : query
        ? `No job posted in the last ${durationLabel(windowMins)} matches “${query}”.`
        : `No ${viewLabel(view)} posted in the last ${durationLabel(windowMins)}. They appear here the moment a watched company posts them${view.country === "india" || view.role === "tech" ? " — or widen the filters above to see other jobs" : ""}.`;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◎</span>
          <span>Job Watcher</span>
        </div>
        <div className="stats">
          <span><strong>{summary.companies.toLocaleString()}</strong> companies</span>
          <span><strong>{totalFresh}</strong> {viewLabel(view)} in last {durationLabel(windowMins)}</span>
          <span className={totalNew ? "has-new" : ""}><strong>{totalNew}</strong> new</span>
          {alerts === "granted" ? (
            <span className="alerts-on">Alerts on</span>
          ) : alerts === "default" ? (
            <button className="alerts-btn" onClick={enableAlerts}>Turn on alerts</button>
          ) : alerts === "denied" ? (
            <span className="alerts-off" title="Allow notifications for this site in your browser settings">Alerts blocked</span>
          ) : null}
        </div>
      </header>

      {loadError ? <div className="banner">{loadError}. Start it with <code>./run.sh</code>.</div> : null}

      <main className="layout">
        <aside className="panel companies-panel">
          <h2>Companies</h2>
          <AddCompany onAdded={(s) => { setSites((prev) => [s, ...prev]); refresh(); }} />
          {summary.companies > 0 ? (
            <>
              <div className="company-tools">
                <button
                  className={`all-companies ${siteId === null ? "selected" : ""}`}
                  onClick={() => { setSiteId(null); setPinnedSite(null); }}
                  aria-pressed={siteId === null}
                >
                  All {summary.companies.toLocaleString()} companies
                </button>
                <input
                  id="company-search"
                  type="search"
                  placeholder="Find a company"
                  value={companyQuery}
                  onChange={(e) => { setCompanyQuery(e.target.value); setCompanyLimit(COMPANY_PAGE); }}
                />
              </div>
              {shownSites.length === 0 && companyQuery ? <p className="hint">No company matches “{companyQuery}”.</p> : null}
              <ul className="company-list">
                {shownSites.map((site) => (
                  <CompanyRow
                    key={site.id}
                    site={site}
                    windowMins={windowMins}
                    selected={site.id === siteId}
                    scanning={scanning.has(site.id)}
                    onSelect={() => {
                      setSiteId(site.id === siteId ? null : site.id);
                      setPinnedSite(site.id === siteId ? null : site);
                    }}
                    onScan={() => scan(site.id)}
                    onRemove={() => remove(site)}
                    onInterval={async (m) => { await api.updateSite(site.id, { interval_minutes: m }); refresh(); }}
                  />
                ))}
              </ul>
              {companyTotal > shownSites.length ? (
                <button className="show-more" onClick={() => setCompanyLimit((n) => n + COMPANY_PAGE)}>
                  Show {Math.min(COMPANY_PAGE, companyTotal - shownSites.length)} more
                  ({(companyTotal - shownSites.length).toLocaleString()} not shown)
                </button>
              ) : null}
            </>
          ) : loaded ? (
            <p className="hint">
              Paste a company's own careers page, e.g. <code>razorpay.com/jobs</code>. Jobs already listed
              when you add it are treated as old; anything posted after that shows up here.
            </p>
          ) : null}
        </aside>

        <section className="panel jobs-panel">
          <div className="jobs-head">
            <h2>{selectedSite ? `Jobs at ${selectedSite.name}` : "Jobs"}</h2>
            <label className="window">
              <span>Posted in the last</span>
              <select id="window" value={windowMins} onChange={(e) => chooseWindow(Number(e.target.value))}>
                {WINDOWS.map((m) => <option key={m} value={m}>{durationLabel(m)}</option>)}
              </select>
            </label>
          </div>
          <div className="view-filters" role="group" aria-label="Which jobs to show">
            <label className={`chip ${view.country === "india" ? "on" : ""}`}>
              <input
                id="filter-india"
                type="checkbox"
                checked={view.country === "india"}
                onChange={(e) => changeView({ country: e.target.checked ? "india" : "all" })}
              />
              India only
            </label>
            <label className={`chip ${view.role === "tech" ? "on" : ""}`}>
              <input
                id="filter-tech"
                type="checkbox"
                checked={view.role === "tech"}
                onChange={(e) => changeView({ role: e.target.checked ? "tech" : "all" })}
              />
              Technical roles only
            </label>
            {view.country !== DEFAULT_VIEW.country || view.role !== DEFAULT_VIEW.role ? (
              <button className="link-btn" onClick={() => changeView(DEFAULT_VIEW)}>Back to default</button>
            ) : null}
          </div>
          {techRoles.length > 0 ? (
            <details className="tech-roles">
              <summary>
                What counts as technical? {techRoles.length} categories,{" "}
                {techRoles.reduce((n, c) => n + c.roles.length, 0)} roles
              </summary>
              <div className="tech-roles-grid">
                {techRoles.map((c) => (
                  <section key={c.category}>
                    <h3>{c.category}</h3>
                    <ul>{c.roles.map((r) => <li key={r}>{r}</li>)}</ul>
                  </section>
                ))}
              </div>
              <p className="tech-roles-note">
                Matched against job titles; sales, recruiting, HR, finance and technician roles are left out even
                when they mention a technical word. Jobs with a vague title count if the company lists them under an
                engineering, technology or data team.
              </p>
            </details>
          ) : null}
          <div className="jobs-tools">
            <input
              id="search"
              type="search"
              placeholder="Filter by title, location or team"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button onClick={markAllSeen} disabled={newInView === 0}>Mark all as seen</button>
          </div>
          {jobs.length > 0 ? (
            <>
              <p className="count">
                {jobs.length} {viewLabel(view, jobs.length)} posted in the last {durationLabel(windowMins)}
              </p>
              <ul className="job-list">
                {jobs.map((job) => (
                  <JobRow key={job.id} job={job} windowMins={windowMins} onOpen={() => openJob(job)} />
                ))}
              </ul>
            </>
          ) : loaded ? (
            <p className="empty">{emptyText}</p>
          ) : null}
        </section>
      </main>
    </div>
  );
}
