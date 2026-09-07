"""An operator's update reaches running API readers through PostgreSQL."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import TEST_DATABASE_URL


def test_operator_update_persists_and_reaches_existing_api_client(client):
    def notice():
        response = client.get("/projects/resource-limits")
        assert response.status_code == 200
        return response.json()["data"]["max_machines_per_project"]

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
    assert command("--machines-per-project", "75") == {
        "previous": 50,
        "max_machines_per_project": 75,
    }
    assert notice() == 75
    assert command() == {"previous": 75, "max_machines_per_project": 75}
    assert command("--machines-per-project", "12")["max_machines_per_project"] == 12
    assert notice() == 12
