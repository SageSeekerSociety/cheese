FROM ghcr.io/berriai/litellm@sha256:e6e1c46cec92ab58b7ff95c790420aba9f05269b662a66c0dd11714171b9c64b

LABEL org.opencontainers.image.title="Cheese LLM gateway"

# Keep idle keep-alive connections open well past the callers' own idle limit.
# Uvicorn's 5 s default closes them first, so a request the metering proxy sends
# on a connection idle for about 5 s lands on a closing socket and fails with
# "server closed connection" (GatewayUnreachable). Callers of the proxy were
# seen idling a connection for up to 195 s; the gateway must outlast that.
# LiteLLM's CLI reads this as --keepalive_timeout.
ENV KEEPALIVE_TIMEOUT=600

# Resolve the pinned telemetry wrapper before request-time auth uses it, and
# wrap DeepSeek's Messages transformation (`deepseek_thinking.py`) and the
# router's outermost call (`provider_credit.py`) before the proxy serves a
# request.
ENV LITELLM_WORKER_STARTUP_HOOKS=litellm.llms:load_guardrail_translation_mappings,litellm.anthropic_beta_headers_manager:reload_beta_headers_config,litellm.integrations.otel.runtime:_otel_runtime,cheese_deepseek_thinking:install,cheese_provider_credit:install

COPY patch_stream_timing.py test_keepalive.py test_stream_timing.py test_provider_http_timing.py test_router_logging.py test_startup_registry.py test_startup_beta_headers.py test_startup_telemetry.py /opt/cheese-gateway/
COPY provider_http_timing.py /app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/provider_http_timing.py
COPY opencode_free.py /app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/opencode_free.py
COPY test_opencode_free.py /opt/cheese-gateway/
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
COPY deepseek_thinking.py /app/.venv/lib/python3.13/site-packages/cheese_deepseek_thinking.py
COPY test_deepseek_thinking.py /opt/cheese-gateway/
COPY upstream_headers.py /app/.venv/lib/python3.13/site-packages/cheese_upstream_headers.py
COPY test_upstream_headers.py test_chat_route.py /opt/cheese-gateway/
COPY no_explicit_cache.py /app/.venv/lib/python3.13/site-packages/cheese_no_explicit_cache.py
COPY test_no_explicit_cache.py /opt/cheese-gateway/
COPY spend_cap.py /app/.venv/lib/python3.13/site-packages/cheese_spend_cap.py
COPY test_spend_cap.py /opt/cheese-gateway/
COPY provider_credit.py /app/.venv/lib/python3.13/site-packages/cheese_provider_credit.py
COPY test_provider_credit.py /opt/cheese-gateway/
COPY qwen_asr.py /app/.venv/lib/python3.13/site-packages/cheese_qwen_asr.py
COPY test_qwen_asr.py /opt/cheese-gateway/
COPY web_search.py /app/.venv/lib/python3.13/site-packages/cheese_web_search.py
COPY deepseek_search.py /app/.venv/lib/python3.13/site-packages/cheese_deepseek_search.py
COPY test_web_search.py /opt/cheese-gateway/
COPY retry_stream.py /app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/retry_stream.py
COPY test_retry_stream.py /opt/cheese-gateway/
COPY patch_deepseek_images.py test_deepseek_images.py test_deepseek_route_order.py test_deepseek_session_affinity.py patch_empty_anthropic_text.py test_empty_anthropic_text.py patch_responses_stream_errors.py test_responses_stream_errors.py patch_responses_done_args.py test_responses_done_args.py patch_anthropic_stream_errors.py test_anthropic_stream_errors.py patch_gemini_generation.py test_gemini_generation.py patch_hosted_vllm_reasoning.py patch_deepseek_search_cost.py test_deepseek_search_cost.py patch_deepseek_search_provider.py test_deepseek_search.py check_config.py config.yaml test_rows.yaml /opt/cheese-gateway/
RUN /app/.venv/bin/python /opt/cheese-gateway/patch_stream_timing.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_deepseek_images.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_empty_anthropic_text.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_responses_stream_errors.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_responses_done_args.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_anthropic_stream_errors.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_gemini_generation.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_hosted_vllm_reasoning.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_deepseek_search_cost.py \
    && /app/.venv/bin/python /opt/cheese-gateway/patch_deepseek_search_provider.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/check_config.py /opt/cheese-gateway/config.yaml \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_images.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_route_order.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_session_affinity.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_search_cost.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_search.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_empty_anthropic_text.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_responses_stream_errors.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_responses_done_args.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_anthropic_stream_errors.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_stream_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_keepalive.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_provider_http_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_opencode_free.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_retry_stream.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_chatgpt_session.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_system_billing_line.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_image_guard.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_deepseek_thinking.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_upstream_headers.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_chat_route.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_gemini_generation.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_no_explicit_cache.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_spend_cap.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_provider_credit.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_qwen_asr.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_web_search.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_router_logging.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_registry.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_beta_headers.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS=True /app/.venv/bin/python /opt/cheese-gateway/test_startup_telemetry.py
