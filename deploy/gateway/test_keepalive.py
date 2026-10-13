"""An idle keep-alive connection to the gateway outlives uvicorn's 5 s default.

The metering proxy reuses its connection to the gateway for as long as the
caller keeps its own connection open. A gateway that closes idle connections
first races the next request onto a socket it is closing, and that request
fails with "server closed connection" before the model is ever reached.

This starts uvicorn with the arguments LiteLLM's CLI builds from this image's
environment, serves one request, idles past 5 s and sends the next request on
the same socket.
"""

import socket
import threading
import time

import uvicorn
from litellm.proxy.proxy_cli import ProxyInitializationHelpers, run_server

IDLE_S = 6


async def ok(scope, receive, send):
    if scope['type'] != 'http':
        return
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-length', b'2')]})
    await send({'type': 'http.response.body', 'body': b'ok'})


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def ask(conn):
    conn.sendall(b'GET / HTTP/1.1\r\nHost: gateway\r\n\r\n')
    conn.settimeout(5)
    data = b''
    while not data.endswith(b'ok'):
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def main():
    params = run_server.make_context('litellm', ['--config', '/app/config.yaml', '--port', '4000']).params
    port = free_port()
    args = ProxyInitializationHelpers._get_default_unvicorn_init_args(
        '127.0.0.1', port, keepalive_timeout=params['keepalive_timeout']
    )
    args['app'] = ok
    args['log_level'] = 'warning'
    server = uvicorn.Server(uvicorn.Config(**args))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        with socket.create_connection(('127.0.0.1', port)) as conn:
            assert ask(conn).startswith(b'HTTP/1.1 200'), 'first request failed'
            time.sleep(IDLE_S)
            second = ask(conn)
            assert second.startswith(b'HTTP/1.1 200'), (
                f'gateway closed a keep-alive connection idle for {IDLE_S} s: {second!r}'
            )
    finally:
        server.should_exit = True
    print(f'A keep-alive connection idle for {IDLE_S} s still serves a request', flush=True)


main()
