"""A copy of the user's resume tailored to one job: only keywords and wording change, never facts.

1. The resume PDF is read once into sections (summary, skills, jobs with their bullet points,
   education, projects). Every line has to come from the resume's own text, so a misread resume
   is rejected rather than invented.
2. For a job, the description's keywords (skills, tools, methods) are split into those the
   user's resume or profile shows, and those it doesn't ("missing": shown to the user, never added).
3. The AI rewords the summary and bullet points to use the job's terms for things the user did,
   and puts the relevant skills first. Each change is checked: a line that gains a number, a name or
   a keyword the user doesn't have goes back to the original. Employers, titles, dates and
   education are copied from the original, never from the AI.
4. The result is written as a clean one-column PDF with the original file name.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re

import httpx

from . import autofill

log = logging.getLogger(__name__)

# the same skill written differently: a keyword counts as shown if any of these is in the resume
SYNONYMS = [
    {"javascript", "js"}, {"typescript", "ts"}, {"postgresql", "postgres"}, {"kubernetes", "k8s"},
    {"machine learning", "ml"}, {"artificial intelligence", "ai"}, {"natural language processing", "nlp"},
    {"google cloud", "gcp", "google cloud platform"}, {"amazon web services", "aws"}, {"microsoft azure", "azure"},
    {"node.js", "nodejs", "node"}, {"react", "react.js", "reactjs"}, {"vue", "vue.js", "vuejs"},
    {"next.js", "nextjs"}, {"ci/cd", "cicd", "continuous integration"}, {"golang", "go"},
    {"c#", "csharp"}, {"c++", "cpp"}, {"rest", "restful", "rest api", "rest apis"}, {"sql", "mysql", "postgresql", "sqlite"},
    {"llm", "llms", "large language models"}, {"deep learning", "dl"}, {"computer vision", "cv"},
    {"data structures", "dsa", "algorithms"}, {"microservices", "micro-services"}, {"elasticsearch", "elastic search"},
]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def _has(text: str, keyword: str) -> bool:
    """Is the keyword (or a synonym of it) in the text, as whole words?"""
    text = _norm(text)
    k = _norm(keyword)
    forms = {k}
    for group in SYNONYMS:
        if k in group:
            forms |= group
    return any(re.search(rf"(?<![\w+#]){re.escape(f)}(?![\w+#])", text) for f in forms if f)


# ── Reading the resume ───────────────────────────────────────────────────────

def extract_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _json_from(reply: str) -> dict:
    found = re.search(r"\{.*\}", reply or "", re.S)
    if not found:
        raise ValueError("the AI didn't return the expected data")
    return json.loads(found.group(0))


def _covered(line: str, source: str) -> float:
    """How much of a line's words are in the source text (1.0 = all)."""
    words = re.findall(r"[a-z0-9+#]+", line.lower())
    if not words:
        return 1.0
    vocab = set(re.findall(r"[a-z0-9+#]+", source.lower()))
    return sum(w in vocab for w in words) / len(words)


def structure(text: str) -> dict:
    """The resume's sections, read by the AI and checked against the resume's own text."""
    if len(text) < 200:
        raise ValueError("couldn't read text from the resume PDF (is it a scanned image?)")
    reply = autofill._ai(
        "Split this resume into JSON, copying the text exactly (don't reword, add or drop anything). Shape:\n"
        '{"name": str, "headline": str, "contact": [str], "summary": str, "skills": [str],\n'
        ' "experience": [{"title": str, "company": str, "location": str, "dates": str, "bullets": [str]}],\n'
        ' "projects": [{"name": str, "bullets": [str]}], "education": [{"degree": str, "school": str, "dates": str, "details": str}],\n'
        ' "certifications": [str], "other": [{"heading": str, "lines": [str]}]}\n'
        "Use empty strings or lists for anything the resume doesn't have. Reply with the JSON only.\n\nRESUME:\n" + text[:12000],
        6000)
    data = _json_from(reply)
    # "Python, Go, SQL" read as one item: one skill each
    data["skills"] = [p.strip() for s in data.get("skills") or [] for p in re.split(r"[,;|•·]", str(s)) if p.strip()]
    lines = [data.get("summary", ""), *data.get("skills", [])]
    for e in data.get("experience", []):
        lines += [e.get("title", ""), e.get("company", ""), *e.get("bullets", [])]
    for p in data.get("projects", []):
        lines += p.get("bullets", [])
    lines = [l for l in lines if l and l.strip()]
    if not lines or sum(_covered(l, text) for l in lines) / len(lines) < 0.9:
        raise ValueError("the resume couldn't be read reliably")
    return data


