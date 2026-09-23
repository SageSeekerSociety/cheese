FROM mitmproxy/mitmproxy:12.1.2@sha256:0d7b2a14b4a71a908cd6f31b8f50e29bbc5c8720fe6caa706acf782da558e5c7
COPY billing_addon.py cheese_billing_core.py control_answers.json healthcheck.py /addons/
RUN python -m py_compile /addons/billing_addon.py /addons/cheese_billing_core.py /addons/healthcheck.py
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 CMD ["python", "/addons/healthcheck.py"]
