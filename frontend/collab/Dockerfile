# syntax=docker/dockerfile:1
# The collaboration service (collab/). Built from the frontend package because
# that is where its schema lives: the service runs the editors' own document
# schema (src/lib/docSchema), and the two must never be built from different
# dependency versions. The image keeps only the bundle.
FROM node:22-slim AS build
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile
COPY collab ./collab
COPY src/lib/docSchema ./src/lib/docSchema
RUN pnpm run build:collab

FROM node:22-slim
WORKDIR /app
COPY --from=build /app/dist-collab/main.mjs ./main.mjs
USER node
ENV PORT=8902
EXPOSE 8902
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD node -e "fetch('http://127.0.0.1:8902/healthz').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"
CMD ["node", "main.mjs"]
