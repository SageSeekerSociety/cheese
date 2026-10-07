"""A rendered page carries its own policy, because the response headers cannot.

The panel never navigates to `/topics/{id}/attachments/html`: it fetches the
bytes with the Authorization header and hands them to a sandboxed frame, and a
response's CSP does not follow bytes into that frame. What the page itself says
is therefore the only policy that applies where it is actually read — and what
it must not do is let the frame reach the network, which those pages do try.
"""

from app.api.routes.topics_attachments import (
    _PAGE_POLICY,
    _PAGE_POLICY_HEADER,
    _with_policy,
)

PAGE = b"<!DOCTYPE html>\n<html><head><title>t</title></head><body>x</body></html>"


def test_the_policy_lands_inside_the_head():
    """Before the title, so it is parsed as part of the document rather than
    after the content it governs."""
    out = _with_policy(PAGE)

    assert out.startswith(b"<!DOCTYPE html>\n<html><head><meta http-equiv")
    assert b'http-equiv="Content-Security-Policy"' in out
    assert out.index(b"<meta http-equiv") < out.index(b"<title>")
    assert out.endswith(b"</html>")


def test_the_page_keeps_its_inline_styles_and_its_own_script():
    """Without `style-src` the page loses every bit of its formatting, and
    without `script-src` a workbook's sheet tabs stop switching sheets."""
    assert "style-src 'unsafe-inline'" in _PAGE_POLICY
    assert "script-src 'unsafe-inline'" in _PAGE_POLICY


def test_the_frame_is_not_allowed_to_reach_out():
    """Those pages fetch a font CDN, a formula CDN and a WebGL library off the
    vendor's host. A reader opening a room's internal document should not have
    their browser call any of them."""
    assert _PAGE_POLICY.startswith("default-src 'none';")
    assert "http" not in _PAGE_POLICY


def test_the_header_carries_the_sandbox_the_meta_tag_cannot():
    """`sandbox` is a header-only directive; a browser ignores it in a meta tag.
    Without `allow-same-origin` the page lands in an opaque origin, so it can
    reach neither this origin nor anything kept there."""
    assert _PAGE_POLICY_HEADER == _PAGE_POLICY + "; sandbox allow-scripts"
    assert "allow-same-origin" not in _PAGE_POLICY_HEADER


def test_the_policy_attaches_whatever_the_head_tag_looks_like():
    """A page served as HTML is read as HTML, whatever case the tag is in.

    The failure this covers is silent: no meta, so no policy inside the frame,
    and nothing anywhere saying the policy was never applied.
    """
    upper = _with_policy(b"<!DOCTYPE html>\n<HTML><HEAD><TITLE>t</TITLE></HEAD>")
    assert upper.startswith(b"<!DOCTYPE html>\n<HTML><HEAD><meta http-equiv")

    attributed = _with_policy(b'<html><head lang="zh"><title>t</title>')
    assert attributed.startswith(b'<html><head lang="zh"><meta http-equiv')


def test_a_page_with_no_head_is_left_alone():
    """Better unstyled than mangled: inserting a tag a browser would not parse
    where it belongs changes nothing, and guessing at the position would."""
    assert _with_policy(b"<p>no head here</p>") == b"<p>no head here</p>"
