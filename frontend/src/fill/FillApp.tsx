import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, getToken, setToken, type Entry, type Key, type ProfileData, type ProfileResponse, type User } from "./api";

/* ── small building blocks ─────────────────────────────────────────────── */

type Opt = string | [string, string];

function Field({ label, value, onChange, type = "text", placeholder, options, hint, wide, multiline }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string;
  options?: Opt[]; hint?: string; wide?: boolean; multiline?: boolean;
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
        <textarea value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} rows={4} {...common} />
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
        <p>Save your details once. Then, on any job application, one click fills the form, attaches your resume and drafts answers to the questions. You review and press Submit.</p>
        <ul>
          <li>Works on Greenhouse, Lever, Ashby, Workday and most careers sites</li>
          <li>Multi-page forms: click again on each page</li>
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

/* ── the profile ────────────────────────────────────────────────────────── */

const SECTIONS = [
  ["about", "About you"], ["links", "Links"], ["work", "Work"], ["eligibility", "Eligibility"], ["resume", "Resume"],
  ["education", "Education"], ["experience", "Experience"], ["skills", "Skills"], ["answers", "Your answers"], ["extension", "Extension"],
] as const;

function completeness(d: ProfileData, hasResume: boolean): { pct: number; missing: string[] } {
  const p = d.personal ?? {}, w = d.work ?? {};
  const checks: [string, boolean][] = [
    ["First name", !!p.first_name], ["Last name", !!p.last_name], ["Email", !!p.email], ["Phone", !!p.phone],
    ["City", !!p.city], ["Country", !!p.country], ["LinkedIn", !!p.linkedin], ["Current role", !!w.current_title],
    ["Years of experience", !!w.years], ["Notice period", !!w.notice_period], ["Expected salary", !!w.desired_salary],
    ["Resume", hasResume], ["Education", !!d.education?.some((e) => e.institution)], ["Experience", !!d.history?.some((h) => h.company)],
    ["Skills", !!d.skills?.trim()], ["Summary", !!d.summary?.trim()],
  ];
  return { pct: Math.round((checks.filter(([, ok]) => ok).length / checks.length) * 100), missing: checks.filter(([, ok]) => !ok).map(([n]) => n) };
}

