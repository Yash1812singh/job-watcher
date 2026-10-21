"""Fill with AI: answers for the fields of a job application form.

The browser extension (extension/) sends the fields on the page the user is looking at, each with
its visible label and options; this decides what goes in each one. Fixed rules answer from the
user's profile (contact details, links, work, eligibility, education, diversity choices, their own
answers…); an AI model (Groq) writes answers to open questions and cover letters from the profile.
Fields it can't answer are left for the user. Nothing is ever submitted from here.

Profiles come from the Fill with AI website (one per user, see accounts.py), or for a single user
from a YAML file:
  JOB_WATCHER_APPLY_PROFILE  profile YAML (default ~/Desktop/Python/autoapply/config/user_profile.yaml)
  JOB_WATCHER_APPLY_RESUME   resume PDF   (default ~/Desktop/Python/autoapply/data/resume.pdf); a path ending
                             in .b64 holds it base64-encoded (hosts whose secret files are text, e.g. Render)
  GROQ_API_KEY               for AI answers (optional); or
  JOB_WATCHER_APPLY_KEYS     a .env-style file holding GROQ_API_KEY= (default ~/Desktop/Python/autoapply/.env)
  JOB_WATCHER_APPLY_AI_MODEL Groq model (default openai/gpt-oss-120b)
"""
from __future__ import annotations

import base64
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
COVER_LETTER_NAME = "cover-letter.txt"


def resume_name_local() -> str:
    """The local resume file's name, or "" if there isn't one."""
    return RESUME_PATH.name.removesuffix(".b64") if RESUME_PATH.is_file() else ""


def resume_bytes() -> bytes | None:
    try:
        data = RESUME_PATH.read_bytes()
    except OSError:
        return None
    return base64.b64decode(data) if RESUME_PATH.suffix == ".b64" else data


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
    # about you
    first_name: str = ""
    last_name: str = ""
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    alt_phone: str = ""
    dob: str = ""
    nationality: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    zip: str = ""
    # links
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    twitter: str = ""
    coding_profile: str = ""  # LeetCode / HackerRank / Codeforces
    # work
    summary: str = ""
    years: str = ""
    relevant_years: str = ""
    employment_status: str = ""
    current_title: str = ""
    current_company: str = ""
    current_salary: str = ""
    current_fixed: str = ""
    current_variable: str = ""
    desired_salary: str = ""
    notice_period: str = ""
    notice_negotiable: str = ""
    last_working_day: str = ""
    availability: str = ""
    reason_for_change: str = ""
    preferred_locations: str = ""
    # eligibility
    work_authorization: str = "Yes"
    require_sponsorship: str = "No"
    visa_status: str = ""
    can_relocate: str = "Yes"
    willing_to_travel: str = ""
    remote_preference: str = ""
    shifts: str = ""
    background_check: str = "Yes"
    # diversity: "" means prefer not to say
    gender: str = ""
    ethnicity: str = ""
    veteran: str = ""
    disability: str = ""
    # how you heard
    how_heard: str = "LinkedIn"
    referral_name: str = ""
    # documents
    cover_letter: str = ""  # template; {company} and {role} are filled in
    # lists
    skills: list[str] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    certifications: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    custom_answers: dict[str, str] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def location(self) -> str:
        return ", ".join(p for p in (self.city, self.state, self.country) if p)

    def edu(self, key: str) -> str:
        return str(self.education[0].get(key, "")) if self.education else ""

    def context(self) -> str:
        jobs = "; ".join(f"{h.get('title', '')} at {h.get('company', '')} ({h.get('start_date', '')}–{h.get('end_date', '')})"
                         + (f": {h.get('description', '')[:300]}" if h.get("description") else "") for h in self.history[:3])
        edu = "; ".join(f"{e.get('degree', '')} {e.get('field', '')} from {e.get('institution', '')} ({e.get('graduation_year', '')})"
                        for e in self.education)
        certs = "; ".join(c.get("name", "") for c in self.certifications if c.get("name"))
        return (f"Name: {self.full_name}\nLocation: {self.location}\nCurrent role: {self.current_title} at {self.current_company}\n"
                f"Experience: {self.years} years\nSummary: {self.summary}\nSkills: {', '.join(self.skills[:30])}\n"
                f"Work history: {jobs}\nEducation: {edu}\nCertifications: {certs}\nNotice period: {self.notice_period}\n"
                f"Reason for change: {self.reason_for_change}\nPreferred locations: {self.preferred_locations}\n"
                f"Work authorization: {self.work_authorization}; needs sponsorship: {self.require_sponsorship}; relocate: {self.can_relocate}")


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):  # e.g. linkedin: {url: ...}
        v = v.get("url") or v.get("value") or ""
    return str(v).strip()


