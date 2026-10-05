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
// What differs per deployment is left as a placeholder, filled when the
// frontend container starts (frontend/scripts/docs-mode.sh), so the image names
// no deployment's domain. Set these to build a site for one origin by hand.
//
// The docs' public origin, in absolute links: llms.txt, each .md page, RSS.
export const SITE = process.env.DOCS_SITE ?? '__DOCS_SITE__'
// The platform, for the links that leave the docs (usage, terms, the demo
// stages). Under /docs/ it is the same origin, so the links stay relative.
// Sign-in goes through the backend instead, which knows where the platform is.
export const PLATFORM = process.env.DOCS_PLATFORM ?? (BASE ? '' : '__DOCS_PLATFORM__')
