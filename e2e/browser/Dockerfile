FROM mirror.gcr.io/library/node:22.23.2-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9

ARG PLAYWRIGHT_VERSION
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# CI uses Chromium headless shell; the other browsers would consume runner cache space.
RUN npm install --global --omit=dev "playwright@${PLAYWRIGHT_VERSION}" \
 && playwright install --with-deps --only-shell chromium \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*

CMD ["playwright", "run-server", "--host", "0.0.0.0", "--port", "3000"]
