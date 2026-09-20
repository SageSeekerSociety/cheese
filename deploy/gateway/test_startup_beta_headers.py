"""Download beta configuration during startup and reuse it for requests."""

import asyncio
import os
from unittest.mock import patch

import httpx
from litellm import anthropic_beta_headers_url
from litellm.anthropic_beta_headers_manager import update_headers_with_filtered_beta
from litellm.proxy import proxy_server


async def main():
    proxy_server.premium_user = True
    os.environ['LITELLM_MASTER_KEY'] = 'synthetic-master'
    phase = 'startup'
    downloads = []

    def fetch(url, **kwargs):
        assert url == anthropic_beta_headers_url
        downloads.append(phase)
        return httpx.Response(200, request=httpx.Request('GET', url), json={
            'anthropic': {'supported': 'mapped', 'unsupported': None},
            'provider_aliases': {'alias': 'anthropic'},
        })

    with patch('httpx.get', fetch):
        async with proxy_server.proxy_startup_event(proxy_server.app):
            phase = 'request'
            for provider in ('anthropic', 'alias'):
                headers = {'anthropic-beta': 'supported,unsupported,unknown', 'other': 'kept'}
                assert update_headers_with_filtered_beta(headers, provider) == {
                    'anthropic-beta': 'mapped', 'other': 'kept'}
                assert update_headers_with_filtered_beta({'anthropic-beta': 'unknown'}, provider) == {}
    assert downloads == ['startup'], f'Unexpected configuration downloads: {downloads}'
    print('Beta configuration downloaded before readiness and reused by requests', flush=True)


asyncio.run(main())
