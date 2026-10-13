#!/usr/bin/env node
// 给运行中的页面截图（Node 版）—— 不装任何东西，用工作目录附近已经装好的 playwright。
//
// 为什么有两个版本：`shot_live.py` 靠 `uv run --with playwright`，首次要从 PyPI
// 下几十 MB；国内直连 files.pythonhosted.org 常常卡死（实测 0 B/s）。这个版本
// 复用已经装好的 playwright（如 cheese 仓库的 `e2e/node_modules`），零下载、秒起。
// 哪个能跑用哪个：先试这个，跑不动再退 `shot_live.py`。
//
// 用法:
//   node shot_live.mjs <url> <out.png> [选项]
//
// 选项:
//   --user NAME --password PW  先登录后端，把 token 注入 localStorage 再截图
//   --token TOK                已有访问 token 就直接给，跳过登录
//   --api URL                  cheese 后端基址，默认 http://localhost:8081
//   --width PX --height PX     视口，默认 1360 x 860
//   --wait MS                  页面稳定等待，默认 3500（SPA 首屏要这个时间）
//   --click TEXT               截图前点一颗带这个文字的控件；点不到只印提示
//   --headless                 不弹窗口。**默认弹窗口**——要给人看着它跑
//   --slow MS                  每步慢放；默认有头 250、无头 0
//   --hold MS                  截完停多久再关窗；默认有头 2500、无头 0
//
// 找不到 playwright 时用 `CHEESE_PW_ROOT=<含 node_modules 的目录>` 指给它。

import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

function arg(name, dflt) {
  const i = process.argv.indexOf(name)
  return i === -1 ? dflt : process.argv[i + 1]
}
const flag = (name) => process.argv.includes(name)

const url = process.argv[2]
const out = process.argv[3]
if (!url || !out || url.startsWith('--')) {
  console.error('用法: node shot_live.mjs <url> <out.png> [--user U --password P ...]')
  process.exit(2)
}

/** 在工作目录附近找一份装好的 playwright：`<dir>/node_modules` 或 `<dir>/e2e/node_modules`。 */
function findPlaywright() {
  const roots = []
  if (process.env.CHEESE_PW_ROOT) roots.push(process.env.CHEESE_PW_ROOT)
  let dir = process.cwd()
  for (let i = 0; i < 6; i += 1) {
    roots.push(dir, path.join(dir, 'e2e'))
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  roots.push(path.dirname(new URL(import.meta.url).pathname))
  for (const root of roots) {
    for (const pkg of ['@playwright/test', 'playwright']) {
      try {
        return createRequire(path.join(root, 'package.json'))(pkg)
      } catch {
        /* 换下一个候选 */
      }
    }
  }
  return null
}

async function login(api, user, password) {
  const res = await fetch(`${api.replace(/\/$/, '')}/users/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ username: user, password }),
  })
  const body = await res.json()
  const data = body.data || {}
  return { token: data.accessToken, who: data.user || { id: 0, username: user, nickname: user } }
}

const playwright = findPlaywright()
if (!playwright) {
  console.error('[shot_live] 找不到 playwright。用 CHEESE_PW_ROOT=<含 node_modules 的目录> 指给它。')
  process.exit(1)
}

const visible = !flag('--headless')
const slow = Number(arg('--slow', visible ? 250 : 0))
const hold = Number(arg('--hold', visible ? 2500 : 0))
const api = arg('--api', 'http://localhost:8081')
const wait = Number(arg('--wait', 3500))
const click = arg('--click', '')
const width = Number(arg('--width', 1360))
const height = Number(arg('--height', 860))

let token = arg('--token', '')
let who = null
const user = arg('--user', '')
if (user && !token) {
  ;({ token, who } = await login(api, user, arg('--password', '')))
  console.log(`[shot_live] 已登录 ${user}`)
}

const browser = await playwright.chromium
  .launch({ channel: 'chrome', headless: !visible, slowMo: slow })
  .catch(() => playwright.chromium.launch({ headless: !visible, slowMo: slow }))
// try/finally 不是形式：`goto` 超时、截图失败、点不到控件之后抛出去，都必须在同一个
// 出口关掉窗口。有头模式下漏掉一个就是留一个不关的 Chrome —— 屏幕上多一个窗口，
// 机器上多一份常驻 CPU。
try {
  const page = await browser.newPage({ viewport: { width, height } })
  if (token) {
    who = who || { id: 0, username: '', nickname: '' }
    await page.addInitScript(
      ([tok, u]) => {
        localStorage.setItem('accessToken', tok)
        localStorage.setItem('user', JSON.stringify(u))
        localStorage.setItem('cheesex.me', JSON.stringify({ id: String(u.id), handle: u.username, token: tok }))
      },
      [token, who]
    )
  }
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await page.waitForTimeout(wait)
  if (click) {
    try {
      await page.getByText(click, { exact: true }).first().click({ timeout: 5000 })
      await page.waitForTimeout(1600)
    } catch (exc) {
      console.log(`[shot_live] 点不到「${click}」: ${exc.constructor.name}`)
    }
  }
  await page.screenshot({ path: out })
  console.log(`[shot_live] 截图 ${out} | 最终 URL: ${page.url()}`)
  if (token && page.url().includes('login')) {
    console.error('[shot_live] 被弹回登录页：token 没生效或已过期')
  }
  if (hold) await page.waitForTimeout(hold)
} finally {
  await browser.close()
}
if (!fs.existsSync(out)) process.exit(1)
