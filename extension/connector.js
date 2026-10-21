// Fill with AI, on the Fill with AI website only: lets the page see that the extension is installed
// and hand it this user's key, so there's nothing to copy or paste.
(() => {
  if (window.__fwaiConnector) return;
  window.__fwaiConnector = true;
  const post = (msg) => window.postMessage({ source: "fwai-ext", ...msg }, location.origin);
  const status = () =>
    chrome.runtime.sendMessage({ type: "whoami" }, (r) => {
      void chrome.runtime.lastError;
      post({ type: "status", installed: true, version: chrome.runtime.getManifest().version, email: r?.email || null });
    });
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.data?.source !== "fwai-page") return;
    // from this page itself (Firefox hands content scripts a wrapped window, hence the second check)
    if (e.source !== window && e.source !== window.wrappedJSObject) return;
    if (e.data.type === "hello") status();
    if (e.data.type === "connect" && typeof e.data.key === "string" && /^fk_[\w-]{20,}$/.test(e.data.key)) {
      chrome.runtime.sendMessage({ type: "connect", key: e.data.key }, () => { void chrome.runtime.lastError; status(); });
    }
  });
  status();
})();