def load_profile(path: Path = PROFILE_PATH) -> Profile:
    """The single-user profile file (AutoApply's YAML format)."""
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


# website sections -> Profile fields: (section, key on the website, Profile attribute)
FIELD_MAP = [
    ("personal", k, k) for k in ("first_name", "last_name", "preferred_name", "email", "phone", "alt_phone", "dob",
                                 "nationality", "address", "city", "state", "country", "zip", "linkedin", "github",
                                 "portfolio", "twitter", "coding_profile")
] + [("work", k, k) for k in ("years", "relevant_years", "employment_status", "current_title", "current_company",
                              "current_salary", "current_fixed", "current_variable", "desired_salary", "notice_period",
                              "notice_negotiable", "last_working_day", "availability", "reason_for_change", "preferred_locations")
] + [("eligibility", k, k) for k in ("work_authorization", "require_sponsorship", "visa_status", "can_relocate",
                                     "willing_to_travel", "remote_preference", "shifts", "background_check")
] + [("eeo", k, k) for k in ("gender", "ethnicity", "veteran", "disability")
] + [("sourcing", k, k) for k in ("how_heard", "referral_name")]


def profile_from_dict(d: dict) -> Profile:
    """A profile entered on the Fill with AI website."""
    p = Profile()
    for section, key, attr in FIELD_MAP:
        value = _text((d.get(section) or {}).get(key))
        if value:
            setattr(p, attr, value)
    p.summary = _text(d.get("summary"))
    p.cover_letter = _text(d.get("cover_letter"))
    p.skills = [s.strip() for s in re.split(r"[,\n]", str(d.get("skills") or "")) if s.strip()]

    def rows(key: str) -> list[dict]:
        return [e for e in d.get(key) or [] if isinstance(e, dict) and any(_text(v) for v in e.values())]

    p.education, p.history = rows("education"), rows("history")
    p.languages, p.certifications = rows("languages"), rows("certifications")
    p.custom_answers = {_text(a.get("question")): _text(a.get("answer")) for a in d.get("answers") or [] if _text(a.get("question"))}
    return p


def profile_gaps(p: Profile, has_resume: bool | None = None) -> list[str]:
    """Details most forms require that the profile doesn't have."""
    wanted = {"first name": p.first_name, "last name": p.last_name, "email": p.email, "phone": p.phone,
              "city": p.city, "LinkedIn URL": p.linkedin}
    gaps = [k for k, v in wanted.items() if not v]
    if not (RESUME_PATH.is_file() if has_resume is None else has_resume):
        gaps.append("resume PDF")
    return gaps


# ── Rules ────────────────────────────────────────────────────────────────────

YES, NO = "Yes", "No"
DECLINE = "__decline__"   # pick the "prefer not to say" option
AGREE = "__agree__"       # tick the consent box / pick "I agree"
OPEN = None               # recognised, but the user left it empty: an open question may still go to the AI


def _or_decline(value: str) -> str:
    return value or DECLINE


def _languages(p: Profile) -> str:
    return ", ".join(f"{x.get('language', '')} ({x.get('proficiency', '')})".replace(" ()", "") for x in p.languages if x.get("language"))