# ── The job's keywords ───────────────────────────────────────────────────────

def job_description(url: str, page_text: str = "") -> str:
    """The job description, from the hiring tool's own data where it gives it; else the page text."""
    from .scraper import USER_AGENT
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
            m = re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url) or re.search(r"[?&]gh_jid=(\d+)", url)
            if m and m.lastindex == 2:
                d = c.get(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}").json()
                return _plain(d.get("content", "")) or page_text
            m = re.search(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
            if m:
                d = c.get(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}").json()
                lists = " ".join(f"{l.get('text', '')}: {_plain(l.get('content', ''))}" for l in d.get("lists") or [])
                return f"{d.get('descriptionPlain', '')} {lists} {d.get('additionalPlain', '')}".strip() or page_text
            m = re.search(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", url)
            if m:
                board = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{m[1]}").json()
                job = next((j for j in board.get("jobs", []) if j.get("id") == m[2]), None)
                if job:
                    return job.get("descriptionPlain") or _plain(job.get("descriptionHtml", "")) or page_text
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return page_text


def _plain(html: str) -> str:
    from bs4 import BeautifulSoup
    import html as h
    return BeautifulSoup(h.unescape(html or ""), "html.parser").get_text(" ", strip=True)


def keywords(description: str) -> list[str]:
    reply = autofill._ai(
        "List the up to 25 most important hard skills, tools, technologies and methods this job asks for "
        "(e.g. Python, Kubernetes, REST APIs, A/B testing), as short terms. Reply with a JSON object "
        '{"keywords": [..]} only.\n\nJOB DESCRIPTION:\n' + description[:8000], 800)
    try:
        found = _json_from(reply).get("keywords", [])
    except (ValueError, json.JSONDecodeError):
        return []
    return list(dict.fromkeys(k.strip() for k in found if isinstance(k, str) and 1 < len(k.strip()) < 40))[:25]


# ── Which keywords the user has ─────────────────────────────────────────────

def _squash(t: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", (t or "").lower()).strip()


def _base(keyword: str) -> str:
    """"Java 17" -> "Java", "Spring Boot 3.x" -> "Spring Boot": the skill without a version."""
    return re.sub(r"[\s-]*v?\d+(?:\.[\dx]+)*\+?$", "", keyword.strip(), flags=re.I).strip() or keyword


def supported(wanted: list[str], resume_text: str, profile_skills: list[str]) -> dict[str, str]:
    """{keyword: evidence} for the job's keywords the user's resume or profile shows. A keyword is
    shown if it's written there (or a synonym is), or if the AI quotes a line of the resume that
    shows it (a monitoring dashboard shows "Monitoring"); a quote that isn't really in the resume
    doesn't count."""
    shown_in = resume_text + "\n" + ", ".join(profile_skills)
    found = {k: k for k in wanted if _has(shown_in, k)}
    # a version the resume doesn't state still counts as the skill it shows ("Java 17" for "Java");
    # only the plain skill name is ever written into the resume
    found.update({k: _base(k) for k in wanted if k not in found and _base(k) != k and _has(shown_in, _base(k))})
    rest = [k for k in wanted if k not in found]
    if not rest:
        return found
    def ask(batch: list[str]) -> dict:
        reply = autofill._ai(
            "For each keyword, does this resume show the person has that skill or did that kind of work? If yes, copy the "
            "exact words from the resume that show it (a short phrase, character for character); if not, use an empty string. "
            'Reply with JSON only: {"evidence": {"<keyword>": "<exact phrase or empty>"}}\n\n'
            f"KEYWORDS: {json.dumps(batch)}\n\nRESUME:\n{shown_in[:10000]}", 3000)
        try:
            return _json_from(reply).get("evidence") or {}
        except (ValueError, json.JSONDecodeError):
            return {}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:   # a few at a time: long lists get cut short
        evidence = {k: v for part in pool.map(ask, [rest[i:i + 8] for i in range(0, len(rest), 8)]) for k, v in part.items()}
    text = _squash(shown_in)
    for k in rest:
        quote = _squash(str(evidence.get(k) or ""))
        if len(quote) >= 3 and quote in text:
            found[k] = str(evidence[k])
    return found


# ── Tailoring ────────────────────────────────────────────────────────────────

NUMBER = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|x|k|m|\+)?", re.I)
PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z0-9]*(?:[A-Z.+#][\w.+#]*)?")


def _safe_change(old: str, new: str, resume_text: str, allowed: list[str], all_keywords: list[str]) -> bool:
    """Keep a reworded line only if it adds nothing the user doesn't have."""
    if not new or len(new) > max(len(old) * 1.6, len(old) + 80):
        return False
    numbers_old = {n.strip().lower() for n in NUMBER.findall(old)}
    if any(n.strip().lower() not in numbers_old for n in NUMBER.findall(new)):
        return False                                          # a number that wasn't there
    for k in all_keywords:                                    # a job keyword the user doesn't show
        if _has(new, k) and not _has(old, k) and not any(_norm(k) == _norm(a) or _has(a, k) for a in allowed):
            return False
    for word in PROPER.findall(new):                          # a new name, tool or employer
        if word.lower() not in resume_text.lower() and not any(_has(word, a) or _has(a, word) for a in allowed):
            return False
    # any other new word must be the user's own (from anywhere in their resume), a keyword they have,
    # or a joining word: "secure", "large-scale", "expert" can't sneak in
    vocab = set(re.findall(r"[a-z]+", (resume_text + " " + " ".join(allowed)).lower())) | JOINING
    if any(w not in vocab for w in set(re.findall(r"[a-z]+", new.lower())) - set(re.findall(r"[a-z]+", old.lower())) if len(w) > 2):
        return False
    return True


JOINING = set("using use used with and for via the across including through into from while that which this these their "
              "its our team teams such as also both other more".split())


def tailor(resume: dict, resume_text: str, profile_skills: list[str], description: str, role: str, company: str) -> dict:
    """{"resume": tailored sections, "added": keywords now used, "missing": job keywords the user doesn't show}"""
    wanted = keywords(description)
    evidence = supported(wanted, resume_text, profile_skills)
    # the term to use for each: the plain skill for a versioned keyword ("Java", not "Java 17")
    have = list(dict.fromkeys(_base(k) if evidence[k] == _base(k) != k else k for k in wanted if k in evidence))
    missing = [k for k in wanted if k not in evidence]
    out = json.loads(json.dumps(resume))  # a copy; employers, titles, dates, education stay as they are
    if not wanted:
        return {"resume": out, "added": [], "missing": [], "keywords": []}

    bullets = {}
    for i, e in enumerate(resume.get("experience", [])):
        for j, b in enumerate(e.get("bullets", [])):
            bullets[f"e{i}b{j}"] = b
    for i, p in enumerate(resume.get("projects", [])):
        for j, b in enumerate(p.get("bullets", [])):
            bullets[f"p{i}b{j}"] = b
    reply = autofill._ai(
        f"Tailor this resume's wording to a {role or 'job'} at {company or 'the company'}. Rules:\n"
        "- Only reword: use the job's terms (from KEYWORDS) where a bullet already describes that skill or work.\n"
        "- Never add a skill, tool, employer, number, metric or result that isn't already in that bullet.\n"
        "- Keep every bullet's meaning and facts; keep it about the same length. Don't add adjectives or claims "
        "(secure, scalable, large-scale, expert…) that the bullet doesn't already make.\n"
        "- Summary: at most 60 words, only facts from the resume, using the job's terms where true.\n"
        "- Skills: the resume's skills, with the ones the job asks for first; you may add only items from KEYWORDS.\n"
        'Reply with JSON only: {"summary": str, "skills": [str], "bullets": {"<id>": "reworded bullet", ...}} '
        "(include only bullets you changed).\n\n"
        f"KEYWORDS (the applicant has these): {json.dumps(have)}\n"
        f"SUMMARY: {resume.get('summary', '')}\nSKILLS: {json.dumps(resume.get('skills', []))}\n"
        f"BULLETS: {json.dumps(bullets)}\n\nJOB DESCRIPTION (for wording):\n{description[:5000]}", 4000)
    try:
        change = _json_from(reply)
    except (ValueError, json.JSONDecodeError):
        return {"resume": out, "added": [], "missing": missing, "keywords": wanted}

    allowed = have + profile_skills
    for key, new in (change.get("bullets") or {}).items():
        m = re.fullmatch(r"([ep])(\d+)b(\d+)", key)
        if not m or key not in bullets or not isinstance(new, str):
            continue
        section = out["experience" if m[1] == "e" else "projects"][int(m[2])]
        if _safe_change(bullets[key], new.strip(), resume_text, allowed, wanted):
            section["bullets"][int(m[3])] = new.strip()
    summary = (change.get("summary") or "").strip()
    if summary and _safe_change(resume.get("summary", "") or resume_text[:600], summary, resume_text, allowed, wanted):
        out["summary"] = summary
    skills = [s.strip() for s in change.get("skills") or [] if isinstance(s, str) and s.strip()]
    keep = [s for s in skills if any(_norm(s) == _norm(o) for o in resume.get("skills", []))
            or any(_norm(s) == _norm(h) for h in have)]
    if keep:
        out["skills"] = list(dict.fromkeys(keep + [s for s in resume.get("skills", []) if s not in keep]))

    before = json.dumps(resume)
    after = json.dumps(out)
    added = [k for k in have if _has(after, k) and (not _has(before, k) or after.count(k) > before.count(k))]
    return {"resume": out, "added": added, "missing": missing, "keywords": wanted}


# ── Writing the PDF ──────────────────────────────────────────────────────────

_LATIN = str.maketrans({"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "•": "-", "…": "...", " ": " ", "→": "->", "·": "-"})


def _t(s: str) -> str:
    return (s or "").translate(_LATIN).encode("latin-1", "replace").decode("latin-1")


def render_pdf(r: dict) -> bytes:
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_margins(16, 14, 16)
    pdf.set_auto_page_break(True, 14)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def heading(text: str) -> None:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(width, 6, _t(text.upper()), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(150, 150, 150)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(1.5)

    def para(text: str, size: float = 10, style: str = "") -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(width, 4.8, _t(text), new_x="LMARGIN", new_y="NEXT")

    def bullet(text: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(4, 4.8, "-")
        pdf.multi_cell(width - 4, 4.8, _t(text), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(width, 9, _t(r.get("name", "")), new_x="LMARGIN", new_y="NEXT")
    if r.get("headline"):
        para(r["headline"], 11)
    if r.get("contact"):
        para("  |  ".join(r["contact"]), 9)
    if r.get("summary"):
        heading("Summary")
        para(r["summary"])
    if r.get("skills"):
        heading("Skills")
        para(", ".join(r["skills"]))
    if r.get("experience"):
        heading("Experience")
        for e in r["experience"]:
            pdf.set_font("Helvetica", "B", 10.5)
            line = " - ".join(x for x in (e.get("title"), e.get("company")) if x)
            pdf.cell(width * 0.68, 5.5, _t(line))
            pdf.set_font("Helvetica", "", 9.5)
            pdf.cell(width * 0.32, 5.5, _t(" | ".join(x for x in (e.get("dates"), e.get("location")) if x)), align="R",
                     new_x="LMARGIN", new_y="NEXT")
            for b in e.get("bullets", []):
                bullet(b)
            pdf.ln(1)
    if r.get("projects"):
        heading("Projects")
        for p in r["projects"]:
            para(p.get("name", ""), 10.5, "B")
            for b in p.get("bullets", []):
                bullet(b)
            pdf.ln(1)
    if r.get("education"):
        heading("Education")
        for e in r["education"]:
            para(" - ".join(x for x in (e.get("degree"), e.get("school"), e.get("dates")) if x), 10, "B")
            if e.get("details"):
                para(e["details"])
    if r.get("certifications"):
        heading("Certifications")
        for c in r["certifications"]:
            bullet(c)
    for o in r.get("other") or []:
        if o.get("lines"):
            heading(o.get("heading") or "Other")
            for line in o["lines"]:
                bullet(line)
    return bytes(pdf.output())


def fingerprint(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]
