"""Bound content sessions don't follow latest or overwrite another resource's cookie."""

import uuid
from urllib.parse import urlsplit

from app.api.preview_host import cookie_name, preview_origin
from app.core.config import settings
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import preview_hub
from app.domain.library import service as library
from app.domain.textfile import MAX_TEXT_BYTES, content_version
from tests.integration.conftest import session_auth_headers
from tests.integration.test_app_preview_proxy import (
    FakeMachine,
    _project_topic,
    _room_agent,
)
from tests.integration.test_app_preview_proxy import preview_config as preview_config
from tests.integration.test_preview_host import static_preview as static_preview


def test_fixed_file_survives_latest_change_and_rejects_changed_entry(
    client, static_preview
):
    project, topic, html, assets = static_preview
    version = library.preview_file_version(project, topic, "web/report.html")
    response = client.post(
        f"/topics/{topic}/preview-session",
        json={"path": "web/report.html", "version": version},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    bound = response.json()["data"]
    origin = bound["url"].removesuffix("/_cheese/session")
    assert origin != preview_origin(topic)
    assert len(urlsplit(origin).hostname.split(".")[0]) <= 63
    exchange = client.post(
        bound["url"],
        data={"grant": bound["grant"]},
        headers={"Origin": settings.frontend_url},
        follow_redirects=False,
    )
    assert exchange.status_code == 303 and exchange.headers["location"] == "/"
    token = exchange.cookies.get(cookie_name())
    assert client.get(origin + "/").text == html
    bridge = client.get(origin + "/_cheese/runtime.js")
    assert bridge.status_code == 200 and "CheesePreviewRuntime" in bridge.text
    assert settings.frontend_url in bridge.text
    head = client.head(origin + "/_cheese/runtime.js")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(bridge.content))
    assert client.post(origin + "/_cheese/runtime.js").status_code == 405
    assert client.get(preview_origin(topic) + "/_cheese/runtime.js").status_code == 401
    assert client.get(origin + "/main.js").text == assets["main.js"]
    assert (
        client.get(
            preview_origin(topic) + "/", headers={"Cookie": cookie_name() + "=" + token}
        ).status_code
        == 401
    )
    shown = client.post(
        f"/topics/{topic}/shown",
        json={"path": "other/new.html", "as": "html", "content": "new latest"},
        headers=session_auth_headers("alice"),
    )
    assert shown.status_code == 200
    assert client.get(origin + "/").text == html
    live = client.post(
        f"/topics/{topic}/preview-session", headers=session_auth_headers("alice")
    ).json()["data"]
    client.post(
        live["url"],
        data={"grant": live["grant"]},
        headers={"Origin": settings.frontend_url},
        follow_redirects=False,
    )
    assert client.get(preview_origin(topic) + "/").text == "new latest"
    assert client.get(origin + "/").text == html
    library.write_room_file(project, topic, "web/report.html", b"changed")
    assert client.get(origin + "/").status_code == 409
    assert client.get(origin + "/main.js").status_code == 200
    denied = client.post(
        f"/topics/{topic}/preview-session",
        json={"path": "web/report.html", "version": version},
        headers=session_auth_headers("alice"),
    )
    assert denied.status_code == 404


