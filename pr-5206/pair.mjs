// args: <which before|after> ; cleans the room, runs one filing race on that backend through the console API, then shoots 待办 and the task page.
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
const which = process.argv[2]
execFileSync('python3', ['-I', '/tmp/fb108-demo/cleanup.py'])
await fetch(`http://127.0.0.1:13200/__fb108/api/run?which=${which}&delay=5`)
for (;;) { const s = await (await fetch('http://127.0.0.1:13200/__fb108/api/state')).json(); if (!s.busy) { console.log(s.runs[0].out); break } await new Promise((r) => setTimeout(r, 1000)) }
const task = (await (await fetch('http://127.0.0.1:13200/__fb108/api/state')).json()).runs[0].out.match(/task 200 (\S+)/)[1]
const browser = await chromium.launch({ executablePath: '/tmp/fb108-pw/browsers/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell' })
const page = await browser.newPage({ viewport: { width: 1680, height: 900 }, locale: 'zh-CN' })
await page.goto('http://127.0.0.1:13200/'); await page.waitForTimeout(12000)
const agree = page.getByText('同意并继续'); if (await agree.count()) { await agree.click(); await page.waitForTimeout(2000); await page.goto('http://127.0.0.1:13200/inbox'); await page.waitForTimeout(9000) }
await page.screenshot({ path: `/tmp/fb108-demo/${which}-inbox.png` })
await page.goto(`http://127.0.0.1:13200/projects/4eb748cb-c2f4-4423-a793-74dfded56910/topics/ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d/tasks/${task}`); await page.waitForFunction(() => /第 \d 版/.test(document.body.innerText), null, { timeout: 60000 }); await page.waitForTimeout(2000)
await page.screenshot({ path: `/tmp/fb108-demo/${which}-task.png` })
console.log('task', task, (await page.evaluate(() => document.body.innerText)).match(/第 \d 版/g))
await browser.close()
