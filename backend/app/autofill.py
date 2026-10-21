"""Fill-with-AI (local use only): answers for the fields of a job application form.

The "Fill with AI" browser extension (extension/) sends the fields on the page you're looking at,
each with its visible label and options; this decides what goes in each one. Fixed rules answer
the common questions from your profile (name, contact, links, notice period, sponsorship…) and an
AI model (Groq) writes answers to open questions like "Why do you want to join us?". Fields it
can't answer are left empty for you. It never submits anything: you review the page and click
Next or Submit yourself.

Settings (environment variables):
  JOB_WATCHER_APPLY_PROFILE  profile YAML (default ~/Desktop/Python/autoapply/config/user_profile.yaml)
  JOB_WATCHER_APPLY_RESUME   resume PDF   (default ~/Desktop/Python/autoapply/data/resume.pdf)
  GROQ_API_KEY               for AI answers to open questions (optional); or set
  JOB_WATCHER_APPLY_KEYS     a .env-style file holding GROQ_API_KEY= (default ~/Desktop/Python/autoapply/.env)
  JOB_WATCHER_APPLY_AI_MODEL Groq model for those answers (default openai/gpt-oss-120b)
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("jobwatcher.autofill")

HOME = Path.home()
PROFILE_PATH = Path(os.environ.get("JOB_WATCHER_APPLY_PROFILE", HOME / "Desktop/Python/autoapply/config/user_profile.yaml"))
RESUME_PATH = Path(os.environ.get("JOB_WATCHER_APPLY_RESUME", HOME / "Desktop/Python/autoapply/data/resume.pdf"))
GROQ_MODEL = os.environ.get("JOB_WATCHER_APPLY_AI_MODEL", "openai/gpt-oss-120b")
KEYS_PATH = Path(os.environ.get("JOB_WATCHER_APPLY_KEYS", HOME / "Desktop/Python/autoapply/.env"))


def groq_key() -> str:
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    try:
        for line in KEYS_PATH.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ""


# ── Profile ──────────────────────────────────────────────────────────────────

@dataclass
class Profile:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    zip: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    summary: str = ""
    years: str = ""
    current_title: str = ""
    current_company: str = ""
    current_salary: str = ""
    desired_salary: str = ""
    notice_period: str = ""
    availability: str = ""
    work_authorization: str = "Yes"
    require_sponsorship: str = "No"
    can_relocate: str = "Yes"
    willing_to_travel: str = ""
    remote_preference: str = ""
    skills: list[str] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    custom_answers: dict[str, str] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def location(self) -> str:
        return ", ".join(p for p in (self.city, self.state, self.country) if p)

    def context(self) -> str:
        jobs = "; ".join(f"{h.get('title', '')} at {h.get('company', '')} ({h.get('start_date', '')}–{h.get('end_date', '')})"
                         for h in self.history[:3])
        edu = "; ".join(f"{e.get('degree', '')} from {e.get('institution', '')} ({e.get('graduation_year', '')})" for e in self.education)
        return (f"Name: {self.full_name}\nLocation: {self.location}\nCurrent role: {self.current_title} at {self.current_company}\n"
                f"Experience: {self.years} years\nSummary: {self.summary}\nSkills: {', '.join(self.skills[:25])}\n"
                f"Work history: {jobs}\nEducation: {edu}\nNotice period: {self.notice_period}\n"
                f"Work authorization: {self.work_authorization}; needs sponsorship: {self.require_sponsorship}; relocate: {self.can_relocate}")


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):  # e.g. linkedin: {url: ...}
        v = v.get("url") or v.get("value") or ""
    return str(v).strip()


def load_profile(path: Path = PROFILE_PATH) -> Profile:
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    personal, exp, elig = data.get("personal") or {}, data.get("experience") or {}, data.get("eligibility") or {}
    loc = personal.get("location") or {}
    skills: list[str] = []
    for group in (data.get("skills") or {}).values():
        if isinstance(group, list):
            skills += [str(s) for s in group]
    return Profile(
        first_name=_text(personal.get("first_name")), last_name=_text(personal.get("last_name")),
        email=_text(personal.get("email")), phone=_text(personal.get("phone")),
        city=_text(loc.get("city")), state=_text(loc.get("state")), country=_text(loc.get("country")), zip=_text(loc.get("zip")),
        linkedin=_text(personal.get("linkedin")), github=_text(personal.get("github")), portfolio=_text(personal.get("portfolio")),
        summary=_text(data.get("professional_summary")), years=_text(exp.get("years_total")),
        current_title=_text(exp.get("current_title")), current_company=_text(exp.get("current_company")),
        current_salary=_text(exp.get("current_salary")), desired_salary=_text(exp.get("desired_salary")),
        notice_period=_text(exp.get("notice_period")), availability=_text(exp.get("availability")),
        work_authorization=_text(elig.get("work_authorization")) or "Yes", require_sponsorship=_text(elig.get("require_sponsorship")) or "No",
        can_relocate=_text(elig.get("can_relocate")) or "Yes", willing_to_travel=_text(elig.get("willing_to_travel")),
        remote_preference=_text(elig.get("remote_preference")),
        skills=list(dict.fromkeys(skills)), education=list(data.get("education") or []),
        history=list(exp.get("history") or []),
        custom_answers={str(k): _text(v) for k, v in (data.get("custom_answers") or {}).items()},
    )


def profile_gaps(p: Profile) -> list[str]:
    """Details many forms require that the profile doesn't have."""
    wanted = {"first name": p.first_name, "last name": p.last_name, "email": p.email, "phone": p.phone,
              "city": p.city, "LinkedIn URL": p.linkedin}
    gaps = [k for k, v in wanted.items() if not v]
    if not RESUME_PATH.exists():
        gaps.append("resume PDF")
    return gaps


