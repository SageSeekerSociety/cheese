"""GitHub REST API request headers assembled in one place.

``github_headers`` builds the ``Authorization`` and ``Accept`` pair shared by
GitHub App token minting and OAuth user lookups.
``api_version=True`` adds ``X-GitHub-Api-Version: 2022-11-28`` only for callers
that already send it, keeping existing request semantics byte-for-byte intact.
"""

GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"


def github_headers(token: str, *, api_version: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": GITHUB_ACCEPT,
    }
    if api_version:
        headers["X-GitHub-Api-Version"] = GITHUB_API_VERSION
    return headers