# (pattern on the field's label, answer). The first match wins, so the order matters. An answer of
# "" means "we know this field but the profile has nothing": it's left for the user.
RULES: list[tuple[re.Pattern, object]] = [(re.compile(p, re.I), a) for p, a in [
    # consent first: these often mention "certify" or "accurate"
    (r"privacy|consent|i\s*agree|acknowledge|terms\s*(and|&)|i\s*confirm|i\s*certify|accurate|true\s*and\s*complete|i\s*understand", AGREE),
    (r"\bfirst\s*name|given\s*name", lambda p: p.first_name),
    (r"\b(last\s*name|surname|family\s*name)", lambda p: p.last_name),
    (r"middle\s*name", lambda p: ""),
    (r"preferred\s*(first\s*)?name|nick\s*name|known\s*as", lambda p: p.preferred_name or p.first_name),
    (r"^(?!.*(company|employer|school|universit|college|institut|referr|manager|father|mother|spouse|emergency|recruiter)).*\b(full\s*|legal\s*)?name\b",
     lambda p: p.full_name),
    (r"e-?mail", lambda p: p.email),
    (r"(alternate|alternative|secondary|other)\s*(phone|mobile|number)", lambda p: p.alt_phone),
    (r"phone|mobile|contact\s*number|whatsapp", lambda p: p.phone),
    (r"date\s*of\s*birth|\bdob\b|birth\s*date|birthday", lambda p: p.dob),
    (r"nationality|citizenship", lambda p: p.nationality or p.country),
    (r"linked\s*in", lambda p: p.linkedin),
    (r"github|gitlab", lambda p: p.github),
    (r"leetcode|hackerrank|codeforces|codechef|coding\s*profile", lambda p: p.coding_profile),
    (r"twitter|x\.com", lambda p: p.twitter),
    (r"portfolio|personal\s*(web)?site|website|blog", lambda p: p.portfolio or p.github),
    (r"gender\s*identity|\bgender\b|\bsex\b", lambda p: _or_decline(p.gender)),
    (r"race|ethnic|hispanic|latin", lambda p: _or_decline(p.ethnicity)),
    (r"veteran|military", lambda p: _or_decline(p.veteran)),
    (r"disabilit|handicap", lambda p: _or_decline(p.disability)),
    (r"pronoun|sexual\s*orientation|transgender|religion|caste|marital", DECLINE),
    (r"(require|need).{0,40}(sponsor|visa)|sponsorship", lambda p: p.require_sponsorship),
    (r"visa\s*(status|type)|immigration\s*status|work\s*permit\s*type", lambda p: p.visa_status),
    (r"(authori[sz]ed|eligible|legally|right).{0,40}work|work\s*(authori[sz]ation|permit)", lambda p: p.work_authorization),
    (r"relocat", lambda p: p.can_relocate),
    (r"willing.{0,20}travel|travel.{0,20}(required|willing|%)", lambda p: p.willing_to_travel or OPEN),
    (r"background\s*(check|verification)|bgv", lambda p: p.background_check),
    (r"weekend|night\s*shift|rotational|shifts?\b", lambda p: p.shifts),
    (r"notice.{0,40}(negotiable|buy\s*-?\s*out|reduc|early)", lambda p: p.notice_negotiable or OPEN),
    (r"last\s*working\s*day|\blwd\b", lambda p: p.last_working_day),
    (r"notice\s*period|how\s*soon|when\s*can\s*you\s*(join|start)|(start|joining)\s*date|earliest.{0,20}(start|join)",
     lambda p: p.notice_period or p.availability),
    (r"currently\s*(employed|working)|employment\s*status", lambda p: p.employment_status),
    (r"fixed\s*(ctc|salary|pay|component)", lambda p: p.current_fixed or p.current_salary),
    (r"variable\s*(ctc|pay|component)|bonus", lambda p: p.current_variable),
    (r"current\s*(ctc|salary|compensation)|present\s*(ctc|salary)", lambda p: p.current_salary),
    (r"(expected|desired)\s*(ctc|salary|compensation|pay)|salary\s*expectation", lambda p: p.desired_salary),
    (r"reason\s*for\s*(leaving|change|switch|job\s*change)|why\s*(are\s*you\s*)?(leaving|looking|switch|changing)",
     lambda p: p.reason_for_change or OPEN),
    (r"preferred\s*(work\s*|job\s*)?location|which\s*(location|city|office)|location\s*preference", lambda p: p.preferred_locations),
    (r"(current|present|most\s*recent)\s*(company|employer|organi[sz]ation)|^company(\s*name)?$|^org", lambda p: p.current_company),
    (r"(current|present)\s*(title|role|position|designation)|job\s*title", lambda p: p.current_title),
    (r"relevant\s*(years|experience)|years.{0,30}experience\s*(in|with)\s", lambda p: p.relevant_years or OPEN),
    (r"years?\s*(of)?\s*(professional\s*|total\s*|work\s*|overall\s*)?experience|how\s*many\s*years|total\s*experience", lambda p: p.years),
    (r"address\s*line\s*2|apartment|suite", lambda p: ""),
    (r"street|address(\s*line\s*1)?\b|residential\s*address", lambda p: p.address or p.location),
    (r"\bcountry\b", lambda p: p.country),
    (r"\bstate\b|province", lambda p: p.state),
    (r"zip|postal|pin\s*code", lambda p: p.zip),
    (r"\bcity\b|current\s*location|^location|where\s*are\s*you\s*(based|located)", lambda p: p.city or p.location),
    (r"major|field\s*of\s*study|specializ|discipline|stream", lambda p: p.edu("field")),
    (r"gpa|cgpa|grade|percentage|marks", lambda p: p.edu("gpa")),
    (r"^(?!.*\b(are\s*you|which\s*year|currently)\b).*(university|college|school|institution)", lambda p: p.edu("institution")),
    (r"^(?!.*\?).*(degree|qualification)", lambda p: p.edu("degree")),
    (r"graduation\s*(year|date)|year\s*of\s*(graduation|passing)|passing\s*year", lambda p: p.edu("graduation_year")),
    (r"language", lambda p: _languages(p)),
    (r"certifications?\b|licen[cs]es?", lambda p: ", ".join(c.get("name", "") for c in p.certifications if c.get("name")) or OPEN),
    (r"referr(ed|al)\s*by|who\s*referred|referrer|employee\s*referral|referral\s*name", lambda p: p.referral_name),
    (r"how\s*did\s*you\s*(hear|find|learn|come)|^source$|where\s*did\s*you\s*(hear|find)", lambda p: p.how_heard or "LinkedIn"),
    (r"(previously|ever)\s*(worked|been\s*employed)|former\s*employee", lambda p: NO),
    (r"(18|eighteen)\s*years|over\s*the\s*age|of\s*legal\s*age", lambda p: YES),
    (r"skills|tech(nical)?\s*stack|technologies", lambda p: ", ".join(p.skills[:20]) or OPEN),
]]

