// Fill with AI, on the application page.
// Shows a panel at the top, fills every field it can from the user's profile, lists what needs the
// user (questions it can't answer, a CAPTCHA, signing in…), and keeps watching: when the user has
// done their part, or the form shows its next step, it fills that too. It never clicks Submit.
(() => {
  if (window.__fwai) {               // already running here: the toolbar button means "fill again"
    window.__fwai.fill(true);
    return;
  }
  const auto = window.__fwaiAuto || null;   // { jobId } when opened from the Fill with AI website
  const isTop = window === window.top;
  const state = { run: 0, busy: false, filled: 0, byAI: 0, stopped: false, submitted: false, opened: false };

  // ── the panel ─────────────────────────────────────────────────────────────

  let panel = null;
  function ui() {
    if (panel) return panel;
    const host = document.createElement("div");
    host.id = "fwai-panel-host";
    host.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;width:min(680px,calc(100vw - 24px))";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .box { font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #f3f4f1; background: #1d211c;
               border-radius: 14px; box-shadow: 0 12px 36px rgba(0,0,0,.32); overflow: hidden; }
        .head { display: flex; align-items: center; gap: 10px; padding: 10px 12px 10px 14px; }
        .logo { width: 24px; height: 24px; border-radius: 7px; background: #2f6b3a; display: grid; place-items: center; font-size: 13px; flex: none; }
        .title { flex: 1; min-width: 0; }
        .title b { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .status { color: #b9c2b4; }
        .status.ok { color: #8fd49a; } .status.warn { color: #f5c46b; } .status.err { color: #ff9b94; }
        button { font: inherit; border: 0; border-radius: 8px; padding: 6px 11px; cursor: pointer; background: #2c322a; color: #f3f4f1; }
        button:hover { background: #394136; }
        button.primary { background: #3f8a4c; font-weight: 600; } button.primary:hover { background: #4a9a58; }
        button.icon { padding: 4px 8px; background: none; color: #9aa294; font-size: 16px; }
        .actions { display: flex; gap: 6px; flex: none; }
        .needs { border-top: 1px solid #2c322a; padding: 8px 14px 12px; display: grid; gap: 6px; }
        .needs .h { color: #f5c46b; font-weight: 600; }
        .item { display: flex; gap: 8px; align-items: flex-start; text-align: left; background: #262b24; width: 100%; }
        .item:hover { background: #303628; }
        .item .n { color: #f5c46b; flex: none; }
        .hint { color: #9aa294; font-size: 12px; }
        .min .needs { display: none; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .spin { display: inline-block; animation: spin 1s linear infinite; }
      </style>
      <div class="box">
        <div class="head">
          <div class="logo">✦</div>
          <div class="title"><b class="t"></b><span class="status"></span></div>
          <div class="actions">
            <button class="continue primary" hidden>Continue ▸</button>
            <button class="again">Fill again</button>
            <button class="icon toggle" title="Show / hide details">▾</button>
            <button class="icon close" title="Stop filling this tab">×</button>
          </div>
        </div>
        <div class="needs" hidden></div>
      </div>`;
    document.documentElement.appendChild(host);
    const $ = (s) => root.querySelector(s);
    $(".t").textContent = "Fill with AI";
    $(".again").onclick = () => fill(true);
    $(".toggle").onclick = () => $(".box").classList.toggle("min");
    $(".close").onclick = () => {
      state.stopped = true;
      observer?.disconnect();
      chrome.runtime.sendMessage({ type: "stop" }, () => void chrome.runtime.lastError);
      host.remove();
    };
    $(".continue").onclick = () => { const b = nextButton(); if (b) b.click(); };
    panel = {
      title(t) { $(".t").textContent = t; },
      status(t, tone = "") { const s = $(".status"); s.textContent = t; s.className = "status " + tone; },
      busy(t) { const s = $(".status"); s.className = "status"; s.innerHTML = '<span class="spin">◌</span> '; s.append(t); },
      needs(items, note) {
        const box = $(".needs");
        box.hidden = !items.length && !note;
        box.innerHTML = "";
        if (items.length) {
          const h = document.createElement("div");
          h.className = "h";
          h.textContent = `Needs you (${items.length}) · click one to go there`;
          box.append(h);
          items.forEach((it, i) => {
            const b = document.createElement("button");
            b.className = "item";
            b.innerHTML = `<span class="n">${i + 1}.</span><span></span>`;
            b.lastChild.textContent = it.text;
            b.onclick = () => it.el && focusField(it.el);
            box.append(b);
          });
        }
        if (note) {
          const n = document.createElement("div");
          n.className = "hint";
          n.textContent = note;
          box.append(n);
        }
      },
      showContinue(on) { $(".continue").hidden = !on; },
    };
    return panel;
  }

  // ── what's on the page ───────────────────────────────────────────────────

  const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length) && getComputedStyle(e).visibility !== "hidden";
  const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/[*✱]/g, "").trim();

  function captchaChallenge() {
    const sel = "iframe[src*='recaptcha/api2/bframe'], iframe[src*='recaptcha/enterprise/bframe'], iframe[src*='hcaptcha.com'][src*='challenge'], iframe[title*='challenge' i]";
    return [...document.querySelectorAll(sel)].some((f) => visible(f) && f.getBoundingClientRect().height > 100);
  }
  // a CAPTCHA checkbox the user must tick (the invisible kind has no visible box)
  const captchaBox = () => [...document.querySelectorAll(".g-recaptcha, .h-captcha, .cf-turnstile, iframe[src*='turnstile']")]
    .find((e) => visible(e) && e.getBoundingClientRect().height > 30);

  function signInWall() {
    const pw = [...document.querySelectorAll("input[type=password]")].filter(visible);
    return pw.length > 0 && !document.querySelector("input[type=file]");
  }

  const SUCCESS = /thank(s| you) for (applying|your (application|interest))|application (has been |was )?(submitted|received|sent)|we('ve| have) received your application|successfully (submitted|applied)|your application is (in|complete)/i;

  function nextButton() {
    const cands = [...document.querySelectorAll("button, a[role=button], input[type=button], input[type=submit], [role=button]")]
      .filter((b) => visible(b) && !b.closest("#fwai-panel-host"));
    return cands.find((b) => {
      const t = clean(b.innerText || b.value || b.getAttribute("aria-label"));
      return /^(next|continue|save (and|&) continue|proceed|next step|go to next)\b/i.test(t) && !/submit|apply|send|finish|complete/i.test(t);
    });
  }

  function applyButton() {
    const cands = [...document.querySelectorAll("a, button")].filter(visible);
    return cands.find((b) => /^(apply( now| for this (job|position|role))?|i'?m interested|start (your )?application)$/i.test(clean(b.innerText)));
  }

  // ── collecting fields ────────────────────────────────────────────────────

  function collect() {
    const out = [];
    const groups = {};
    let n = 0;
    const run = ++state.run;
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
    const isRequired = (e) => e.required || e.getAttribute("aria-required") === "true" || /[*✱]/.test((e.labels && e.labels[0] && e.labels[0].innerText) || "");
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
      const shown = combo ? clean(e.closest('[class*="select__control"], [class*="control"]')?.innerText) : "";
      const chosen = shown && !/^(select|choose|search|type)/i.test(shown) ? shown : "";
      out.push({
        id, kind, label: labelOf(e), name: e.name || e.id || "", required: isRequired(e),
        value: kind === "file" ? (e.files?.length ? "attached" : "") : e.value || chosen || "",
        options: e.tagName === "SELECT" ? [...e.options].map((o) => clean(o.text)) : [], el: e, ids: [e],
      });
    }
    return out.map((f) => (f.kind === "checkbox" && f.ids.length === 1 ? { ...f, options: [] } : f));
  }

  function isDone(f) {
    if (f.kind === "radio" || f.kind === "checkbox") return f.ids.some((e) => e.checked);
    if (f.kind === "file") return !!(f.el.files && f.el.files.length) || !!f.el.dataset.fwaiFile;
    if (f.kind === "select") return !!f.el.value && f.el.selectedIndex > 0;
    return !!(f.value || "").trim();
  }

  // ── filling (works with React/Vue forms, which ignore a plain .value change) ─

  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
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

  function attach(el, name, bytes, type) {
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], name, { type }));
    el.files = dt.files;
    el.dataset.fwaiFile = name;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  const picks = new WeakSet(); // dropdowns where we typed but the user has to choose from the list

  async function apply(f, a) {
    const el = f.el, value = a.value;
    switch (f.kind) {
      case "file": {
        if (a.source === "cover") {
          attach(el, value, new TextEncoder().encode(a.text || ""), "text/plain");
          return true;
        }
        const res = await ask({ type: "resume" });
        if (!res.ok) return false;
        attach(el, res.data.name, Uint8Array.from(atob(res.data.b64), (c) => c.charCodeAt(0)), "application/pdf");
        return true;
      }
      case "select": {
        const opt = [...el.options].find((o) => clean(o.text) === value);
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
        const target = f.ids[f.options.indexOf(value)];
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
          const pick = opts.find((o) => o.innerText.trim().toLowerCase() === want) || opts.find((o) => o.innerText.toLowerCase().includes(want));
          if (pick) {
            pick.click();
            picks.delete(el);
            return true;
          }
        }
        picks.add(el);
        return false;
      }
      default:
        el.focus();
        setValue(el, value);
        el.dispatchEvent(new Event("blur", { bubbles: true }));
        return true;
    }
  }

  function mark(f, color, strong = false) {
    const box = f.ids.length > 1 ? f.el.closest("fieldset, .application-question, [role=radiogroup], [class*=question]") || f.el : f.el;
    box.style.outline = `2px solid ${color}`;
    box.style.outlineOffset = "2px";
    box.style.background = strong ? "rgba(245,165,36,.12)" : "";
    box.dataset.fwaiMark = strong ? "needs" : "done";
  }

  function focusField(el) {
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(() => el.focus({ preventScroll: true }), 300);
  }

  function ask(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => resolve(r || { ok: false, error: chrome.runtime.lastError?.message || "No reply from the extension." }));
      } catch (e) {
        resolve({ ok: false, error: "The extension was updated: refresh this page." });
      }
    });
  }

  // ── one pass ──────────────────────────────────────────────────────────────

  async function fill(manual = false) {
    if (state.busy || state.stopped) return;
    state.busy = true;
    try {
      await pass(manual);
    } finally {
      state.busy = false;
    }
  }

  async function pass(manual) {
    if (SUCCESS.test(document.body?.innerText || "")) {
      if (!state.submitted) {
        state.submitted = true;
        ask({ type: "report", payload: { job_id: auto?.jobId || null, url: location.href, title: document.title, status: "submitted" } });
      }
      if (isTop || panel) {
        ui().status("Application submitted. It's in My applications. 🎉", "ok");
        ui().needs([], "");
        ui().showContinue(false);
      }
      return;
    }

    const fields = collect();
    if (!fields.length) {
      if (!isTop) return; // a frame without a form (ads, chat widgets): stay quiet
      const p = ui();
      const openForm = applyButton();
      if (signInWall()) {
        p.status("This site needs you to sign in or create an account.", "warn");
        p.needs([], "Do that here, then I'll fill the application as soon as it appears.");
      } else {
        p.status(openForm ? `Click “${clean(openForm.innerText)}” to open the application form.` : "Waiting for the application form…", "warn");
        p.needs([], "I'll start filling as soon as the form appears. If it's in a box lower on the page, I'm filling it there.");
      }
      return;
    }

    const p = ui();
    p.title(`Fill with AI · ${document.title.replace(/\s+/g, " ").trim().slice(0, 80) || location.hostname}`);
    if (auto?.jobId && !state.opened) {
      state.opened = true;
      ask({ type: "report", payload: { job_id: auto.jobId, url: location.href, status: "opened" } });
    }

    // only fields not looked at yet (or all of them when the user asks to fill again)
    const wanted = fields.filter((f) => (manual || !f.el.dataset.fwaiTried) && !isDone(f));
    if (wanted.length) {
      p.busy(`Filling ${wanted.length} field${wanted.length === 1 ? "" : "s"}…`);
      const pageText = (document.querySelector("main, [role=main], #content, body")?.innerText || "").replace(/\s+/g, " ").slice(0, 3000);
      const res = await ask({
        type: "decide",
        payload: { url: location.href, job: document.title.trim().slice(0, 200), job_id: auto?.jobId || null, page_text: pageText,
                   fields: wanted.map(({ el, ids, ...f }) => f) },
      });
      for (const f of wanted) for (const e of f.ids) e.dataset.fwaiTried = "1";
      if (!res.ok) {
        p.status(res.error, "err");
        return;
      }
      const byId = Object.fromEntries(wanted.map((f) => [f.id, f]));
      for (const a of res.data.answers) {
        const f = byId[a.id];
        if (!f) continue;
        try {
          if (await apply(f, a)) {
            state.filled++;
            if (a.source === "ai" || a.source === "cover") state.byAI++;
            mark(f, "#2f9e55");
          }
        } catch (e) {
          console.warn("Fill with AI: couldn't fill", f.label, e);
        }
      }
    }
    review();
  }

  // What's still left for the user; re-checked as they work and after every pass.
  function review() {
    if (!panel || state.submitted || state.stopped) return;
    const needs = [];
    for (const f of collect()) {
      const done = isDone(f) && !picks.has(f.el);
      if (f.required && !done) {
        needs.push({ el: f.el, text: picks.has(f.el) ? `Pick “${f.label}” from its list` : f.kind === "file" ? `Attach: ${f.label}` : `Answer: ${f.label}` });
        mark(f, "#f5a524", true);
      } else if (done && f.el.dataset.fwaiMark === "needs") {
        mark(f, "#2f9e55");
      }
    }
    const cap = captchaBox();
    if (captchaChallenge() || cap) needs.unshift({ el: cap || null, text: "Complete the CAPTCHA (I can't do that for you)" });
    const next = nextButton();
    const filled = `${state.filled} filled` + (state.byAI ? `, ${state.byAI} written by AI (please read them)` : "");
    if (needs.length) {
      panel.status(`${filled}. ${needs.length} thing${needs.length === 1 ? "" : "s"} need${needs.length === 1 ? "s" : ""} you.`, "warn");
      panel.needs(needs, "I'll carry on by myself once you've done these.");
      panel.showContinue(false);
    } else {
      panel.status(next ? `This page is done: ${filled}. Check it, then Continue.`
                        : `All done: ${filled}. Check everything, then click Submit yourself.`, "ok");
      panel.needs([], "");
      panel.showContinue(!!next);
    }
  }

  // ── keep watching: the user's own edits, new steps, the confirmation page ──

  let observer = null;
  let timer = null;
  function later(fn, ms) {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  }
  function watch() {
    if (state.stopped) return;
    observer = new MutationObserver((muts) => {
      if (muts.every((m) => m.target.closest?.("#fwai-panel-host") || m.target.id === "fwai-panel-host")) return;
      later(() => {
        const fresh = collect().some((f) => !f.el.dataset.fwaiTried && !isDone(f));
        if (fresh || SUCCESS.test(document.body?.innerText || "")) fill(false);
        else review();
      }, 1200);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("input", () => later(review, 600), true);
    document.addEventListener("change", () => later(review, 600), true);
  }

  window.__fwai = { fill };
  fill(!auto).then(watch);
})();
