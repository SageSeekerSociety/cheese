"""Model changes are accepted only on individual project agents."""

import pytest

from tests.integration.conftest import post_project


@pytest.mark.parametrize("path", ["model-profile", "execution-profile"])
def test_project_model_control_is_retired(client, path):
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    assert (
        client.put(f"/projects/{pid}/{path}", json={"profile": "default"}).status_code
        == 404
    )


@pytest.mark.parametrize("path", ["model-profiles", "execution-profiles"])
def test_project_model_catalog_is_retired(client, path):
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    assert client.get(f"/projects/{pid}/{path}").status_code == 404
