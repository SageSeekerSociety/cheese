"""The skill bundle a session fetches by digest, scoped to its own project.

The route is what a device launch calls when its machine has never seen the
digest in its script (device_launch). It carries no project in its path: the
credential names the project, and the served bytes must be exactly the ones
that credential's project hashes to — no cross-project read, and a digest that
does not match this project is a miss, not a leak.
"""

import hashlib

from fastapi.testclient import TestClient

from app.core.sandbox_auth import SANDBOX_TOKEN, mint_scoped_token
from app.domain.project_skill import service
from app.domain.project_skill.service import session_skill_bundle
from app.main import app

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


def _token(project: str) -> str:
    return mint_scoped_token(
        project_id=project, topic_id="33333333-3333-3333-3333-333333333333"
    )


def _digest(project: str) -> str:
    return hashlib.sha256(session_skill_bundle(project)).hexdigest()


def _a_project_of_its_own(tmp_path, project: str, name: str):
    """The mirror dir ``session_skill_files`` reads a project's skills from, with
    one skill in it, so two projects here do NOT share a bundle."""
    root = tmp_path / project
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


def test_the_bundle_is_served_by_its_own_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "mirror_root", lambda pid: tmp_path / str(pid))
    bundle = session_skill_bundle(A)
    digest = hashlib.sha256(bundle).hexdigest()

    got = TestClient(app).get(
        f"/connector/skill-bundles/{digest}", headers={"x-cheese-token": _token(A)}
    )

    assert got.status_code == 200
    assert got.content == bundle
    assert got.headers["X-Checksum-SHA256"] == digest


def test_a_credential_only_reaches_its_own_projects_bundle(tmp_path, monkeypatch):
    roots = {}
    monkeypatch.setattr(service, "mirror_root", lambda pid: roots[str(pid)])
    roots[A] = _a_project_of_its_own(tmp_path, A, "a-skill")
    roots[B] = _a_project_of_its_own(tmp_path, B, "b-skill")
    digest_a = _digest(A)
    assert digest_a != _digest(B)
    client = TestClient(app)

    # A's credential asks for A's digest: its own bytes.
    allowed = client.get(
        f"/connector/skill-bundles/{digest_a}", headers={"x-cheese-token": _token(A)}
    )
    assert allowed.status_code == 200
    assert allowed.content == session_skill_bundle(A)
    assert b"b-skill" not in allowed.content

    # B's credential asks for the same digest: B's bundle hashes differently, so
    # this is a miss — B is never handed A's skills.
    refused = client.get(
        f"/connector/skill-bundles/{digest_a}", headers={"x-cheese-token": _token(B)}
    )
    assert refused.status_code == 404


def test_no_project_credential_no_bundle():
    client = TestClient(app)
    digest = _digest(A)

    # No token at all.
    assert client.get(f"/connector/skill-bundles/{digest}").status_code == 401
    # The global dev secret names no project, so it cannot answer "whose skills".
    assert (
        client.get(
            f"/connector/skill-bundles/{digest}",
            headers={"x-cheese-token": SANDBOX_TOKEN},
        ).status_code
        == 401
    )


def test_a_malformed_digest_is_a_bad_request():
    got = TestClient(app).get(
        "/connector/skill-bundles/not-a-digest", headers={"x-cheese-token": _token(A)}
    )
    assert got.status_code == 400


def test_a_launch_gets_the_bundle_it_was_built_with_after_the_skills_change(
    tmp_path, monkeypatch
):
    """The launcher names a digest when it is built and the machine asks later.
    Skills edited in between must not turn that fetch into a miss: the copy
    frozen at launch is served, and only to its own project."""
    roots = {}
    monkeypatch.setattr(service, "mirror_root", lambda pid: roots[str(pid)])
    monkeypatch.setattr(service.settings, "workspace_root", str(tmp_path / "ws"))
    roots[A] = _a_project_of_its_own(tmp_path, A, "a-skill")
    roots[B] = _a_project_of_its_own(tmp_path, B, "b-skill")
    frozen = session_skill_bundle(A)
    digest = service.publish_session_skill_bundle(A)
    assert digest == hashlib.sha256(frozen).hexdigest()

    (roots[A] / "a-skill" / "SKILL.md").write_text("# edited\n", encoding="utf-8")
    assert _digest(A) != digest
    client = TestClient(app)

    got = client.get(
        f"/connector/skill-bundles/{digest}", headers={"x-cheese-token": _token(A)}
    )
    assert got.status_code == 200
    assert got.content == frozen
    assert got.headers["Cache-Control"].startswith("private")

    other = client.get(
        f"/connector/skill-bundles/{digest}", headers={"x-cheese-token": _token(B)}
    )
    assert other.status_code == 404
