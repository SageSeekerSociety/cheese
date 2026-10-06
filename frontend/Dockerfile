# syntax=docker/dockerfile:1
FROM node:22-slim as base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
WORKDIR /app
RUN corepack enable

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
ENV VITE_DOCS_ORIGIN=__VITE_DOCS_ORIGIN__
# The commit this build is made from, shown in the desktop app's 关于 dialog.
ARG GIT_SHA=
ENV VITE_WEB_BUILD=$GIT_SHA
# The build needs under 2 GB of Node heap (Vite 8: Rolldown bundles, Oxc
# minifies). Node's own limit depends on the machine's memory and can land
# just under that, so the cap is set, with room to grow.
ENV NODE_OPTIONS=--max-old-space-size=4096
RUN pnpm build

FROM nginx:1.25.4 as prod
COPY nginx.conf /etc/nginx/nginx.conf
# The docs' two ways of being served; docker-entrypoint.sh picks one at start.
COPY nginx/docs /etc/nginx/docs-modes
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY scripts/check-static-assets.sh /usr/local/bin/check-static-assets
COPY scripts/docs-mode.sh /usr/local/bin/docs-mode
RUN chmod +x /docker-entrypoint.sh /usr/local/bin/check-static-assets /usr/local/bin/docs-mode
COPY --from=build /app/dist /usr/share/nginx/html
# The docs built for a host of their own (DOCS_BASE=, build.yml), outside the
# html root so no platform path reaches it.
COPY docs-host /usr/share/nginx/docs-host
RUN /usr/local/bin/check-static-assets /usr/share/nginx/html
HEALTHCHECK --interval=15s --timeout=8s --start-period=10s --retries=5 \
  CMD /usr/local/bin/check-static-assets /usr/share/nginx/html >/dev/null \
    && curl -fsS http://127.0.0.1:80/ >/dev/null || exit 1
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
