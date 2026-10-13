// 任务页「AI 队友」那一行的效果图：拿 vite 起真前端，挂真组件，拍三张。
//   owner      负责人，这件事还没单独指定队友（跟随频道）
//   picked     负责人点开「改」、从菜单里换成了「无言」
//   readonly   协作者看同一行：看得见，换不了
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { chromium } from '@playwright/test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { createServer: createVite } = require('vite')
const root = fileURLToPath(new URL('../frontend/', import.meta.url))
process.chdir(root)
const OUT = process.env.OUT || '/tmp/task-agent-shots'
await mkdir(OUT, { recursive: true })

const SHOTS = [
  { name: '01-owner-follows-room', me: 'alice', pick: null },
  { name: '02-owner-picked', me: 'alice', pick: '无言' },
  { name: '03-collaborator-readonly', me: 'chiruotong', pick: null },
]

const fixturePlugin = {
  name: 'preview-task-agent-fixture',
  configureServer(server) {
    server.middlewares.use('/preview-task-agent', (_req, res) => {
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end(`<!doctype html><html lang="zh"><meta charset="utf-8"><title>任务页页头效果图</title>
<style>
html,body{margin:0;min-height:100%}
body{background:#f6f6f8}
.preview-bar{border-bottom:1px solid rgba(0,0,0,.08);background:#fff}
.preview-note{padding:14px 24px 6px;color:#8a8a95;font:13px/1.6 system-ui,sans-serif}
</style>
<div class="preview-bar"><div id="app-bar-slot"></div></div>
<div class="preview-note">效果图：这一行跑的是任务页页头真组件、真样式，只有数据是编的。</div>
<div id="app"></div>
<script type="module" src="/@fs/${fileURLToPath(new URL('./preview-task-agent-fixture.ts', import.meta.url))}"></script>
</html>`)
    })
  },
}

const vite = await createVite({
  root,
  resolve: {
    alias: {
      vue: fileURLToPath(new URL('../frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)),
    },
    dedupe: ['vue'],
  },
  plugins: [fixturePlugin],
  server: {
    host: '127.0.0.1',
    port: 0,
    fs: { allow: [fileURLToPath(new URL('../', import.meta.url))] },
  },
  logLevel: 'error',
})
await vite.listen()
const base = vite.resolvedUrls.local[0]
console.log('fixture', new URL('preview-task-agent', base).href)

const browser = await chromium.launch()
try {
  for (const { name, me, pick } of SHOTS) {
    const ctx = await browser.newContext({
      viewport: { width: 1180, height: 460 },
      deviceScaleFactor: 2,
      reducedMotion: 'reduce',
    })
    await ctx.addInitScript((handle) => {
      localStorage.setItem('accessToken', 'preview-token')
      localStorage.setItem('user', JSON.stringify({ id: 1, username: handle, nickname: handle, avatarId: 0 }))
      localStorage.setItem(
        'cheesex.me',
        JSON.stringify({ id: '1', handle, name: handle, token: 'preview-token' })
      )
    }, me)
    await ctx.route(
      (url) => url.pathname.startsWith('/api/') || url.pathname.startsWith('/users/'),
      (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ code: 200, message: 'ok', data: null }),
        })
    )
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.error('pageerror', e.message))
    page.on('console', (m) => {
      if (m.type() === 'error') console.error('console', m.text())
    })
    await page.goto(new URL('preview-task-agent', base).href, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.locator('[data-testid=task-details]').click()
    await page.locator('.task-details').waitFor({ timeout: 15000 })
    if (pick) {
      // 那一行现在是「值 + 行尾一颗『改』+ 点开的菜单」，不再是下拉。
      await page.locator('[data-testid=task-agent-edit]').click()
      await page.locator('.task-agent-menu__row', { hasText: pick }).click()
      await page.waitForTimeout(300)
    }
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${OUT}/${name}.png` })
    console.log('shot', `${OUT}/${name}.png`)
    await ctx.close()
  }
} finally {
  await browser.close()
  await vite.close()
}
