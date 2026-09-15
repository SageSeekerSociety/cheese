# Built and pushed by .github/workflows/build.yml, and started by
# deploy/compose/docker-compose.base.yml. Both are required, and the second is
# the one that gets forgotten: an image nothing builds and an endpoint nothing
# sets are the two ways a service like this silently never runs.
FROM debian:12-slim

# LibreOffice converts Word, PowerPoint and Excel to PDF faithfully, which is the
# whole reason this is a container of its own: it wants a writable profile
# directory and installs ~800MB, so it cannot go in the sandbox image (that one
# is other people's base) and has no business in the backend image.
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
# Debian's CDN is unreachable from here, and this base ships no CA bundle — so
# the mirror is reached over http for the single transaction that installs
# ca-certificates along with everything else.
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' \
      /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-impress libreoffice-calc libreoffice-math \
      fonts-noto-cjk fonts-dejavu-core \
      python3 python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

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
