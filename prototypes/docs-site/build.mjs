// Bundles the prototype into ONE self-contained index.html.
//   npm install && npm run build
//   MANUAL_REF=<git ref>     read docs/manual from that ref instead of the working tree
//   CHANGELOG_HEAD=<git ref> end of the "unreleased" range (default origin/main)
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'
import { marked } from 'marked'
import { SECTIONS, DEV, HIGHLIGHTS, WHO } from './src/outline.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '../..')
const MOTION = path.join(REPO, 'prototypes/cheese-motion/src')
const MANUAL_REF = process.env.MANUAL_REF || ''
const OUT = process.env.OUT || path.join(HERE, 'index.html')

const git = (...args) => execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8', maxBuffer: 64 << 20, env: { ...process.env, TZ: 'Asia/Shanghai' } })
// `local:<file>` pages are written here (content/) until they move into docs/manual.
function readManual(file) {
  if (file.startsWith('local:')) return fs.readFileSync(path.join(HERE, 'content', file.slice(6)), 'utf8')
  if (MANUAL_REF) return git('show', `${MANUAL_REF}:docs/manual/${file}`)
  const f = path.join(REPO, 'docs/manual', file)
  if (!fs.existsSync(f)) throw new Error(`docs/manual/${file} not in this checkout; until PR #1737 lands, run with MANUAL_REF=origin/task/126fa903`)
  return fs.readFileSync(f, 'utf8')
}
const manualDate = (file) => file.startsWith('local:') ? git('log', '-1', '--date=format-local:%Y-%m-%d', '--format=%ad', '--', `prototypes/docs-site/content/${file.slice(6)}`).trim() || '待提交' : git('log', '-1', '--date=format-local:%Y-%m-%d', '--format=%ad', MANUAL_REF || 'HEAD', '--', `docs/manual/${file}`).trim()

// ---------- logo motion (direction A「冒孔」) lifted from the motion prototype ----------
const tpl = fs.readFileSync(path.join(MOTION, 'template.html'), 'utf8')
const parts = JSON.parse(fs.readFileSync(path.join(MOTION, 'parts.json'), 'utf8'))
const GRADS = parts.grads; delete parts.grads
const cut = (a, b) => { const i = tpl.indexOf(a), j = tpl.indexOf(b, i); if (i < 0 || j < 0) throw new Error('marker ' + a); return tpl.slice(i, j) }
const motionCore = cut('const P = __PARTS__;', '// ---------- 四个方向').replace('__PARTS__', JSON.stringify(parts))
const bubble = cut("{\n  key:'bubble'", "{\n  key:'bite'").trim().replace(/,$/, '')
const stage = cut('function stageFor(svg){', '// 调试用')
const motionModule = `${motionCore}\nconst BUBBLE = ${bubble};\n${stage}\nexport { build, stageFor, BUBBLE };`
// The finished frame of the animation is the static logo; it carries its own gradients.
const buildLogo = new Function(motionCore + '\nreturn build;')()
const LOGO_SVG = buildLogo('lg').replace('<defs>', '<defs>' + GRADS).replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ')
const LOGO_URI = 'data:image/svg+xml;base64,' + Buffer.from(LOGO_SVG).toString('base64')

// ---------- user manual ----------
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
// Prose calls the product 知是, as the app does; the lockup 知是 · Cheese is for the brand only.
const fix = (s) => s.replace(/小队/g, '团队').replace(/(?<![\w-])Cheese(?![\w-])/g, '知是')
const INFO = '<svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>'
const slugOf = {}, WHERE = {}
SECTIONS.forEach(([sec, , , groups]) => groups.forEach(([, items]) => items.forEach(([slug, , src]) => {
  WHERE[slug] = sec
  if (typeof src === 'string') slugOf[src.replace(/^local:/, '').replace(/\.md$/, '')] = slug
})))
DEV.forEach(([, items]) => items.forEach(([slug]) => { WHERE[slug] = 'dev' }))

