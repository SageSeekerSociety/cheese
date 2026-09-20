"""Project webhook keys cannot authenticate a deployment's relay connection."""

import hashlib
import hmac
import uuid


def project_secret(deployment_secret: str, project_id: uuid.UUID) -> str:
    return hmac.new(
        deployment_secret.encode(),
        f"forge-webhook:{project_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
