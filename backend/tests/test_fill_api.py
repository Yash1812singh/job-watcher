import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import accounts, autofill, db, fill_api, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fill.db"))
    monkeypatch.setattr(autofill, "ai_answer", lambda *a, **k: "")
    fill_api._attempts.clear()
    db.init()
    accounts.init()
    return TestClient(main.app)


def signup(c, name, email, pw="correct horse"):
    r = c.post("/api/fill/signup", json={"name": name, "email": email, "password": pw})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


PDF = b"%PDF-1.4 asha's resume"


def test_sign_up_log_in_and_wrong_password(client):
    signup(client, "Asha Rao", "Asha@Example.com")
    assert client.post("/api/fill/signup", json={"name": "x", "email": "asha@example.com", "password": "another pw"}).status_code == 409
    assert client.post("/api/fill/login", json={"email": "asha@example.com", "password": "wrong!!!!"}).status_code == 401
    r = client.post("/api/fill/login", json={"email": "ASHA@example.com", "password": "correct horse"})
    me = client.get("/api/fill/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert me["email"] == "asha@example.com" and me["name"] == "Asha Rao"
    # signing up starts the profile with the name and email
    p = client.get("/api/fill/profile", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert p["data"]["personal"] == {"first_name": "Asha", "last_name": "Rao", "email": "asha@example.com"}


def test_each_user_only_sees_and_fills_with_their_own_data(client):
    asha = signup(client, "Asha Rao", "asha@example.com")
    ben = signup(client, "Ben Lee", "ben@example.com")
    client.put("/api/fill/profile", headers=asha, json={"personal": {"first_name": "Asha", "phone": "+91 1"}, "work": {"notice_period": "30 days"}})
    client.put("/api/fill/profile", headers=ben, json={"personal": {"first_name": "Ben", "phone": "+1 2"}})
    assert client.put("/api/fill/resume", headers={**asha, "X-Filename": "asha.pdf"}, content=PDF).status_code == 200

    assert client.get("/api/fill/profile", headers=ben).json()["data"]["personal"]["first_name"] == "Ben"
    assert client.get("/api/fill/resume", headers=ben).status_code == 404       # Asha's resume isn't Ben's
    assert client.get("/api/fill/resume", headers=asha).content == PDF

    ben_key = {"Authorization": f"Bearer {client.post('/api/fill/keys', headers=ben, json={'label': 'Chrome'}).json()['key']}"}
    fields = [{"id": "1", "kind": "text", "label": "First Name", "required": True},
              {"id": "2", "kind": "file", "label": "Resume/CV", "required": True},
              {"id": "3", "kind": "text", "label": "Notice period", "required": True}]
    out = client.post("/api/fill/decide", headers=ben_key, json={"fields": fields}).json()
    assert {a["id"]: a["value"] for a in out["answers"]} == {"1": "Ben"}
    assert set(out["missing"]) == {"Resume/CV", "Notice period"}               # nothing of Asha's leaks in


def test_extension_keys_can_fill_but_not_edit_and_can_be_revoked(client):
    asha = signup(client, "Asha Rao", "asha@example.com")
    client.put("/api/fill/resume", headers=asha, content=PDF)
    key = client.post("/api/fill/keys", headers=asha, json={"label": "Laptop"}).json()["key"]
    k = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/fill/status", headers=k).json()["resume"] == "resume.pdf"
    assert client.get("/api/fill/resume-file", headers=k).content == PDF
    assert client.put("/api/fill/profile", headers=k, json={}).status_code == 401  # a key can't change the profile
    key_id = client.get("/api/fill/keys", headers=asha).json()[0]["id"]
    assert client.delete(f"/api/fill/keys/{key_id}", headers=asha).status_code == 204
    assert client.get("/api/fill/status", headers=k).status_code == 401


def test_fill_has_its_own_accounts_while_job_watcher_keeps_the_site_password(client, monkeypatch):
    monkeypatch.setattr(main, "PASSWORD", "site-secret")
    assert client.get("/api/sites").status_code == 401
    assert client.post("/api/fill/signup", json={"name": "A", "email": "a@example.com", "password": "12345678"}).status_code == 201
    assert client.get("/api/fill/profile").status_code == 401                    # but not without logging in


def test_resume_must_be_a_pdf_and_the_extension_download_points_here(client):
    asha = signup(client, "Asha Rao", "asha@example.com")
    assert client.put("/api/fill/resume", headers=asha, content=b"not a pdf").status_code == 422
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/fill/extension.zip").content))
    assert "fill-with-ai/manifest.json" in z.namelist()
    assert b"http://testserver" in z.read("fill-with-ai/background.js")


def test_logins_are_rate_limited(client):
    for _ in range(10):
        client.post("/api/fill/login", json={"email": "x@example.com", "password": "nope"})
    assert client.post("/api/fill/login", json={"email": "x@example.com", "password": "nope"}).status_code == 429
