interface ImportMetaEnv {
  readonly API_BASE_URL: string;
  readonly NEW_API_BASE_URL: string;
  /** PDF 上传/解析请求的超时时间（毫秒），默认 300000（5 分钟） */
  readonly VITE_PDF_UPLOAD_TIMEOUT_MS: string;
  /** The commit the web build was made from (frontend/Dockerfile); absent in a local build. */
  readonly VITE_WEB_BUILD?: string;
  /** The docs site's own origin, filled in at container start (src/lib/docsSite.ts); empty when the platform serves them under /docs/. */
  readonly VITE_DOCS_ORIGIN?: string;
}
