// Authenticated screenshot harness for the fusion demo.
// Usage: node shot.mjs <path> <out.png>
// Env: MAIN_TOKEN (sub=int user id, main axios), CX_TOKEN (sub=handle, cheesex /api).
import { chromium } from '@playwright/test'
const [path = '/', out = 'shot.png'] = process.argv.slice(2)
const mainTok = process.env.MAIN_TOKEN || ''
const cxTok = process.env.CX_TOKEN || ''
const user = { id: 1, username: 'alice', nickname: 'Alice', avatarId: 1 }
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1360, height: 860 } })
await p.addInitScript(([m, c, u]) => {
  localStorage.setItem('accessToken', m)
  localStorage.setItem('user', JSON.stringify(u))
  localStorage.setItem('cheesex.me', JSON.stringify({ handle: u.username, name: u.nickname, token: c }))
}, [mainTok, cxTok, user])
await p.goto('http://localhost:5200' + path, { waitUntil: 'domcontentloaded', timeout: 25000 }).catch(e => console.log('goto:', e.message))
await p.waitForTimeout(3800)
await p.screenshot({ path: out })
console.log('shot:', out, '| final url:', p.url())
await b.close()
