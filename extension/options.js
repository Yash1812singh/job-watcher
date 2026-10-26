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

// Job site passwords (made on sign-up pages; see background.js)
function showPasswords() {
  chrome.storage.local.get({ passwords: {} }, ({ passwords }) => {
    const table = $("pw");
    table.replaceChildren();
    const hosts = Object.keys(passwords).sort();
    $("nopw").hidden = hosts.length > 0;
    for (const host of hosts) {
      const row = table.insertRow();
      row.insertCell().textContent = host;
      const pw = row.insertCell();
      const code = document.createElement("code");
      code.textContent = "••••••••••";
      pw.append(code);
      const actions = row.insertCell();
      actions.style.textAlign = "right";
      const button = (label, onclick) => {
        const b = document.createElement("button");
        b.textContent = label;
        b.onclick = onclick;
        actions.append(b);
      };
      button("Show", () => (code.textContent = code.textContent.startsWith("•") ? passwords[host].password : "••••••••••"));
      button("Copy", () => navigator.clipboard.writeText(passwords[host].password));
      button("Forget", () => {
        if (!confirm(`Forget the password for ${host}? You'd need the site's "forgot password" to sign in again.`)) return;
        delete passwords[host];
        chrome.storage.local.set({ passwords }, showPasswords);
      });
    }
  });
}
showPasswords();
