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
    assert autofill.pick_option("Bangalore, Karnataka", ["Mumbai", "Bengaluru, Karnataka"]) == "Bengaluru, Karnataka"
    assert autofill.pick_option("India", ["British Indian Ocean Territory", "India +91", "Indonesia"]) == "India +91"
    assert autofill.pick_option("India", ["British Indian Ocean Territory", "Republic of India"]) == "Republic of India"


def test_dropdown_without_the_profiles_exact_words_goes_to_ai(monkeypatch):
    asked = []
    monkeypatch.setattr(autofill, "ai_answer", lambda p, q, job, text, options=None: asked.append(q) or "30-60 days")
    profile = autofill.Profile(first_name="Asha", notice_period="45 days")
    out = autofill.decide([{"id": "n", "kind": "select", "label": "Notice period", "options": ["Select", "Immediate", "30-60 days", "90 days"], "required": True}],
                          profile=profile, resume_name="")
    assert out["answers"] == [{"id": "n", "value": "30-60 days", "source": "ai"}]
    assert "45 days" in asked[0]


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


def test_codes_only_the_user_has_are_left_for_them(profile):
    fields = _fields(
        {"kind": "text", "label": "Enter the referral code from your invite email"},
        {"kind": "text", "label": "Verification code sent to your phone"},
        {"kind": "text", "label": "OTP"},
        {"kind": "text", "label": "PIN code"},
        {"kind": "text", "label": "Email"},
    )
    out = autofill.decide(fields, "", "", profile)
    got = {a["id"]: a["value"] for a in out["answers"]}
    assert got == {"4": "asha@example.com"}   # PIN code (postal) is simply empty in this profile
    assert out["missing"][:3] == ["Enter the referral code from your invite email", "Verification code sent to your phone", "OTP"]
    assert profile.asked == []                 # and the AI isn't asked to make one up


def test_declined_personal_questions_never_get_another_answer(monkeypatch):
    monkeypatch.setattr(autofill, "ai_answer", lambda *a, **k: "Others")
    profile = autofill.Profile(first_name="Asha", gender="Prefer not to say", current_title="Software Engineer", notice_period="30 days")
    fields = [{"id": "g", "kind": "select", "label": "Gender", "options": ["Male", "Female", "Others"], "required": True},
              {"id": "g2", "kind": "select", "label": "Gender", "options": ["Male", "Female", "I don't wish to answer"], "required": True},
              {"id": "y", "kind": "text", "label": "Start date year", "required": True},
              {"id": "m", "kind": "select", "label": "Start date month", "options": ["January", "November"], "required": True},
              {"id": "t", "kind": "text", "label": "Title", "required": True},
              {"id": "j", "kind": "text", "label": "Earliest start date", "required": True}]
    out = autofill.decide(fields, profile=profile, resume_name="")
    got = {a["id"]: a["value"] for a in out["answers"]}
    assert got == {"g2": "I don't wish to answer", "t": "Software Engineer", "j": "30 days"}
    assert out["missing"] == ["Gender", "Start date year", "Start date month"]


def test_city_and_state_together():
    p = autofill.Profile(city="Bangalore", state="Karnataka")
    assert autofill.rule_answer("In what city and state is your primary residence? (e.g. San Jose, CA)", p) == "Bangalore, Karnataka"
    assert autofill.rule_answer("State", p) == "Karnataka"


def test_total_compensation_wording():
    p = autofill.Profile(current_salary="18 LPA", desired_salary="26 LPA")
    assert autofill.rule_answer("What is your current total compensation (CTC)", p) == "18 LPA"
    assert autofill.rule_answer("What is your expected total compensation (CTC) for your next role?", p) == "26 LPA"
    assert autofill.rule_answer("Please share your expected total CTC in INR (Lakhs per annum).", p) == "26 LPA"
    assert autofill.rule_answer("Current CTC", p) == "18 LPA"


def test_wordings_seen_on_real_forms():
    p = autofill.Profile(first_name="Yash", last_name="Singh", years="1")
    hear = "How did you hear about this job? (If through one of our current employees, please list their name)"
    assert autofill.rule_answer(hear, p) == "Company website"
    assert autofill.rule_answer("Full name", p) == "Yash Singh"
    k8s = "Experience in K8S upgrade, If yes, please elaborate on your years of experience"
    assert autofill.rule_answer(k8s, p) is None                         # for the AI, from the profile; never "1"
    assert autofill.rule_answer("Total years of experience", p) == "1"
    assert autofill.rule_answer("Years of professional experience", p) == "1"
    salary = "What is your minimum required yearly salary (in INR) to consider a new position?"
    assert autofill.rule_answer(salary, p) == "Negotiable"              # the profile has none (a number-only box goes to the user)
    assert autofill.rule_answer(salary, autofill.Profile(desired_salary="12 LPA")) == "12 LPA"


def test_a_salary_range_is_never_guessed(monkeypatch):
    monkeypatch.setattr(autofill, "ai_answer", lambda *a, **k: "10-15 LPA")
    out = autofill.decide([{"id": "s", "kind": "select", "label": "Expected CTC", "options": ["5-10 LPA", "10-15 LPA", "15+ LPA"], "required": True}],
                          profile=autofill.Profile(first_name="Yash"), resume_name="")
    assert out["answers"] == [] and out["missing"] == ["Expected CTC"]
