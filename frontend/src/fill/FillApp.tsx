import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api, ApiError, getToken, imageUrl, setToken,
  type Application, type Entry, type Job, type Key, type ProfileData, type ProfileResponse, type User,
} from "./api";

/* ── small building blocks ─────────────────────────────────────────────── */

type Opt = string | [string, string];

function Field({ label, value, onChange, type = "text", placeholder, options, hint, wide, multiline, rows = 4 }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string;
  options?: Opt[]; hint?: string; wide?: boolean; multiline?: boolean; rows?: number;
}) {
  const id = useId();
  const common = { id, ...(hint ? { "aria-describedby": `${id}-hint` } : {}) };
  return (
    <div className={`f-field${wide ? " f-wide" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {options ? (
        <select value={value} onChange={(e) => onChange(e.target.value)} {...common}>
          <option value="">Choose…</option>
          {options.map((o) => {
            const [v, l] = Array.isArray(o) ? o : [o, o];
            return <option key={v} value={v}>{l}</option>;
          })}
        </select>
      ) : multiline ? (
        <textarea value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} rows={rows} {...common} />
      ) : (
        <input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} {...common} />
      )}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  );
}

function Section({ id, title, intro, children }: { id: string; title: string; intro?: string; children: ReactNode }) {
  return (
    <section className="f-card" id={id}>
      <h2>{title}</h2>
      {intro && <p className="f-muted">{intro}</p>}
      {children}
    </section>
  );
}

const YES_NO: Opt[] = ["Yes", "No"];
// same windows as Job Watcher, 30 minutes by default
const WINDOWS: [string, string][] = [["15", "15 min"], ["30", "30 min"], ["60", "1 hour"], ["120", "2 hours"], ["360", "6 hours"], ["1440", "24 hours"]];
const WINDOW_KEY = "fill-with-ai.window";
const DECLINE: Opt[] = [["", "Prefer not to say"]];
const ago = (iso: string) => {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};

/* ── sign up / log in ───────────────────────────────────────────────────── */

function Auth({ onIn }: { onIn: (u: User) => void }) {
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = mode === "signup" ? { name, email, password } : { email, password };
      const r = await api<{ user: User; token: string }>(`/${mode}`, { method: "POST", body: JSON.stringify(body) });
      setToken(r.token);
      onIn(r.user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="f-auth">
      <div className="f-hero">
        <div className="f-logo">✦</div>
        <h1>Fill with AI</h1>
        <p>Answer the usual application questions once. Then pick a job, and its application opens with a panel that fills the form for you, tells you what only you can do, and carries on when you've done it. You review and press Submit.</p>
        <ul>
          <li>New jobs from hundreds of companies, updated every few minutes</li>
          <li>Greenhouse, Lever, Ashby, Workday and most careers sites</li>
          <li>Multi-page forms, resume and cover letter included</li>
          <li>Never submits on its own</li>
        </ul>
      </div>
      <form className="f-card f-auth-form" onSubmit={submit}>
        <div className="f-tabs">
          <button type="button" className={mode === "signup" ? "on" : ""} onClick={() => setMode("signup")}>Create account</button>
          <button type="button" className={mode === "login" ? "on" : ""} onClick={() => setMode("login")}>Log in</button>
        </div>
        {mode === "signup" && <Field label="Your name" value={name} onChange={setName} placeholder="Asha Rao" />}
        <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
        <Field label="Password" type="password" value={password} onChange={setPassword} hint={mode === "signup" ? "At least 8 characters." : undefined} />
        {error && <p className="f-error">{error}</p>}
        <button className="f-primary" disabled={busy}>{busy ? "…" : mode === "signup" ? "Create account" : "Log in"}</button>
      </form>
    </div>
  );
}

/* ── profile pieces ─────────────────────────────────────────────────────── */

function completeness(d: ProfileData, hasResume: boolean): { pct: number; missing: string[] } {
  const p = d.personal ?? {}, w = d.work ?? {};
  const checks: [string, boolean][] = [
    ["First name", !!p.first_name], ["Last name", !!p.last_name], ["Email", !!p.email], ["Phone", !!p.phone],
    ["City", !!p.city], ["Country", !!p.country], ["LinkedIn", !!p.linkedin], ["Current role", !!w.current_title],
    ["Years of experience", !!w.years], ["Notice period", !!w.notice_period], ["Current salary", !!w.current_salary],
    ["Expected salary", !!w.desired_salary], ["Reason for change", !!w.reason_for_change], ["Resume", hasResume],
    ["Education", !!d.education?.some((e) => e.institution)], ["Experience", !!d.history?.some((h) => h.company)],
    ["Skills", !!d.skills?.trim()], ["Summary", !!d.summary?.trim()],
  ];
  return { pct: Math.round((checks.filter(([, ok]) => ok).length / checks.length) * 100), missing: checks.filter(([, ok]) => !ok).map(([n]) => n) };
}

function ListEditor({ items, onChange, fields, addLabel, empty }: {
  items: Entry[]; onChange: (v: Entry[]) => void; addLabel: string; empty: string;
  fields: { key: string; label: string; placeholder?: string; wide?: boolean; multiline?: boolean; options?: Opt[] }[];
}) {
  return (
    <div className="f-list">
      {items.length === 0 && <p className="f-muted">{empty}</p>}
      {items.map((item, i) => (
        <div className="f-list-item" key={i}>
          <div className="f-grid">
            {fields.map((f) => (
              <Field key={f.key} label={f.label} placeholder={f.placeholder} wide={f.wide} multiline={f.multiline} options={f.options}
                value={item[f.key] ?? ""} onChange={(v) => onChange(items.map((it, j) => (j === i ? { ...it, [f.key]: v } : it)))} />
            ))}
          </div>
          <button type="button" className="f-link-danger" onClick={() => onChange(items.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...items, {}])}>+ {addLabel}</button>
    </div>
  );
}

function ResumeBox({ resume, onUploaded }: { resume: ProfileResponse["resume"]; onUploaded: (p: ProfileResponse) => void }) {
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = async (file?: File) => {
    if (!file) return;
    setError("");
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) return setError("Please choose a PDF.");
    if (file.size > 5 * 1024 * 1024) return setError("That file is over 5 MB.");
    setBusy(true);
    try {
      onUploaded(await api<ProfileResponse>("/resume", { method: "PUT", body: file, headers: { "X-Filename": file.name, "Content-Type": "application/pdf" } }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const view = async () => {
    const res = await fetch("/api/fill/resume", { headers: { Authorization: `Bearer ${getToken()}` } });
    if (res.ok) window.open(URL.createObjectURL(await res.blob()), "_blank");
  };
  return (
    <div>
      <div className={`f-drop${drag ? " drag" : ""}`} role="button" tabIndex={0} onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}>
        <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => upload(e.target.files?.[0])} />
        {busy ? "Uploading…" : resume
          ? <>📄 <b>{resume.name}</b> · {Math.max(1, Math.round(resume.size / 1024))} KB · drop a new PDF to replace it</>
          : <>Drop your resume PDF here, or <u>choose a file</u></>}
      </div>
      {resume && <button type="button" className="f-link" onClick={view}>View current resume</button>}
      {error && <p className="f-error">{error}</p>}
    </div>
  );
}

/* ── the browser extension ──────────────────────────────────────────────── */

interface ExtState { checked: boolean; installed: boolean; version: string; email: string | null }

/** Talks to the extension's connector on this page: is it installed, and whose account is it using? */
function useExtension(): [ExtState, (key: string) => void] {
  const [ext, setExt] = useState<ExtState>({ checked: false, installed: false, version: "", email: null });
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.source !== window || e.origin !== location.origin || e.data?.source !== "fwai-ext" || e.data.type !== "status") return;
      setExt({ checked: true, installed: true, version: e.data.version, email: e.data.email });
    };
    window.addEventListener("message", onMsg);
    const hello = () => window.postMessage({ source: "fwai-page", type: "hello" }, location.origin);
    hello();
    const timers = [setTimeout(hello, 800), setTimeout(hello, 2000), setTimeout(() => setExt((x) => ({ ...x, checked: true })), 3000)];
    const poll = setInterval(hello, 5000); // notices an install made while the page is open
    return () => { window.removeEventListener("message", onMsg); timers.forEach(clearTimeout); clearInterval(poll); };
  }, []);
  const connect = useCallback((key: string) => window.postMessage({ source: "fwai-page", type: "connect", key }, location.origin), []);
  return [ext, connect];
}

type Stores = { store_url: string; edge_url: string; firefox_url: string };
type Browser = "chrome" | "edge" | "firefox" | "other";
const BROWSER: Browser = /Edg\//.test(navigator.userAgent) ? "edge" : /Firefox\//.test(navigator.userAgent) ? "firefox"
  : /Chrome\//.test(navigator.userAgent) ? "chrome" : "other";
const STORE_NAMES: Record<keyof Stores, [string, string]> = {
  store_url: ["Chrome", "Add to Chrome"], edge_url: ["Microsoft Edge", "Get it for Edge"], firefox_url: ["Firefox", "Add to Firefox"],
};

/* ── tabs ───────────────────────────────────────────────────────────────── */

type Tab = "jobs" | "applications" | "profile" | "extension";
const TABS: [Tab, string][] = [["jobs", "Jobs"], ["applications", "My applications"], ["profile", "Profile"], ["extension", "Extension"]];

function JobsTab({ ext, profilePct, go }: { ext: ExtState; profilePct: number; go: (t: Tab) => void }) {
  const hasKey = !ext.checked || !!ext.email;
  const [q, setQ] = useState("");
  const [india, setIndia] = useState(true);
  const [tech, setTech] = useState(true);
  const [within, setWithinState] = useState(() => {
    try {
      const v = localStorage.getItem(WINDOW_KEY) ?? "";
      return WINDOWS.some(([m]) => m === v) ? v : "30";
    } catch {
      return "30";
    }
  });
  const setWithin = (v: string) => {
    setWithinState(v);
    try { localStorage.setItem(WINDOW_KEY, v); } catch { /* not remembered in private mode */ }
  };
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    const params = new URLSearchParams({ q, india: String(india), tech: String(tech), within_minutes: within, limit: "200" });
    api<Job[]>(`/jobs?${params}`).then((j) => { setJobs(j); setError(""); }).catch((e) => setError((e as Error).message));
  }, [q, india, tech, within]);
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => {
    const t = setInterval(load, 30_000); // companies are checked every minute or two
    return () => clearInterval(t);
  }, [load]);
  const open = (j: Job) => {
    window.open(`${j.apply_url}#fill-with-ai=${j.id}`, "_blank", "noopener");
    setJobs((list) => list?.map((x) => (x.id === j.id && !x.application ? { ...x, application: "opened" } : x)) ?? null);
  };
  // Apply to all: the extension opens these one after another in one tab, fills each page and goes on
  const todo = (jobs ?? []).filter((j) => j.application !== "submitted");
  const [queued, setQueued] = useState("");
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.source !== window || e.origin !== location.origin || e.data?.source !== "fwai-ext" || e.data.type !== "queued") return;
      setQueued(e.data.ok ? `Applying to ${e.data.count} jobs in a new tab. Check each one and click Submit; the next opens by itself.`
                          : `Couldn't start: ${e.data.error || "is the extension up to date?"}`);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);
  const applyAll = () => {
    if (!confirm(`Apply to ${todo.length} job${todo.length === 1 ? "" : "s"}?\n\nThey open one after another in a new tab. Each form is filled, ` +
                 "your resume is matched to the job, and Next is clicked for you. You check each one and click Submit; then the next opens. " +
                 "Close the tab to stop.")) return;
    window.postMessage({ source: "fwai-page", type: "queue", jobs: todo.map((j) => ({ id: j.id, url: j.apply_url })) }, location.origin);
  };
  return (
    <div className="f-tab">
      {!hasKey && (
        <div className="f-banner warn">
          <b>{ext.installed ? "Connecting the extension…" : "Add the extension first."}</b> It's what fills the forms.{" "}
          {!ext.installed && <button className="f-link" onClick={() => go("extension")}>Add it →</button>}
        </div>
      )}
      {hasKey && profilePct < 70 && (
        <div className="f-banner">
          Your profile is {profilePct}% complete. The more it has, the less you'll type. <button className="f-link" onClick={() => go("profile")}>Finish it →</button>
        </div>
      )}
      <div className="f-card f-filters">
        <input aria-label="Search jobs" placeholder="Search title, company or city" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="f-check"><input type="checkbox" checked={india} onChange={(e) => setIndia(e.target.checked)} /> India only</label>
        <label className="f-check"><input type="checkbox" checked={tech} onChange={(e) => setTech(e.target.checked)} /> Tech roles only</label>
        <label className="f-check">Posted in the last
          <select value={within} onChange={(e) => setWithin(e.target.value)}>
            {WINDOWS.map(([m, label]) => <option key={m} value={m}>{label}</option>)}
          </select>
        </label>
      </div>
      {error && <p className="f-error">{error}</p>}
      {queued && <div className="f-banner">{queued}</div>}
      {hasKey && ext.installed && todo.length > 1 && (
        <div className="f-applyall">
          <button className="f-primary" onClick={applyAll}>Apply to all {todo.length}</button>
          <span className="f-muted small">Opens them one after another: filled, resume matched to each job, pages moved through. You only check and click Submit.</span>
        </div>
      )}
      {jobs === null ? <p className="f-muted">Loading jobs…</p> : jobs.length === 0 ? (
        <div className="f-card f-empty">
          No new jobs in the last {WINDOWS.find(([m]) => m === within)?.[1]}{q ? ` matching “${q}”` : ""}.
          They appear here as soon as they're posted, so leave this open or pick a longer window.
        </div>
      ) : (
        <ul className="f-jobs">
          {jobs.map((j) => (
            <li key={j.id} className="f-card f-job">
              <div className="f-job-main">
                <a href={j.url} target="_blank" rel="noreferrer" className="f-job-title">{j.title}</a>
                <div className="f-muted small">
                  <b className="f-company">{j.company}</b>{j.location ? ` · ${j.location}` : ""} · {ago(j.fresh_at)}
                  {j.tech_category ? <span className="f-tag">{j.tech_category}</span> : null}
                </div>
              </div>
              <div className="f-job-actions">
                {j.application === "submitted" && <span className="f-chip ok">Applied ✓</span>}
                {j.application === "opened" && <span className="f-chip">In progress</span>}
                {j.application === "filled" && <span className="f-chip">Ready to submit</span>}
                <button className="f-primary" onClick={() => open(j)}>{j.application === "submitted" ? "Open again" : "Apply with AI"}</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const STATUS: Record<Application["status"], [string, string]> = {
  submitted: ["Applied ✓", "ok"], filled: ["Ready to submit", ""], opened: ["In progress", ""],
};

function Proof({ id }: { id: number }) {
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url = "";
    imageUrl(`/applications/${id}/proof`).then((u) => { url = u; setSrc(u); }).catch(() => setFailed(true));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id]);
  if (failed) return <p className="f-muted small">The screenshot couldn't be loaded.</p>;
  return src ? <a href={src} target="_blank" rel="noreferrer"><img className="f-proof" src={src} alt="The site's confirmation page after you submitted" /></a>
             : <p className="f-muted small">Loading the screenshot…</p>;
}

function ApplicationDetails({ a, onSkills }: { a: Application; onSkills: (skills: string[]) => Promise<void> }) {
  const d = a.details || {};
  const [added, setAdded] = useState<string[]>([]);
  const missing = (d.resume?.missing ?? []).filter((k) => !added.includes(k));
  const confirm = async (skills: string[]) => { await onSkills(skills); setAdded((x) => [...x, ...skills]); };
  return (
    <div className="f-app-details">
      {a.status === "submitted" && (
        <section>
          <h4>Confirmation</h4>
          <p>Submitted {a.submitted_at ? new Date(a.submitted_at).toLocaleString() : ""}.{d.confirmation ? <> The site said: <q>{d.confirmation}</q></> : null}</p>
          {a.has_proof && <Proof id={a.id} />}
        </section>
      )}
      {d.resume && (
        <section>
          <h4>Resume sent</h4>
          {d.resume.tailored ? (
            <p>Your resume, with its wording matched to this job{d.resume.added.length ? <>: <b>{d.resume.added.join(", ")}</b></> : null}. Nothing was added that your resume or profile doesn't show.</p>
          ) : <p>Your resume as it is ({d.resume.name}).{d.resume.note ? ` ${d.resume.note}` : ""}</p>}
          {missing.length > 0 && (
            <div className="f-missing">
              <p className="f-muted small">The job also asks for these, which your resume doesn't show, so they weren't added. If you do have one, add it to your skills and future resumes will use it:</p>
              <div className="f-chips">
                {missing.map((k) => <button key={k} className="f-chip-btn" onClick={() => confirm([k])} title={`Add “${k}” to your skills`}>+ {k}</button>)}
              </div>
            </div>
          )}
          {added.length > 0 && <p className="f-muted small">Added to your skills: {added.join(", ")}.</p>}
        </section>
      )}
      {d.account?.created && <section><h4>Account</h4><p>Made an account password for {d.account.site} (saved in the extension's Options page).</p></section>}
      {(d.needs?.length ?? 0) > 0 && a.status !== "submitted" && (
        <section><h4>Left for you</h4><ul>{d.needs!.map((n) => <li key={n}>{n}</li>)}</ul></section>
      )}
      {(d.filled?.length ?? 0) > 0 && (
        <section>
          <h4>Filled in ({d.filled!.length})</h4>
          <table className="f-filled"><tbody>
            {d.filled!.map((f, i) => (
              <tr key={i}><th>{f.label}</th><td>{f.value}{f.source === "ai" ? <span className="f-tag">AI</span> : null}</td></tr>
            ))}
          </tbody></table>
        </section>
      )}
    </div>
  );
}

function ApplicationsTab({ go }: { go: (t: Tab) => void }) {
  const [apps, setApps] = useState<Application[] | null>(null);
  useEffect(() => { api<Application[]>("/applications").then(setApps).catch(() => setApps([])); }, []);
  const addSkills = async (skills: string[]) => {
    await api("/profile/skills", { method: "POST", body: JSON.stringify({ skills }) });
  };
  if (apps === null) return <p className="f-muted">Loading…</p>;
  const sent = apps.filter((a) => a.status === "submitted").length;
  const ready = apps.filter((a) => a.status === "filled").length;
  return (
    <div className="f-tab">
      <div className="f-stats">
        <div className="f-card"><b>{sent}</b><span>applied</span></div>
        <div className="f-card"><b>{ready}</b><span>ready to submit</span></div>
        <div className="f-card"><b>{apps.length - sent - ready}</b><span>in progress</span></div>
      </div>
      {apps.length === 0 ? (
        <div className="f-card f-empty">Nothing yet. <button className="f-link" onClick={() => go("jobs")}>Pick a job to apply to →</button></div>
      ) : (
        <ul className="f-jobs">
          {apps.map((a) => {
            const [label, tone] = STATUS[a.status] ?? STATUS.opened;
            return (
              <li key={a.id} className="f-card f-job f-app">
                <div className="f-job-row">
                  <div className="f-job-main">
                    <a href={a.url} target="_blank" rel="noreferrer" className="f-job-title">{a.title || a.url}</a>
                    <div className="f-muted small"><b className="f-company">{a.company || a.details?.site}</b> · {a.status === "submitted" ? "applied" : "last worked on"} {ago(a.submitted_at || a.updated_at)}</div>
                  </div>
                  <div className="f-job-actions">
                    <span className={`f-chip${tone ? ` ${tone}` : ""}`}>{label}</span>
                    {a.status !== "submitted" && (
                      <button onClick={() => window.open(a.job_id ? `${a.url}#fill-with-ai=${a.job_id}` : a.url, "_blank", "noopener")}>Continue</button>
                    )}
                  </div>
                </div>
                <details>
                  <summary>What was sent{a.has_proof ? " · with screenshot" : ""}</summary>
                  <ApplicationDetails a={a} onSkills={addSkills} />
                </details>
              </li>
            );
          })}
        </ul>
      )}
      <p className="f-muted small">An application counts as applied when the panel sees the site's confirmation after you submit; a screenshot of it is kept here as proof.</p>
    </div>
  );
}

function ExtensionSetup({ ext, user, stores }: { ext: ExtState; user: User; stores: Stores }) {
  const mine: keyof Stores = BROWSER === "edge" ? "edge_url" : BROWSER === "firefox" ? "firefox_url" : "store_url";
  const storeUrl = stores[mine];
  const others = (Object.keys(STORE_NAMES) as (keyof Stores)[]).filter((k) => k !== mine && stores[k]);
  const [keys, setKeys] = useState<Key[]>([]);
  const [fresh, setFresh] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => { api<Key[]>("/keys").then(setKeys).catch(() => {}); }, [ext.email]);
  const create = async () => {
    const r = await api<{ key: string; keys: Key[] }>("/keys", { method: "POST", body: JSON.stringify({ label: "Created by hand" }) });
    setFresh(r.key);
    setKeys(r.keys);
    setCopied(false);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fresh);
      setCopied(true);
    } catch {
      /* the key is selectable */
    }
  };
  const revoke = async (id: string) => {
    await api(`/keys/${id}`, { method: "DELETE" });
    setKeys(keys.filter((k) => k.id !== id));
  };
  const connected = ext.installed && ext.email === user.email;
  return (
    <div className="f-tab">
      <section className={`f-card f-ext ${connected ? "ok" : ""}`}>
        {connected ? (
          <>
            <div className="f-ext-icon">✓</div>
            <div>
              <h2>The extension is connected</h2>
              <p className="f-muted">It fills forms as <b>{user.email}</b>. Pick a job in <b>Jobs</b> and click <b>Apply with AI</b>; nothing else to set up.</p>
            </div>
          </>
        ) : ext.installed ? (
          <>
            <div className="f-ext-icon">⟳</div>
            <div><h2>Connecting…</h2><p className="f-muted">The extension is installed; it's linking to your account.</p></div>
          </>
        ) : (
          <>
            <div className="f-ext-icon">✦</div>
            <div>
              <h2>Add Fill with AI to {BROWSER === "edge" ? "Edge" : BROWSER === "firefox" ? "Firefox" : "your browser"}</h2>
              {storeUrl ? (
                <>
                  <p className="f-muted">One click. It connects to your account by itself; there's nothing to set up.</p>
                  <a className="f-button big" href={storeUrl} target="_blank" rel="noreferrer">{STORE_NAMES[mine][1]}</a>
                </>
              ) : BROWSER === "firefox" ? (
                <p className="f-muted">The Firefox version isn't published yet. Until it is, use Chrome or Edge.</p>
              ) : (
                <>
                  <p className="f-muted">It connects to your account by itself once it's added. Until it's on {BROWSER === "edge" ? "Edge Add-ons" : "the Chrome Web Store"}, adding it takes two steps:</p>
                  <ol className="f-steps">
                    <li><a className="f-button" href="/api/fill/extension.zip">Download the extension</a> and unzip it.</li>
                    <li>Open <code>{BROWSER === "edge" ? "edge://extensions" : "chrome://extensions"}</code>, turn on <b>Developer mode</b>, click <b>Load unpacked</b> and pick the <code>fill-with-ai</code> folder.</li>
                  </ol>
                </>
              )}
              {others.length > 0 && (
                <p className="f-muted small">Also for {others.map((k, i) => (
                  <span key={k}>{i ? ", " : ""}<a href={stores[k]} target="_blank" rel="noreferrer">{STORE_NAMES[k][0]}</a></span>))}.</p>
              )}
              <p className="f-muted small">{ext.checked ? "Not installed in this browser yet. This page notices as soon as it is." : "Checking…"}</p>
            </div>
          </>
        )}
      </section>

      <section className="f-card">
        <h2>How it works</h2>
        <p className="f-muted">In <b>Jobs</b>, click <b>Apply with AI</b>. The application opens with a panel at the top that fills it. When it lists something for you (a CAPTCHA, signing in, a question only you can answer), do it and the panel carries on. Click <b>Continue</b> between pages and press Submit yourself at the end. On any other job site, click the extension icon (or <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>F</kbd>).</p>
      </section>

      <details className="f-card f-advanced">
        <summary>Advanced: extension keys</summary>
        <p className="f-muted small">The extension gets a key automatically. Create one by hand only for a browser that can't open this page (paste it in the extension's Options).</p>
        {fresh ? (
          <div className="f-key">
            <code>{fresh}</code>
            <button type="button" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
            <small>Shown once. Anyone with this key can fill forms with your details.</small>
          </div>
        ) : <button type="button" onClick={create}>Create a key</button>}
        {keys.length > 0 && (
          <ul className="f-keys">
            {keys.map((k) => (
              <li key={k.id}>
                <span>{k.label} · created {new Date(k.created_at).toLocaleDateString()}{k.last_used_at ? ` · last used ${new Date(k.last_used_at).toLocaleDateString()}` : " · not used yet"}</span>
                <button type="button" className="f-link-danger" onClick={() => revoke(k.id)}>Revoke</button>
              </li>
            ))}
          </ul>
        )}
      </details>
    </div>
  );
}

/* ── the profile form ───────────────────────────────────────────────────── */

const PROFILE_SECTIONS = [
  ["about", "About you"], ["links", "Links"], ["work", "Work"], ["eligibility", "Eligibility"], ["documents", "Resume & cover letter"],
  ["education", "Education"], ["experience", "Experience"], ["skills", "Skills"], ["languages", "Languages & certifications"],
  ["diversity", "Diversity"], ["sourcing", "How you heard"], ["answers", "Your answers"],
] as const;

function ProfileForm({ d, setD, saved, setSaved, pct, missing }: {
  d: ProfileData; setD: (f: (x: ProfileData) => ProfileData) => void; saved: ProfileResponse; setSaved: (p: ProfileResponse) => void;
  pct: number; missing: string[];
}) {
  const p = d.personal ?? {}, w = d.work ?? {}, el = d.eligibility ?? {}, eeo = d.eeo ?? {}, src = d.sourcing ?? {};
  const set = (section: "personal" | "work" | "eligibility" | "eeo" | "sourcing", key: string) => (v: string) =>
    setD((x) => ({ ...x, [section]: { ...(x[section] ?? {}), [key]: v } }));
  const setList = (key: "education" | "history" | "languages" | "certifications") => (v: Entry[]) => setD((x) => ({ ...x, [key]: v }));
  return (
    <div className="f-layout">
      <aside className="f-side">
        <div className="f-card">
          <div className="f-meter-label"><b>Profile {pct}% complete</b></div>
          <div className="f-meter"><div style={{ width: `${pct}%` }} /></div>
          {missing.length > 0
            ? <p className="f-muted small">Add: {missing.slice(0, 6).join(", ")}{missing.length > 6 ? "…" : ""}</p>
            : <p className="f-ok small">Everything most forms ask for is here.</p>}
        </div>
        <nav className="f-nav">
          {PROFILE_SECTIONS.map(([id, label]) => <a key={id} href={`#profile-${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }); }}>{label}</a>)}
        </nav>
      </aside>

      <div className="f-main">
        <Section id="about" title="About you">
          <div className="f-grid">
            <Field label="First name" value={p.first_name ?? ""} onChange={set("personal", "first_name")} />
            <Field label="Last name" value={p.last_name ?? ""} onChange={set("personal", "last_name")} />
            <Field label="Preferred name" value={p.preferred_name ?? ""} onChange={set("personal", "preferred_name")} hint="If different from your first name." />
            <Field label="Email" type="email" value={p.email ?? ""} onChange={set("personal", "email")} hint="The one employers should reply to." />
            <Field label="Phone" type="tel" value={p.phone ?? ""} onChange={set("personal", "phone")} placeholder="+91 98765 43210" />
            <Field label="Alternate phone" type="tel" value={p.alt_phone ?? ""} onChange={set("personal", "alt_phone")} />
            <Field label="Date of birth" type="date" value={p.dob ?? ""} onChange={set("personal", "dob")} hint="Only used when a form asks for it." />
            <Field label="Nationality" value={p.nationality ?? ""} onChange={set("personal", "nationality")} placeholder="Indian" />
            <Field label="Street address" wide value={p.address ?? ""} onChange={set("personal", "address")} />
            <Field label="City" value={p.city ?? ""} onChange={set("personal", "city")} placeholder="Bengaluru" />
            <Field label="State" value={p.state ?? ""} onChange={set("personal", "state")} />
            <Field label="Country" value={p.country ?? ""} onChange={set("personal", "country")} placeholder="India" />
            <Field label="PIN / ZIP code" value={p.zip ?? ""} onChange={set("personal", "zip")} />
          </div>
        </Section>

        <Section id="links" title="Links">
          <div className="f-grid">
            <Field label="LinkedIn URL" type="url" value={p.linkedin ?? ""} onChange={set("personal", "linkedin")} placeholder="https://linkedin.com/in/…" hint="Most forms require this." />
            <Field label="GitHub URL" type="url" value={p.github ?? ""} onChange={set("personal", "github")} />
            <Field label="Portfolio / website" type="url" value={p.portfolio ?? ""} onChange={set("personal", "portfolio")} />
            <Field label="LeetCode / HackerRank" type="url" value={p.coding_profile ?? ""} onChange={set("personal", "coding_profile")} />
            <Field label="Twitter / X" type="url" value={p.twitter ?? ""} onChange={set("personal", "twitter")} />
          </div>
        </Section>

        <Section id="work" title="Work">
          <div className="f-grid">
            <Field label="Employment status" options={["Employed", "Not employed", "Serving notice", "Student", "Freelancing"]} value={w.employment_status ?? ""} onChange={set("work", "employment_status")} />
            <Field label="Current job title" value={w.current_title ?? ""} onChange={set("work", "current_title")} placeholder="Backend Engineer" />
            <Field label="Current company" value={w.current_company ?? ""} onChange={set("work", "current_company")} />
            <Field label="Total experience (years)" type="number" value={w.years ?? ""} onChange={set("work", "years")} />
            <Field label="Relevant experience (years)" type="number" value={w.relevant_years ?? ""} onChange={set("work", "relevant_years")} />
            <Field label="Current salary (CTC)" value={w.current_salary ?? ""} onChange={set("work", "current_salary")} placeholder="18 LPA" />
            <Field label="Fixed part" value={w.current_fixed ?? ""} onChange={set("work", "current_fixed")} placeholder="16 LPA" />
            <Field label="Variable part" value={w.current_variable ?? ""} onChange={set("work", "current_variable")} placeholder="2 LPA" />
            <Field label="Expected salary" value={w.desired_salary ?? ""} onChange={set("work", "desired_salary")} placeholder="24 LPA" />
            <Field label="Notice period" value={w.notice_period ?? ""} onChange={set("work", "notice_period")} placeholder="30 days" />
            <Field label="Is it negotiable / can you buy it out?" value={w.notice_negotiable ?? ""} onChange={set("work", "notice_negotiable")} placeholder="Yes, can join in 15 days" />
            <Field label="Last working day" type="date" value={w.last_working_day ?? ""} onChange={set("work", "last_working_day")} hint="If you're serving notice." />
            <Field label="Earliest start" value={w.availability ?? ""} onChange={set("work", "availability")} placeholder="Within 30 days" />
            <Field label="Preferred work locations" value={w.preferred_locations ?? ""} onChange={set("work", "preferred_locations")} placeholder="Bengaluru, Pune, Remote" />
            <Field label="Why are you looking for a change?" wide multiline rows={3} value={w.reason_for_change ?? ""} onChange={set("work", "reason_for_change")} hint="Used as-is; if empty, the AI writes one from your profile." />
          </div>
        </Section>

        <Section id="eligibility" title="Eligibility" intro="The yes/no questions most applications ask.">
          <div className="f-grid">
            <Field label="Authorised to work in the job's country?" options={YES_NO} value={el.work_authorization ?? ""} onChange={set("eligibility", "work_authorization")} />
            <Field label="Need visa sponsorship?" options={YES_NO} value={el.require_sponsorship ?? ""} onChange={set("eligibility", "require_sponsorship")} />
            <Field label="Visa / work permit status" value={el.visa_status ?? ""} onChange={set("eligibility", "visa_status")} placeholder="Indian citizen" />
            <Field label="Willing to relocate?" options={YES_NO} value={el.can_relocate ?? ""} onChange={set("eligibility", "can_relocate")} />
            <Field label="Willing to travel?" options={["Yes", "No", "Occasionally", "Up to 25%", "Up to 50%"]} value={el.willing_to_travel ?? ""} onChange={set("eligibility", "willing_to_travel")} />
            <Field label="Work preference" options={["Remote", "Hybrid", "On-site", "Any"]} value={el.remote_preference ?? ""} onChange={set("eligibility", "remote_preference")} />
            <Field label="Night / weekend / rotational shifts?" options={YES_NO} value={el.shifts ?? ""} onChange={set("eligibility", "shifts")} />
            <Field label="OK with a background check?" options={YES_NO} value={el.background_check ?? ""} onChange={set("eligibility", "background_check")} />
          </div>
        </Section>

        <Section id="documents" title="Resume & cover letter" intro="Your resume is attached to every application (PDF, up to 5 MB).">
          <ResumeBox resume={saved.resume} onUploaded={(r) => setSaved({ ...r, data: saved.data })} />
          <div className="f-grid" style={{ marginTop: 16 }}>
            <Field label="Cover letter template" wide multiline rows={7} value={d.cover_letter ?? ""} onChange={(v) => setD((x) => ({ ...x, cover_letter: v }))}
              placeholder={"Dear Hiring Team at {company},\n\nI'm excited to apply for the {role} role…"}
              hint="{company} and {role} are filled in for each job. Leave it empty and the AI writes one for each job from your profile." />
          </div>
        </Section>

        <Section id="education" title="Education" intro="Highest first.">
          <ListEditor items={d.education ?? []} onChange={setList("education")} addLabel="Add education" empty="No education added yet."
            fields={[{ key: "degree", label: "Degree", placeholder: "B.Tech" }, { key: "field", label: "Field of study", placeholder: "Computer Science" },
              { key: "institution", label: "College / university" }, { key: "start_year", label: "Start year", placeholder: "2017" },
              { key: "graduation_year", label: "Graduation year", placeholder: "2021" }, { key: "gpa", label: "CGPA / %" }]} />
        </Section>

        <Section id="experience" title="Experience" intro="Most recent first. The AI uses this to answer questions about you.">
          <ListEditor items={d.history ?? []} onChange={setList("history")} addLabel="Add a job" empty="No jobs added yet."
            fields={[{ key: "title", label: "Title" }, { key: "company", label: "Company" }, { key: "location", label: "Location" },
              { key: "start_date", label: "From", placeholder: "Jan 2022" }, { key: "end_date", label: "To", placeholder: "Present" },
              { key: "description", label: "What you did", wide: true, multiline: true }]} />
        </Section>

        <Section id="skills" title="Skills and summary">
          <div className="f-grid">
            <Field label="Skills" wide multiline rows={3} value={d.skills ?? ""} onChange={(v) => setD((x) => ({ ...x, skills: v }))} placeholder="Python, Django, PostgreSQL, AWS, React" hint="Comma-separated." />
            <Field label="Professional summary" wide multiline rows={3} value={d.summary ?? ""} onChange={(v) => setD((x) => ({ ...x, summary: v }))} placeholder="Two or three lines about you." />
          </div>
          <label className="f-check f-pref">
            <input type="checkbox" checked={d.preferences?.tailor_resume !== false}
                   onChange={(e) => setD((x) => ({ ...x, preferences: { ...x.preferences, tailor_resume: e.target.checked } }))} />
            <span><b>Match my resume to each job.</b> Its wording uses the job's terms for skills your resume or these skills show (nothing you don't have is ever added). Employers, titles, dates and numbers stay exactly as they are.</span>
          </label>
        </Section>

        <Section id="languages" title="Languages & certifications">
          <h3 className="f-sub">Languages</h3>
          <ListEditor items={d.languages ?? []} onChange={setList("languages")} addLabel="Add a language" empty="None added."
            fields={[{ key: "language", label: "Language", placeholder: "English" },
              { key: "proficiency", label: "Proficiency", options: ["Native", "Fluent", "Professional", "Conversational", "Basic"] }]} />
          <h3 className="f-sub">Certifications</h3>
          <ListEditor items={d.certifications ?? []} onChange={setList("certifications")} addLabel="Add a certification" empty="None added."
            fields={[{ key: "name", label: "Certification", placeholder: "AWS Solutions Architect" }, { key: "issuer", label: "Issued by" }, { key: "year", label: "Year" }]} />
        </Section>

        <Section id="diversity" title="Diversity questions" intro="Optional. Anything left on “Prefer not to say” is answered that way.">
          <div className="f-grid">
            <Field label="Gender" options={[...DECLINE, "Female", "Male", "Non-binary"]} value={eeo.gender ?? ""} onChange={set("eeo", "gender")} />
            <Field label="Ethnicity / race" value={eeo.ethnicity ?? ""} onChange={set("eeo", "ethnicity")} placeholder="Prefer not to say" />
            <Field label="Veteran status" options={[...DECLINE, "I am not a veteran", "I am a veteran"]} value={eeo.veteran ?? ""} onChange={set("eeo", "veteran")} />
            <Field label="Disability" options={[...DECLINE, "No, I don't have a disability", "Yes, I have a disability"]} value={eeo.disability ?? ""} onChange={set("eeo", "disability")} />
          </div>
        </Section>

        <Section id="sourcing" title="How you heard about the job">
          <div className="f-grid">
            <Field label="Usual answer" options={["LinkedIn", "Company website", "Job board", "Referral", "Recruiter", "Other"]} value={src.how_heard ?? ""} onChange={set("sourcing", "how_heard")} />
            <Field label="Referred by (name)" value={src.referral_name ?? ""} onChange={set("sourcing", "referral_name")} hint="Leave empty if none." />
          </div>
        </Section>

        <Section id="answers" title="Your answers" intro="Questions you often see, answered your way. Used before the AI writes anything (matched on words in the question).">
          <ListEditor items={(d.answers ?? []) as unknown as Entry[]} onChange={(v) => setD((x) => ({ ...x, answers: v as unknown as ProfileData["answers"] }))}
            addLabel="Add an answer" empty={'For example: "What are you most proud of?"'}
            fields={[{ key: "question", label: "When a question mentions…", placeholder: "most proud of", wide: true }, { key: "answer", label: "Answer with", wide: true, multiline: true }]} />
        </Section>
      </div>
    </div>
  );
}

/* ── the signed-in app ──────────────────────────────────────────────────── */

function Home({ user, onOut }: { user: User; onOut: () => void }) {
  const [tab, setTabState] = useState<Tab>(() => (TABS.find(([t]) => location.hash === `#${t}`)?.[0] ?? "jobs"));
  const [saved, setSaved] = useState<ProfileResponse | null>(null);
  const [d, setDState] = useState<ProfileData>({});
  const [ext, connectExt] = useExtension();
  const [stores, setStores] = useState<Stores>({ store_url: "", edge_url: "", firefox_url: "" });
  const connecting = useRef(false);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const setD = useCallback((f: (x: ProfileData) => ProfileData) => setDState(f), []);
  const go = useCallback((t: Tab) => { setTabState(t); history.pushState(null, "", `#${t}`); window.scrollTo(0, 0); }, []);
  useEffect(() => { // back/forward and #links switch tabs too
    const sync = () => { const t = TABS.find(([x]) => location.hash === `#${x}`)?.[0]; if (t) setTabState(t); };
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, []);

  useEffect(() => {
    api<ProfileResponse>("/profile").then((p) => {
      setSaved(p);
      setDState(p.data);
      if (!location.hash && completeness(p.data, !!p.resume).pct < 40) setTabState("profile"); // new users start with the profile
    }).catch((e) => e instanceof ApiError && e.status === 401 && onOut());
    fetch("/api/fill/config").then((r) => r.json()).then((c: Stores) => setStores(c)).catch(() => {});
  }, [onOut]);

  // The extension is in this browser but not linked to this account: link it, no copying or pasting.
  useEffect(() => {
    if (!ext.installed || ext.email === user.email || connecting.current) return;
    connecting.current = true;
    api<{ key: string }>("/keys", { method: "POST", body: JSON.stringify({ label: "Chrome (connected automatically)" }) })
      .then((r) => connectExt(r.key))
      .catch(() => {})
      .finally(() => setTimeout(() => { connecting.current = false; }, 4000));
  }, [ext.installed, ext.email, user.email, connectExt]);

  const dirty = useMemo(() => saved !== null && JSON.stringify(d) !== JSON.stringify(saved.data), [d, saved]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async () => {
    setState("saving");
    try {
      const p = await api<ProfileResponse>("/profile", { method: "PUT", body: JSON.stringify(d) });
      setSaved(p);
      setState("saved");
      setTimeout(() => setState("idle"), 2000);
    } catch (e) {
      setError((e as Error).message);
      setState("error");
    }
  };
  const logout = async () => {
    try { await api("/logout", { method: "POST" }); } catch { /* already gone */ }
    setToken("");
    onOut();
  };
  const deleteAccount = async () => {
    await api("/me", { method: "DELETE" });
    setToken("");
    onOut();
  };

  if (!saved) return <div className="f-loading">Loading…</div>;
  const done = completeness(d, !!saved.resume);

  return (
    <div className="f-app">
      <header className="f-top">
        <div className="f-brand"><span className="f-logo sm">✦</span> Fill with AI</div>
        <nav className="f-tabs-top" aria-label="Sections">
          {TABS.map(([t, label]) => (
            <button key={t} className={tab === t ? "on" : ""} aria-current={tab === t ? "page" : undefined} onClick={() => go(t)}>{label}</button>
          ))}
        </nav>
        <div className="f-top-right"><span className="f-muted">{user.email}</span><button onClick={logout}>Log out</button></div>
      </header>

      {tab === "jobs" && <div className="f-page"><JobsTab ext={ext} profilePct={done.pct} go={go} /></div>}
      {tab === "applications" && <div className="f-page"><ApplicationsTab go={go} /></div>}
      {tab === "extension" && <div className="f-page"><ExtensionSetup ext={ext} user={user} stores={stores} /></div>}
      {tab === "profile" && (
        <>
          <ProfileForm d={d} setD={setD} saved={saved} setSaved={setSaved} pct={done.pct} missing={done.missing} />
          <div className="f-page">
            <section className="f-card f-danger-zone">
              <h2>Delete account</h2>
              <p className="f-muted">Removes your profile, resume, applications and extension keys for good.</p>
              {confirmDelete
                ? <div className="f-row"><button className="f-danger" onClick={deleteAccount}>Yes, delete everything</button><button onClick={() => setConfirmDelete(false)}>Cancel</button></div>
                : <button onClick={() => setConfirmDelete(true)}>Delete my account</button>}
            </section>
          </div>
        </>
      )}

      <div className={`f-savebar${dirty || state !== "idle" ? " show" : ""}`}>
        <span>{state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? error : "You have unsaved changes"}</span>
        <button className="f-primary" onClick={save} disabled={!dirty || state === "saving"}>Save</button>
      </div>
    </div>
  );
}

export default function FillApp() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(!!getToken());
  const out = useCallback(() => setUser(null), []);
  useEffect(() => {
    if (!getToken()) return;
    api<User>("/me").then(setUser).catch(() => setToken("")).finally(() => setChecking(false));
  }, []);
  if (checking) return <div className="f-loading">Loading…</div>;
  return user ? <Home user={user} onOut={out} /> : <Auth onIn={setUser} />;
}
