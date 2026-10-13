// Unit tests for the request-path gate's pure logic, run by `node --test` from
// `pnpm run test:ratchet` in CI.
//
// Written against the ways this gate could be wrong in the direction that
// matters: reporting a call it did not understand as if it had judged it, or
// letting a renamed path through because the shape matched loosely. The
// complimentary failure — a false alarm on a path that is fine — is what makes a
// gate get deleted, so the skipping rules are tests too.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { findOffenders, formatReport, segmentList, shapeSegments, shouldScan } from './request-paths-core.mjs'

const ROUTES = [
  { protocol: 'http', path: '/openapi.json', methods: ['GET'] },
  { protocol: 'http', path: '/topics', methods: ['GET'] },
  { protocol: 'http', path: '/topics/{topic_id}/reopen', methods: ['POST'] },
  { protocol: 'http', path: '/connector/auth/device/start', methods: ['POST'] },
]

const judge = (source, routes = ROUTES) => findOffenders([{ path: 'src/api/x.ts', text: source }], routes)

test('a literal path is judged against the index', () => {
  assert.equal(judge("request('/topics')").checked, 1)
  assert.deepEqual(judge("request('/topics')").offenders, [])
})

test('a path the backend does not serve is reported with its line', () => {
  const result = judge(['const a = 1', "request('/topic')"].join('\n'))
  assert.equal(result.checked, 1)
  assert.deepEqual(result.offenders, [{ file: 'src/api/x.ts', line: 2, raw: '/topic' }])
})

test('an interpolation standing alone is one segment', () => {
  const source = 'request(`/topics/${encodeURIComponent(id)}/reopen`)'
  assert.deepEqual(judge(source).offenders, [])
  assert.equal(judge('request(`/topics/${id}/clos`)').offenders.length, 1)
})

test('an interpolation glued into a segment is skipped, not guessed at', () => {
  // How a query string or a suffix gets appended. What `/feedback` ends up
  // being here depends on `feedbackQuery`, which is not in this file.
  const result = judge('request(`/topics/${id}/transcript${qs ? `?page=${p}` : ``}`)')
  assert.equal(result.checked, 0)
  assert.equal(result.unjudged, 1)
})

test('a path built from a helper or a variable is skipped', () => {
  const result = judge(['request(`${taskPath(id)}/task`)', 'request(path)'].join('\n'))
  assert.equal(result.checked, 0)
  assert.equal(result.unjudged, 2)
})

test('the connector helper carries its own prefix', () => {
  assert.deepEqual(judge("connectorRequest('/auth/device/start')").offenders, [])
  assert.equal(judge("connectorRequest('/auth/device/start')").checked, 1)
})

test('another object’s request() is not a path call', () => {
  // axios and the Web Locks API both have one, and neither takes a path.
  const result = judge(["instance.request({ url: '/knowledge' })", "locks.request('lock', work)"].join('\n'))
  assert.equal(result.checked, 0)
  assert.equal(result.unjudged, 0)
})

test('only the script of a .vue file is read', () => {
  const files = [
    {
      path: 'src/views/Thing.vue',
      text: [
        '<template>',
        '  <p>POST /topic-proposals no longer exists</p>',
        '</template>',
        '<script setup lang="ts">',
        'async function go() {',
        "  await request('/topics/{topic_id}/clos')",
        '}',
        '</script>',
      ].join('\n'),
    },
  ]
  const result = findOffenders(files, ROUTES)
  assert.equal(result.checked, 1)
  assert.deepEqual(result.offenders, [{ file: 'src/views/Thing.vue', line: 6, raw: '/topics/{topic_id}/clos' }])
})

test('spec files and other extensions are out of scope', () => {
  assert.equal(shouldScan('src/api/x.ts'), true)
  assert.equal(shouldScan('src/api/x.spec.ts'), false)
  assert.equal(shouldScan('src/api/__tests__/x.ts'), false)
  assert.equal(shouldScan('src/api/x.md'), false)
})

test('the shape of a path is its segments, a parameter taking one', () => {
  assert.deepEqual(segmentList('/topics/{topic_id}/reopen'), ['topics', '\u0000', 'reopen'])
  assert.deepEqual(segmentList('/'), [])
  assert.deepEqual(shapeSegments('/topics/${}'), ['topics', '\u0000'])
  assert.equal(shapeSegments('/topics/${}/reopen${}'), null)
})

test('the report names the count it judged and the count it skipped', () => {
  const report = formatReport(judge("request('/topic')"))
  assert.match(report, /backend does not serve/)
  assert.match(report, /src\/api\/x\.ts:1/)
  assert.match(report, /route_index --emit/)
})
