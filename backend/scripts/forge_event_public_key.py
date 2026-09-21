"""Export the configured GitHub App's public verification key for the relay."""

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.core.config import settings


def public_configuration() -> dict:
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return {}
    key = serialization.load_pem_private_key(
        Path(settings.github_app_private_key_path).read_bytes(), password=None
    )
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("GitHub App signing requires an RSA key")
    return {
        "app_id": settings.github_app_id,
        "public_key": key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode(),
    }


if __name__ == "__main__":
    print(json.dumps(public_configuration()))
