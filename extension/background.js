// Click the toolbar button (or Alt+Shift+F) on an application page: fill it. Click again on the next page.
const DEFAULT_SERVER = "https://job-watcher-w5jm.onrender.com";

// With an extension key (from the Fill with AI page) it fills with that person's profile and resume.
// Without one it uses the single-user setup (Job Watcher's own profile file and site password).
async function server() {
  const { server, key, password } = await chrome.storage.sync.get({ server: DEFAULT_SERVER, key: "", password: "" });
  const base = server.replace(/\/+$/, "");
  if (key) return { base, headers: { Authorization: `Bearer ${key}` }, decide: "/api/fill/decide", resume: "/api/fill/resume-file" };
  const headers = password ? { Authorization: "Basic " + btoa("me:" + password) } : {};
  return { base, headers, decide: "/api/autofill", resume: "/api/autofill/resume" };
}

chrome.action.onClicked.addListener(async (tab) => {
  try {
    // every frame too: some careers sites embed the Greenhouse/Lever form in an iframe
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["content.js"] });
  } catch (e) {
    console.warn("Fill with AI can't run on this page:", e);
  }
});

// The page asks; the extension (not the page) talks to the server, so no site sees your key.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    const s = await server();
    try {
      if (msg.type === "decide") {
        const r = await fetch(s.base + s.decide, { method: "POST", headers: { ...s.headers, "Content-Type": "application/json" }, body: JSON.stringify(msg.payload) });
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
      }
    } catch (e) {
      reply({ ok: false, error: `Fill with AI (${s.base}): ${e.message}.` });
    }
  })();
  return true; // reply comes later
});
