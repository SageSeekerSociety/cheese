FROM ghcr.io/berriai/litellm@sha256:d1fc1c122e2c1a0cec2b00540b220ff0d2682842758eb8bc64e373a1aedfec7e

COPY patch_stream_timing.py test_stream_timing.py /opt/cheese-gateway/
RUN /app/.venv/bin/python /opt/cheese-gateway/patch_stream_timing.py \
    && LITELLM_LOCAL_MODEL_COST_MAP=True /app/.venv/bin/python /opt/cheese-gateway/test_stream_timing.py
