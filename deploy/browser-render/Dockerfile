# Playwright's own image, not a bare python one: it ships the browser AND the
# system libraries Chromium needs (about 20 shared objects). Installing those by
# hand is where a browser container usually fails — apt-get succeeds, then
# Chromium refuses to start with a missing libglib/libnss and the error names a
# file, not the cause.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# crawl4ai brings the extraction and the browser driving; the rest is the
# service shell. Pinned so a rebuild does not silently change extraction.
RUN pip install --no-cache-dir \
      "crawl4ai==0.7.4" \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.32"

# The image already carries Chromium. This only writes crawl4ai's own database
# and config; it does NOT re-download a browser.
RUN crawl4ai-setup || true

COPY server.py /app/server.py

# Root is the default in this image and Chromium refuses to run as root without
# --no-sandbox. The service passes --no-sandbox already, but dropping privileges
# is the part that matters if a page ever finds a renderer bug.
RUN useradd -m render && chown -R render /app
USER render

EXPOSE 8900
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8900/healthz',timeout=3)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8900"]
