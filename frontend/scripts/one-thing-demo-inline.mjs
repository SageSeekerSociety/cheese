// 把单文件构建的 CSS（含字体）内联进 index.html，JS 作为同目录的 app.js 经典脚本加载。
// 用法：node scripts/one-thing-demo-inline.mjs <dist 目录> <输出 html>
import fs from 'node:fs'
import path from 'node:path'

const [dist, out] = process.argv.slice(2)
let html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8')

// The publisher refuses raw U+FFFD and control bytes; inside the minified
// bundle they only occur in string/regex literals, where the escape is the same character.
const esc = (s) =>
  s
    .replace(/�/g, '\\ufffd')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, (c) => '\\x' + c.charCodeAt(0).toString(16).padStart(2, '0'))

const scripts = []
html = html.replace(/<script[^>]*src="\.\/(assets\/[^"]+\.js)"[^>]*><\/script>/g, (_, f) => {
  scripts.push(f) // moved to the end of <body> so the DOM exists when the IIFE runs
  return ''
})
html = html.replace(/<link[^>]*rel="modulepreload"[^>]*>/g, '')
html = html.replace(
  /<link[^>]*rel="stylesheet"[^>]*href="\.\/(assets\/[^"]+\.css)"[^>]*>/g,
  (_, f) => `<style>${fs.readFileSync(path.join(dist, f), 'utf8').replace(/<\/style/gi, '<\\/style')}</style>`,
)
// The bundle goes in a file of its own beside the page, loaded as a classic
// script: a classic <script src> loads in no-cors mode, which a null-origin
// sandbox allows, and a 12 MB inline script gets cut by the host's HTML pass.
const outDir = path.dirname(out)
const body = scripts
  .map((f, i) => {
    const name = `app${i ? i : ''}.js`
    fs.writeFileSync(path.join(outDir, name), esc(fs.readFileSync(path.join(dist, f), 'utf8')))
    return `<script src="./${name}"></script>`
  })
  .join('\n')
// What shows until app.js has downloaded and Vue mounts over it: the host can
// take tens of seconds to send a few MB, and a blank page reads as broken.
const boot =
  '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;height:100vh;font:14px/1.6 system-ui,sans-serif;color:#6b7280">' +
  '<div style="width:22px;height:22px;border:2px solid #e5e7eb;border-top-color:#f57f17;border-radius:50%;animation:ot-boot-spin .8s linear infinite"></div>' +
  `<div>正在加载演示（约 ${(fs.statSync(path.join(outDir, 'app.js')).size / 1e6).toFixed(1)} MB），第一次打开可能要十几秒…</div>` +
  '<style>@keyframes ot-boot-spin{to{transform:rotate(360deg)}}</style></div>'
html = html.replace(/<div id="app"><\/div>/, () => `<div id="app">${boot}</div>`)
// A function, so `$&` and friends inside the bundle are not read as replacement patterns.
html = html.replace(/<\/body>/i, () => body + '\n</body>')
fs.writeFileSync(out, html)
console.log('wrote', out, (fs.statSync(out).size / 1e6).toFixed(1), 'MB; scripts:', scripts.length)
