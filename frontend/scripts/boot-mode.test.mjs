// index.html 里决定第一帧画什么的那段启动脚本（data-boot）。跑法：`pnpm run test:ratchet`。
//
// 它在任何模块之前、就地跑在浏览器里，所以没法 import 进 vitest；这里把 index.html 里那一段
// 原样取出来，在一个假的 window 里跑。守的是三条谁都说得出来的规矩：没登录、读中文的访客在
// 预渲染的公开页上看到的就是那一页；登录的人看到的是他要落到的那一页的骨架，不是推广页；
// 读英文的访客不会先看到一页中文。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'index.html'), 'utf8')
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1])
  .find((body) => body.includes('html.dataset.boot = boot'))

const SIGNED_IN = { accessToken: 'token', user: JSON.stringify({ id: 1, onboarded_at: '2026-01-01' }) }

/** 在 `path` 上冷打开，浏览器里存着 `stored`，返回启动脚本选的那一种。 */
function boot(path, { stored = {}, language = 'zh-CN', prerendered = null, desktopApp = false, width = 1440 } = {}) {
  const dataset = {}
  const window = {
    innerWidth: width,
    ...(desktopApp ? { __TAURI__: {} } : {}),
  }
  const context = {
    window,
    location: { pathname: path },
    navigator: { language },
    localStorage: { getItem: (key) => (key in stored ? stored[key] : null) },
    document: {
      documentElement: { dataset, style: { setProperty() {} } },
      querySelector: (selector) =>
        selector === 'meta[name="cx-prerendered"]' && prerendered !== null ? { content: prerendered } : null,
    },
  }
  vm.runInNewContext(script, context)
  return dataset.boot
}

test('the boot script is still in index.html', () => {
  assert.ok(script, 'no inline script in index.html sets data-boot')
})

test('a visitor who is not signed in and reads Chinese gets the prerendered page itself', () => {
  assert.equal(boot('/', { prerendered: '/' }), 'page')
  assert.equal(boot('/solutions', { prerendered: '/solutions' }), 'page')
  assert.equal(boot('/download', { prerendered: '/download' }), 'page')
})

test('a public page with no prerendered copy for this address is drawn by the app, over its outline', () => {
  assert.equal(boot('/download'), 'landing')
  assert.equal(boot('/solutions', { prerendered: '/' }), 'landing')
})

test('a visitor reading English never sees the Chinese prerendered page first', () => {
  assert.equal(boot('/', { prerendered: '/', language: 'en-US' }), 'landing')
  assert.equal(boot('/', { prerendered: '/', stored: { 'cheese:locale': 'en' } }), 'landing')
  assert.equal(boot('/', { prerendered: '/', language: 'en-US', stored: { 'cheese:locale': 'zh-CN' } }), 'page')
})

test('someone signed in opening / gets the outline of where they land, not the marketing page', () => {
  const layout = JSON.stringify({ lastProjectId: 'p1' })
  assert.equal(boot('/', { prerendered: '/', stored: { ...SIGNED_IN, 'cheesex.layout': layout } }), 'workspace')
  assert.equal(boot('/', { prerendered: '/', stored: SIGNED_IN }), 'inbox')
  const fresh = { accessToken: 'token', user: JSON.stringify({ id: 1, onboarded_at: null }) }
  assert.equal(boot('/', { prerendered: '/', stored: fresh }), 'plain')
})

test('someone signed in opening a link inside the app gets that page’s outline', () => {
  assert.equal(boot('/projects/abc/channels/1', { stored: SIGNED_IN }), 'room')
  assert.equal(boot('/projects/abc/overview', { stored: SIGNED_IN }), 'workspace')
  assert.equal(boot('/inbox', { stored: SIGNED_IN }), 'inbox')
  assert.equal(boot('/settings/profile', { stored: SIGNED_IN }), 'shell')
})

test('in the desktop app nobody gets the marketing page', () => {
  assert.notEqual(boot('/', { prerendered: '/', desktopApp: true }), 'page')
  assert.notEqual(boot('/', { prerendered: '/', desktopApp: true }), 'landing')
})

test('a visitor who is not signed in, away from the public pages, gets no app frame', () => {
  assert.equal(boot('/account/signin'), 'plain')
  assert.equal(boot('/projects/abc/channels/1'), 'plain')
})

test('storage that throws still leaves a frame to paint', () => {
  const dataset = {}
  vm.runInNewContext(script, {
    window: { innerWidth: 1440 },
    location: { pathname: '/inbox' },
    navigator: { language: 'zh-CN' },
    get localStorage() {
      throw new Error('SecurityError')
    },
    document: { documentElement: { dataset, style: { setProperty() {} } }, querySelector: () => null },
  })
  assert.ok(dataset.boot)
})
