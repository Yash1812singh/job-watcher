const $ = (id) => document.getElementById(id);
chrome.storage.sync.get({ server: "http://127.0.0.1:8090" }, ({ server }) => ($("server").value = server));
$("save").onclick = async () => {
  const server = $("server").value.trim().replace(/\/+$/, "") || "http://127.0.0.1:8090";
  await chrome.storage.sync.set({ server });
  try {
    const s = await (await fetch(`${server}/api/autofill/status`)).json();
    const p = s.profile;
    $("out").textContent = p.loaded
      ? `Connected. Filling as ${p.name} (${p.email}).\nResume: ${s.resume || "missing"} · AI answers: ${s.ai ? "on" : "off (no GROQ_API_KEY)"}` +
        (p.gaps.length ? `\nMissing from your profile: ${p.gaps.join(", ")}` : "")
      : `Connected, but the profile couldn't be read: ${p.error}`;
  } catch (e) {
    $("out").textContent = `Can't reach ${server}. Start Job Watcher, then try again.`;
  }
};
