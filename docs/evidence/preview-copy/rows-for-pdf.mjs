// 从对照页逐行截「现状 | 建议」，供 PDF 用。
// 单独一个脚本：整页那张太高，压进 A4 会把字缩到看不清；每行一张才读得清。
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import fs from 'node:fs'

const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(resolve(here, '../../../e2e/package.json'))
const { chromium } = require('@playwright/test')

const OUT = '/var/tmp/shots-copy'
fs.mkdirSync(OUT, { recursive: true })
const BASE = process.env.PREVIEW_BASE || 'http://127.0.0.1:5210/feedback-proto.html'

const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } })
await page.goto(`${BASE}#/copy-preview`, { waitUntil: 'domcontentloaded' })
await page.waitForSelector('.cp__row')

const rows = await page.locator('.cp__row').all()
const ids = []
for (let i = 0; i < rows.length; i++) {
  // 行的编号写在自己的标题里（`#1 反馈队列 · 列表视图`），文件名跟着它走，
  // PDF 里的图注和这里的行号才不会错位。
  const where = (await rows[i].locator('.cp__where').innerText()).trim()
  const id = where.split(/\s+/)[0].replace('#', 'n')
  ids.push(id)
  await rows[i].scrollIntoViewIfNeeded()
  await rows[i].screenshot({ path: `${OUT}/row-${id}.png` })
}
const table = await page.locator('.cp__table').first()
await table.scrollIntoViewIfNeeded()
await table.screenshot({ path: `${OUT}/write-table.png` })
console.log('rows:', ids.join(' '))
console.log('wrote', rows.length + 1, 'figures to', OUT)
await browser.close()
