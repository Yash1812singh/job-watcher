"""/api/fill: accounts, profiles and the browser extension for Fill with AI (many users).

The website (/fill) logs in with a session token; the extension uses an extension key the user
creates on the website. Both go in the Authorization header as "Bearer <token>". This part of the
site has its own accounts, so it isn't behind the Job Watcher site password.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
import re
from collections import defaultdict, deque
from pathlib import Path

from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import parse_qsl, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from . import accounts, autofill, db
from .scraper import detect_ats

router = APIRouter(prefix="/api/fill", tags=["fill with ai"])
EXTENSION_DIR = Path(os.environ.get("JOB_WATCHER_EXTENSION_DIR", Path(__file__).resolve().parents[2] / "extension"))

_attempts: dict[str, deque] = defaultdict(deque)


def _slow_down(request: Request, limit: int = 10, overall: int = 60) -> None:
    """At most `limit` sign-up/login attempts a minute from one address, and `overall` in total
    (the address comes from a proxy header, which a determined client can vary)."""
    who = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
    now = time.monotonic()
    for key, most in ((who, limit), ("*", overall)):
        q = _attempts[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= most:
            raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
    _attempts[who].append(now)
    _attempts["*"].append(now)


def _token(authorization: str | None) -> str:
    return (authorization or "").removeprefix("Bearer ").strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    user = accounts.user_for_token(_token(authorization), ("session",))
    if user is None:
        raise HTTPException(401, "Please log in.")
    return user


def extension_user(authorization: str | None = Header(default=None)) -> dict:
    user = accounts.user_for_token(_token(authorization), ("extension", "session"))
    if user is None:
        raise HTTPException(401, "This extension key isn't valid. Create a new one on the Fill with AI page.")
    return user


# ── Accounts ─────────────────────────────────────────────────────────────────

def _email(v: str) -> str:
    v = v.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v) or len(v) > 200:
        raise ValueError("Enter a valid email address.")
    return v


class SignUp(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str
    password: str = Field(min_length=8, max_length=200)
    _check_email = field_validator("email")(_email)


class LogIn(BaseModel):
    email: str
    password: str = Field(max_length=200)
    _check_email = field_validator("email")(_email)


@router.post("/signup", status_code=201)
def sign_up(body: SignUp, request: Request) -> dict:
    _slow_down(request)
    try:
        user, token = accounts.sign_up(body.name, str(body.email), body.password)
    except accounts.AccountError as e:
        raise HTTPException(409, str(e)) from e
    return {"user": user, "token": token}


@router.post("/login")
def log_in(body: LogIn, request: Request) -> dict:
    _slow_down(request)
    try:
        user, token = accounts.log_in(str(body.email), body.password)
    except accounts.AccountError as e:
        raise HTTPException(401, str(e)) from e
    return {"user": user, "token": token}


@router.post("/logout", status_code=204, response_class=Response)
def log_out(authorization: str | None = Header(default=None)) -> Response:
    accounts.drop_token(_token(authorization))
    return Response(status_code=204)


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@router.delete("/me", status_code=204, response_class=Response)
def delete_me(user: dict = Depends(current_user)) -> Response:
    accounts.delete_user(user["id"])
    return Response(status_code=204)


# ── Profile and resume ───────────────────────────────────────────────────────

def _summary(user_id: int) -> dict:
    stored = accounts.get_profile(user_id)
    p = autofill.profile_from_dict(stored["data"])
    return {**stored, "gaps": autofill.profile_gaps(p, has_resume=bool(stored["resume"]))}


@router.get("/profile")
def get_profile(user: dict = Depends(current_user)) -> dict:
    return _summary(user["id"])


@router.put("/profile")
def put_profile(data: dict, user: dict = Depends(current_user)) -> dict:
    if len(str(data)) > 100_000:
        raise HTTPException(413, "That profile is too long.")
    accounts.save_profile(user["id"], data)
    return _summary(user["id"])


@router.put("/resume")
async def put_resume(request: Request, user: dict = Depends(current_user)) -> dict:
    """Upload the resume as the request body (a PDF); its name in the X-Filename header."""
    content = await request.body()
    try:
        accounts.save_resume(user["id"], request.headers.get("x-filename", "resume.pdf")[:120], content)
    except accounts.AccountError as e:
        raise HTTPException(422, str(e)) from e
    return _summary(user["id"])


def _resume_response(user_id: int) -> Response:
    found = accounts.get_resume(user_id)
    if not found:
        raise HTTPException(404, "No resume uploaded yet.")
    name, data = found
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/resume")
def get_resume(user: dict = Depends(current_user)) -> Response:
    return _resume_response(user["id"])


# ── The extension ────────────────────────────────────────────────────────────

class NewKey(BaseModel):
    label: str = Field(default="Chrome", max_length=60)


@router.get("/keys")
def list_keys(user: dict = Depends(current_user)) -> list[dict]:
    return accounts.extension_keys(user["id"])


@router.post("/keys", status_code=201)
def create_key(body: NewKey, user: dict = Depends(current_user)) -> dict:
    return {"key": accounts.new_token(user["id"], "extension", body.label), "keys": accounts.extension_keys(user["id"])}


@router.delete("/keys/{key_id}", status_code=204, response_class=Response)
def delete_key(key_id: str, user: dict = Depends(current_user)) -> Response:
    if not accounts.drop_extension_key(user["id"], key_id):
        raise HTTPException(404, "Key not found.")
    return Response(status_code=204)


@router.get("/extension.zip")
def extension_zip(request: Request) -> Response:
    """The extension, set up to talk to this site."""
    if not EXTENSION_DIR.is_dir():
        raise HTTPException(404, "The extension isn't available on this server.")
    site = f"{request.headers.get('x-forwarded-proto', request.url.scheme)}://{request.headers.get('host', request.url.netloc)}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(EXTENSION_DIR.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                data = f.read_bytes()
                if f.name in ("background.js", "options.js"):
                    data = data.replace(b"https://job-watcher-w5jm.onrender.com", site.encode())
                z.writestr(f"fill-with-ai/{f.name}", data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="fill-with-ai.zip"'})


class Field_(BaseModel):
    id: str
    kind: str = "text"
    label: str = ""
    name: str = ""
    options: list[str] = []
    required: bool = False
    value: str = ""


class DecideRequest(BaseModel):
    url: str = ""
    job: str = ""
    job_id: int | None = None   # set when the form was opened from the Jobs list
    page_text: str = ""
    fields: list[Field_] = Field(max_length=400)


@router.get("/status")
def status(user: dict = Depends(extension_user)) -> dict:
    s = _summary(user["id"])
    p = s["data"].get("personal") or {}
    return {"user": user, "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or user["name"],
            "email": p.get("email") or user["email"], "resume": (s["resume"] or {}).get("name"),
            "ai": bool(autofill.groq_key()), "gaps": s["gaps"]}


@router.post("/decide")
def decide(body: DecideRequest, user: dict = Depends(extension_user)) -> dict:
    stored = accounts.get_profile(user["id"])
    profile = autofill.profile_from_dict(stored["data"])
    info = accounts.job_info(body.job_id) if body.job_id else None
    if body.job_id:
        accounts.record_application(user["id"], body.job_id, "opened")
    return autofill.decide([f.model_dump() for f in body.fields], body.job, body.page_text, profile,
                           resume_name=(stored["resume"] or {}).get("name") or "",
                           role=(info or {}).get("title", ""), company=(info or {}).get("company", ""))


@router.get("/resume-file")
def resume_for_extension(user: dict = Depends(extension_user)) -> Response:
    return _resume_response(user["id"])


# ── Jobs and applications ────────────────────────────────────────────────────

@router.get("/jobs")
def jobs(q: str = "", india: bool = True, tech: bool = True, within_minutes: int = Query(30, ge=1, le=30 * 24 * 60),
         limit: int = Query(100, ge=1, le=500), user: dict = Depends(current_user)) -> list[dict]:
    """Open jobs posted in the last `within_minutes` (30 by default, like Job Watcher), newest first,
    with this user's application status."""
    where = ["j.is_active = 1", "j.fresh_at >= ?"]
    params: list = [db.to_iso(datetime.now(timezone.utc) - timedelta(minutes=within_minutes))]
    if india:
        where.append("j.is_india = 1")
    if tech:
        where.append("j.is_tech = 1")
    if q.strip():
        where.append("(j.title LIKE ? OR s.name LIKE ? OR j.location LIKE ?)")
        params += [f"%{q.strip()}%"] * 3
    sql = f"""SELECT j.id, j.title, j.url, j.location, j.department, j.fresh_at, j.tech_category, s.name AS company,
                     s.url AS site_url, a.status AS application
              FROM jobs j JOIN sites s ON s.id = j.site_id
              LEFT JOIN fill_applications a ON a.job_id = j.id AND a.user_id = ?
              WHERE {' AND '.join(where)} ORDER BY j.fresh_at DESC, j.id DESC LIMIT ?"""
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, (user["id"], *params, limit))]
    for r in rows:
        r["apply_url"] = application_url(r["url"], r.pop("site_url") or "")
    return rows


