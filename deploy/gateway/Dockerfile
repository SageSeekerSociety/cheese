FROM ghcr.io/berriai/litellm@sha256:e6e1c46cec92ab58b7ff95c790420aba9f05269b662a66c0dd11714171b9c64b

LABEL org.opencontainers.image.title="Cheese LLM gateway"

# Keep idle keep-alive connections open well past the callers' own idle limit.
# Uvicorn's 5 s default closes them first, so a request the metering proxy sends
# on a connection idle for about 5 s lands on a closing socket and fails with
# "server closed connection" (GatewayUnreachable). Callers of the proxy were
# seen idling a connection for up to 195 s; the gateway must outlast that.
# LiteLLM's CLI reads this as --keepalive_timeout.
ENV KEEPALIVE_TIMEOUT=600

# Resolve the pinned telemetry wrapper before request-time auth uses it.
ENV LITELLM_WORKER_STARTUP_HOOKS=litellm.llms:load_guardrail_translation_mappings,litellm.anthropic_beta_headers_manager:reload_beta_headers_config,litellm.integrations.otel.runtime:_otel_runtime

COPY patch_stream_timing.py test_keepalive.py test_stream_timing.py test_provider_http_timing.py test_router_logging.py test_startup_registry.py test_startup_beta_headers.py test_startup_telemetry.py /opt/cheese-gateway/
COPY provider_http_timing.py /app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/provider_http_timing.py
COPY chatgpt_session.py /app/.venv/lib/python3.13/site-packages/cheese_chatgpt_session.py
COPY test_chatgpt_session.py /opt/cheese-gateway/
COPY system_billing_line.py /app/.venv/lib/python3.13/site-packages/cheese_billing_line.py
COPY test_system_billing_line.py /opt/cheese-gateway/
# The base image ships no installer, and image_guard.py needs Pillow. uv is
# mounted for this one step only, so it does not stay in the image.
COPY image_guard.requirements.txt /opt/cheese-gateway/
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.1,source=/uv,target=/usr/local/bin/uv \
    uv pip install --python /app/.venv/bin/python --require-hashes --no-deps \
        -r /opt/cheese-gateway/image_guard.requirements.txt
COPY image_guard.py /app/.venv/lib/python3.13/site-packages/cheese_image_guard.py
COPY test_image_guard.py /opt/cheese-gateway/
COPY retry_stream.py /app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/retry_stream.py
COPY test_retry_stream.py /opt/cheese-gateway/
COPY patch_deepseek_images.py test_deepseek_images.py test_deepseek_route_order.py test_deepseek_session_affinity.py patch_empty_anthropic_text.py test_empty_anthropic_text.py patch_responses_stream_errors.py test_responses_stream_errors.py check_config.py config.yaml /opt/cheese-gateway/
RUN /app/.venv/bin/python /opt/cheese-gateway/patch_stream_timing.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_deepseek_images.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_empty_anthropic_text.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_responses_stream_errors.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/check_config.py /opt/cheese-gateway/config.yaml \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_images.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_route_order.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_session_affinity.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_empty_anthropic_text.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_responses_stream_errors.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_stream_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_keepalive.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_provider_http_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_retry_stream.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_chatgpt_session.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_system_billing_line.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_image_guard.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_router_logging.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_registry.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_beta_headers.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_telemetry.py
