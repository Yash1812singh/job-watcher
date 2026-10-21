const $ = (id) => document.getElementById(id);
const DEFAULTS = { server: "https://job-watcher-w5jm.onrender.com", password: "" };
chrome.storage.sync.get(DEFAULTS, ({ server, password }) => {
  $("server").value = server;
  $("password").value = password;
});
$("save").onclick = async () => {
  const server = $("server").value.trim().replace(/\/+$/, "") || DEFAULTS.server;
  const password = $("password").value;
  await chrome.storage.sync.set({ server, password });
  $("out").textContent = "Testing…";
  try {
    const r = await fetch(`${server}/api/autofill/status`, { headers: password ? { Authorization: "Basic " + btoa("me:" + password) } : {} });
    if (r.status === 401) return ($("out").textContent = "Connected, but the password is wrong.");
    const s = await r.json();
    const p = s.profile;
    $("out").textContent = p.loaded
      ? `Connected. Filling as ${p.name} (${p.email}).\nResume: ${s.resume || "missing"} · AI answers: ${s.ai ? "on" : "off (no GROQ_API_KEY)"}` +
        (p.gaps.length ? `\nMissing from your profile: ${p.gaps.join(", ")}` : "")
      : `Connected, but the profile couldn't be read: ${p.error}`;
  } catch (e) {
    $("out").textContent = `Can't reach ${server}. Check the address (and that Job Watcher is running).`;
  }
};
