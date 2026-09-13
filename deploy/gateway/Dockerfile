FROM ghcr.io/berriai/litellm@sha256:d1fc1c122e2c1a0cec2b00540b220ff0d2682842758eb8bc64e373a1aedfec7e

# Initialize tool adapters and beta configuration before accepting requests.
ENV LITELLM_WORKER_STARTUP_HOOKS=litellm.llms:load_guardrail_translation_mappings,litellm.anthropic_beta_headers_manager:reload_beta_headers_config

COPY patch_stream_timing.py test_stream_timing.py test_provider_http_timing.py test_router_logging.py test_startup_registry.py test_startup_beta_headers.py /opt/cheese-gateway/
COPY provider_http_timing.py /app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/provider_http_timing.py
RUN /app/.venv/bin/python /opt/cheese-gateway/patch_stream_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_stream_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_provider_http_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_router_logging.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_registry.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_beta_headers.py
