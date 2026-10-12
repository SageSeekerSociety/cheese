"""Purpose-bound tokens (`app/core/scoped_jwt.py`): each purpose signs with its
own derived key, and the three callers mint exactly the tokens they minted
before the key derivation moved here — tokens in flight stay valid."""

import hashlib
import hmac
import uuid

import jwt
import pytest

from app.api import preview_host
from app.core import scoped_jwt
from app.core.config import settings
from app.domain.docs_site import access
from app.domain.site import hosting

# The domains as the wire format fixes them (`docs/manual/dev/sites.md`):
# changing one signs every outstanding token of that purpose out.
DOMAINS = {
    preview_host: b"cheese:topic-preview:v1",
    hosting: b"cheese:site-read:v1",
    access: b"cheese:docs-sign-in:v1",
}


def _key_as_before(domain: bytes) -> bytes:
    return hmac.digest(settings.jwt_secret.encode(), domain, hashlib.sha256)


@pytest.fixture
def hosts(monkeypatch):
    monkeypatch.setattr(settings, "sites_domain", "sites.localhost")
    monkeypatch.setattr(settings, "sites_scheme", "http")
    monkeypatch.setattr(settings, "sites_port", None)
    monkeypatch.setattr(settings, "frontend_url", "http://platform.localhost")
    monkeypatch.setattr(settings, "docs_origin", "https://docs.example.test")


def test_each_caller_keeps_its_domain():
    for module, domain in DOMAINS.items():
        assert module.TOKEN_DOMAIN == domain
    assert len(set(DOMAINS.values())) == len(DOMAINS)


def test_the_key_is_the_one_derived_before():
    for domain in DOMAINS.values():
        assert scoped_jwt.scoped_key(domain) == _key_as_before(domain)


def _minted():
    topic, project = uuid.uuid4(), uuid.uuid4()
    return {
        preview_host: preview_host.mint_preview_token(
            topic, "alice", purpose="preview-grant", ttl=60
        ),
        hosting: hosting.mint_site_token(
            project, "alice", purpose="site-grant", ttl=60
        ),
        access: access.mint_grant(7, uuid.uuid4()),
    }


def test_minted_tokens_are_byte_for_byte_the_old_ones(hosts):
    """Re-signing a token's own claims the way the callers used to must give
    back the very same string."""
    for module, token in _minted().items():
        claims = jwt.decode(token, options={"verify_signature": False})
        before = jwt.encode(claims, _key_as_before(DOMAINS[module]), algorithm="HS256")
        assert token == before, module.__name__


def test_a_token_verifies_only_for_its_own_purpose(hosts):
    claims = {"sub": "alice", "aud": "https://a.test", "iat": 0, "exp": 2**40}
    token = scoped_jwt.sign(claims, domain=b"cheese:one:v1")
    assert (
        scoped_jwt.verify(
            token, domain=b"cheese:one:v1", audience="https://a.test", require=["sub"]
        )
        == claims
    )
    with pytest.raises(jwt.InvalidSignatureError):
        scoped_jwt.verify(
            token, domain=b"cheese:two:v1", audience="https://a.test", require=[]
        )
    with pytest.raises(jwt.InvalidAudienceError):
        scoped_jwt.verify(
            token, domain=b"cheese:one:v1", audience="https://b.test", require=[]
        )
    with pytest.raises(jwt.MissingRequiredClaimError):
        scoped_jwt.verify(
            token, domain=b"cheese:one:v1", audience="https://a.test", require=["jti"]
        )