# ── Rules ────────────────────────────────────────────────────────────────────

YES, NO = "Yes", "No"
DECLINE = "__decline__"   # pick the "prefer not to say" option
AGREE = "__agree__"       # tick the consent box / pick "I agree"

# (pattern on the field's label, answer). The first match wins, so the order matters.
RULES: list[tuple[re.Pattern, object]] = [(re.compile(p, re.I), a) for p, a in [
    (r"\bfirst\s*name|given\s*name", lambda p: p.first_name),
    (r"\b(last\s*name|surname|family\s*name)", lambda p: p.last_name),
    (r"middle\s*name", lambda p: ""),
    (r"preferred\s*(first\s*)?name", lambda p: p.first_name),
    (r"^(?!.*(company|employer|school|universit|college|institut|referr|manager|father|mother|spouse|emergency)).*\b(full\s*)?name\b",
     lambda p: p.full_name),
    (r"e-?mail", lambda p: p.email),
    (r"phone|mobile|contact\s*number", lambda p: p.phone),
    (r"linked\s*in", lambda p: p.linkedin),
    (r"github", lambda p: p.github),
    (r"portfolio|personal\s*(web)?site|website|blog", lambda p: p.portfolio or p.github),
    (r"twitter|x\.com", lambda p: ""),
    (r"(gender|sex\b|pronoun|race|ethnic|hispanic|latin|veteran|disabilit|sexual\s*orientation|transgender|religion|caste)", DECLINE),
    (r"(require|need).{0,40}(sponsor|visa)|sponsorship", lambda p: p.require_sponsorship),
    (r"(authori[sz]ed|eligible|legally|right).{0,40}work|work\s*(authori[sz]ation|permit)", lambda p: p.work_authorization),
    (r"relocat", lambda p: p.can_relocate),
    (r"willing.{0,20}travel|travel.{0,20}(required|willing|%)", lambda p: p.willing_to_travel or YES),
    (r"notice\s*period|how\s*soon|when\s*can\s*you\s*(join|start)|(start|joining)\s*date|earliest.{0,20}(start|join)",
     lambda p: p.notice_period or p.availability),
    (r"current\s*(ctc|salary|compensation)", lambda p: p.current_salary),
    (r"(expected|desired)\s*(ctc|salary|compensation|pay)|salary\s*expectation", lambda p: p.desired_salary),
    (r"(current|present|most\s*recent)\s*(company|employer|organi[sz]ation)|^company$|^org", lambda p: p.current_company),
    (r"(current|present)\s*(title|role|position|designation)|job\s*title", lambda p: p.current_title),
    (r"years?\s*(of)?\s*(relevant\s*|professional\s*|total\s*|work\s*)?experience|how\s*many\s*years", lambda p: p.years),
    (r"\bcountry\b", lambda p: p.country),
    (r"\bstate\b|province", lambda p: p.state),
    (r"zip|postal|pin\s*code", lambda p: p.zip),
    (r"\bcity\b|current\s*location|^location|where\s*are\s*you\s*(based|located)|address", lambda p: p.city or p.location),
    (r"^(?!.*\b(are\s*you|which\s*year|currently)\b).*(university|college|school|institution)", lambda p: (p.education[0].get("institution", "") if p.education else "")),
    (r"^(?!.*\?).*(degree|qualification)", lambda p: (p.education[0].get("degree", "") if p.education else "")),
    (r"graduation\s*(year|date)|year\s*of\s*graduation", lambda p: str(p.education[0].get("graduation_year", "")) if p.education else ""),
    (r"how\s*did\s*you\s*(hear|find|learn)|^source$", "LinkedIn"),
    (r"(previously|ever)\s*(worked|been\s*employed)|former\s*employee", lambda p: NO),
    (r"(18|eighteen)\s*years|over\s*the\s*age", lambda p: YES),
    (r"(privacy|consent|i\s*agree|acknowledge|terms|i\s*confirm|certif|accurate)", AGREE),
]]

