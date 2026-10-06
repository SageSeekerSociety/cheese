// One beacon per docs page load, and what it is allowed to carry.
//
//     node docs/site/test/visit-beacon.test.mjs                    (builds into a temp dir)
//     OUT=/tmp/docs-visit node docs/site/test/visit-beacon.test.mjs  (uses what is there)
//
// The beacon is the only thing on the docs site that writes to the backend, and
// the only thing a stranger's page load can make the platform record. What is
// worth checking is therefore not that it works but that it stays the size it is
// supposed to be: two fields, on the pages that are public, once per page load,
// and silent in every direction it can fail. Plus the one thing a source-level
// test cannot see — that the shipping bundle still carries the call.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.dirname(HERE)

const failures = []
let checks = 0
const ok = (yes, what) => {
  checks++
  if (!yes) failures.push(what)
}
const eq = (got, want, what) => ok(String(got) === String(want), `${what}: got «${got}», wanted «${want}»`)
const has = (hay, needle, what) => ok(String(hay).includes(needle), `${what}: «${needle}» is not in it`)
const hasNot = (hay, needle, what) => ok(!String(hay).includes(needle), `${what}: «${needle}» is in it`)

const { beaconBody, pageSlug, recordVisit, visitorKey } = await import(`${path.join(SITE, 'src/visit.js')}?v=${Math.random()}`)

/** A localStorage stand-in. `writes` records what the module tried to keep. */
function fakeStore(seed = {}) {
  const map = { ...seed }
  const writes = []
  return {
    writes,
    getItem: (k) => (k in map ? map[k] : null),
    setItem: (k, v) => {
      writes.push([k, v])
      map[k] = v
    },
  }
}

/** A fetch stand-in that records the calls and answers 204. */
function spy(answer = { status: 204 }) {
  const calls = []
  const send = (url, init) => {
    calls.push({ url, init })
    if (answer.throws) return Promise.reject(new Error('offline'))
    return Promise.resolve(answer)
  }
  send.calls = calls
  return send
}

const DOC = { kind: 'doc', section: 'user', slug: 'quickstart', title: '快速开始' }

// ---------- the two fields, and nothing else ----------
{
  const send = spy()
  const store = fakeStore()
  await recordVisit(DOC, { send, storage: store })

  eq(send.calls.length, 1, 'a public doc page sends exactly one beacon')
  const { url, init } = send.calls[0]
  eq(url, '/api/docs/visit', 'it goes to the endpoint the backend mounts')
  eq(init.method, 'POST', 'as a POST')
  const body = JSON.parse(init.body)
  eq(Object.keys(body).sort().join(','), 'page,visitor', 'the body carries the visitor and the page, and nothing else')
  eq(body.page, 'quickstart', 'the page is the slug the reader landed on')
  ok(/^[A-Za-z0-9_-]{8,64}$/.test(body.visitor), `the visitor id is what the backend accepts: «${body.visitor}»`)
  has(init.headers['Content-Type'], 'application/json', 'and it is JSON')

  const sent = Object.keys(init.headers).map((h) => h.toLowerCase()).sort()
  eq(sent.join(','), 'content-type', 'no header rides along that could name the reader (no X-Forwarded, no UA, no Referer)')
  hasNot(init.headers.Authorization || '', 'Bearer', 'no token is sent: the sign-in is the site cookie')
  eq(init.credentials, 'same-origin', 'and that cookie rides along only to this site')
}

// ---------- one id per browser, reused ----------
{
  const store = fakeStore()
  const first = visitorKey(store)
  eq(visitorKey(store), first, 'the same browser keeps the same id')
  eq(store.writes.length, 1, 'and writes it once, not on every page load')
  eq(store.writes[0][0], 'cheese:docs-visitor', 'under the key the site owns')

  const kept = fakeStore({ 'cheese:docs-visitor': first })
  eq(visitorKey(kept), first, 'a stored id is read back, not regenerated')
  eq(kept.writes.length, 0, 'and nothing is rewritten')

  // A value the backend would drop (too short, wrong characters) is replaced
  // rather than sent: a beacon we know is refused is a request not worth making.
  const junk = fakeStore({ 'cheese:docs-visitor': '短' })
  ok(/^[A-Za-z0-9_-]{8,64}$/.test(visitorKey(junk)), 'a stored id the backend would refuse is replaced')
}

// ---------- pages that are not visits ----------
{
  const send = spy()
  for (const page of [
    { kind: 'dev-gate' },
    { kind: '404' },
    {},
    undefined,
  ]) {
    eq(beaconBody(page, { storage: fakeStore() }), null, `nothing is sent for ${JSON.stringify(page)}`)
    await recordVisit(page, { send, storage: fakeStore() })
  }
  eq(send.calls.length, 0, 'not one request for the gate, a 404, or a page with no kind')

  // The dev pages are behind the admin gate. The visit counts (an admin reading
  // them is a visit) but the slug does not travel: it is not a public name.
  const dev = { kind: 'doc', section: 'dev', slug: 'feature-stats', dev: true }
  eq(pageSlug(dev), null, 'a developer page records no slug')
  const body = beaconBody(dev, { storage: fakeStore() })
  ok(body, 'but the visit is still counted')
  eq(body.page, null, 'with the page left blank')

  // Home, changelog and download have no slug to record; the visit still counts.
  const home = beaconBody({ kind: 'home' }, { storage: fakeStore() })
  ok(home, 'the home page is a visit')
  eq(home.page, null, 'and records no slug')
}

// ---------- silence in every direction ----------
{
  const offline = spy({ throws: true })
  let threw = null
  try {
    await recordVisit(DOC, { send: offline, storage: fakeStore() })
  } catch (e) {
    threw = e
  }
  ok(!threw, 'a beacon that cannot be sent does not throw at the page')

  // Storage refused (privacy modes throw on access, not just on write).
  const refused = {
    getItem() {
      throw new Error('denied')
    },
    setItem() {
      throw new Error('denied')
    },
  }
  const send = spy()
  await recordVisit(DOC, { send, storage: refused })
  eq(send.calls.length, 1, 'storage being refused still counts the visit')
  eq(JSON.parse(send.calls[0].init.body).visitor, '', 'as an anonymous one — the backend drops it rather than inventing an id')

  // A backend that answers 500 is answered with silence too (the response is
  // never read: there is nothing to do with it).
  const error = spy({ status: 500, ok: false })
  await recordVisit(DOC, { send: error, storage: fakeStore() })
  eq(error.calls.length, 1, 'an error response is not retried from the page')
}

// ---------- the shipping bundle carries it ----------
{
  const out = process.env.OUT || fs.mkdtempSync(path.join(os.tmpdir(), 'docs-visit-'))
  if (!process.env.OUT) {
    execFileSync(process.execPath, ['build.mjs'], { cwd: SITE, env: { ...process.env, OUT: out }, stdio: ['ignore', 'ignore', 'inherit'] })
  }
  const assets = path.join(out, 'assets')
  const bundle = fs.existsSync(assets)
    ? fs
        .readdirSync(assets)
        .filter((f) => f.endsWith('.js'))
        .map((f) => fs.readFileSync(path.join(assets, f), 'utf8'))
        .join('\n')
    : ''
  has(bundle, '/api/docs/visit', 'the built bundle calls the visit endpoint')
  has(bundle, 'cheese:docs-visitor', 'and carries the storage key the id lives under')
}

if (failures.length) {
  console.error(`visit-beacon: ${failures.length} of ${checks} check(s) failed`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`visit-beacon: ${checks} checks passed`)
