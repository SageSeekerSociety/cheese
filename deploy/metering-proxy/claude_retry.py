"""Replay a rejected request inside mitmproxy, retaining its streaming transport.

The hook is pinned to the image's mitmproxy version and covered by a real proxy
test. Addon response hooks alone cannot retry before sending downstream headers.
"""

import tempfile

ATTEMPTS = {}


class Attempt:
    def __init__(self, flow, account, retry):
        self.flow = flow
        self.account = account
        self.retry = retry
        self.complete = False
        self.replayed = False
        self.body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        self.size = 0

    def tee(self, chunk):
        self.size += len(chunk)
        if self.size <= 64 * 1024 * 1024:
            self.body.write(chunk)
        return chunk

    def close(self):
        self.body.close()


def install():
    from mitmproxy import version
    from mitmproxy.proxy.layers.http import HttpStream, SendHttp
    from mitmproxy.proxy.layers.http._events import (
        RequestData,
        RequestEndOfMessage,
        RequestHeaders,
    )
    from mitmproxy.proxy.layers.http._http2 import Http2Client

    if version.VERSION != "12.1.2":
        raise RuntimeError("Claude retry requires the tested mitmproxy 12.1.2")
    original = HttpStream.send_response
    if getattr(original, "cheese_retry", False):
        return
    replaying = set()
    original_h2 = Http2Client._handle_event

    def h2_event(connection, event):
        if (
            isinstance(event, RequestHeaders)
            and (connection.conn.id, event.stream_id) in replaying
        ):
            # HTTP/2 requires a new upstream stream ID, while the downstream
            # request keeps its original ID. The rejected stream is complete.
            old = connection.our_stream_id.get(event.stream_id)
            if old is not None:
                if not connection.h2_conn.streams[old].closed:
                    raise RuntimeError("cannot replay an open HTTP/2 stream")
                connection.our_stream_id.pop(event.stream_id)
        yield from original_h2(connection, event)

    Http2Client._handle_event = h2_event

    def send_response(stream, already_streamed=False):
        flow = stream.flow
        attempt = ATTEMPTS.get(flow.id)
        if attempt and not already_streamed and flow.response.status_code == 429:
            # retry() records cooldown even if the upload was rejected early
            # or the body exceeds the replay bound. Neither can be replayed.
            if attempt.retry(attempt):
                attempt.replayed = True
                flow.response = None
                if not (yield from stream.make_server_connection()):
                    return
                stream.server_state = stream.state_wait_for_response_headers
                key = (stream.context.server.id, stream.stream_id)
                replaying.add(key)
                try:
                    yield SendHttp(
                        RequestHeaders(
                            stream.stream_id, flow.request, end_stream=False
                        ),
                        stream.context.server,
                    )
                finally:
                    replaying.discard(key)
                attempt.body.seek(0)
                while chunk := attempt.body.read(64 * 1024):
                    yield SendHttp(
                        RequestData(stream.stream_id, chunk), stream.context.server
                    )
                yield SendHttp(
                    RequestEndOfMessage(stream.stream_id), stream.context.server
                )
                return
        yield from original(stream, already_streamed)

    send_response.cheese_retry = True
    HttpStream.send_response = send_response
