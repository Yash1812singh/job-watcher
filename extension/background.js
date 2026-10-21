// Click the toolbar button (or Alt+Shift+F) on an application page: fill it. Click again on the next page.
const DEFAULT_SERVER = "https://job-watcher-w5jm.onrender.com";

async function server() {
  const { server, password } = await chrome.storage.sync.get({ server: DEFAULT_SERVER, password: "" });
  // the hosted Job Watcher asks for its site password (any username)
  const headers = password ? { Authorization: "Basic " + btoa("me:" + password) } : {};
  return { base: server.replace(/\/+$/, ""), headers };
}

chrome.action.onClicked.addListener(async (tab) => {
  try {
    // every frame too: some careers sites embed the Greenhouse/Lever form in an iframe
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["content.js"] });
  } catch (e) {
    console.warn("Fill with AI can't run on this page:", e);
  }
});

// The page asks; the extension (not the page) talks to your local server, so no site sees it.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    const { base, headers } = await server();
    try {
      if (msg.type === "decide") {
        const r = await fetch(`${base}/api/autofill`, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(msg.payload) });
        if (r.status === 401) throw new Error("wrong password (set it in the extension's Options)");
        if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
        reply({ ok: true, data: await r.json() });
      } else if (msg.type === "resume") {
        const r = await fetch(`${base}/api/autofill/resume`, { headers });
        if (!r.ok) throw new Error(`No resume (${r.status})`);
        const name = (r.headers.get("content-disposition") || "").match(/filename="?([^";]+)/)?.[1] || "resume.pdf";
        const bytes = new Uint8Array(await r.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        reply({ ok: true, data: { name, b64: btoa(bin) } });
      }
    } catch (e) {
      reply({ ok: false, error: `Can't reach Job Watcher at ${base}: ${e.message}. Is it running?` });
    }
  })();
  return true; // reply comes later
});
