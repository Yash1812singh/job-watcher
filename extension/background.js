// Fill with AI, background.
// - Jobs opened from the Fill with AI website carry "#fill-with-ai=<job id>": that tab is filled
//   automatically, on this page and every later page of the application (multi-page forms).
// - "Apply to all" on the website hands over a list of jobs: they're opened one after another in one
//   tab ("&run=1": the form's Next/Continue is clicked for you), and once you've submitted one, the
//   next opens. It never clicks Submit: that's always you.
// - On any other page, click the toolbar button (or Alt+Shift+F) to fill it.
const DEFAULT_SERVER = "https://job-watcher-w5jm.onrender.com";
const AUTO_HOURS = 3; // a tab stops auto-filling after this long

// With an extension key (from the Fill with AI page) it fills with that person's profile and resume.
// Without one it uses the single-user setup (Job Watcher's own profile file and site password).
async function server() {
  const { server, key, password } = await chrome.storage.sync.get({ server: DEFAULT_SERVER, key: "", password: "" });
  const base = server.replace(/\/+$/, "");
  if (key) return { base, key, headers: { Authorization: `Bearer ${key}` }, decide: "/api/fill/decide", resume: "/api/fill/resume-file" };
  const headers = password ? { Authorization: "Basic " + btoa("me:" + password) } : {};
  return { base, key: "", headers, decide: "/api/autofill", resume: "/api/autofill/resume" };
}

async function start(tabId, auto) {
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: (a) => { window.__fwaiAuto = a; }, args: [auto] });
    // every frame too: some careers sites embed the Greenhouse/Lever form in an iframe
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["content.js"] });
  } catch (e) {
    console.warn("Fill with AI can't run on this page:", e);
  }
}

const autoKey = (tabId) => `auto:${tabId}`;

// The Fill with AI website this extension belongs to: the default one, or the one set in Options.
async function siteOrigins() {
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return new Set([new URL(DEFAULT_SERVER).origin, new URL(server).origin]);
}

// ── Apply to all: a list of jobs, one after another in one tab ──────────────

async function queue() {
  return (await chrome.storage.local.get({ queue: null })).queue;
}

const jobLink = (job) => `${job.url.split("#")[0]}#fill-with-ai=${job.id}&run=1`;

async function startQueue(jobs) {
  const tab = await chrome.tabs.create({ url: jobLink(jobs[0]) });
  await chrome.storage.local.set({ queue: { jobs, index: 0, tabId: tab.id, done: [], skipped: [] } });
}

// the next job in the list (after the current one was submitted or skipped), or the end
async function advance(how) {
  const q = await queue();
  if (!q) return;
  const job = q.jobs[q.index];
  if (job) (how === "submitted" ? q.done : q.skipped).push(job.id);
  q.index += 1;
  await chrome.storage.local.set({ queue: q });
  if (q.index < q.jobs.length) {
    await chrome.tabs.update(q.tabId, { url: jobLink(q.jobs[q.index]) });
  } else {
    await chrome.storage.local.remove("queue");
    const { server: site } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
    await chrome.tabs.update(q.tabId, { url: `${site.replace(/\/+$/, "")}/fill/#applications` });
  }
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  chrome.storage.session.remove(autoKey(tabId));
  const q = await queue();
  if (q && q.tabId === tabId) chrome.storage.local.remove("queue"); // closing the tab stops Apply to all
});

// ── Passwords for job sites' sign-up pages: made here, kept only in this browser ──

function newPassword() {
  const sets = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnopqrstuvwxyz", "23456789", "!@#$%*?-_"];
  const all = sets.join("");
  const pick = (s) => s[crypto.getRandomValues(new Uint32Array(1))[0] % s.length];
  const chars = [...sets.map(pick), ...Array.from({ length: 14 }, () => pick(all))];
  for (let i = chars.length - 1; i > 0; i--) { // shuffle
    const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

// one account per site: a careers site's sign-up and sign-in share it (e.g. acme.wd1.myworkdayjobs.com)
async function sitePassword(host, create) {
  const { passwords } = await chrome.storage.local.get({ passwords: {} });
  if (passwords[host]) return { ...passwords[host], created: false };
  if (!create) return null;
  passwords[host] = { password: newPassword(), createdAt: new Date().toISOString() };
  await chrome.storage.local.set({ passwords });
  return { ...passwords[host], created: true };
}

// ── Starting on the right tabs ───────────────────────────────────────────────

// Installed: open the website, which connects the extension to the user's account by itself.
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  if (reason !== "install") return;
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  chrome.tabs.create({ url: `${server.replace(/\/+$/, "")}/fill/#extension` });
});

// Started by hand: keep filling this tab's later pages too (multi-page applications), until the
// panel is closed or AUTO_HOURS pass.
chrome.action.onClicked.addListener(async (tab) => {
  await chrome.storage.session.set({ [autoKey(tab.id)]: { jobId: null, started: Date.now() } });
  start(tab.id, null);
});

chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {
  if (change.status !== "complete" || !tab.url || !/^https?:/.test(tab.url)) return;
  const url = new URL(tab.url);
  if (url.pathname.startsWith("/fill") && (await siteOrigins()).has(url.origin)) {
    chrome.scripting.executeScript({ target: { tabId }, files: ["connector.js"] }).catch(() => {});
    return;
  }
  const m = tab.url.match(/#fill-with-ai(?:=(\d+))?(&run=1)?/);
  let auto = (await chrome.storage.session.get(autoKey(tabId)))[autoKey(tabId)];
  if (m) {
    auto = { jobId: m[1] ? Number(m[1]) : null, run: !!m[2], started: Date.now() };
    await chrome.storage.session.set({ [autoKey(tabId)]: auto });
  }
  if (!auto || Date.now() - auto.started >= AUTO_HOURS * 3600_000) return;
  const q = await queue();
  if (q && q.tabId === tabId) auto = { ...auto, run: true, position: q.index + 1, total: q.jobs.length };
  start(tabId, auto);
});

// ── Messages from the page (content.js) and the website (connector.js) ──────

// The page asks; the extension (not the page) talks to the server, so no site sees your key.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    const s = await server();
    try {
      if (msg.type === "decide") {
        const ask = () => fetch(s.base + s.decide, { method: "POST", headers: { ...s.headers, "Content-Type": "application/json" }, body: JSON.stringify(msg.payload) });
        let r = await ask();
        if (r.status >= 500) { // the free server can hiccup (or be waking up): once more
          await new Promise((ok) => setTimeout(ok, 3000));
          r = await ask();
        }
        if (r.status === 401) throw new Error("your extension key isn't valid. Open the extension's Options and paste a key from the Fill with AI page");
        if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
        reply({ ok: true, data: await r.json() });
      } else if (msg.type === "resume") {
        const r = await fetch(s.base + s.resume, { headers: s.headers });
        if (!r.ok) throw new Error(r.status === 404 ? "no resume uploaded yet" : `couldn't get your resume (${r.status})`);
        const name = (r.headers.get("content-disposition") || "").match(/filename="?([^";]+)/)?.[1] || "resume.pdf";
        const bytes = new Uint8Array(await r.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        reply({ ok: true, data: { name, b64: btoa(bin) } });
      } else if (msg.type === "tailoredResume") {
        // the resume reworded for this job (keywords only); the plain one when that isn't possible
        if (!s.key) throw new Error("tailored resumes need an extension key");
        const r = await fetch(`${s.base}/api/fill/tailored-resume`, { method: "POST", headers: { ...s.headers, "Content-Type": "application/json" }, body: JSON.stringify(msg.payload) });
        if (!r.ok) throw new Error(r.status === 404 ? "no resume uploaded yet" : `couldn't get your resume (${r.status})`);
        reply({ ok: true, data: await r.json() });
      } else if (msg.type === "report") {
        if (s.key) await fetch(`${s.base}/api/fill/applications`, { method: "POST", headers: { ...s.headers, "Content-Type": "application/json" }, body: JSON.stringify(msg.payload) });
        reply({ ok: true });
        // Apply to all: once this job is submitted, the next one opens
        const q = await queue();
        if (msg.payload.status === "submitted" && q && sender.tab?.id === q.tabId) setTimeout(() => advance("submitted"), 4000);
      } else if (msg.type === "proof") {
        // a screenshot of the confirmation page, kept with the application as proof
        const shot = await chrome.tabs.captureVisibleTab(sender.tab.windowId, { format: "jpeg", quality: 60 }).catch(() => null);
        reply({ ok: true, data: shot });
      } else if (msg.type === "password") {
        const host = new URL(sender.tab.url).hostname;
        reply({ ok: true, data: await sitePassword(host, !!msg.create) });
      } else if (msg.type === "skip") {
        const q = await queue();
        if (q && sender.tab?.id === q.tabId) await advance("skipped");
        reply({ ok: true });
      } else if (msg.type === "stopQueue") {
        await chrome.storage.local.remove("queue");
        reply({ ok: true });
      } else if (msg.type === "connect" || msg.type === "whoami" || msg.type === "queue") {
        // only the Fill with AI website itself may connect the extension or start Apply to all
        const origin = sender.origin || (sender.tab?.url && new URL(sender.tab.url).origin);
        if (!(await siteOrigins()).has(origin)) throw new Error("not the Fill with AI website");
        if (msg.type === "queue") {
          const jobs = (msg.jobs || []).filter((j) => Number.isInteger(j.id) && /^https:\/\/|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(j.url)).slice(0, 200);
          if (!jobs.length) throw new Error("no jobs to apply to");
          await startQueue(jobs);
          return reply({ ok: true, count: jobs.length });
        }
        if (msg.type === "connect") await chrome.storage.sync.set({ server: origin, key: msg.key });
        const { key, server: stored } = await chrome.storage.sync.get({ key: "", server: DEFAULT_SERVER });
        if (!key || new URL(stored).origin !== origin) return reply({ ok: true, email: null });
        const r = await fetch(`${origin}/api/fill/status`, { headers: { Authorization: `Bearer ${key}` } });
        reply({ ok: true, email: r.ok ? (await r.json()).user.email : null });
      } else if (msg.type === "stop") {
        await chrome.storage.session.remove(autoKey(sender.tab.id));
        reply({ ok: true });
      }
    } catch (e) {
      reply({ ok: false, error: `Fill with AI (${s.base}): ${e.message}.` });
    }
  })();
  return true; // reply comes later
});
