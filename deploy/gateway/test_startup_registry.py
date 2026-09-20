"""Check readiness and tool-name extraction without model or database access."""

import asyncio
import os

import litellm.llms
from litellm.proxy import proxy_server


async def main():
    proxy_server.premium_user = True
    os.environ['LITELLM_MASTER_KEY'] = 'synthetic-master'
    assert litellm.llms.endpoint_guardrail_translation_mappings is None
    async with proxy_server.proxy_startup_event(proxy_server.app):
        mappings = litellm.llms.endpoint_guardrail_translation_mappings
        assert mappings, 'Gateway accepted requests before tool adapters were loaded'
        from litellm.proxy.guardrails.tool_name_extraction import extract_request_tool_names

        cases = [
            ('/v1/messages', {'tools': [{'name': 'read_file', 'input_schema': {
                'type': 'object', 'properties': {}}}]}),
            ('/v1/chat/completions', {'tools': [{'type': 'function', 'function': {
                'name': 'read_file', 'parameters': {'type': 'object', 'properties': {}}}}]}),
        ]
        for route, data in cases:
            assert set(extract_request_tool_names(data=data, route=route)) == {'read_file'}
            assert extract_request_tool_names(data={}, route=route) == []
            assert litellm.llms.endpoint_guardrail_translation_mappings is mappings
    print('Startup registry and both tool-name formats passed', flush=True)


asyncio.run(main())
