// 把单文件构建的 JS / CSS 内联进 index.html，输出一个自包含的页面。
// 用法：node scripts/one-thing-demo-inline.mjs <dist 目录> <输出 html>
import fs from 'node:fs'
import path from 'node:path'
const [dist, out] = process.argv.slice(2)
let html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8')
const esc = (s) => s.replace(/\ufffd/g, '\\ufffd').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, (c) => '\\x' + c.charCodeAt(0).toString(16).padStart(2, '0'))
html = html.replace(/<script[^>]*src="\.\/(assets\/[^"]+\.js)"[^>]*><\/script>/g, (_, f) => {
  const js = esc(fs.readFileSync(path.join(dist, f), 'utf8')).replace(/<\/script/gi, '<\\/script')
  return '' // moved to end of body so the DOM exists when the IIFE runs
  + `<!--inline:${f}-->`
})
const scripts = [...html.matchAll(/<!--inline:(assets\/[^>]+\.js)-->/g)].map((m) => m[1])
html = html.replace(/<!--inline:[^>]+-->/g, '')
html = html.replace(/<link[^>]*rel="modulepreload"[^>]*>/g, '')
html = html.replace(/<link[^>]*rel="stylesheet"[^>]*href="\.\/(assets\/[^"]+\.css)"[^>]*>/g, (_, f) => `<style>${fs.readFileSync(path.join(dist, f), 'utf8').replace(/<\/style/gi, '<\\/style')}</style>`)
const body = scripts.map((f) => `<script>${esc(fs.readFileSync(path.join(dist, f), 'utf8')).replace(/<\/script/gi, '<\\/script')}</script>`).join('\n')
html = html.replace(/<\/body>/i, () => body + '\n</body>') // a function, so `$&` etc. inside the bundle are not read as replacement patterns
fs.writeFileSync(out, html)
console.log('wrote', out, (fs.statSync(out).size / 1e6).toFixed(1), 'MB; scripts:', scripts.length)
