"""Real content-host grants, cookies, membership and tunnel route, with a gated peer.

The app/machine messages are explicit wire-level peers; no native socket pressure
or production deployment is claimed by this in-process database integration.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.api.preview_host import cookie_name, mint_preview_token, preview_origin
from app.domain.agent import preview_tunnel as wire
from tests.integration.conftest import session_auth_headers
from tests.integration.test_app_preview_proxy import (
    _open_preview,
    _project_topic,
    _room_agent,
    _tunnel_url,
)
from tests.integration.test_app_preview_proxy import (
    preview_config as preview_config,
)


def test_content_host_auth_precedes_gated_live_tunnel_stream(client, preview_config):
    project, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    seat = _room_agent(client, topic_id)
    first = threading.Event()
    release = threading.Event()
    requests = []
    errors = []
    with client.websocket_connect(
        _tunnel_url(project, topic_id, seat),
        headers={wire.CAPS_HEADER: ",".join(sorted(wire.CAPABILITIES))},
    ) as tunnel:

        def machine_peer():
            try:
                while True:
                    op, sid, payload = wire.decode(tunnel.receive_bytes())
                    if op != wire.OP_REQ:
                        continue
                    meta, _ = wire.decode_meta(payload)
                    requests.append(meta)
                    tunnel.send_bytes(
                        wire.encode(
                            wire.OP_RESP,
                            sid,
                            wire.encode_meta(
                                {
                                    "status": 200,
                                    "headers": [["content-type", "text/event-stream"]],
                                }
                            ),
                        )
                    )
                    tunnel.send_bytes(wire.encode(wire.OP_DATA, sid, b"first\n"))
                    if meta["path"].startswith("/events"):
                        first.set()
                        assert release.wait(5), "body release"
                        tunnel.send_bytes(wire.encode(wire.OP_END, sid))
                        return
                    tunnel.send_bytes(wire.encode(wire.OP_END, sid))
            except Exception as exc:
                errors.append(repr(exc))

        peer = threading.Thread(target=machine_peer, daemon=True)
        peer.start()
        try:
            shown = client.post(
                f"/topics/{topic_id}/shown",
                json={"path": "http://localhost:5173", "as": "app"},
                headers=session_auth_headers("alice"),
            )
            assert shown.status_code == 200, shown.text
            requests.clear()
            origin = preview_origin(topic_id)
            assert client.get(origin + "/events").status_code == 401
            assert (
                client.get(
                    origin + "/events", headers=session_auth_headers("alice")
                ).status_code
                == 401
            )
            assert not requests
            grant_response = client.post(
                f"/topics/{topic_id}/preview-session",
                headers=session_auth_headers("alice"),
            )
            assert grant_response.status_code == 200
            grant = grant_response.json()["data"]
            bad = client.post(
                grant["url"],
                data={"grant": grant["grant"]},
                headers={"Origin": "https://wrong.example"},
                follow_redirects=False,
            )
            assert bad.status_code == 403 and "set-cookie" not in bad.headers
            _, exchange = _open_preview(client, topic_id)
            assert exchange.status_code == 303
            assert "Domain=" not in exchange.headers["set-cookie"]
            cookie = client.cookies.get(cookie_name())
            for invalid in (
                mint_preview_token(
                    topic_id, "alice", purpose="preview-session", ttl=-1
                ),
                mint_preview_token(
                    uuid.uuid4(), "alice", purpose="preview-session", ttl=60
                ),
            ):
                response = client.get(
                    origin + "/events",
                    headers={"Cookie": cookie_name() + "=" + invalid},
                )
                assert response.status_code == 401
            assert not requests
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.get,
                    origin + "/events?a=1&a=2&escaped=%2F",
                    headers={"Cookie": cookie_name() + "=" + cookie},
                )
                assert first.wait(4), errors
                assert not pending.done(), "response must still own unfinished body"
                release.set()
                response = pending.result(timeout=5)
            assert response.status_code == 200 and response.content == b"first\n"
            assert requests[-1]["path"] == "/events?a=1&a=2&escaped=%2F"
            assert cookie not in str(requests[-1])
            assert not errors
        finally:
            release.set()
            peer.join(5)
            assert not peer.is_alive(), errors
