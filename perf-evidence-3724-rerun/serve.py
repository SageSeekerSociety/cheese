"""Serve the unchanged CI artifacts against one isolated local backend."""
import argparse
import asyncio
from pathlib import Path
from aiohttp import ClientSession, WSMsgType, web


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    args = parser.parse_args()
    async with ClientSession() as client:
        runners = []

        def make_handler(directory):
            root = Path(directory).resolve()

            async def handler(request):
                path = request.path
                candidate = (root / path.lstrip('/')).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    return web.FileResponse(candidate, headers={'Cache-Control': 'no-cache'})
                proxy = path.startswith(('/api/', '/collab')) or request.headers.get('Sec-Fetch-Dest') == 'empty' or request.method != 'GET'
                if not proxy:
                    return web.FileResponse(root / 'index.html', headers={'Cache-Control': 'no-cache'})
                backend_path = request.rel_url.path_qs
                target = 'http://127.0.0.1:8902' if path.startswith('/collab') else 'http://127.0.0.1:8081'
                if backend_path.startswith('/api/'):
                    backend_path = backend_path[4:]
                elif backend_path.startswith('/collab'):
                    backend_path = backend_path[7:] or '/'
                url = target + backend_path
                if request.headers.get('Upgrade', '').lower() == 'websocket':
                    blocked = {'host', 'connection', 'upgrade', 'sec-websocket-key', 'sec-websocket-version', 'sec-websocket-extensions'}
                    remote = await client.ws_connect(url, headers={k: v for k, v in request.headers.items() if k.lower() not in blocked})
                    websocket = web.WebSocketResponse()
                    await websocket.prepare(request)

                    async def forward(source, destination):
                        async for message in source:
                            if message.type == WSMsgType.TEXT:
                                await destination.send_str(message.data)
                            elif message.type == WSMsgType.BINARY:
                                await destination.send_bytes(message.data)
                        await destination.close()

                    await asyncio.gather(forward(websocket, remote), forward(remote, websocket))
                    return websocket
                headers = {k: v for k, v in request.headers.items() if k.lower() not in {'host', 'connection', 'content-length', 'accept-encoding'}}
                async with client.request(request.method, url, headers=headers, data=await request.read(), allow_redirects=False) as response:
                    result = web.StreamResponse(status=response.status)
                    for key, value in response.headers.items():
                        if key.lower() not in {'transfer-encoding', 'connection', 'content-encoding', 'content-length'}:
                            result.headers.add(key, value)
                    await result.prepare(request)
                    async for chunk in response.content.iter_any():
                        await result.write(chunk)
                    await result.write_eof()
                    return result

            return handler

        for directory, port in [(args.base, 3000), (args.head, 3001)]:
            app = web.Application()
            app.router.add_route('*', '/{tail:.*}', make_handler(directory))
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, '127.0.0.1', port).start()
            runners.append(runner)
        print('base :3000, head :3001', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            for runner in runners:
                await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