OPEN_QUESTION = re.compile(r"\?|\bwhy\b|\bwhat\b|\bhow\b|describe|tell us|explain|share|cover\s*letter|anything else|additional", re.I)


def rule_answer(label: str, p: Profile):
    """The profile's answer for a field, "" if a rule knows the field but the profile is empty,
    or None if no rule recognises it."""
    for key, ans in p.custom_answers.items():
        if key and key.lower() in label.lower():
            return ans
    for pattern, ans in RULES:
        if pattern.search(label):
            return ans(p) if callable(ans) else ans
    return None


def pick_option(answer: str, options: list[str]) -> str | None:
    """The option that best matches an answer (Yes/No, a decline, consent, or free text)."""
    opts = [o for o in options if o.strip() and not re.fullmatch(r"(select|choose|--|please select).*", o.strip(), re.I)]
    if not opts or not answer:
        return None
    if answer == DECLINE:
        return next((o for o in opts if re.search(r"decline|prefer not|don.?t wish|do not wish|not to (say|disclose|answer)|rather not", o, re.I)), None)
    if answer == AGREE:
        hit = next((o for o in opts if re.search(r"\b(yes|agree|accept|acknowledge|confirm|i do)\b", o, re.I)), None)
        return hit or (opts[0] if len(opts) == 1 else None)
    a = answer.strip().lower()
    for o in opts:
        if o.strip().lower() == a:
            return o
    if a.split()[0] in ("yes", "no"):
        w = a.split()[0]
        hit = next((o for o in opts if re.match(rf"\s*{w}\b", o, re.I)), None)
        if hit:
            return hit
    return next((o for o in opts if a in o.lower() or o.lower() in a), None)


# ── AI ───────────────────────────────────────────────────────────────────────

