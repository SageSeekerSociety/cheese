# syntax=docker/dockerfile:1
FROM node:21-slim as base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
WORKDIR /app
RUN corepack enable
# `postinstall` runs `husky install`, which only has something to do in a git
# checkout. The image has no .git (see .dockerignore), so say so outright rather
# than relying on husky's own early return.
ENV HUSKY=0

# The manifests alone, so editing a component does not reinstall the world: this
# layer is reused until the lockfile itself changes. The pnpm store lives on a
# cache mount, so even a lockfile edit fetches only what it added.
FROM base as deps
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

FROM deps as dev
COPY . ./
CMD ["pnpm", "dev"]

FROM deps as build
COPY . ./
ENV VITE_API_BASE_URL=__VITE_API_BASE_URL__
ENV VITE_NEW_API_BASE_URL=__VITE_NEW_API_BASE_URL__
ENV VITE_AI_API_BASE_URL=__VITE_AI_API_BASE_URL__
ENV VITE_CONNECTOR_WS_BASE=__VITE_CONNECTOR_WS_BASE__
# The fusion bundle outgrew Node's default ~2 GB heap — `vite build` OOM-killed
# the CI runner (exit 134, "Reached heap limit"). Raise the old-space cap.
ENV NODE_OPTIONS=--max-old-space-size=4096
RUN pnpm build

FROM nginx:1.25.4 as prod
COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY scripts/check-static-assets.sh /usr/local/bin/check-static-assets
RUN chmod +x /docker-entrypoint.sh /usr/local/bin/check-static-assets
COPY --from=build /app/dist /usr/share/nginx/html
RUN /usr/local/bin/check-static-assets /usr/share/nginx/html
HEALTHCHECK --interval=15s --timeout=8s --start-period=10s --retries=5 \
  CMD /usr/local/bin/check-static-assets /usr/share/nginx/html >/dev/null \
    && curl -fsS http://127.0.0.1:80/ >/dev/null || exit 1
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