function mdPage(file) {
  const raw = fix(readManual(file)).replace(/^---\n[\s\S]*?\n---\n/, '')
  const renderer = new marked.Renderer()
  let n = 0
  renderer.heading = function ({ tokens, depth }) {
    let t = this.parser.parseInline(tokens), id = ''
    t = t.replace(/\s*\{#([\w-]+)\}\s*$/, (_, x) => { id = x; return '' })
    if (depth === 1) return ''
    id ||= 'h' + n++
    return depth === 2 ? `<h2 id="${id}">${t}<a class="anchor" href="#${id}">#</a></h2>` : `<h${depth} id="${id}">${t}</h${depth}>`
  }
  renderer.link = function ({ href, tokens }) {
    const t = this.parser.parseInline(tokens)
    const m = /^\/([\w-]+)(?:\.md)?(?:#([\w-]+))?$/.exec(href)
    if (m) { const slug = slugOf[m[1]] || m[1]; return `<a class="link" href="#/${WHERE[slug] || 'start'}/${slug}${m[2] ? '#' + m[2] : ''}">${t}</a>` }
    return `<a class="link" href="${href}" target="_blank" rel="noopener">${t}</a>`
  }
  renderer.image = ({ text }) => `<figure class="shot-todo"><span>截图待补：${esc(text || '界面截图')}</span></figure>`
  renderer.blockquote = function ({ tokens }) { return `<div class="callout note">${INFO}<div>${this.parser.parse(tokens)}</div></div>` }
  let html = marked.parse(raw, { renderer })
  let lede = ''
  html = html.replace(/^\s*<p>([\s\S]*?)<\/p>/, (_, p) => { lede = p; return '' })
  return { lede, body: html, src: file.startsWith('local:') ? `prototypes/docs-site/content/${file.slice(6)}` : `docs/manual/${file}`, updated: manualDate(file) }
}

const outlineBody = (points, dev) => `<div class="callout ${dev ? 'note' : 'warn'}">${INFO}<p>${dev ? '<strong>这篇还没写正文。</strong>下面是按当前代码核实过的要点，正文照着它展开。' : '<strong>这一页还没写。</strong>下面是它要讲的内容，上线前会补齐。'}</p></div>
<h2 id="outline">要讲的内容<a class="anchor" href="#outline">#</a></h2><ol class="outline">${points.map((p) => `<li>${esc(p)}</li>`).join('')}</ol>`

// diagrams/<slug>.<type>.json is the source; src/diagrams.sh renders it to diagrams/<slug>.html with archify.
// The rendered viewer is inlined (iframe srcdoc) so the preview stays one file; its query reads are
// redirected so the page can pass embed mode and the current theme.
const DIAGRAMS = {}
function diagramFor(slug) {
  const dir = path.join(HERE, 'diagrams')
  const spec = fs.existsSync(dir) && fs.readdirSync(dir).find((f) => f.startsWith(slug + '.') && f.endsWith('.json'))
  if (!spec) return ''
  const file = path.join(dir, slug + '.html')
  if (!fs.existsSync(file)) throw new Error(`diagrams/${slug}.html missing; run src/diagrams.sh`)
  const { meta } = JSON.parse(fs.readFileSync(path.join(dir, spec), 'utf8'))
  DIAGRAMS[slug] = fs.readFileSync(file, 'utf8').replace(/URLSearchParams\((?:window\.)?location\.search\)/g, 'URLSearchParams(window.__archifyQuery || location.search)')
  const [w, h] = meta.viewBox || [1200, 760]
  return `<figure class="archify"><iframe data-diagram="${slug}" title="${esc(meta.title)}" loading="lazy" style="aspect-ratio:${w}/${h}"></iframe><figcaption><span>${esc(meta.title)}</span><button data-diagram-open="${slug}">全屏查看：可缩放、搜索、导出 ↗</button></figcaption></figure>`
}

const P = {}
function addPages(sec, groups) {
  return groups.map(([group, items]) => [group, items.map(([slug, title, src]) => {
    const draft = typeof src !== 'string'
    P[sec + '/' + slug] = draft
      ? { title, lede: esc(src.lede), points: src.points, body: diagramFor(slug) + outlineBody(src.points, sec === 'dev'), src: sec === 'dev' ? '按当前代码撰写' : 'docs/manual', updated: '待写', draft }
      : { title, ...mdPage(src) }
    return draft ? [slug, title, 1] : [slug, title]
  })])
}
const NAV = {
  ...Object.fromEntries(SECTIONS.map(([key, label, icon, groups]) => [key, { label, icon, first: groups[0][1][0][0], groups: addPages(key, groups) }])),
  dev: { label: '开发文档', icon: 'code', first: 'overview', lock: '仅管理员', groups: addPages('dev', DEV) },
  changelog: { label: '更新日志', icon: 'log', first: '', groups: [] },
}

const trouble = fix(readManual('troubleshooting.md'))
const FAQ = [...trouble.matchAll(/^## (.+?)\s*\{#([\w-]+)\}\n+([\s\S]*?)(?=\n## |$)/gm)].map((m) => ({ q: m[1], id: m[2], a: marked.parseInline(m[3].trim().split(/\n\n/)[0]) }))

// ---------- changelog ----------
// 455c4530 is what the 2026-09-23 "Deploy (prod RUC box)" run shipped; 0.17.0 is the last GitHub release.
const PROD_0180 = '455c453012d316506f15da35298d46a1c1d55417'
const HEAD_REF = process.env.CHANGELOG_HEAD || 'origin/main'
const prLog = (range) => git('log', '--first-parent', '--date=format-local:%Y-%m-%d', '--format=%ad%x09%s', range).trim().split('\n').map((l) => l.split('\t')).map(([d, s]) => {
  const pr = (/\(#(\d+)\)\s*$/.exec(s) || [])[1]
  const kind = (/^(\w+)/.exec(s) || [])[1]
  return { d, s: s.replace(/\s*\(#\d+\)\s*$/, ''), pr, kind }
}).filter((x) => x.pr)
const unrel = prLog(`${PROD_0180}..${HEAD_REF}`), r018 = prLog(`0.17.0..${PROD_0180}`)
const nfix = (l) => l.filter((x) => x.kind === 'fix').length
const RELEASES = [
  { ver: '未发布', id: 'unreleased', date: `${unrel.at(-1)?.d.slice(5).replace('-', '/')} 之后`, env: '已在测试环境，下次正式发布时带上', list: unrel, hl: HIGHLIGHTS.unreleased },
  { ver: '0.18.0', id: 'v0-18-0', date: '2026-09-23', env: '上线正式环境 · 建议版本号', list: r018, hl: { ...HIGHLIGHTS['0.18.0'], fix: [[`共 ${nfix(r018)} 项修复，集中在远端执行、预览、会话恢复和文案`, null]] } },
  { ver: '0.17.0', id: 'v0-17-0', date: '2026-07-15', env: '正式发布', list: [{ d: '2026-07-15', s: 'Fusion merge: unify the platform and the AI layer into one platform', pr: '46', kind: 'feat' }], hl: HIGHLIGHTS['0.17.0'] },
]

// ---------- bundle ----------
// island/room.html is built from frontend/ by island/build.mjs and committed, so
// this build does not need the frontend's dependencies.
const ROOM = fs.readFileSync(path.join(HERE, 'island/room.html'), 'utf8')
const DATA = { NAV, P, WHERE, FAQ, RELEASES, WHO, DIAGRAMS, ROOM, LOGO: LOGO_URI }
const virtual = {
  name: 'virtual',
  setup(b) {
    b.onResolve({ filter: /^virtual:/ }, (a) => ({ path: a.path, namespace: 'virtual' }))
    b.onLoad({ filter: /.*/, namespace: 'virtual' }, (a) => ({
      contents: a.path === 'virtual:motion' ? motionModule : Object.entries(DATA).map(([k, v]) => `export const ${k} = ${JSON.stringify(v)};`).join('\n'),
      loader: 'js',
    }))
  },
}
const { outputFiles } = await esbuild.build({ entryPoints: [path.join(HERE, 'src/main.js')], bundle: true, format: 'iife', minify: true, target: 'es2022', write: false, plugins: [virtual] })
const js = outputFiles[0].text

let html = fs.readFileSync(path.join(HERE, 'src/index.html'), 'utf8')
html = html.replace('/*__CSS__*/', () => fs.readFileSync(path.join(HERE, 'src/style.css'), 'utf8'))
  .replace('<!--__GRADS__-->', () => GRADS)
  .replace('/*__JS__*/', () => js.replace(/<\/script/g, '<\\/script'))
  .replaceAll('__IMG_LOGO_SVG__', LOGO_URI)
fs.writeFileSync(OUT, html)
console.log(`${path.relative(process.cwd(), OUT)} ${(html.length / 1024).toFixed(0)} KB · pages ${Object.keys(P).length} · faq ${FAQ.length} · unreleased ${unrel.length} · 0.18.0 ${r018.length}`)
