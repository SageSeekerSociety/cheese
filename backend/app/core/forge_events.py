"""Scoped credentials for project webhooks and GitHub event subscriptions."""

import hashlib
import hmac
import time
import uuid

import jwt


def subscription_assertion(
    *,
    app_id: int,
    private_key: str,
    deployment: str,
    repositories: list[tuple[int, str]],
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": str(app_id),
            "aud": f"forge-events:{deployment}",
            "iat": now,
            "exp": now + 300,
            "repositories": repositories,
        },
        private_key,
        algorithm="RS256",
    )


def verify_subscriptions(
    assertion: str,
    *,
    app_id: int,
    public_key: str,
    deployment: str,
) -> tuple[list[tuple[int, str]], float]:
    claims = jwt.decode(
        assertion,
        public_key,
        algorithms=["RS256"],
        issuer=str(app_id),
        audience=f"forge-events:{deployment}",
        options={"require": ["iss", "aud", "iat", "exp", "repositories"]},
    )
    rows = claims["repositories"]
    if not isinstance(rows, list) or claims["exp"] - claims["iat"] > 300:
        raise ValueError("Invalid subscription claims")
    repositories = []
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or type(row[0]) is not int
            or row[0] <= 0
            or not isinstance(row[1], str)
            or len(row[1]) > 512
            or row[1].count("/") != 1
            or not all(row[1].split("/"))
        ):
            raise ValueError("Invalid subscription repository")
        repositories.append((row[0], row[1].lower()))
    return repositories, float(claims["exp"])


def project_secret(deployment_secret: str, project_id: uuid.UUID) -> str:
    return hmac.new(
        deployment_secret.encode(),
        f"forge-webhook:{project_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
