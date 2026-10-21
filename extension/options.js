const $ = (id) => document.getElementById(id);
const DEFAULTS = { server: "https://job-watcher-w5jm.onrender.com", key: "", password: "" };
chrome.storage.sync.get(DEFAULTS, (s) => {
  $("server").value = s.server;
  $("key").value = s.key;
  $("password").value = s.password;
  $("site").href = `${s.server}/fill/`;
});
$("save").onclick = async () => {
  const server = $("server").value.trim().replace(/\/+$/, "") || DEFAULTS.server;
  const key = $("key").value.trim();
  const password = $("password").value;
  await chrome.storage.sync.set({ server, key, password });
  $("site").href = `${server}/fill/`;
  const out = $("out");
  out.hidden = false;
  out.textContent = "Testing…";
  try {
    if (key) {
      const r = await fetch(`${server}/api/fill/status`, { headers: { Authorization: `Bearer ${key}` } });
      if (r.status === 401) return (out.textContent = "That key isn't valid. Create a new one on the Fill with AI page.");
      const s = await r.json();
      out.textContent = `Ready. Filling as ${s.name} (${s.email}).\nResume: ${s.resume || "not uploaded yet"} · AI answers: ${s.ai ? "on" : "off"}` +
        (s.gaps.length ? `\nStill missing from your profile: ${s.gaps.join(", ")}` : "");
    } else {
      const r = await fetch(`${server}/api/autofill/status`, { headers: password ? { Authorization: "Basic " + btoa("me:" + password) } : {} });
      if (r.status === 401) return (out.textContent = "Paste your extension key above (or the site password under Advanced).");
      const s = await r.json();
      out.textContent = s.profile.loaded ? `Ready (single-user). Filling as ${s.profile.name}.` : `Connected, but no profile: ${s.profile.error}`;
    }
  } catch (e) {
    out.textContent = `Can't reach ${server}.`;
  }
};
