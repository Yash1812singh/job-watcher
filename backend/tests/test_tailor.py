import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import accounts, autofill, db, fill_api, main, tailor

RESUME_TEXT = """Riya Testwala
Software Engineer
SKILLS Python, Go, PostgreSQL, Redis, Docker
EXPERIENCE
Software Engineer II - Globex Payments  Jan 2023 - Present
Built a payment reconciliation service in Go handling 2 million transactions a day.
Set up Prometheus and Grafana dashboards and on-call alerts for 14 services.
"""
SECTIONS = {"name": "Riya Testwala", "headline": "Software Engineer", "contact": [], "summary": "",
            "skills": ["Python", "Go", "PostgreSQL", "Redis", "Docker"],
            "experience": [{"title": "Software Engineer II", "company": "Globex Payments", "location": "", "dates": "Jan 2023 - Present",
                            "bullets": ["Built a payment reconciliation service in Go handling 2 million transactions a day.",
                                        "Set up Prometheus and Grafana dashboards and on-call alerts for 14 services."]}],
            "projects": [], "education": [], "certifications": [], "other": []}


def test_a_reworded_line_may_not_add_numbers_skills_names_or_claims():
    old = "Set up Prometheus and Grafana dashboards and on-call alerts for 14 services."
    allowed = ["Monitoring", "Python"]
    keywords = ["Monitoring", "Ruby", "Kubernetes"]
    ok = "Set up monitoring with Prometheus and Grafana dashboards and on-call alerts for 14 services."
    assert tailor._safe_change(old, ok, RESUME_TEXT, allowed, keywords)
    assert not tailor._safe_change(old, old.replace("14", "40"), RESUME_TEXT, allowed, keywords)           # a number
    assert not tailor._safe_change(old, old + " Used Ruby.", RESUME_TEXT, allowed, keywords)               # a skill they lack
    assert not tailor._safe_change(old, old.replace("Set up", "Set up Datadog,"), RESUME_TEXT, allowed, keywords)  # a new tool
    assert not tailor._safe_change(old, old.replace("Set up", "Set up secure, large-scale"), RESUME_TEXT, allowed, keywords)  # a claim


def test_reading_the_resume_rejects_invented_lines(monkeypatch):
    invented = dict(SECTIONS, experience=[dict(SECTIONS["experience"][0], bullets=["Led a team of 40 engineers at Google."])])
    monkeypatch.setattr(autofill, "_ai", lambda *a, **k: json.dumps(invented))
    with pytest.raises(ValueError, match="reliably"):
        tailor.structure(RESUME_TEXT * 3)
    monkeypatch.setattr(autofill, "_ai", lambda *a, **k: json.dumps(dict(SECTIONS, skills=["Python, Go, PostgreSQL, Redis, Docker"])))
    assert tailor.structure(RESUME_TEXT * 3)["skills"] == ["Python", "Go", "PostgreSQL", "Redis", "Docker"]   # one skill each


def test_a_keyword_counts_only_with_a_real_quote_from_the_resume(monkeypatch):
    monkeypatch.setattr(autofill, "_ai", lambda *a, **k: json.dumps({"evidence": {
        "Monitoring": "Prometheus and Grafana dashboards", "Ruby": "wrote Ruby on Rails apps", "GraphQL": ""}}))
    assert tailor.supported(["Postgres", "Monitoring", "Ruby", "GraphQL"], RESUME_TEXT, []) == {
        "Postgres": "Postgres", "Monitoring": "Prometheus and Grafana dashboards"}   # synonym; real quote; made-up quote rejected


def test_tailoring_rewords_only_what_is_true_and_keeps_the_facts(monkeypatch):
    replies = iter([
        json.dumps({"keywords": ["Monitoring", "Ruby"]}),                                  # the job's keywords
        json.dumps({"evidence": {"Monitoring": "Grafana dashboards and on-call alerts", "Ruby": ""}}),
        json.dumps({"summary": "", "skills": ["Monitoring", "Ruby", "Python"], "bullets": {
            "e0b0": "Built a payment reconciliation service in Go handling 5 million transactions a day.",   # number changed: refused
            "e0b1": "Set up monitoring with Prometheus and Grafana dashboards and on-call alerts for 14 services."}}),
    ])
    monkeypatch.setattr(autofill, "_ai", lambda *a, **k: next(replies))
    r = tailor.tailor(SECTIONS, RESUME_TEXT, [], "We need monitoring and Ruby.", "SRE", "Acme")
    out = r["resume"]
    assert out["experience"][0]["bullets"] == [SECTIONS["experience"][0]["bullets"][0],
                                               "Set up monitoring with Prometheus and Grafana dashboards and on-call alerts for 14 services."]
    assert "Ruby" not in out["skills"] and out["skills"][:2] == ["Monitoring", "Python"]
    assert (out["experience"][0]["company"], out["experience"][0]["dates"]) == ("Globex Payments", "Jan 2023 - Present")
    assert r["missing"] == ["Ruby"] and r["added"] == ["Monitoring"]
    assert tailor.render_pdf(out).startswith(b"%PDF")


# ── the website's endpoints ──────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fill.db"))
    fill_api._attempts.clear()
    db.init()
    accounts.init()
    return TestClient(main.app)


