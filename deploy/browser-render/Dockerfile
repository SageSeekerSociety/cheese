# Built and pushed by .github/workflows/build.yml, like the other images.
# It was not, for the first six months it existed: docker-compose.base.yml
# pulled `browser-render:${IMAGE_TAG}` from a registry path nothing ever wrote
# to, and both the pull and the `up` are deliberately `|| log WARNING` so that a
# browser which will not start cannot hold back a backend that would have
# served. The result read as a working deployment on every check we have.
#
# A small Python base plus Chromium, NOT mcr.microsoft.com/playwright/python.
# That image is the obvious choice and it was the first one here, because it
# ships the browser together with the ~20 system libraries Chromium needs, which
# is where a browser container usually fails: apt-get succeeds, then Chromium
# refuses to start over a missing libnss and the error names a file rather than
# the cause. Two things make it the wrong trade here:
#
#   - It carries Firefox and WebKit as well, and this service drives only
#     Chromium — 755MB of one layer, for browsers nothing launches.
#   - It comes from mcr.microsoft.com, and the BuildKit config in build.yml
#     mirrors `docker.io` only, so that pull crosses the same network the
#     workflow's own header calls intermittently unreachable. Measured: 18
#     minutes for this job on CI against 1m14s for the sandbox image, and the
#     box has ONE runner slot, so the backend and frontend images queued behind
#     all 18 of them.
#
# `playwright install --with-deps chromium` is not the hand-installation the old
# comment warned about — it is Playwright's own command, which knows the exact
# package list for this distribution. Measured end to end on a developer box:
# 2m38s total, against a base-image pull alone that was tracking to two hours.
FROM python:3.12-slim-bookworm

WORKDIR /app

# Debian's CDN is unreachable from the machines that build this; the mirror
# substitution persists into the layers below, which is what lets
# `playwright install --with-deps` reach its packages later.
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' \
      /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Browsers go somewhere every user can read. The default is $HOME/.cache, which
# would put them under root's home and leave the unprivileged user this image
# runs as unable to launch anything it just installed.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# crawl4ai brings the extraction and the browser driving; the rest is the
# service shell. Pinned so a rebuild does not silently change extraction.
#
# `playwright` is pinned too, and that pin is load-bearing rather than tidiness:
# crawl4ai asks for `playwright>=1.49` with no upper bound, so pip takes whatever
# is newest. Playwright locates its browsers by a revision tied to its own
# version, so an unpinned upgrade makes the browser installed below stop
# counting as installed, and the service reports every page as unreachable.
ARG PLAYWRIGHT_VERSION=1.49.0
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
      "playwright==${PLAYWRIGHT_VERSION}" \
      "crawl4ai==0.7.4" \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.32"

# The browser and the system libraries it needs, in one command that knows which
# ones those are.
RUN playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# A build that ends without a usable browser must not ship, so the browser is
# checked rather than assumed: this resolves the executable playwright would
# actually launch, which is the thing the version pin above protects.
RUN python -c "\
import pathlib, sys; \
from playwright.sync_api import sync_playwright; \
p = sync_playwright().start(); \
exe = pathlib.Path(p.chromium.executable_path); \
p.stop(); \
sys.exit(f'chromium missing at {exe}') if not exe.exists() else print(f'chromium ok: {exe}')"

COPY server.py /app/server.py

# Root is the default and Chromium refuses to run as root without --no-sandbox.
# The service passes --no-sandbox already, but dropping privileges is the part
# that matters if a page ever finds a renderer bug.
RUN useradd -m render && chown -R render /app
USER render

# crawl4ai's cache directory and its SQLite database, prepared once at build time
# rather than on the first request.
#
# It runs HERE, after the USER switch, because `crawl4ai-setup` builds that tree
# under `Path.home()`. Run as root it made /root/.crawl4ai, which the service —
# running as `render`, HOME=/home/render — never opens. The step was in this
# image from the start and had never once done the thing it was added for.
#
# CRAWL4AI_MODE=api is crawl4ai's own switch for "do the setup, skip the browser
# install", and without it this step downloads a SECOND browser: `patchright`, a
# playwright fork that arrives as a transitive dependency, carries its own
# revision (v1161 against playwright 1.49's v1148) and installs with `--force`.
# That is 76 seconds of every build and several hundred MB of a layer, for an
# engine this service never launches — it drives plain playwright.
RUN CRAWL4AI_MODE=api crawl4ai-setup

EXPOSE 8900
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8900/healthz',timeout=3)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8900"]
