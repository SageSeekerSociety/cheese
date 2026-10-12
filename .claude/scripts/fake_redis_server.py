"""Serve fakeredis over TCP: dev-db.sh's Redis on a host that cannot build Valkey.

Started by dev-db.sh only; it writes its own pid to --pidfile once it is bound.
"""

import argparse
import os
import signal
from pathlib import Path

from fakeredis import TcpFakeServer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pidfile", type=Path, required=True)
    args = parser.parse_args()

    server = TcpFakeServer(("127.0.0.1", args.port), server_type="valkey", server_version=(8, 0))
    server.daemon_threads = True
    args.pidfile.write_text(f"{os.getpid()}\n")

    def stop(*_: object) -> None:
        args.pidfile.unlink(missing_ok=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()


if __name__ == "__main__":
    main()