def _user(c):
    r = c.post("/api/fill/signup", json={"name": "Riya Testwala", "email": "riya@example.com", "password": "correct horse"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_tailored_resume_falls_back_to_the_original_without_ai(client, monkeypatch):
    h = _user(client)
    pdf = tailor.render_pdf(SECTIONS)
    client.put("/api/fill/resume", headers={**h, "X-Filename": "Riya-Resume.pdf"}, content=pdf)
    monkeypatch.setattr(autofill, "groq_key", lambda: "")
    r = client.post("/api/fill/tailored-resume", headers=h, json={"url": "https://example.com/job"}).json()
    assert r["tailored"] is False and base64.b64decode(r["b64"]) == pdf and r["name"] == "Riya-Resume.pdf" and "AI" in r["note"]


def test_tailored_resume_is_made_once_per_job(client, monkeypatch):
    h = _user(client)
    client.put("/api/fill/resume", headers={**h, "X-Filename": "Riya-Resume.pdf"}, content=tailor.render_pdf(SECTIONS))
    monkeypatch.setattr(autofill, "groq_key", lambda: "key")
    monkeypatch.setattr(tailor, "extract_text", lambda pdf: RESUME_TEXT * 3)
    monkeypatch.setattr(tailor, "structure", lambda text: SECTIONS)
    monkeypatch.setattr(tailor, "job_description", lambda url, page_text="": "monitoring")
    calls = []

    def fake_tailor(sections, text, skills, desc, role, company):
        calls.append(desc)
        out = json.loads(json.dumps(sections))
        out["skills"] = ["Monitoring", *out["skills"]]
        return {"resume": out, "added": ["Monitoring"], "missing": ["Ruby"], "covered": ["Monitoring"], "keywords": ["Monitoring", "Ruby"]}
    monkeypatch.setattr(tailor, "tailor", fake_tailor)
    first = client.post("/api/fill/tailored-resume", headers=h, json={"url": "https://example.com/job/1"}).json()
    again = client.post("/api/fill/tailored-resume", headers=h, json={"url": "https://example.com/job/1#fill-with-ai"}).json()
    assert first["tailored"] and first["added"] == ["Monitoring"] and first["missing"] == ["Ruby"] and first["covered"] == ["Monitoring"]
    assert client.post("/api/fill/tailored-resume", headers=h, json={"url": "https://example.com/job/1"}).json()["covered"] == ["Monitoring"]
    assert again["b64"] == first["b64"] and len(calls) == 1          # the same job gets the same resume

    # the user says they do have Ruby: it goes into their profile, and the next resume is made afresh
    p = client.post("/api/fill/profile/skills", headers=h, json={"skills": ["Ruby", "python"]}).json()
    assert p["data"]["skills"] == "Ruby, python"
    client.post("/api/fill/tailored-resume", headers=h, json={"url": "https://example.com/job/1"})
    assert len(calls) == 2


def test_applications_keep_details_and_proof_and_never_go_back(client):
    h = _user(client)
    url = "https://jobs.example.com/apply/1"
    post = lambda **b: client.post("/api/fill/applications", headers=h, json={"url": url, "title": "SRE", **b})
    assert post(status="opened", details={"filled": [{"label": "Email", "value": "riya@example.com"}]}).status_code == 204
    post(status="filled", details={"filled": [{"label": "Phone", "value": "+91 98765 43210"}], "resume": {"tailored": True}})
    post(status="filled", details={"filled": [], "resume": None})        # the confirmation page knows nothing of it
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    assert post(status="submitted", proof_b64=base64.b64encode(png).decode(), details={"confirmation": "Thank you for applying"}).status_code == 204
    post(status="opened")                                           # visiting it again later
    [a] = client.get("/api/fill/applications", headers=h).json()
    assert a["status"] == "submitted" and a["submitted_at"] and a["has_proof"]
    assert [f["label"] for f in a["details"]["filled"]] == ["Email", "Phone"] and a["details"]["confirmation"] == "Thank you for applying"
    assert a["details"]["resume"] == {"tailored": True}
    assert client.get(f"/api/fill/applications/{a['id']}/proof", headers=h).content == png
    assert post(status="submitted", proof_b64=base64.b64encode(b"<script>").decode()).status_code == 422
    other = client.post("/api/fill/signup", json={"name": "B", "email": "b@example.com", "password": "correct horse"}).json()["token"]
    assert client.get(f"/api/fill/applications/{a['id']}/proof", headers={"Authorization": f"Bearer {other}"}).status_code == 404


def test_a_version_the_resume_does_not_state_still_counts_as_the_skill(monkeypatch):
    monkeypatch.setattr(autofill, "_ai", lambda *a, **k: json.dumps({"evidence": {}}))
    text = "Backend development with Java (Spring Boot) and Python (Django)."
    found = tailor.supported(["Java 17", "Spring Boot 3", "Kubernetes"], text, [])
    assert found == {"Java 17": "Java", "Spring Boot 3": "Spring Boot"}      # Kubernetes really is missing
    assert tailor._base("Spring Boot 3.x") == "Spring Boot" and tailor._base("C++") == "C++" and tailor._base("Python") == "Python"
