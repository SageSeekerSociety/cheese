import { chromium } from 'playwright'
import { renameSync } from 'node:fs'
const [version, label] = process.argv.slice(2)
const C = 'http://127.0.0.1:3419/api/__accept'
await fetch(`${C}/reset`); await fetch(`${C}/set?version=${version}&say=409&autoDone=0`)
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', recordVideo: { dir: '/tmp/acc5208/video', size: { width: 1440, height: 900 } } })
const p = await ctx.newPage()
p.on('pageerror', (e) => console.log('pageerror', e.message))
await p.goto('http://127.0.0.1:3419/', { waitUntil: 'domcontentloaded' })
await p.waitForURL(/market/, { timeout: 60000 })
await p.waitForTimeout(4000)
const modHas = await p.evaluate(async () => (await (await fetch('/src/stores/agentThread.ts')).text()).includes('heldBack'))
// 左侧叠一块说明板：版本、当前步骤、代理记下的接口时序（每 300ms 读一次控制台状态）
await p.evaluate(({ label, modHas }) => {
  const box = document.createElement('div')
  box.id = 'ev-overlay'
  box.style.cssText = 'position:fixed;left:90px;top:70px;width:900px;z-index:99999;background:rgba(20,20,28,.92);color:#fff;font:15px/1.55 "Noto Sans CJK SC",sans-serif;padding:14px 18px;border-radius:10px;box-shadow:0 4px 18px rgba(0,0,0,.3);pointer-events:none'
  box.innerHTML = `<div style="font-size:18px;font-weight:700">${label}</div>
<div style="color:#bbb">页面上跑的对话模块：${modHas ? '本 PR（含 heldBack）' : '主干（不含 heldBack）'} · 回答文字和收尾帧（done）都由验收代理模拟，没有接真模型</div>
<div id="ev-step" style="margin-top:10px;font-size:17px;color:#ffd666"></div>
<div style="margin-top:10px;color:#9cf">接口时序（代理日志，UTC）</div><pre id="ev-log" style="margin:4px 0 0;white-space:pre-wrap;font:13px/1.5 monospace;color:#e6e6e6;max-height:330px;overflow:hidden"></pre>
<div style="margin-top:8px;color:#9cf">服务端存下的消息</div><pre id="ev-msgs" style="margin:4px 0 0;white-space:pre-wrap;font:13px/1.5 monospace;color:#e6e6e6"></pre>`
  document.body.appendChild(box)
  setInterval(async () => {
    try {
      const j = await (await fetch('/api/__accept/state')).json()
      document.getElementById('ev-log').textContent = j.log.slice().reverse().filter((l) => !/控制台：(清空|设置)|page-timings/.test(l)).slice(-12).join('\n')
      document.getElementById('ev-msgs').textContent = j.messages.join('\n') || '（还没有）'
    } catch {}
  }, 300)
}, { label, modHas })
const step = (t) => p.evaluate((t) => (document.getElementById('ev-step').textContent = t), t)
await step('① 打开芝士面板')
await p.locator('[data-testid="agent-entry"]').click()
await p.waitForTimeout(1500)
const box = p.getByRole('textbox', { name: '输入问题或要做的事' }).last()
await step('② 首问：把三份报告按评分表打分')
await box.click(); await p.keyboard.type('把三份报告按评分表打分', { delay: 60 }); await p.waitForTimeout(500); await p.keyboard.press('Enter')
await p.getByText('先把三份报告各读一遍').first().waitFor({ timeout: 20000 })
await step('③ 首答已写出；服务端这一轮已结束，但收尾帧（done）被代理扣住，页面仍在「回答中」')
await p.waitForTimeout(3500)
await step('④ 这时补一句：只看第 3 份 → POST /say')
await box.click(); await p.keyboard.type('只看第 3 份', { delay: 80 }); await p.waitForTimeout(500); await p.keyboard.press('Enter')
await p.waitForTimeout(1200)
await step(version === 'before' ? '⑤ /say 回 409（芝士现在没有在回答）→ 改前：补的话被删掉，报「芝士暂时答不上来」' : '⑤ /say 回 409（芝士现在没有在回答）→ 改后：补的话留在面板上，不报错，等这一轮收尾')
await p.waitForTimeout(4500)
await step('⑥ 代理放行收尾帧（done）')
await fetch(`${C}/done`)
await p.waitForTimeout(2500)
await step(version === 'before' ? '⑦ 结果（改前）：补的话没有存下、也没有被问，面板留着失败提示' : '⑦ 结果（改后）：收尾后把「只看第 3 份」作为新问题发出（POST /ask），并得到回答')
await p.waitForTimeout(5000)
const st = await (await fetch(`${C}/state`)).json()
console.log(JSON.stringify({ version, modHas, server: st.messages }))
const v = p.video(); await ctx.close(); await b.close()
renameSync(await v.path(), `/tmp/acc5208/video/pr5208-409-${version}.webm`)
