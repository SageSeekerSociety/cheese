"""Host timer entrypoint, executed inside a backend container with its credentials."""

import json
from urllib.request import Request, urlopen

from app.core.sandbox_auth import SANDBOX_TOKEN


def main() -> None:
    request = Request(
        "http://127.0.0.1:8081/sandbox/storage-sweep",
        data=b"",
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        answer = json.load(response)
    if answer.get("code") != 200:
        raise RuntimeError("Backend did not accept the cleanup trigger")


if __name__ == "__main__":
    main()
