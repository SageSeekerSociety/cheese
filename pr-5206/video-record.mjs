// Record a real browser session of the acceptance entry. args: before|after|flows
// Captions are a small banner injected into the live page (not composed frames).
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { renameSync, readdirSync } from 'node:fs'
const which = process.argv[2]
const E = 'http://127.0.0.1:13200'
const P = '/projects/4eb748cb-c2f4-4423-a793-74dfded56910/topics/ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d/tasks/'
const SHA = { before: '主干 37767844d', after: 'PR #5206 head a5118f39', flows: 'PR #5206 head a5118f39' }[which]
execFileSync('python3', ['-I', '/tmp/fb108-demo/cleanup.py']); execFileSync('python3', ['-I', '/tmp/fb108-demo/flows.py', 'reset'])
const browser = await chromium.launch({ executablePath: '/tmp/fb108-pw/browsers/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell' })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN', recordVideo: { dir: '/tmp/fb108-video/raw', size: { width: 1440, height: 900 } } })
const page = await ctx.newPage()
const bj = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 19).replace('T', ' ')
let caption = ''
const cap = async (text) => {
  caption = text
  await page.evaluate(([t, s, time]) => {
    let b = document.getElementById('fb108-cap')
    if (!b) { b = document.createElement('div'); b.id = 'fb108-cap'; b.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:rgba(20,20,20,.86);color:#fff;font:15px/1.5 system-ui,sans-serif;padding:8px 14px;pointer-events:none'; document.body.appendChild(b) }
    b.textContent = `${t}　｜　${s}　｜　${time}（北京）　｜　隔离栈：真 FastAPI + 真 Postgres 种子库，forge 为替身`
  }, [text, SHA, bj()]).catch(() => {})
}
page.on('load', () => { if (caption) setTimeout(() => cap(caption), 300) })
const go = async (url, text, wait = 6000) => { caption = text; await page.goto(E + url); await page.waitForTimeout(wait); await cap(text) }
const enter = async (user, to, text) => {
  await go(`/?as=${user}&to=${encodeURIComponent(to)}`, text, 9000)
  const agree = page.getByText('同意并继续'); if (await agree.count()) { await agree.click(); await page.waitForTimeout(1500); await go(`/?as=${user}&to=${encodeURIComponent(to)}`, text, 9000) }
}
const idle = async () => { await page.waitForTimeout(800); await page.waitForFunction(() => !document.querySelector('#status')?.textContent, null, { timeout: 90000 }); await page.waitForTimeout(1500) }
const scrollTo = async (sel) => { await page.locator(sel).first().scrollIntoViewIfNeeded().catch(() => {}); await page.waitForTimeout(800) }
const state = async () => (await (await fetch(E + '/__fb108/api/state')).json())

if (which === 'before' || which === 'after') {
  const label = which === 'before' ? '改前代码：同时递两次卡' : '修后代码：同时递两次卡'
  await go('/__fb108', `控制台 ${E}/__fb108：同一条任务同时递两次卡（forge 替身延迟 5 秒，第二次晚 1 秒发出）`, 2500)
  await scrollTo(`button[data-which="${which}"]`)
  await page.fill('#delay', '5'); await page.waitForTimeout(600)
  await cap(`点「${label}」`); await page.locator(`button[data-which="${which}"]`).click()
  await idle(); await scrollTo('#runs'); await cap(which === 'before' ? '回包：两次都 200，建出两张卡' : '回包：第一次 200，第二次 409「另一次递卡还在处理中」'); await page.waitForTimeout(5000)
  const task = (await state()).runs[0].out.match(/task 200 (\S+)/)[1]
  await enter('alice', '/inbox', `产品页 ${E}/inbox（alice）：看这条任务的版本`); await page.waitForTimeout(5000)
  await enter('alice', P + task, `任务页：${which === 'before' ? '第 2 版 = 这条任务已有两张卡' : '第 1 版 = 只有一张卡'}`); await page.waitForTimeout(5000)
} else {
  const step = async (s, text) => { await go('/__fb108', `控制台：${text}`, 2000); await scrollTo(`button[data-step="${s}"]`); await page.locator(`button[data-step="${s}"]`).click(); await idle(); await scrollTo('#flows'); await cap(`控制台：${text} → 回包见最上面一条记录`); await page.waitForTimeout(5000) }
  await step('a1', 'A1 递第一张卡（交 https://example.com/v1，验收人 bobby）')
  const a = (await state()).flowState.a
  await enter('bobby', '/inbox', 'bobby 的待办：A 任务第 1 版'); await page.waitForTimeout(3000)
  await enter('bobby', P + a, 'A2 bobby 在产品页打开 A 任务读卡'); await page.getByText('改动', { exact: true }).first().click(); await page.waitForTimeout(1000); await cap('A2 bobby 读卡：「改动」里是 v1'); await page.waitForTimeout(4000)
  await step('a2', 'A2 读完后看状态：卡仍是 pending、第 1 版')
  await step('a3', 'A3 交付更新为 v2 后再递一张：422「已有待处理的验收卡」')
  await step('a4', 'A4 走产品的「换交付物」更新原卡：卡号不变，换成 v2')
  await enter('bobby', P + a, 'A4 之后 bobby 打开 A 任务'); await page.getByText('改动', { exact: true }).first().click(); await page.waitForTimeout(1000); await cap('A4 之后：「改动」里是 v2，对话里有「换了审阅中的内容」通知'); await page.waitForTimeout(4000)
  await enter('bobby', '/inbox', 'A4 之后 bobby 的待办：仍是第 1 版，动态里有通知'); await page.waitForTimeout(4000)
  await step('b1', 'B1 另一条任务递第一张卡')
  await step('b2', 'B2 bobby 退回')
  await step('b3', 'B3 退回后再递：200，新卡')
  const b = (await state()).flowState.b
  await enter('bobby', '/inbox', 'B3 之后 bobby 的待办：B 任务第 2 版'); await page.waitForTimeout(4000)
  await enter('bobby', P + b, 'B 任务页：「Bob 退回了改动」之后是第 2 版待审阅'); await page.waitForTimeout(4000)
}
const v = page.video(); await ctx.close(); await browser.close()
const out = `/tmp/fb108-video/fb108-${which}.webm`; renameSync(await v.path(), out); console.log('saved', out)
