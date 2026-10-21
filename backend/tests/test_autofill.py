import pytest

from app import autofill

PROFILE_YAML = """
personal:
  first_name: Asha
  last_name: Rao
  email: asha@example.com
  phone: "+91-9000000000"
  location: {city: Pune, state: Maharashtra, country: India}
  linkedin:
experience:
  years_total: 4
  current_title: Backend Engineer
  current_company: Acme
  notice_period: 30 days
  desired_salary: 25 LPA
education:
  - {degree: B.Tech, institution: IIT Delhi, graduation_year: 2021}
eligibility:
  work_authorization: "Yes"
  require_sponsorship: "No"
"""


@pytest.fixture
def profile(tmp_path, monkeypatch):
    path = tmp_path / "profile.yaml"
    path.write_text(PROFILE_YAML)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(autofill, "RESUME_PATH", resume)
    asked = []
    monkeypatch.setattr(autofill, "ai_answer", lambda p, q, job, text, options=None: asked.append(q) or (options[0] if options else f"AI: {q}"))
    p = autofill.load_profile(path)
    p.asked = asked
    return p


def _fields(*specs):
    return [{"id": str(i), "required": True, "options": [], "value": "", "name": "", **s} for i, s in enumerate(specs)]


def test_a_typical_page(profile):
    fields = _fields(
        {"kind": "text", "label": "First Name"},
        {"kind": "text", "label": "Last Name"},
        {"kind": "email", "label": "Email"},
        {"kind": "text", "label": "College/ University Name"},
        {"kind": "textarea", "label": "Are you currently in college? Which year are you in?"},
        {"kind": "text", "label": "What is your notice period?"},
        {"kind": "textarea", "label": "Why do you want to join Acme?"},
        {"kind": "radio", "label": "Will you require visa sponsorship?", "options": ["Yes", "No"]},
        {"kind": "select", "label": "Gender", "options": ["Select...", "Female", "Male", "Decline to self-identify"], "required": False},
        {"kind": "checkbox", "label": "I agree to the privacy policy"},
        {"kind": "file", "label": "Resume/CV"},
        {"kind": "text", "label": "LinkedIn Profile"},
        {"kind": "text", "label": "Email (confirm)", "value": "already@typed.com"},
    )
    out = autofill.decide(fields, "Backend Engineer at Acme", "", profile)
    got = {a["id"]: a["value"] for a in out["answers"]}
    assert got["0"] == "Asha" and got["1"] == "Rao" and got["2"] == "asha@example.com"
    assert got["3"] == "IIT Delhi"                       # not the applicant's own name
    assert got["4"].startswith("AI:")                     # a question about college, not the college name
    assert got["5"] == "30 days"
    assert got["6"] == "AI: Why do you want to join Acme?"  # "join" alone isn't a notice-period question
    assert got["7"] == "No"
    assert got["8"] == "Decline to self-identify"
    assert got["9"] == "checked"
    assert got["10"] == "resume.pdf"
    assert "12" not in got                                # something already typed is left alone
    assert out["missing"] == ["LinkedIn Profile"]         # empty in the profile: left for you, not AI prose
    assert "LinkedIn Profile" not in profile.asked


def test_choices_the_rules_dont_know_go_to_the_ai_constrained_to_the_options(profile):
    fields = _fields({"kind": "radio", "label": "Which team interests you most?", "options": ["Platform", "Payments"]})
    out = autofill.decide(fields, "", "", profile)
    assert out["answers"] == [{"id": "0", "value": "Platform", "source": "ai"}]


def test_pick_option_understands_yes_no_and_declines():
    assert autofill.pick_option("No", ["Yes, I will", "No, I won't"]) == "No, I won't"
    assert autofill.pick_option(autofill.DECLINE, ["Male", "I don't wish to answer"]) == "I don't wish to answer"
    assert autofill.pick_option(autofill.AGREE, ["I acknowledge"]) == "I acknowledge"
    assert autofill.pick_option("Pune", ["Select…", "Mumbai", "Pune"]) == "Pune"
    assert autofill.pick_option("Bengaluru", ["Mumbai", "Pune"]) is None


def test_a_base64_resume_is_served_decoded(tmp_path, monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    pdf = b"%PDF-1.4 real bytes"
    path = tmp_path / "resume.pdf.b64"
    path.write_bytes(base64.b64encode(pdf))
    monkeypatch.setattr(autofill, "RESUME_PATH", path)
    r = TestClient(main.app).get("/api/autofill/resume")
    assert r.status_code == 200 and r.content == pdf and 'filename="resume.pdf"' in r.headers["content-disposition"]