@router.get("/applications")
def list_applications(user: dict = Depends(current_user)) -> list[dict]:
    return accounts.applications(user["id"])


class ApplicationReport(BaseModel):
    job_id: int | None = None
    url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=300)
    status: Literal["opened", "submitted"]


@router.post("/applications", status_code=204, response_class=Response)
def report_application(body: ApplicationReport, user: dict = Depends(extension_user)) -> Response:
    """The extension saw the form opened, or the confirmation page after the user submitted."""
    accounts.record_application(user["id"], body.job_id, body.status, body.url, body.title)
    return Response(status_code=204)


def application_url(job_url: str, site_url: str = "") -> str:
    """The page with the application form for a posting (Lever /apply, Ashby /application,
    Greenhouse's hosted form for jobs shown on a company's own site)."""
    u = urlparse(job_url)
    host = u.netloc.lower()
    if "lever.co" in host and not u.path.rstrip("/").endswith("/apply"):
        return job_url.rstrip("/") + "/apply"
    if "ashbyhq.com" in host and not u.path.rstrip("/").endswith("/application"):
        return job_url.rstrip("/") + "/application"
    gh_id = dict(parse_qsl(u.query)).get("gh_jid")
    if gh_id and "greenhouse.io" not in host:
        ats = detect_ats(site_url) or detect_ats(job_url)
        if ats and ats[0] == "greenhouse":
            return f"https://job-boards.greenhouse.io/{ats[1]}/jobs/{gh_id}"
    return job_url
