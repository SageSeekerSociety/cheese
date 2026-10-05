// Where this build of the site is served, and where it links out to.
//
// One source, two builds (build.mjs reads these from the environment):
//
//   DOCS_BASE=/docs (default)  the platform serves the site under /docs/, which is
//                              what a deployment with no docs host of its own uses.
//   DOCS_BASE= (empty)         the site is the root of its own host, docs.okcheese.com.
//
// The frontend image carries both and its nginx serves one (frontend/nginx/).
// The browser bundle gets these as constants (build.mjs passes them to esbuild).
export const BASE = process.env.DOCS_BASE ?? '/docs'
// The origin in absolute links: llms.txt, each page's .md twin, the RSS feed.
export const SITE = process.env.DOCS_SITE ?? (BASE ? 'https://okcheese.com' : 'https://docs.okcheese.com')
// The platform, for the links that leave the docs (usage, terms, the demo
// stages). Under /docs/ it is the same origin, so the links stay relative.
// Sign-in goes through the backend instead, which knows where the platform is.
export const PLATFORM = process.env.DOCS_PLATFORM ?? (BASE ? '' : 'https://okcheese.com')