# codes the user receives (by email, SMS, invite…) that no profile can know
ONLY_YOU = re.compile(r"\b(code|otp|one[-\s]*time\s*password|verification|captcha|security\s*question|token)\b", re.I)
NOT_A_SECRET = re.compile(r"zip|postal|pin\s*code|post\s*code|area\s*code|country\s*code|dial(ling)?\s*code|isd", re.I)

OPEN_QUESTION = re.compile(r"\?|\bwhy\b|\bwhat\b|\bhow\b|describe|tell us|explain|share|cover\s*letter|anything else|additional|about\s*(you|yourself)", re.I)
COVER = re.compile(r"cover\s*letter|motivation\s*letter", re.I)


def rule_answer(label: str, p: Profile):
    """The profile's answer for a field; "" if a rule knows the field but the profile is empty;
    None if no rule recognises it (or it's an open question the user left empty)."""
    for key, ans in p.custom_answers.items():
        if key and key.lower() in label.lower():
            return ans
    if ONLY_YOU.search(label) and not NOT_A_SECRET.search(label):
        return ""  # a code from an email/SMS, an OTP…: only the user has it (never their email address)
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
        return next((o for o in opts if re.search(r"decline|prefer not|don.?t wish|do not wish|not to (say|disclose|answer)|rather not|not specified", o, re.I)), None)
    if answer == AGREE:
        hit = next((o for o in opts if re.search(r"\b(yes|agree|accept|acknowledge|confirm|i do|i understand)\b", o, re.I)), None)
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

def _ai(prompt: str, max_tokens: int = 900) -> str:
    key = groq_key()
    if not key:
        return ""
    try:
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=45,
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": GROQ_MODEL, "temperature": 0.3, "max_completion_tokens": max_tokens, "reasoning_effort": "low",
                             "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip().strip('"')
    except Exception as exc:
        log.warning("AI answer failed: %s", exc)
        return ""