def ai_answer(profile: Profile, question: str, job: str, page_text: str, options: list[str] | None = None) -> str:
    key = groq_key()
    if not key:
        return ""
    choose = f"\nReply with exactly one of these options and nothing else: {' | '.join(options)}" if options else ""
    prompt = (f"You are filling in a job application on behalf of the applicant below. The job: {job}\n"
              f"Some text from the application page: {page_text[:1500]}\n\nApplicant:\n{profile.context()}\n\n"
              f"Question on the form: {question}{choose}\n"
              "Answer as the applicant, in first person, truthfully and only from the details above, in at most 80 words of plain text. "
              "Don't invent employers, numbers or links. If the details don't cover it, give a short honest answer.")
    try:
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=40,
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": GROQ_MODEL, "temperature": 0.3, "max_completion_tokens": 900, "reasoning_effort": "low",
                             "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip().strip('"')
    except Exception as exc:
        log.warning("AI answer failed: %s", exc)
        return ""


# ── Deciding a page ──────────────────────────────────────────────────────────

URLISH = re.compile(r"url|link|website|profile\b|github|linkedin|portfolio", re.I)


def decide(fields: list[dict], job: str = "", page_text: str = "", profile: Profile | None = None) -> dict:
    """What to put in each field of one page.

    fields: [{id, kind (text|email|tel|number|url|textarea|select|combobox|radio|checkbox|file|date),
              label, options, required, value}]
    returns {"answers": [{id, value, source}], "missing": [label]}; source is "profile", "ai" or "resume".
    """
    profile = profile or load_profile()
    answers: list[dict] = []
    missing: list[str] = []
    ask_ai: list[tuple[dict, list[str] | None]] = []

    for f in fields:
        label = (f.get("label") or f.get("name") or "").strip()
        kind = f.get("kind", "text")
        options = f.get("options") or []
        required = bool(f.get("required"))
        if kind == "file":
            words = label + " " + (f.get("name") or "")
            if re.search(r"resume|\bcv\b|curriculum|autofill", words, re.I) or (required and not re.search(r"cover|photo|transcript", words, re.I)):
                if RESUME_PATH.exists():
                    answers.append({"id": f["id"], "value": RESUME_PATH.name, "source": "resume"})
                elif required:
                    missing.append(label or "Resume")
            continue
        if kind not in ("radio", "checkbox", "select") and (f.get("value") or "").strip():
            continue  # already filled (by you, or by the site from the resume)
        answer = rule_answer(label, profile)
        if kind in ("radio", "checkbox", "select", "combobox") and options:
            choice = pick_option(answer, options) if answer else None
            if choice is None and answer is None and (required or kind == "radio"):
                ask_ai.append((f, options))
            elif choice is not None:
                answers.append({"id": f["id"], "value": choice, "source": "profile"})
            elif required:
                missing.append(label)
            continue
        if kind == "checkbox":  # a lone checkbox: consent boxes only
            if answer == AGREE:
                answers.append({"id": f["id"], "value": "checked", "source": "profile"})
            elif required:
                missing.append(label)
            continue
        value = answer if isinstance(answer, str) and not answer.startswith("__") else ""
        if value:
            answers.append({"id": f["id"], "value": value, "source": "profile"})
        elif answer is None and not URLISH.search(label) and (kind == "textarea" or (required and OPEN_QUESTION.search(label))):
            ask_ai.append((f, None))
        elif required:
            missing.append(label)

    if ask_ai:  # open questions on one page go to the AI together
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda fo: ai_answer(profile, fo[0].get("label", ""), job, page_text, fo[1]), ask_ai))
        for (f, options), reply in zip(ask_ai, replies):
            value = pick_option(reply, options) if options else reply
            if value:
                answers.append({"id": f["id"], "value": value, "source": "ai"})
            elif f.get("required"):
                missing.append(f.get("label") or "")
    return {"answers": answers, "missing": [m for m in dict.fromkeys(missing) if m]}


def status() -> dict:
    try:
        p = load_profile()
        prof = {"loaded": True, "name": p.full_name, "email": p.email, "gaps": profile_gaps(p)}
    except Exception as exc:
        prof = {"loaded": False, "error": str(exc)[:200], "gaps": []}
    return {"profile": prof, "resume": RESUME_PATH.name if RESUME_PATH.exists() else None,
            "ai": bool(groq_key()), "ai_model": GROQ_MODEL}
