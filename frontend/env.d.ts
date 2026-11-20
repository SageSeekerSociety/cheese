interface ImportMetaEnv {
  readonly API_BASE_URL: string;
  readonly NEW_API_BASE_URL: string;
  /** The commit the web build was made from (frontend/Dockerfile); absent in a local build. */
  readonly VITE_WEB_BUILD?: string;
  /** The docs site's own origin, filled in at container start (src/lib/docsSite.ts); empty when the platform serves them under /docs/. */
  readonly VITE_DOCS_ORIGIN?: string;
  /** The API generation this build speaks (backend/api-generation, vite.config.ts); absent in builds made by the other vite configs. */
  readonly VITE_API_GENERATION?: string;
}
