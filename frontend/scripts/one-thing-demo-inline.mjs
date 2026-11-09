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
// A function, so `$&` and friends inside the bundle are not read as replacement patterns.
html = html.replace(/<\/body>/i, () => body + '\n</body>')
fs.writeFileSync(out, html)
console.log('wrote', out, (fs.statSync(out).size / 1e6).toFixed(1), 'MB; scripts:', scripts.length)
