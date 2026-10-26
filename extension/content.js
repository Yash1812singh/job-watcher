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
  const state = { run: 0, busy: false, pending: false, filled: 0, byAI: 0, stopped: false, submitted: false, opened: false };
  // what this page's filling did, sent with the application so the user can check it later
  const record = { filled: [], resume: null, account: null };
  // Which question each box was last filled for. Multi-step forms often reuse the same input
  // boxes for the next step's questions, so "this box was handled" alone isn't enough.
  let tried = new WeakMap();
  const sig = (f) => `${location.pathname}${location.hash}|${f.label}|${f.options.join("|")}`;
  // Boxes we filled that came back empty: the page redrew itself after loading (Greenhouse can, late)
  // and wiped them. Fill those again (twice at most, and only soon after: never fight the user).
  const filledAt = new WeakMap();
  const counted = new Set();
  const refills = new WeakMap();
  const wiped = (f) => Date.now() - (filledAt.get(f.el) || 0) < 60000 && (refills.get(f.el) || 0) < 2;
  const isFresh = (f) => !isDone(f) && (tried.get(f.el) !== sig(f) || wiped(f));

  // ── the panel ─────────────────────────────────────────────────────────────

  let panel = null;
  let panelHost = null;
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
            <button class="skip" hidden title="Leave this job and open the next one">Skip ▸</button>
            <button class="again">Fill again</button>
            <button class="icon toggle" title="Show / hide details">▾</button>
            <button class="icon close" title="Stop filling this tab">×</button>
          </div>
        </div>
        <div class="needs" hidden></div>
      </div>`;
    document.documentElement.appendChild(host);
    panelHost = host;
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
    $(".skip").hidden = !auto?.total;
    $(".skip").onclick = () => chrome.runtime.sendMessage({ type: "skip" }, () => void chrome.runtime.lastError);
    panel = {
      title(t) { $(".t").textContent = (auto?.total ? `Job ${auto.position} of ${auto.total} · ` : "") + t; },
      status(t, tone = "") { const s = $(".status"); s.textContent = t; s.className = "status " + tone; },
      busy(t) {
        const s = $(".status");
        const spin = document.createElement("span");
        spin.className = "spin";
        spin.textContent = "◌";
        s.className = "status";
        s.replaceChildren(spin, " " + t);
      },
      needs(items, note) {
        const box = $(".needs");
        box.hidden = !items.length && !note;
        box.replaceChildren();
        if (items.length) {
          const h = document.createElement("div");
          h.className = "h";
          h.textContent = `Needs you (${items.length}) · click one to go there`;
          box.append(h);
          items.forEach((it, i) => {
            const b = document.createElement("button");
            b.className = "item";
            const n = document.createElement("span");
            n.className = "n";
            n.textContent = `${i + 1}.`;
            const t = document.createElement("span");
            t.textContent = it.text;
            b.append(n, t);
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

  // Some forms (SmartRecruiters' Easy Apply, and other web-component forms) keep their boxes inside
  // shadow roots, where document.querySelectorAll can't see them. Look inside every one, closed too.
  const shadowOf = (e) => e.shadowRoot || (e.localName.includes("-") && (globalThis.chrome?.dom?.openOrClosedShadowRoot?.(e) || e.openOrClosedShadowRoot)) || null;
  function roots() {
    const out = [document];
    for (let i = 0; i < out.length; i++) {
      for (const e of out[i].querySelectorAll("*")) {
        if (e.id === "fwai-panel-host") continue;
        const r = shadowOf(e);
        if (r) out.push(r);
      }
    }
    return out;
  }
  const all = (sel) => roots().flatMap((r) => [...r.querySelectorAll(sel)]);
  // closest(), climbing out of shadow roots to the component that holds them
  function closestDeep(e, sel) {
    for (let n = e; n; n = n.getRootNode().host) {
      const m = n.closest(sel);
      if (m) return m;
    }
    return null;
  }
  const byId = (e, id) => e.getRootNode().getElementById?.(id) || document.getElementById(id);
  // the page's text, including what's inside components
  const pageText = () => roots().map((r) => (r === document ? document.body?.innerText || ""
    : [...r.children].filter((c) => c.localName !== "style").map((c) => c.innerText || "").join(" "))).join(" ");

  function captchaChallenge() {
    const sel = "iframe[src*='recaptcha/api2/bframe'], iframe[src*='recaptcha/enterprise/bframe'], iframe[src*='hcaptcha.com'][src*='challenge'], iframe[title*='challenge' i]";
    return all(sel).some((f) => visible(f) && f.getBoundingClientRect().height > 100);
  }
  // a CAPTCHA checkbox the user must tick (the invisible kind has no visible box)
  const captchaBox = () => all(".g-recaptcha, .h-captcha, .cf-turnstile, iframe[src*='turnstile']")
    .find((e) => visible(e) && e.getBoundingClientRect().height > 30);

  function signInWall() {
    const pw = all("input[type=password]").filter(visible);
    return pw.length > 0 && !all("input[type=file]").length;
  }

  const SUCCESS = /thank(s| you) for (applying|your (application|interest))|application (has been |was )?(submitted|received|sent)|we('ve| have) received your application|successfully (submitted|applied)|your application is (in|complete)/i;

  function nextButton() {
    const cands = all("button, a[role=button], input[type=button], input[type=submit], [role=button]")
      .filter((b) => visible(b) && !b.closest("#fwai-panel-host"));
    return cands.find((b) => {
      const t = clean(b.innerText || b.value || b.getAttribute("aria-label") || b.getRootNode().host?.innerText);
      return /^(next|continue|save (and|&) continue|proceed|next step|go to next)\b/i.test(t) && !/submit|apply|send|finish|complete/i.test(t);
    });
  }

  function applyButton() {
    const cands = all("a, button").filter(visible);
    return cands.find((b) => /^(apply( now| for this (job|position|role))?|i'?m interested|start (your )?application)$/i.test(clean(b.innerText || b.getRootNode().host?.innerText)));
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
      if (!t && by) t = by.split(" ").map((id) => byId(e, id)?.innerText || "").join(" ");
      const root = e.getRootNode();
      if (!t && root !== document) {   // a box inside a component: the component's label
        t = root.host.getAttribute("label") || root.host.getAttribute("aria-label") || "";
        if (!t && root.querySelectorAll("input, textarea, select").length === 1) t = root.querySelector("label, legend, [class*='label']")?.innerText || "";
      }
      if (!t) {
        const box = closestDeep(e, 'fieldset, .field, .application-question, [data-automation-id^="formField"], [class*="fieldEntry"], [class*="question"], [class*="form-group"], [class*="FormField"]');
        if (box) t = box.querySelector('legend, label, [class*="label"], [class*="Label"], [class*="title"]')?.innerText || "";
      }
      return clean(t || e.placeholder || e.name || "");
    };
    const groupLabel = (e) => {
      const box = closestDeep(e, 'fieldset, .application-question, [data-automation-id^="formField"], [class*="fieldEntry"], [class*="question"], [role="radiogroup"], [role="group"], [class*="form-group"]');
      if (!box) return "";
      const head = box.querySelector('legend, .application-label, [class*="question-label"], [class*="title"], [class*="Label"]:not(:has(input)), [class*="label"]:not(:has(input))');
      return clean(head?.innerText || box.getAttribute("aria-label") || "");
    };
    const isRequired = (e) => e.required || e.getAttribute("aria-required") === "true" || !!e.getRootNode().host?.hasAttribute("required") || /[*✱]/.test((e.labels && e.labels[0] && e.labels[0].innerText) || "");
    for (const e of all("input, textarea, select")) {
      const type = (e.type || e.tagName).toLowerCase();
      const combo = e.getAttribute("role") === "combobox" || e.getAttribute("aria-autocomplete") === "list" || /^(listbox|true)$/.test(e.getAttribute("aria-haspopup") || "")
        || !!e.closest('[class*="select__"], [class*="Select-"]')
        // a box with a suggestion list next to it (Lever's location): a place must be picked from the list
        || (type === "text" && !!e.parentElement?.querySelector(':scope > [class*="dropdown"], :scope > [class*="autocomplete"], :scope > [class*="suggest"]'));
      if (["hidden", "submit", "button", "search", "image", "reset", "password"].includes(type) && !(type === "search" && combo)) continue;
      if (type !== "file" && (!visible(e) || e.disabled || (e.readOnly && !combo))) continue;
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
      const kind = e.tagName === "SELECT" ? "select" : type === "textarea" ? "textarea" : type === "file" ? "file" : combo ? "combobox" : type;
      const shown = combo ? clean(e.closest('[class*="select__control"], [class*="control"]')?.innerText) : "";
      const chosen = shown && !/^(select|choose|search|type)/i.test(shown) ? shown : "";
      out.push({
        id, kind, label: labelOf(e), name: e.name || e.id || "", required: isRequired(e) || (combo && requiredNear(e)),
        value: kind === "file" ? (e.files?.length ? "attached" : "") : e.value || chosen || "",
        options: e.tagName === "SELECT" ? [...e.options].map((o) => clean(o.text)) : seen.get(e) || [], el: e, ids: [e],
      });
    }
    // dropdowns that aren't <select> boxes: a button or box that opens a list of choices
    for (const e of all(DROPDOWN)) {
      if (!visible(e) || e.closest('[aria-hidden="true"]') || e.getAttribute("aria-disabled") === "true") continue;
      if (e.querySelector('input:not([type=hidden]):not([aria-hidden="true"]), select, textarea')) continue; // its box is collected above
      if (e.parentElement && closestDeep(e.parentElement, DROPDOWN)) continue; // the outer one only
      const id = `fwai-${run}-${n++}`;
      e.dataset.fwai = id;
      // select2 draws a box for a hidden <select>: that <select> has the real label
      const real = e.closest(".select2-container")?.previousElementSibling;
      out.push({
        id, kind: "dropdown", label: labelOf(real?.tagName === "SELECT" ? real : e), name: e.getAttribute("name") || e.id || "",
        required: isRequired(e) || requiredNear(e) || /\brequired\b/i.test(e.getAttribute("aria-label") || "") || !!real?.required,
        value: shownChoice(e), options: seen.get(e) || [], el: e, ids: [e],
      });
    }
    return out.map((f) => (f.kind === "checkbox" && f.ids.length === 1 ? { ...f, options: [] } : f));
  }

  const DROPDOWN = '[role="combobox"]:not(input), button[aria-haspopup="listbox"], [aria-haspopup="listbox"]:not(input), .select2-selection';
  // what a dropdown shows: "" while it still says "Select…"
  function shownChoice(e) {
    const t = clean((e.innerText || e.value || e.getAttribute("aria-valuetext") || "").replace(/\u200b/g, ""));
    return !t || /^(select|choose|please|pick|search|type|none selected|--)/i.test(t) ? "" : t;
  }
  // a "*" next to the question (custom dropdowns can't be marked required the usual way)
  function requiredNear(e) {
    const box = closestDeep(e, 'fieldset, [class*="field"], [class*="Field"], [class*="question"], [class*="form-group"], [data-automation-id^="formField"]');
    return /[*✱]/.test(box?.querySelector('label, legend, [class*="label"], [class*="Label"]')?.innerText || "");
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
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  async function typeKeys(el, text) {
    setValue(el, "");
    let typed = "";
    for (const ch of text) {
      typed += ch;
      el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true, composed: true }));
      setValue(el, typed);
      el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true, composed: true }));
      await sleep(60);
    }
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  const OPTION = '[role="option"], [class*="select__option"], .select2-results__option, [role="listbox"] li, [class*="dropdown-results"] > *, [class*="suggestions"] > li';
  const optText = (o) => clean(o.innerText || o.getAttribute("aria-label") || o.textContent);
  const seen = new WeakMap(); // dropdown → the choices it showed when opened

  function fire(el, types) {
    for (const t of types) {
      const E = t.startsWith("pointer") ? PointerEvent : MouseEvent;
      el.dispatchEvent(new E(t, { bubbles: true, composed: true, cancelable: true, view: window, button: 0 }));
    }
  }
  const key = (el, k, code) => el?.dispatchEvent(new KeyboardEvent("keydown", { key: k, code: k, keyCode: code, bubbles: true, composed: true }));

  // Open a dropdown and wait for its choices: a whole click, the way a mouse does it (react-select only
  // opens on the full press-release-click), else ↓.
  async function openList(el) {
    const shown = async (ms) => {
      for (let t = 0; t < ms / 150; t++) {
        await sleep(150);
        const o = optionsFor(el);
        if (o.length) return o;
      }
      return [];
    };
    el.scrollIntoView({ block: "center" });
    fire(el, ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
    const o = await shown(900);
    if (o.length) return o;
    el.focus();
    key(el, "ArrowDown", 40);
    return shown(900);
  }
  function closeList(el) {
    key(el, "Escape", 27);
    if (document.activeElement !== el) key(document.activeElement, "Escape", 27);
  }
  // Read a dropdown's choices so the right one can be picked (then close it again).
  async function peek(el) {
    const texts = [...new Set((await openList(el)).map(optText).filter((t) => t && !/^(no options|loading|searching)/i.test(t)))].slice(0, 300);
    closeList(el);
    await sleep(200);
    if (texts.length) seen.set(el, texts);
    return texts;
  }

  const PLACES = { bangalore: "bengaluru", bombay: "mumbai", gurgaon: "gurugram", calcutta: "kolkata", madras: "chennai" };
  const norm = (t) => clean(t).toLowerCase().replace(/\b(bangalore|bombay|gurgaon|calcutta|madras)\b/g, (m) => PLACES[m]);
  function bestOption(opts, value) {
    const v = norm(value);
    if (!v) return null;
    const first = v.split(",")[0].trim();
    const t = opts.map((o) => [o, norm(optText(o))]);
    const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const word = (x, w) => new RegExp(`(^|[^\\p{L}\\p{N}])${esc(w)}($|[^\\p{L}\\p{N}])`, "u").test(x);
    // "India" is "India (+91)", not "British Indian Ocean Territory": whole words first
    const hit = t.find(([, x]) => x === v) || t.find(([, x]) => x.startsWith(v) && word(x, v)) || t.find(([, x]) => word(x, v))
      || t.find(([, x]) => x.length > 2 && word(v, x)) || t.find(([, x]) => x.includes(v))
      || (first !== v && (t.find(([, x]) => x === first) || t.find(([, x]) => x.startsWith(first + ","))));
    return hit ? hit[0] : null;
  }

  function optionsFor(el) {
    const ids = [el.getAttribute("aria-controls"), el.getAttribute("aria-owns")].filter(Boolean).join(" ").split(/\s+/).filter(Boolean);
    let lists = ids.map((id) => byId(el, id)).filter(Boolean);
    if (!lists.length) {
      const box = closestDeep(el, '[class*="select__"], [class*="Select"], [class*="combobox"], [class*="autocomplete"], [class*="field"]');
      if (box) lists = [box.parentElement || box];
    }
    const usable = (o) => visible(o) && o.getAttribute("aria-disabled") !== "true" && !o.closest("#fwai-panel-host");
    const opts = lists.flatMap((l) => [...l.querySelectorAll(OPTION)]).filter(usable);
    return opts.length ? opts : all(OPTION).filter(usable); // a list drawn elsewhere on the page (a popup at the end of <body>)
  }

  function attach(el, name, bytes, type) {
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], name, { type }));
    el.files = dt.files;
    el.dataset.fwaiFile = name;
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
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
        // the resume with its keywords matched to this job (never new facts); the plain one otherwise
        let res = await ask({ type: "tailoredResume", payload: { job_id: auto?.jobId || null, url: location.href,
                                                                 page_text: pageText().replace(/\s+/g, " ").slice(0, 15000) } });
        if (res.ok) {
          const { name, tailored, added, missing, note } = res.data;
          record.resume = { name, tailored, added, missing, note: note || null };
        } else {
          res = await ask({ type: "resume" });
          if (!res.ok) return false;
          record.resume = { name: res.data.name, tailored: false, added: [], missing: [], note: null };
        }
        attach(el, res.data.name, Uint8Array.from(atob(res.data.b64), (c) => c.charCodeAt(0)), "application/pdf");
        return true;
      }
      case "select": {
        const opt = [...el.options].find((o) => clean(o.text) === value) || bestOption([...el.options].slice(1), value);
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
      case "dropdown":
      case "combobox": {
        const typeable = f.kind === "combobox" && !el.readOnly;
        let pick = f.options.length || !typeable ? bestOption(await openList(el), value) : null;
        // a searchable box: type, then pick from what it finds (place lookups can take a few seconds)
        const city = value.split(",")[0].trim();
        const renamed = norm(city) !== city.toLowerCase() ? norm(city).replace(/^./, (c) => c.toUpperCase()) : city; // Bangalore → Bengaluru
        const until = Date.now() + 15000; // a box that never offers a match shouldn't hold up the rest
        for (const q of typeable && !pick ? [...new Set([value, city, renamed])] : []) {
          if (Date.now() > until) break;
          // in one go first (some React boxes reset a letter at a time); some lookups only start on keystrokes
          for (const how of ["once", "keys"]) {
            el.focus();
            if (how === "once") setValue(el, q);
            else await typeKeys(el, q);
            for (let t = 0; t < (how === "once" ? 10 : 24) && !pick; t++) {
              await sleep(250);
              pick = bestOption(optionsFor(el), value);
            }
            if (pick) break;
          }
          if (pick) break;
        }
        if (pick) {
          pick.scrollIntoView({ block: "nearest" });
          fire(pick, ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
          await sleep(300);
          picks.delete(el);
          return true;
        }
        if (!typeable) closeList(el);
        picks.add(el);
        return false;
      }
      default: {
        let v = value;
        if (el.type === "number") { // a number box takes digits only: "+91 98765 43210" → 9876543210, "18 LPA" → 18
          const digits = v.replace(/\D/g, "");
          v = /phone|mobile|contact\s*n/i.test(f.label) && digits.length >= 10 ? digits.slice(-10) : (v.match(/\d+(\.\d+)?/) || [""])[0];
          if (!v) return false;
        }
        el.focus();
        setValue(el, v);
        el.dispatchEvent(new Event("blur", { bubbles: true, composed: true }));
        return true;
      }
    }
  }

  function mark(f, color, strong = false) {
    const box = f.ids.length > 1 ? closestDeep(f.el, "fieldset, .application-question, [role=radiogroup], [class*=question]") || f.el : f.el;
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
    if (state.stopped) return;
    if (state.busy) {           // the next step appeared while this one was still filling: do it right after
      state.pending = true;
      return;
    }
    state.busy = true;
    try {
      await pass(manual);
    } catch (e) {
      console.warn("Fill with AI:", e);
      if (panel) panel.status(`Something went wrong on this page (${e.message}). Click “Fill again”.`, "err");
    } finally {
      state.busy = false;
      if (state.pending) {
        state.pending = false;
        setTimeout(() => fill(false), 300);
      }
    }
  }

  async function pass(manual) {
    if (SUCCESS.test(pageText())) {
      if (!state.submitted) {
        state.submitted = true;
        const said = (pageText().match(new RegExp(`[^.!\\n]{0,80}(?:${SUCCESS.source})[^.!\\n]{0,80}`, "i")) || [""])[0].trim();
        if (panel) panel.status("Application submitted. Saving a screenshot of this page as proof…", "ok");
        await sleep(1200); // let the confirmation finish drawing
        const shot = isTop ? await ask({ type: "proof" }) : { ok: false };
        ask({ type: "report", payload: { job_id: auto?.jobId || null, url: location.href, title: document.title, status: "submitted",
                                         proof_b64: shot.ok && shot.data ? shot.data : null,
                                         details: { confirmation: said.slice(0, 300), ...record } } });
      }
      if (isTop || panel) {
        ui().status(auto?.total && auto.position < auto.total ? "Application submitted 🎉 Opening the next job…"
                                                              : "Application submitted. It's in My applications. 🎉", "ok");
        ui().needs([], "");
        ui().showContinue(false);
      }
      return;
    }

    const account = await accountPasswords();
    let fields = collect();
    if (!isForm(fields)) {
      if (!isTop) return; // a frame without a form (ads, chat widgets): stay quiet
      const p = ui();
      const openForm = applyButton();
      p.showContinue(false);
      if (account) {
        p.status(account.created ? "Made a strong password for this site and filled it in. Click the button to create your account."
                                 : "Filled in your saved password for this site. Click the button to sign in.", "warn");
        p.needs([], "Saved in the extension (its Options page lists them). If the site emails you a code, enter it here; I'll carry on after.");
        if (account.created) { record.account = { site: location.hostname, created: true }; report("opened"); }
        keepButtonsVisible(null);
      } else if (formFrame()) {
        p.status("The application form is lower on this page: I'm filling it there.", "ok");
        p.needs([], "Scroll down to it; anything that needs you is listed in its own panel.");
      } else if (signInWall()) {
        p.status("This site needs you to sign in or create an account.", "warn");
        p.needs([], "Do that here, then I'll fill the application as soon as it appears.");
      } else if (openForm && auto?.run && !state.clickedApply) {
        state.clickedApply = true;           // Apply to all: open the form (this never submits anything)
        p.status(`Opening the application form (“${clean(openForm.innerText)}”)…`, "ok");
        await sleep(800);
        openForm.click();
      } else {
        p.status(openForm ? `Click “${clean(openForm.innerText)}” to open the application form.` : "Waiting for the application form…", "warn");
        p.needs([], "I'll start filling as soon as the form appears. If it's in a box lower on the page, I'm filling it there.");
      }
      return;
    }

    const p = ui();
    if (!(await interactive(fields, p))) fields = collect(); // it redrew itself meanwhile: look again
    p.title(`Fill with AI · ${document.title.replace(/\s+/g, " ").trim().slice(0, 80) || location.hostname}`);
    if (auto?.jobId && !state.opened) {
      state.opened = true;
      ask({ type: "report", payload: { job_id: auto.jobId, url: location.href, status: "opened" } });
    }

    // only fields not looked at yet (or all of them when the user asks to fill again)
    const wanted = fields.filter((f) => (manual ? !isDone(f) : isFresh(f)));
    if (wanted.length) {
      p.busy(`Filling ${wanted.length} field${wanted.length === 1 ? "" : "s"}…`);
      for (const f of wanted) {
        if ((f.kind === "dropdown" || f.kind === "combobox") && !f.options.length) f.options = await peek(f.el);
      }
      const text = pageText().replace(/\s+/g, " ").slice(0, 3000);
      const res = await ask({
        type: "decide",
        payload: { url: location.href, job: document.title.trim().slice(0, 200), job_id: auto?.jobId || null, page_text: text,
                   fields: wanted.map(({ el, ids, ...f }) => (f.kind === "dropdown" ? { ...f, kind: f.options.length ? "select" : "text" } : f)) },
      });
      for (const f of wanted) for (const e of f.ids) tried.set(e, sig(f));
      if (!res.ok) {
        p.status(res.error, "err");
        return;
      }
      const byId = Object.fromEntries(wanted.map((f) => [f.id, f]));
      for (const a of res.data.answers) {
        const f = byId[a.id];
        if (!f) continue;
        try {
          if (filledAt.has(f.el)) refills.set(f.el, (refills.get(f.el) || 0) + 1); // wiped by the page, or cleared
          if (await apply(f, a)) {
            filledAt.set(f.el, Date.now());
            // each question counts once, even when the page redraws it and it's filled again
            const q = `${location.pathname}|${f.label}`;
            if (!counted.has(q)) {
              counted.add(q);
              state.filled++;
              if (a.source === "ai" || a.source === "cover") state.byAI++;
              record.filled.push({ label: f.label.slice(0, 160), source: a.source,
                                   value: a.source === "cover" ? "(cover letter)" : String(a.value).slice(0, 300) });
            }
            mark(f, "#2f9e55");
          }
        } catch (e) {
          console.warn("Fill with AI: couldn't fill", f.label, e);
        }
      }
    }
    const left = review(true);
    if (left && account) {   // an account page with the details on it: the user creates the account / signs in
      panel.status(account.created ? "Filled your details and a new strong password (saved in the extension). Click the button to create your account."
                                   : "Filled your details and your saved password. Click the button to sign in.", "warn");
      if (account.created) record.account = { site: location.hostname, created: true };
      report("opened", left.needs.map((n) => n.text));
    } else if (left) {
      report(left.needs.length || left.next ? "opened" : "filled", left.needs.map((n) => n.text));
      // Apply to all: a finished page goes on by itself (Next/Continue only; never Submit)
      if (auto?.run && !left.needs.length && left.next && !state.stopped) {
        const step = sig({ label: left.next.innerText || "", options: [] });
        if (state.lastStep !== step) {
          state.lastStep = step;
          panel?.status(`This page is done: ${state.filled} filled. Going on to the next page…`, "ok");
          setTimeout(() => { if (!state.stopped && nextButton() === left.next) left.next.click(); }, 1500);
        }
      }
    }
  }

  // The panel sits at the top of the page; on a short form the button the user has to click (Submit,
  // Create account…) can be right under it. Then it moves to the bottom of the window.
  function keepButtonsVisible(next) {
    if (!panelHost) return;
    const box = panelHost.getBoundingClientRect();
    const buttons = [next, ...all("button, input[type=submit], [role=button]").filter((b) =>
      visible(b) && !b.closest?.("#fwai-panel-host") && /submit|apply|create|sign|register|continue|next/i.test(clean(b.innerText || b.value || "")))];
    const covered = buttons.some((b) => {
      if (!b) return false;
      const r = b.getBoundingClientRect();
      return r.bottom > box.top && r.top < box.bottom && r.right > box.left && r.left < box.right;
    });
    if (covered) {
      panelHost.style.top = "auto";
      panelHost.style.bottom = "12px";
    }
  }

  function report(status, needs = []) {
    ask({ type: "report", payload: { job_id: auto?.jobId || null, url: location.href, title: document.title, status,
                                     details: { ...record, needs, site: location.hostname } } });
  }

  // Job sites' account pages: a new password is made (and kept in the extension) for a sign-up form,
  // the saved one filled into a sign-in form. Returns what it did, or null when there's no password box.
  async function accountPasswords() {
    const boxes = all("input[type=password]").filter((b) => visible(b) && !b.disabled);
    if (!boxes.length || boxes.every((b) => b.value)) return boxes.length ? state.account || null : null;
    const words = (b) => [b.autocomplete, b.name, b.id, b.placeholder, b.getAttribute("aria-label"), b.labels?.[0]?.innerText].join(" ");
    const signUp = boxes.length >= 2 || boxes.some((b) => /new-password|creat|new|confirm|re-?enter|repeat|verify/i.test(words(b)));
    const res = await ask({ type: "password", create: signUp });
    if (!res.ok || !res.data) return null;
    for (const b of boxes) {
      b.focus();
      setValue(b, res.data.password);
      b.dispatchEvent(new Event("blur", { bubbles: true, composed: true }));
    }
    state.account = { created: !!res.data.created };
    return state.account;
  }

  // A page can look ready before its scripts have taken over the form (Greenhouse's dropdowns don't
  // open for a few seconds). Click one dropdown to check; until it opens, wait instead of filling
  // into a page that ignores it. Returns false if the page redrew itself while waiting.
  async function interactive(fields, p) {
    const probe = fields.find((f) => f.kind === "combobox" && f.el.getAttribute("aria-expanded") === "false");
    if (!probe) return true;
    const el = probe.el;
    for (let i = 0; i < 20 && el.isConnected; i++) {
      fire(el, ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
      for (let t = 0; t < 6; t++) {
        await sleep(150);
        if (el.getAttribute("aria-expanded") === "true") {
          closeList(el);
          el.blur();
          await sleep(150);
          return true;
        }
      }
      if (i === 0) p.busy("Waiting for the page to finish loading…");
      await sleep(1000);
    }
    return el.isConnected;
  }

  // A job description page has stray boxes too (search, cookie settings…): that's not the form yet.
  // an application form shown inside this page (Greenhouse, Lever… on a company's own careers site)
  const formFrame = () => all("iframe").find((f) => visible(f) && f.getBoundingClientRect().height > 200
    && /greenhouse|lever\.co|ashbyhq|smartrecruiters|workable|myworkday|icims|jobvite|recruitee|breezy|bamboohr|teamtailor|personio/i.test(f.src || ""));

  function isForm(fields) {
    if (!fields.length) return false;
    if (!applyButton() && !(isTop && formFrame())) return true;
    // a newsletter box next to the description isn't the application: that has several questions
    return fields.filter((f) => f.required || ["email", "tel", "file", "textarea"].includes(f.kind) || /name|email|phone|resume|\bcv\b/i.test(f.label)).length >= 2;
  }

  // What's still left for the user; re-checked as they work and after every pass.
  function review(final = false) {
    if (!panel || state.submitted || state.stopped) return null;
    if (state.busy && !final) return null; // mid-fill: the panel says "Filling…" until the pass is over
    const fields = collect();
    if (!isForm(fields)) return null; // nothing to fill here: the panel keeps saying what to click
    const needs = [];
    for (const f of fields) {
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
    keepButtonsVisible(next);
    const filled = `${state.filled} filled` + (state.byAI ? `, ${state.byAI} written by AI (please read them)` : "")
      + (record.resume?.tailored ? `, resume matched to this job (${record.resume.added.length} keywords)` : "");
    if (needs.length) {
      panel.status(`${filled}. ${needs.length} thing${needs.length === 1 ? "" : "s"} need${needs.length === 1 ? "s" : ""} you.`, "warn");
      panel.needs(needs, "I'll carry on by myself once you've done these.");
      panel.showContinue(false);
    } else {
      panel.status(next ? (auto?.run ? `This page is done: ${filled}.` : `This page is done: ${filled}. Check it, then Continue.`)
                        : `All done: ${filled}. Check everything, then click Submit yourself.`, "ok");
      panel.needs([], record.resume?.missing?.length && !next
        ? `The job also asks for: ${record.resume.missing.slice(0, 8).join(", ")}. Your resume doesn't show these, so they weren't added; if you have them, add them on the website.`
        : "");
      panel.showContinue(!!next && !auto?.run);
    }
    return { needs, next };
  }

  // ── keep watching: the user's own edits, new steps, the confirmation page ──

  let observer = null;
  let fillTimer = null;
  let reviewTimer = null;
  let lastHref = location.href;
  const watched = new WeakSet();
  function observeAll() {
    if (!observer) return;
    for (const r of roots()) {
      if (watched.has(r)) continue;
      watched.add(r);
      // characterData: React often keeps the same boxes and only changes the question text
      observer.observe(r === document ? document.body : r, { childList: true, subtree: true, characterData: true });
    }
  }
  function checkForNewStep() {
    clearTimeout(fillTimer);
    fillTimer = setTimeout(() => {
      if (location.href !== lastHref) lastHref = location.href; // a step change in a single-page form
      observeAll();
      if (collect().some(isFresh) || SUCCESS.test(pageText())) fill(false);
      else review();
    }, 1000);
  }
  function watch() {
    if (state.stopped) return;
    observer = new MutationObserver((muts) => {
      if (muts.every((m) => (m.target.nodeType === 1 ? m.target : m.target.parentElement)?.closest?.("#fwai-panel-host"))) return;
      checkForNewStep();
    });
    observeAll();
    // some sites (Greenhouse's new boards) redraw the whole page right after it loads: that removes the
    // panel and replaces the <body> being watched. Put both back.
    let body = document.body;
    setInterval(() => {
      if (state.stopped) return;
      if (panelHost && !panelHost.isConnected) document.documentElement.appendChild(panelHost);
      if (document.body && document.body !== body) {
        body = document.body;
        observer.observe(body, { childList: true, subtree: true, characterData: true });
        checkForNewStep();
      }
    }, 1500);
    window.addEventListener("popstate", checkForNewStep);
    window.addEventListener("hashchange", checkForNewStep);
    // the user's own typing (and ours) only re-checks what's left; it never delays spotting a new step
    const recheck = () => { clearTimeout(reviewTimer); reviewTimer = setTimeout(review, 600); };
    document.addEventListener("input", recheck, true);
    document.addEventListener("change", recheck, true);
  }

  window.__fwai = { fill };
  // start once the page has finished loading (and its scripts have taken over the form)
  const ready = () => new Promise((ok) => (document.readyState === "complete" ? ok() : addEventListener("load", ok, { once: true })))
    .then(() => sleep(800));
  ready().then(() => fill(!auto)).then(watch);
})();