def test_oversized_file_reports_the_version_its_session_check_uses(
    client, static_preview
):
    """A file past MAX_TEXT_BYTES has no body to hand over, but it still has a
    version -- and the preview panel opens a session for the version it was
    shown. Reporting `null` there made every large file unopenable: the panel
    asked, the route compared `null` against the real hash, and answered
    "Preview entry changed or unavailable" for a file that had not changed.
    """
    project, topic, _html, _assets = static_preview
    body = b"<html>" + b"x" * MAX_TEXT_BYTES + b"</html>"
    library.write_room_file(project, topic, "web/huge.html", body)

    read = client.get(
        f"/topics/{topic}/preview/file",
        params={"path": "web/huge.html"},
        headers=session_auth_headers("alice"),
    )
    assert read.status_code == 200, read.text
    data = read.json()["data"]
    assert data["too_large"] is True and data["content"] is None
    assert data["version"] == content_version(body)
    assert data["version"] == library.preview_file_version(
        project, topic, "web/huge.html"
    )

    opened = client.post(
        f"/topics/{topic}/preview-session",
        json={"path": "web/huge.html", "version": data["version"]},
        headers=session_auth_headers("alice"),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["data"]["resource"]["version"] == data["version"]


class InstanceMachine(FakeMachine):
    """Exercise production grant/host/hub routing with a controlled wire peer."""

    instance = "1" * 64

    async def send_bytes(self, data):
        op, stream, payload = wire.decode(data)
        if op == wire.OP_REQ:
            meta, _ = wire.decode_meta(payload)
            if meta.get("inspect_instance"):
                self.reply(
                    wire.encode(
                        wire.OP_RESP,
                        stream,
                        wire.encode_meta(
                            {
                                "status": 200,
                                "headers": [["x-cheese-instance", self.instance]],
                            }
                        ),
                    )
                )
                self.reply(wire.encode(wire.OP_END, stream))
                return
            if meta.get("instance") and meta["instance"] != self.instance:
                self.reply(wire.encode(wire.OP_ERR, stream, b"preview instance gone"))
                return
        await super().send_bytes(data)


def test_fixed_app_host_keeps_instance_after_latest_and_helper_changes(
    client, preview_config
):
    _, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    seat = _room_agent(client, topic_id)
    machine = InstanceMachine()
    machine.machine = preview_hub.attach(
        topic_id, seat, machine, capabilities=frozenset({"instance-v1"})
    )
    try:
        assert (
            client.post(
                f"/topics/{topic_id}/shown",
                json={"path": "http://localhost:5173", "as": "app"},
                headers=session_auth_headers("alice"),
            ).status_code
            == 200
        )
        meta = client.get(
            f"/topics/{topic_id}/preview", headers=session_auth_headers("alice")
        ).json()["data"]
        selection = {"artifact_id": meta["artifact_id"], "instance": machine.instance}
        response = client.post(
            f"/topics/{topic_id}/preview-session",
            json=selection,
            headers=session_auth_headers("alice"),
        )
        assert response.status_code == 200, response.text
        bound = response.json()["data"]
        origin = bound["url"].removesuffix("/_cheese/session")
        exchange = client.post(
            bound["url"],
            data={"grant": bound["grant"]},
            headers={"Origin": settings.frontend_url},
            follow_redirects=False,
        )
        assert exchange.status_code == 303
        assert "Domain=" not in exchange.headers["set-cookie"]
        assert client.get(origin + "/asset?x=1").status_code == 200
        assert machine.requests[-1][0]["instance"] == "1" * 64
        assert machine.requests[-1][0]["path"] == "/asset?x=1"
        assert (
            client.post(
                preview_origin(topic_id) + "/_cheese/session",
                data={"grant": bound["grant"]},
                headers={"Origin": settings.frontend_url},
                follow_redirects=False,
            ).status_code
            == 401
        )
        assert (
            client.post(
                f"/topics/{topic_id}/shown",
                json={"path": "new.html", "as": "html", "content": "latest changed"},
                headers=session_auth_headers("alice"),
            ).status_code
            == 200
        )
        assert client.get(origin + "/").status_code == 200
        machine.detach()
        assert client.get(origin + "/").status_code == 404
        machine.machine = preview_hub.attach(
            topic_id, seat, machine, capabilities=frozenset({"instance-v1"})
        )
        assert client.get(origin + "/").status_code == 200
        machine.instance = "2" * 64
        assert client.get(origin + "/").status_code == 404
        machine.detach()
        machine.attach(topic_id, seat)
        assert client.get(origin + "/").status_code == 404
    finally:
        machine.detach()
