"""MCP Apps (`io.modelcontextprotocol/ui`) pure rules, and the third-party CSP.

The security-relevant half of this: the policy a third party's View is served
under must NOT re-grant ``allow-same-origin`` (that is exactly what the room's
own artifacts have), and it must only name the origins the server declared.
"""

from app.domain.remote_mcp import apps


def test_capability_names_the_ui_extension_and_mime() -> None:
    assert apps.capabilities() == {
        "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": [apps.MIME]}}
    }
    assert apps.MIME == "text/html;profile=mcp-app"


def test_a_tool_names_its_view_only_through_meta_ui() -> None:
    assert (
        apps.tool_resource_uri({"_meta": {"ui": {"resourceUri": "ui://weather"}}})
        == "ui://weather"
    )
    # Not a `ui://` resource, or no metadata at all: the tool opens no View.
    not_a_view = {"_meta": {"ui": {"resourceUri": "http://x"}}}
    assert apps.tool_resource_uri(not_a_view) is None
    assert apps.tool_resource_uri({"name": "plain"}) is None
    assert apps.is_ui_uri("ui://a") and not apps.is_ui_uri("https://a")


def test_pointer_round_trips_and_rejects_what_is_not_a_view() -> None:
    text = apps.pointer("figma", "ui://pkg/app.html")
    assert apps.pointer_parts(text) == ("figma", "ui://pkg/app.html")
    assert apps.pointer_parts("not json") is None
    assert apps.pointer_parts('{"server": "figma", "uri": "http://x"}') is None
    assert apps.pointer_parts('{"server": "", "uri": "ui://x"}') is None


def test_declared_origins_keep_only_real_http_origins() -> None:
    entry = {
        "_meta": {
            "ui": {
                "csp": {
                    "connectDomains": [
                        "https://api.example.com/v1",
                        "javascript:alert(1)",
                    ],
                    "resourceDomains": ["https://cdn.example.com"],
                }
            }
        }
    }
    connect, resources = apps.declared_origins(entry)
    assert connect == ("https://api.example.com",)
    assert resources == ("https://cdn.example.com",)
    assert apps.declared_origins({"text": "no meta"}) == ((), ())


def test_the_view_csp_never_regrants_same_origin() -> None:
    from app.api.preview_host import _mcp_app_csp

    entry = {
        "_meta": {
            "ui": {
                "csp": {"connectDomains": ["https://api.example.com"]},
            }
        }
    }
    csp = _mcp_app_csp(entry)
    assert "allow-same-origin" not in csp
    assert "sandbox allow-scripts allow-forms" in csp
    assert "connect-src https://api.example.com" in csp
    # An undeclared resource this View would reach is refused, not allowed.
    assert "connect-src 'none'" not in csp
