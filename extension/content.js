// Fill with AI: runs each time you click the extension on an application page.
// Collects the visible form fields, asks your local Job Watcher what goes in each, fills them,
// highlights anything left for you, and never clicks Submit or Next.
(async () => {
  if (window.__fillWithAIBusy) return;
  window.__fillWithAIBusy = true;
  const run = (window.__fillWithAIRun = (window.__fillWithAIRun || 0) + 1);
  try {
    await fillPage(run);
  } finally {
    window.__fillWithAIBusy = false;
  }

  async function fillPage(run) {
    const fields = collect(run);
    if (!fields.length) return; // a frame without a form (ads, chat widgets): stay quiet

    const toast = showToast("Filling this page…");
    const job = document.title.trim().slice(0, 200);
    const pageText = (document.querySelector("main, [role=main], #content, body")?.innerText || "").replace(/\s+/g, " ").slice(0, 3000);
    const res = await ask({ type: "decide", payload: { url: location.href, job, page_text: pageText, fields: fields.map(({ el, ids, ...f }) => f) } });
    if (!res.ok) return toast.update(res.error, true);

    const byId = Object.fromEntries(fields.map((f) => [f.id, f]));
    let filled = 0, byAI = 0;
    const failed = [];
    for (const a of res.data.answers) {
      const f = byId[a.id];
      if (!f) continue;
      try {
        const ok = await apply(f, a.value);
        if (ok) {
          filled++;
          if (a.source === "ai") byAI++;
          mark(f, "#0a9d6e");
        } else failed.push(f.label);
      } catch (e) {
        console.warn("Fill with AI: couldn't fill", f.label, e);
        failed.push(f.label);
      }
    }
    const missing = [...res.data.missing, ...failed];
    for (const f of fields) if (missing.includes(f.label)) mark(f, "#f5a524", true);
    const first = fields.find((f) => missing.includes(f.label));
    if (first) first.el.scrollIntoView({ block: "center", behavior: "smooth" });
    toast.update(
      `Filled ${filled} field${filled === 1 ? "" : "s"}${byAI ? ` (${byAI} written by AI, please read them)` : ""}.` +
        (missing.length ? `\nNeeds you: ${[...new Set(missing)].slice(0, 6).join(", ")}${missing.length > 6 ? "…" : ""}` : "\nNothing left to fill.") +
        "\nCheck the page, then click Next or Submit yourself.",
      false,
      !missing.length
    );
  }

  // ── Fields ────────────────────────────────────────────────────────────────

  function collect(run) {
    const out = [];
    const groups = {};
    let n = 0;
    const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length) && getComputedStyle(e).visibility !== "hidden";
    const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/[*✱]/g, "").trim();
    const labelOf = (e) => {
      let t = (e.labels && e.labels[0] && e.labels[0].innerText) || e.getAttribute("aria-label") || "";
      const by = e.getAttribute("aria-labelledby");
      if (!t && by) t = by.split(" ").map((id) => document.getElementById(id)?.innerText || "").join(" ");
      if (!t) {
        const box = e.closest('fieldset, .field, .application-question, [data-automation-id^="formField"], [class*="fieldEntry"], [class*="question"], [class*="form-group"], [class*="FormField"]');
        if (box) t = box.querySelector('legend, label, [class*="label"], [class*="Label"], [class*="title"]')?.innerText || "";
      }
      return clean(t || e.placeholder || e.name || "");
    };
    const groupLabel = (e) => {
      const box = e.closest('fieldset, .application-question, [data-automation-id^="formField"], [class*="fieldEntry"], [class*="question"], [role="radiogroup"], [role="group"], [class*="form-group"]');
      if (!box) return "";
      const head = box.querySelector('legend, .application-label, [class*="question-label"], [class*="title"], [class*="Label"]:not(:has(input)), [class*="label"]:not(:has(input))');
      return clean(head?.innerText || box.getAttribute("aria-label") || "");
    };
    const isRequired = (e) =>
      e.required || e.getAttribute("aria-required") === "true" || /[*✱]/.test((e.labels && e.labels[0] && e.labels[0].innerText) || "");

    for (const e of document.querySelectorAll("input, textarea, select")) {
      const type = (e.type || e.tagName).toLowerCase();
      if (["hidden", "submit", "button", "search", "image", "reset", "password"].includes(type)) continue;
      if (type !== "file" && (!visible(e) || e.disabled || e.readOnly)) continue;
      if (type !== "file" && e.closest('[aria-hidden="true"]')) continue;
      const id = `fwai-${run}-${n++}`;
      e.dataset.fwai = id;
      if (type === "radio" || type === "checkbox") {
        const key = type + ":" + (e.name || groupLabel(e));
        const optLabel = clean((e.labels && e.labels[0] && e.labels[0].innerText) || e.closest("label")?.innerText || e.value);
        if (groups[key] !== undefined && (type === "radio" || e.name)) {
          const g = out[groups[key]];
          g.options.push(optLabel);
          g.ids.push(e);
          g.required = g.required || isRequired(e);
          continue;
        }
        groups[key] = out.length;
        out.push({ id, kind: type, label: groupLabel(e) || optLabel, name: e.name || "", options: [optLabel], required: isRequired(e), value: "", el: e, ids: [e] });
        continue;
      }
      const combo = e.getAttribute("role") === "combobox" || e.getAttribute("aria-autocomplete") === "list" || !!e.closest('[class*="select__"], [class*="Select-"]');
      const kind = e.tagName === "SELECT" ? "select" : type === "textarea" ? "textarea" : type === "file" ? "file" : combo ? "combobox" : type;
      // a dropdown showing a choice already (e.g. the phone country code) counts as filled
      const shown = combo ? clean(e.closest('[class*="select__control"], [class*="control"]')?.innerText) : "";
      const chosen = shown && !/^(select|choose|search|type)/i.test(shown) ? shown : "";
      out.push({
        id, kind, label: labelOf(e), name: e.name || e.id || "", required: isRequired(e), value: kind === "file" ? "" : e.value || chosen || "",
        options: e.tagName === "SELECT" ? [...e.options].map((o) => clean(o.text)) : [], el: e, ids: [e],
      });
    }
    // lone checkboxes stay "checkbox" with one option; a group of them with a shared name keeps its options
    return out.map((f) => (f.kind === "checkbox" && f.ids.length === 1 ? { ...f, options: [] } : f));
  }

  // ── Filling (works with React/Vue forms, which ignore a plain .value change) ──

  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function apply(f, value) {
    const el = f.el;
    switch (f.kind) {
      case "file": {
        const res = await ask({ type: "resume" });
        if (!res.ok) return false;
        const bytes = Uint8Array.from(atob(res.data.b64), (c) => c.charCodeAt(0));
        const dt = new DataTransfer();
        dt.items.add(new File([bytes], res.data.name, { type: "application/pdf" }));
        el.files = dt.files;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
      case "select": {
        const opt = [...el.options].find((o) => o.text.replace(/\s+/g, " ").replace(/[*✱]/g, "").trim() === value);
        if (!opt) return false;
        setValue(el, opt.value);
        return true;
      }
      case "radio":
      case "checkbox": {
        if (f.ids.length === 1 && f.options.length === 0) { // a lone consent box
          if (!el.checked) el.click();
          return el.checked;
        }
        const i = f.options.indexOf(value);
        const target = f.ids[i];
        if (!target) return false;
        if (!target.checked) (target.closest("label") || target).click();
        if (!target.checked) target.click();
        return target.checked;
      }
      case "combobox": {
        el.focus();
        el.click();
        setValue(el, value); // one go: React keeps it (a letter at a time gets reset)
        const want = value.toLowerCase();
        for (let t = 0; t < 24; t++) { // place lookups can take a few seconds
          await sleep(250);
          const opts = optionsFor(el);
          if (opts.length) {
            const pick = opts.find((o) => o.innerText.trim().toLowerCase() === want) || opts.find((o) => o.innerText.toLowerCase().includes(want));
            if (!pick) continue; // the list is still showing what was there before typing
            pick.click();
            return true;
          }
        }
        f.label = f.label + " (pick a suggestion)";
        return false; // typed, but you need to pick from its list
      }
      default:
        el.focus();
        setValue(el, value);
        el.dispatchEvent(new Event("blur", { bubbles: true }));
        return true;
    }
  }

  function optionsFor(el) {
    const ids = [el.getAttribute("aria-controls"), el.getAttribute("aria-owns")].filter(Boolean).join(" ").split(/\s+/).filter(Boolean);
    let lists = ids.map((id) => document.getElementById(id)).filter(Boolean);
    if (!lists.length) {
      const box = el.closest('[class*="select__"], [class*="Select"], [class*="combobox"], [class*="autocomplete"], [class*="field"]');
      if (box) lists = [box.parentElement || box];
    }
    return lists.flatMap((l) => [...l.querySelectorAll('[role="option"]')]).filter((o) => o.offsetParent !== null);
  }

  function mark(f, color, strong = false) {
    const box = f.ids.length > 1 ? f.el.closest("fieldset, .application-question, [role=radiogroup], [class*=question]") || f.el : f.el;
    box.style.outline = `2px solid ${color}`;
    box.style.outlineOffset = "2px";
    if (strong) box.style.background = "rgba(245,165,36,.12)";
  }

  // ── Talking to the extension, and the little status card ─────────────────

  function ask(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => resolve(r || { ok: false, error: chrome.runtime.lastError?.message || "No reply from the extension." }));
      } catch (e) {
        resolve({ ok: false, error: "The extension was reloaded: refresh this page and try again." });
      }
    });
  }

  function showToast(text) {
    document.getElementById("fwai-toast")?.remove();
    const box = document.createElement("div");
    box.id = "fwai-toast";
    box.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:360px;padding:14px 38px 14px 16px;" +
      "border-radius:12px;background:#16161d;color:#fff;font:13px/1.5 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.3);white-space:pre-line";
    const close = document.createElement("button");
    close.textContent = "×";
    close.style.cssText = "position:absolute;top:6px;right:10px;background:none;border:0;color:#aaa;font-size:18px;cursor:pointer";
    close.onclick = () => box.remove();
    const msg = document.createElement("div");
    msg.textContent = "✨ " + text;
    box.append(msg, close);
    document.body.appendChild(box);
    return {
      update(t, error = false, done = false) {
        msg.textContent = (error ? "⚠️ " : done ? "✅ " : "✨ ") + t;
        box.style.background = error ? "#7f1d1d" : "#16161d";
        if (!error) setTimeout(() => box.remove(), 20000);
      },
    };
  }
})();
