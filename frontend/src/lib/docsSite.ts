// Where the docs site is. VITE_DOCS_ORIGIN is a placeholder in the image that
// frontend/docker-entrypoint.sh fills from DOCS_ORIGIN when the container starts:
// the docs' own host ("https://docs.okcheese.com"), or empty when this
// deployment serves them under /docs/ (frontend/nginx/docs/).

/** A page of the docs by its path within the site: `/`, `/quickstart#talk`. */
export function docsUrl(path = '/'): string {
  const origin = (import.meta.env.VITE_DOCS_ORIGIN as string | undefined) ?? ''
  return origin ? `${origin}${path}` : `/docs${path}`
}
