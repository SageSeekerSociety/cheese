FROM node:21-slim as base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
WORKDIR /app
RUN corepack enable

FROM base as dev
COPY . ./
RUN pnpm install --frozen-lockfile
CMD ["pnpm", "dev"]

FROM base as build
COPY . ./
RUN pnpm install --frozen-lockfile
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
RUN chmod +x /docker-entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
