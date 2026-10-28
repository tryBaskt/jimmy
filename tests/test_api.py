from fastapi.testclient import TestClient
from apis.app import create_app
from paperlab.storage import digest


def client(settings, store, service="all"):
    return TestClient(create_app(settings, store, service))


def test_service_boundaries(settings, store):
    c = client(settings, store, "projects")
    assert c.get("/api/identity/me").status_code == 404
    assert c.get("/api/projects").status_code == 200
    assert c.post("/api/research/projects/no/runs", json={}).status_code == 404


def test_ownership_hidden(settings, store):
    store.put("project#private", "meta", {"id": "private", "owner": "other"})
    c = client(settings, store)
    for path in [
        "/api/projects/private",
        "/api/data/projects/private/uploads",
        "/api/activity/projects/private/activity",
        "/api/artifacts/projects/private/artifacts",
        "/api/research/projects/private/runs/absent",
    ]:
        response = (
            c.post(path, json={"name": "p.pdf", "kind": "paper", "size": 3, "checksum": "a" * 64})
            if path.endswith("uploads")
            else c.get(path)
        )
        assert response.status_code == 404


def test_upload_freezes_private_input_and_rejects_reupload(settings, store):
    c = client(settings, store)
    p = c.post("/api/projects", json={"title": "Paper"}).json()["id"]
    content = b"%PDF-1.7\nnot a complete PDF, validated later by parser"
    u = c.post(
        f"/api/data/projects/{p}/uploads",
        json={"name": "paper.pdf", "kind": "paper", "size": len(content), "checksum": digest(content)},
    ).json()
    assert c.put(u["url"], content=content).status_code == 204
    completed = c.post(f"/api/data/projects/{p}/uploads/{u['id']}/complete").json()
    assert completed["status"] == "completed"
    assert "/inputs/" in completed["key"]
    assert c.put(u["url"], content=content).status_code == 409


def test_checksum_failure_and_non_pdf(settings, store):
    c = client(settings, store)
    p = c.post("/api/projects", json={"title": "P"}).json()["id"]
    u = c.post(
        f"/api/data/projects/{p}/uploads",
        json={"name": "p.pdf", "kind": "paper", "size": 3, "checksum": digest(b"abc")},
    ).json()
    assert c.put(u["url"], content=b"xyz").status_code == 422
    assert c.put(u["url"], content=b"abc").status_code == 204
    assert c.post(f"/api/data/projects/{p}/uploads/{u['id']}/complete").status_code == 422


def test_readonly_interpretation(settings, store):
    c = client(settings, store)
    p = c.post("/api/projects", json={"title": "P"}).json()["id"]
    assert c.post(f"/api/projects/{p}/specifications", json={}).status_code == 404
    assert c.get(f"/api/activity/projects/{p}/activity?after=bad").status_code == 422


def test_activity_replay(settings, store):
    c = client(settings, store)
    p = c.post("/api/projects", json={"title": "P"}).json()["id"]
    first = c.get(f"/api/activity/projects/{p}/activity").json()
    assert first["items"][0]["kind"] == "project_created"
    assert (
        c.get(f"/api/activity/projects/{p}/activity", params={"after": first["cursor"]}).json()["items"] == []
    )
