// index.html 里在应用代码到之前先发请求的那段启动脚本：过期的登录先续上，再替应用问项目
// 清单。跑法：`pnpm run test:ratchet`。
//
// 它在任何模块之前、就地跑在浏览器里，没法 import 进 vitest；这里把 index.html 里那一段
// 原样取出来，在一个假的 window 里跑。守的规矩：项目清单只用一个活令牌去问；登录续不上
// 就不问；没登录、看推广页的人一个请求都不发。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'index.html'), 'utf8')
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1])
  .find((body) => body.includes('cheese:session-refresh'))

const jwt = (expSeconds) =>
  'h.' + Buffer.from(JSON.stringify({ sub: '1', exp: expSeconds })).toString('base64url') + '.s'
const LIVE = jwt(Date.now() / 1000 + 600)
const EXPIRED = jwt(Date.now() / 1000 - 60)
const RENEWED = jwt(Date.now() / 1000 + 900)

/** 在 `boot` 那种第一帧下冷打开，浏览器里存着 `token`；续签答 `renewal`。返回发出的请求和
 *  留给应用的那份项目清单答复。 */
async function open({ boot = 'workspace', token = LIVE, renewal = 'ok' } = {}) {
  const stored = { accessToken: token, user: '{"id":1}' }
  const sent = []
  const window = {}
  const fetch = async (url, init = {}) => {
    sent.push({ url, auth: init.headers?.Authorization ?? null })
    if (url.includes('refresh-token')) {
      if (renewal === 'down') throw new TypeError('network')
      if (renewal === 'refused') return { ok: false, json: async () => ({}) }
      return { ok: true, json: async () => ({ data: { accessToken: RENEWED, user: { id: 1 } } }) }
    }
    return { ok: true, json: async () => ({ code: 200, data: { data: [{ id: 'p1' }] } }) }
  }
  window.fetch = fetch
  vm.runInNewContext(script, {
    window,
    fetch,
    AbortController,
    setTimeout,
    clearTimeout,
    atob,
    JSON,
    Date,
    navigator: { locks: { request: async (_name, task) => task() } },
    localStorage: {
      getItem: (key) => (key in stored ? stored[key] : null),
      setItem: (key, value) => (stored[key] = value),
    },
    document: { documentElement: { dataset: { boot } } },
  })
  const answer = await window.__cxEarlyProjects
  return { sent, answer, hasAnswer: '__cxEarlyProjects' in window }
}

test('the script is still in index.html', () => {
  assert.ok(script, 'no inline script in index.html takes the session-refresh lock')
})

test('a live session asks for the project list at once, with its token', async () => {
  const { sent, answer } = await open()
  assert.deepEqual(sent, [{ url: '/api/projects', auth: `Bearer ${LIVE}` }])
  assert.equal(answer.token, LIVE)
})

test('an expired session is renewed first, and the list asked with the new token', async () => {
  const { sent, answer } = await open({ token: EXPIRED })
  assert.equal(sent[0].url, '/api/users/auth/refresh-token')
  assert.deepEqual(sent[1], { url: '/api/projects', auth: `Bearer ${RENEWED}` })
  assert.equal(answer.token, RENEWED)
})

test('a session that could not be renewed asks for nothing with its dead token', async () => {
  for (const renewal of ['refused', 'down']) {
    const { sent, answer } = await open({ token: EXPIRED, renewal })
    assert.deepEqual(
      sent.map((r) => r.url),
      ['/api/users/auth/refresh-token']
    )
    assert.equal(answer, null)
  }
})

test('nobody is asked for on the marketing pages, or before a signed-in frame', async () => {
  for (const boot of ['page', 'landing']) {
    const { sent, hasAnswer } = await open({ boot })
    assert.deepEqual(sent, [])
    assert.equal(hasAnswer, false)
  }
  const plain = await open({ boot: 'plain' })
  assert.deepEqual(plain.sent, [])
})
