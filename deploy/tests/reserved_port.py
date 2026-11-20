"""A loopback port a test can hand to a server it is about to start.

Binding port 0 and closing the socket at once gives back a number the kernel is
free to hand to the very next bind(0) — on Linux about one run in a thousand of
six such picks repeats a number — so a test that starts two servers on two of
those numbers can give both the same one, and the second never starts.

reserve_port() leaves the port in TIME_WAIT instead: it accepts one connection
to itself and closes the accepted end first. Until TIME_WAIT expires (60 s on
Linux) no bind(0) on the machine, a later reserve_port() included, is given the
number, while a server that sets SO_REUSEADDR before binding — nginx,
http.server, asyncio — takes it on 127.0.0.1 or 0.0.0.0. This is the technique
of Yelp's ephemeral-port-reserve; these suites run on the system python, which
cannot install it.
"""

import socket


def reserve_port() -> int:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname()):
            accepted, _ = listener.accept()
            accepted.close()
        return listener.getsockname()[1]
