# Built and pushed by .github/workflows/build.yml, and started by
# deploy/compose/docker-compose.base.yml. Both are required, and the second is
# the one that gets forgotten: an image nothing builds and an endpoint nothing
# sets are the two ways a service like this silently never runs.
FROM debian:12-slim

# LibreOffice converts Word, PowerPoint and Excel to PDF faithfully, which is the
# whole reason this is a container of its own: it wants a writable profile
# directory and installs ~800MB, so a room cannot install it (it has no root)
# and it has no business in the backend image.
#
# `libreoffice-math` is in this list for a reason that costs a reader nothing to
# keep and a user everything to lose. Without it LibreOffice still converts, still
# exits 0, still writes a PDF of a normal size — and every equation is GONE.
# Measured on a paper carrying four OMML equations: the display equation came out
# as blank space and an inline `\lambda` vanished mid-sentence, leaving
# "其中  为正则化系数". No automated check can see that; only a person reading
# the page can.
#
# `fonts-noto-cjk` is the same failure one layer down: with no CJK face every
# Chinese character renders as an empty box, and the exit code is still 0.
#
# Keep the base image's Debian sources for the transaction that installs
# ca-certificates along with the document tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-impress libreoffice-calc libreoffice-math \
      fonts-noto-cjk fonts-dejavu-core \
      python3 python3-pip ca-certificates curl libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

# OfficeCLI turns a Word file, a workbook or a deck into one HTML page. That page
# is what a preview shows when a reader asks for the web view, and it is the only
# way any of these formats arrives on screen with its charts, its table fills and
# its element addresses intact — a cell keeps `/数据/B2` on it, which is what
# makes "点这一格" mean the same cell to the agent that wrote it.
#
# It sits here rather than in the backend image for the reason this container
# exists at all: it is a document engine (35MB, .NET runtime included), it reads
# every file a room produces, and the API image should not carry one.
#
# Pinned by version and by the digest from that release's SHA256SUMS, because the
# bytes sit behind a URL nobody here controls: `latest` would let a running
# deployment start executing a different binary without anyone choosing it.
# Bumping the version means editing both lines.
ARG OFFICECLI_VERSION=1.0.155
ARG OFFICECLI_SHA256=1a5da5cb7e9d995db7a6560beb7466760c79617648f72e65f4ce55897c407f67
RUN curl -fsSL -o /usr/local/bin/officecli \
      "https://github.com/iOfficeAI/OfficeCLI/releases/download/v${OFFICECLI_VERSION}/officecli-linux-x64" \
 && echo "${OFFICECLI_SHA256}  /usr/local/bin/officecli" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/officecli

RUN pip3 install --break-system-packages --no-cache-dir \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.32"

WORKDIR /app
COPY server.py /app/server.py

# Not root: soffice has no reason to be, and the per-request profile directories
# it writes live under this user's own temp space.
RUN useradd -m office && chown -R office /app
USER office

EXPOSE 8901
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8901/healthz',timeout=3)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8901"]
