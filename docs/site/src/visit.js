// The docs site's one beacon: a page load is a visit, counted once per reader
// per UTC day.
//
// The docs are static files — nothing on the server sees a page load — so the
// page has to say so itself, and it has to be the lightest thing on the page.
// Four properties, each of which decides a line below:
//
//   * **Fire and forget.** The reader already has the page; there is nobody to
//     show a failure to. Every error path here ends in silence (a rejected
//     `fetch`, a refused `localStorage`, a 500 from the backend) — a broken
//     counter must not become something the reader sees.
//   * **No IP, no user agent, no trail.** We send two fields: `visitor` (a
//     random string this browser made up and keeps in localStorage) and `page`
//     (the slug of the page they landed on). The backend stores those and
//     nothing else — so adding a header here would be putting it in the row.
//   * **Signed in is better, and optional.** A reader signed in to the docs
//     carries the site's own sign-in cookie, and the backend counts the
//     account instead of the browser string, so the same person on two
//     browsers is one visitor (the report asks 「有多少人来了」, and a person is
//     one person). Nothing is done to get signed in for this: a reader without
//     the cookie is simply an anonymous visit.
//   * **Once a day is the backend's job.** We send on every page load; the row
//     is keyed `(day, visitor)` and the backend keeps the first one. That is
//     where 「记的是今天落地的第一页」 comes from — and it is why there is no
//     day bookkeeping here (a client that remembers what it sent is wrong the
//     first time the tab is duplicated or storage is cleared).
const ENDPOINT = '/api/docs/visit'
const KEY = 'cheese:docs-visitor'

// What the backend will accept as an anonymous id (`docs_site/visits.py`
// `_VISITOR`). Checked here too: a value we know will be dropped is a request
// not worth sending.
const VISITOR = /^[A-Za-z0-9_-]{8,64}$/

// What it will accept as a page slug. The same bound matters on this side
// because the slug is the reader's own URL — a slug outside this shape is one
// we do not want in a grouping key.
const SLUG = /^[a-z0-9-]{1,64}$/

// Kinds that are somebody's page. `dev-gate` is the door to the developer docs
// (not a public page) and `404` is a URL nobody meant to visit — neither is a
// visit to the docs, and counting them would make the report answer a question
// it was not asked.
const COUNTED = new Set(['doc', 'home', 'changelog', 'download'])

function store() {
  try {
    return localStorage
  } catch {
    // Storage can be refused outright (some privacy modes throw on access).
    return null
  }
}

/** 32 characters of randomness, from the platform's CSPRNG when there is one. */
function randomKey() {
  const crypto = globalThis.crypto
  if (crypto?.getRandomValues) {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
  }
  // No CSPRNG (an old browser, a stripped test environment). A weaker id is
  // still not an identity, and it is still per-browser; the alternative is not
  // counting anonymous readers at all.
  return Math.random().toString(36).slice(2).padEnd(24, '0').slice(0, 24)
}

/** This browser's anonymous id, creating it on first use; '' when it cannot. */
export function visitorKey(storage = store()) {
  if (!storage) return ''
  let key = ''
  try {
    key = storage.getItem(KEY) || ''
  } catch {
    return ''
  }
  if (VISITOR.test(key)) return key
  key = randomKey()
  try {
    storage.setItem(KEY, key)
  } catch {
    // Not stored: this visit is anonymous and the next one gets a new id. The
    // storage refusal is the reader's setting, not a failure to report.
  }
  return key
}

/** The slug to record for a page, or null when there is none to record.
 *
 * Developer pages have slugs like any other page, but they are behind the admin
 * gate — their names are not public, so the visit is counted and the page is
 * left blank. `null` reaches the report as a dash, which is the true statement.
 */
export function pageSlug(page) {
  if (!page || page.section === 'dev' || page.dev) return null
  return typeof page.slug === 'string' && SLUG.test(page.slug) ? page.slug : null
}

/** The two fields the backend gets, or null when this page is not a visit. */
export function beaconBody(page, { storage = store() } = {}) {
  if (!COUNTED.has(page?.kind)) return null
  return { visitor: visitorKey(storage), page: pageSlug(page) }
}

/** Send it. Resolves either way; the caller never has to handle a rejection. */
export function recordVisit(page, { storage, send } = {}) {
  return Promise.resolve()
    .then(() => {
      const body = beaconBody(page, { storage: storage === undefined ? store() : storage })
      if (!body) return null
      const post = send || ((url, init) => fetch(url, init))
      return post(ENDPOINT, {
        method: 'POST',
        // The sign-in cookie, if there is one, rides along; see the header comment.
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'same-origin',
        // The reader may leave the page before this lands; keepalive lets the
        // request outlive the document without delaying it.
        keepalive: true,
      })
    })
    .catch(() => null)
}
