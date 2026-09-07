"""An operator's update reaches running API readers through PostgreSQL."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import TEST_DATABASE_URL, seed_user


def test_operator_update_persists_and_reaches_existing_api_client(client):
    def notice():
        response = client.get("/projects/resource-limits")
        assert response.status_code == 200
        return response.json()["data"]["max_machines_per_team"]

    def command(*args):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.resource_limits", *args],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    assert notice() == 50
    assert command("--machines-per-team", "75") == {
        "previous": 50,
        "max_machines_per_team": 75,
    }
    assert notice() == 75
    assert command() == {"previous": 75, "max_machines_per_team": 75}
    assert command("--machines-per-team", "12")["max_machines_per_team"] == 12
    assert notice() == 12

    token = seed_user(client, "quota_operator")
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post(
        "/projects", json={"name": "Team quota"}, headers=headers
    ).json()["data"]
    team_id = str(project["team_id"])
    path = f"/teams/{team_id}/resource-quotas"
    assert (
        command("--team-id", team_id, "--machines-per-team", "20")[
            "max_machines_per_team"
        ]
        == 20
    )
    command("--machines-per-team", "30")
    assert client.get(path, headers=headers).json()["data"]["machines"]["limit"] == 20
    assert (
        command("--team-id", team_id, "--inherit-machines")["max_machines_per_team"]
        == 30
    )
    grant = command("--team-id", team_id, "--grant-credits", "100")
    assert grant["credits_granted"] == 100
    assert grant["grant_id"]
    assert (
        client.get(path, headers=headers).json()["data"]["credits"]["credits_remaining"]
        == 100
    )