function ListEditor({ items, onChange, fields, addLabel, empty }: {
  items: Entry[]; onChange: (v: Entry[]) => void; addLabel: string; empty: string;
  fields: { key: string; label: string; placeholder?: string; wide?: boolean; multiline?: boolean }[];
}) {
  return (
    <div className="f-list">
      {items.length === 0 && <p className="f-muted">{empty}</p>}
      {items.map((item, i) => (
        <div className="f-list-item" key={i}>
          <div className="f-grid">
            {fields.map((f) => (
              <Field key={f.key} label={f.label} placeholder={f.placeholder} wide={f.wide} multiline={f.multiline} value={item[f.key] ?? ""}
                onChange={(v) => onChange(items.map((it, j) => (j === i ? { ...it, [f.key]: v } : it)))} />
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
  const download = async () => {
    const res = await fetch("/api/fill/resume", { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    window.open(url, "_blank");
  };
  return (
    <div>
      <div className={`f-drop${drag ? " drag" : ""}`} onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}>
        <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => upload(e.target.files?.[0])} />
        {busy ? "Uploading…" : resume ? <>📄 <b>{resume.name}</b> · {(resume.size / 1024).toFixed(0)} KB · drop a new PDF to replace it</> : <>Drop your resume PDF here, or <u>choose a file</u></>}
      </div>
      {resume && <button type="button" className="f-link" onClick={download}>View current resume</button>}
      {error && <p className="f-error">{error}</p>}
    </div>
  );
}

function ExtensionSetup() {
  const [keys, setKeys] = useState<Key[]>([]);
  const [fresh, setFresh] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => { api<Key[]>("/keys").then(setKeys).catch(() => {}); }, []);
  const create = async () => {
    const r = await api<{ key: string; keys: Key[] }>("/keys", { method: "POST", body: JSON.stringify({ label: navigator.userAgent.includes("Mac") ? "Chrome on Mac" : "Chrome" }) });
    setFresh(r.key);
    setKeys(r.keys);
    setCopied(false);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fresh);
      setCopied(true);
    } catch {
      /* select it manually */
    }
  };
  const revoke = async (id: string) => {
    await api(`/keys/${id}`, { method: "DELETE" });
    setKeys(keys.filter((k) => k.id !== id));
  };
  return (
    <ol className="f-steps">
      <li>
        <b>Download the extension</b>
        <p><a className="f-button" href="/api/fill/extension.zip">Download fill-with-ai.zip</a> and unzip it.</p>
      </li>
      <li>
        <b>Add it to Chrome</b>
        <p>Open <code>chrome://extensions</code>, turn on <b>Developer mode</b> (top right), click <b>Load unpacked</b> and pick the unzipped <code>fill-with-ai</code> folder. Pin it to the toolbar.</p>
      </li>
      <li>
        <b>Connect it to your profile</b>
        <p>Create a key, then right-click the extension icon → <b>Options</b>, paste it and click <b>Save and test</b>.</p>
        {fresh ? (
          <div className="f-key">
            <code>{fresh}</code>
            <button type="button" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
            <small>Shown once. Anyone with this key can fill forms with your details, so keep it to yourself.</small>
          </div>
        ) : <button type="button" className="f-primary" onClick={create}>Create extension key</button>}
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
      </li>
      <li>
        <b>Use it</b>
        <p>Open a job application and click the extension (or press <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>F</kbd>). Green outlines are filled; yellow ones need you. Check the page, click Next or Submit yourself, and click the extension again on the next page.</p>
      </li>
    </ol>
  );
}

function Profile({ user, onOut }: { user: User; onOut: () => void }) {
  const [saved, setSaved] = useState<ProfileResponse | null>(null);
  const [d, setD] = useState<ProfileData>({});
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    api<ProfileResponse>("/profile").then((p) => { setSaved(p); setD(p.data); }).catch((e) => e instanceof ApiError && e.status === 401 && onOut());
  }, [onOut]);

  const dirty = useMemo(() => saved !== null && JSON.stringify(d) !== JSON.stringify(saved.data), [d, saved]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const set = (section: "personal" | "work" | "eligibility", key: string) => (v: string) =>
    setD((x) => ({ ...x, [section]: { ...(x[section] ?? {}), [key]: v } }));
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

  if (!saved) return <div className="f-loading">Loading your profile…</div>;
  const p = d.personal ?? {}, w = d.work ?? {}, el = d.eligibility ?? {};
  const done = completeness(d, !!saved.resume);

  return (
    <div className="f-app">
      <header className="f-top">
        <div className="f-brand"><span className="f-logo sm">✦</span> Fill with AI</div>
        <div className="f-top-right"><span className="f-muted">{user.email}</span><button onClick={logout}>Log out</button></div>
      </header>
      <div className="f-layout">
        <aside className="f-side">
          <div className="f-card">
            <div className="f-meter-label"><b>Profile {done.pct}% complete</b></div>
            <div className="f-meter"><div style={{ width: `${done.pct}%` }} /></div>
            {done.missing.length > 0
              ? <p className="f-muted small">Add: {done.missing.slice(0, 6).join(", ")}{done.missing.length > 6 ? "…" : ""}</p>
              : <p className="f-ok small">Everything most forms ask for is here.</p>}
          </div>
          <nav className="f-nav">
            {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
          </nav>
        </aside>

        <main className="f-main">
          <Section id="about" title="About you">
            <div className="f-grid">
              <Field label="First name" value={p.first_name ?? ""} onChange={set("personal", "first_name")} />
              <Field label="Last name" value={p.last_name ?? ""} onChange={set("personal", "last_name")} />
              <Field label="Email" type="email" value={p.email ?? ""} onChange={set("personal", "email")} hint="The one employers should reply to." />
              <Field label="Phone" type="tel" value={p.phone ?? ""} onChange={set("personal", "phone")} placeholder="+91 98765 43210" />
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
            </div>
          </Section>

          <Section id="work" title="Work">
            <div className="f-grid">
              <Field label="Current job title" value={w.current_title ?? ""} onChange={set("work", "current_title")} placeholder="Backend Engineer" />
              <Field label="Current company" value={w.current_company ?? ""} onChange={set("work", "current_company")} />
              <Field label="Years of experience" type="number" value={w.years ?? ""} onChange={set("work", "years")} />
              <Field label="Notice period" value={w.notice_period ?? ""} onChange={set("work", "notice_period")} placeholder="30 days" />
              <Field label="Earliest start" value={w.availability ?? ""} onChange={set("work", "availability")} placeholder="Within 30 days" />
              <Field label="Current salary (CTC)" value={w.current_salary ?? ""} onChange={set("work", "current_salary")} placeholder="18 LPA" />
              <Field label="Expected salary" value={w.desired_salary ?? ""} onChange={set("work", "desired_salary")} placeholder="24 LPA" />
            </div>
          </Section>

          <Section id="eligibility" title="Eligibility" intro="Used for the yes/no questions most applications ask.">
            <div className="f-grid">
              <Field label="Authorised to work in the job's country?" options={YES_NO} value={el.work_authorization ?? ""} onChange={set("eligibility", "work_authorization")} />
              <Field label="Need visa sponsorship?" options={YES_NO} value={el.require_sponsorship ?? ""} onChange={set("eligibility", "require_sponsorship")} />
              <Field label="Willing to relocate?" options={YES_NO} value={el.can_relocate ?? ""} onChange={set("eligibility", "can_relocate")} />
              <Field label="Willing to travel?" options={["Yes", "No", "Occasionally"]} value={el.willing_to_travel ?? ""} onChange={set("eligibility", "willing_to_travel")} />
              <Field label="Work preference" options={["Remote", "Hybrid", "On-site", "Any"]} value={el.remote_preference ?? ""} onChange={set("eligibility", "remote_preference")} />
            </div>
            <p className="f-muted small">Gender, ethnicity, disability and veteran questions are always answered "prefer not to say".</p>
          </Section>

          <Section id="resume" title="Resume" intro="Attached to every application. PDF, up to 5 MB.">
            <ResumeBox resume={saved.resume} onUploaded={(r) => setSaved({ ...r, data: saved.data })} />
          </Section>

          <Section id="education" title="Education">
            <ListEditor items={d.education ?? []} onChange={(v) => setD({ ...d, education: v })} addLabel="Add education" empty="No education added yet."
              fields={[{ key: "degree", label: "Degree", placeholder: "B.Tech, Computer Science" }, { key: "institution", label: "College / university" },
                { key: "graduation_year", label: "Graduation year", placeholder: "2021" }, { key: "gpa", label: "CGPA / %" }]} />
          </Section>

          <Section id="experience" title="Experience" intro="Most recent first. The AI uses this to answer questions about you.">
            <ListEditor items={d.history ?? []} onChange={(v) => setD({ ...d, history: v })} addLabel="Add a job" empty="No jobs added yet."
              fields={[{ key: "title", label: "Title" }, { key: "company", label: "Company" }, { key: "start_date", label: "From", placeholder: "Jan 2022" },
                { key: "end_date", label: "To", placeholder: "Present" }, { key: "description", label: "What you did", wide: true, multiline: true }]} />
          </Section>

          <Section id="skills" title="Skills and summary">
            <div className="f-grid">
              <Field label="Skills" wide multiline value={d.skills ?? ""} onChange={(v) => setD({ ...d, skills: v })} placeholder="Python, Django, PostgreSQL, AWS, React" hint="Comma-separated." />
              <Field label="Professional summary" wide multiline value={d.summary ?? ""} onChange={(v) => setD({ ...d, summary: v })} placeholder="Two or three lines about you." />
            </div>
          </Section>

          <Section id="answers" title="Your answers" intro="Questions you often see, answered your way. These are used before the AI writes anything (matched on words in the question).">
            <ListEditor items={(d.answers ?? []) as unknown as Entry[]} onChange={(v) => setD({ ...d, answers: v as unknown as ProfileData["answers"] })}
              addLabel="Add an answer" empty={'For example: "Why are you looking for a change?"'}
              fields={[{ key: "question", label: "When a question mentions…", placeholder: "why are you looking", wide: true }, { key: "answer", label: "Answer with", wide: true, multiline: true }]} />
          </Section>

          <Section id="extension" title="Set up the extension">
            <ExtensionSetup />
          </Section>

          <section className="f-card f-danger-zone">
            <h2>Delete account</h2>
            <p className="f-muted">Removes your profile, resume and extension keys for good.</p>
            {confirmDelete
              ? <div className="f-row"><button className="f-danger" onClick={deleteAccount}>Yes, delete everything</button><button onClick={() => setConfirmDelete(false)}>Cancel</button></div>
              : <button onClick={() => setConfirmDelete(true)}>Delete my account</button>}
          </section>
        </main>
      </div>

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
  return user ? <Profile user={user} onOut={out} /> : <Auth onIn={setUser} />;
}