def ai_answer(profile: Profile, question: str, job: str, page_text: str, options: list[str] | None = None) -> str:
    choose = f"\nReply with exactly one of these options and nothing else: {' | '.join(options)}" if options else ""
    return _ai(f"You are filling in a job application on behalf of the applicant below. The job: {job}\n"
               f"Some text from the application page: {page_text[:1500]}\n\nApplicant:\n{profile.context()}\n\n"
               f"Question on the form: {question}{choose}\n"
               "Answer as the applicant, in first person, truthfully and only from the details above, in at most 80 words of plain text. "
               "Don't invent employers, numbers or links. If the details don't cover it, give a short honest answer.")


def cover_letter(profile: Profile, role: str, company: str, page_text: str = "") -> str:
    """The user's template with {company}/{role} filled in; otherwise one the AI writes; otherwise a plain one."""
    role, company = role or "this role", company or "your company"
    if profile.cover_letter:
        return profile.cover_letter.replace("{company}", company).replace("{role}", role)
    written = _ai(f"Write a short, specific cover letter (150-200 words, plain text, no placeholders, no address block) from the "
                  f"applicant below for the {role} role at {company}. Use only facts from their details; don't invent any.\n"
                  f"Job page text: {page_text[:1500]}\n\nApplicant:\n{profile.context()}\n\nSign it with their name.", 1200)
    if written:
        return written
    lead = profile.summary or (f"I'm a {profile.current_title} at {profile.current_company}." if profile.current_title else "")
    return (f"Dear Hiring Team at {company},\n\nI'm writing to apply for the {role} role. {lead}\n\n"
            f"My skills include {', '.join(profile.skills[:8]) or 'a strong engineering background'}. "
            f"I'd welcome the chance to discuss how I can contribute.\n\nKind regards,\n{profile.full_name}")


# ── Deciding a page ──────────────────────────────────────────────────────────

URLISH = re.compile(r"url|link|website|profile\b|github|linkedin|portfolio", re.I)


def decide(fields: list[dict], job: str = "", page_text: str = "", profile: Profile | None = None,
           resume_name: str | None = None, role: str = "", company: str = "") -> dict:
    """What to put in each field of one page.

    fields: [{id, kind (text|email|tel|number|url|textarea|select|combobox|radio|checkbox|file|date),
              label, options, required, value}]
    returns {"answers": [{id, value, source}], "missing": [label]}; source is "profile", "ai", "resume" or
    "cover" (a cover-letter file answer also carries its "text").
    resume_name: the user's resume file name ("" = none); None = the local resume file, if any.
    """
    profile = profile or load_profile()
    if resume_name is None:
        resume_name = resume_name_local()
    answers: list[dict] = []
    missing: list[str] = []
    ask_ai: list[tuple[dict, list[str] | None]] = []
    letter: list[str] = []  # written once per page, and only if a field wants it

    def the_letter() -> str:
        if not letter:
            letter.append(cover_letter(profile, role, company, page_text))
        return letter[0]

    for f in fields:
        label = (f.get("label") or f.get("name") or "").strip()
        kind = f.get("kind", "text")
        options = f.get("options") or []
        required = bool(f.get("required"))
        words = label + " " + (f.get("name") or "")
        if kind == "file":
            if COVER.search(words):
                answers.append({"id": f["id"], "value": COVER_LETTER_NAME, "source": "cover", "text": the_letter()})
            elif re.search(r"resume|\bcv\b|curriculum|autofill", words, re.I) or (
                    required and not re.search(r"photo|transcript|\bid\b|aadhaar|\bpan\b|passport", words, re.I)):
                if resume_name:
                    answers.append({"id": f["id"], "value": resume_name, "source": "resume"})
                elif required:
                    missing.append(label or "Resume")
            elif required:
                missing.append(label or "File upload")
            continue
        if kind not in ("radio", "checkbox", "select") and (f.get("value") or "").strip():
            continue  # already filled (by the user, or by the site from the resume)
        if kind == "textarea" and COVER.search(words):
            answers.append({"id": f["id"], "value": the_letter(), "source": "cover"})
            continue
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
        job_desc = f"{role} at {company}" if role or company else job
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda fo: ai_answer(profile, fo[0].get("label", ""), job_desc, page_text, fo[1]), ask_ai))
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
    return {"profile": prof, "resume": resume_name_local() or None, "ai": bool(groq_key()), "ai_model": GROQ_MODEL}
