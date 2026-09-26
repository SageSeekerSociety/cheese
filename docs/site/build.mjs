// Builds the documentation site into frontend/public/docs (served at /docs/).
//
//   cd docs/site && npm ci && npm run build          # OUT=<dir> to build elsewhere
//
// Content is docs/manual/*.md (user docs, public) and docs/manual/dev/*.md
// (developer docs, platform admins only — nginx asks the backend before serving
// anything under /docs/dev/). Every URL is a prerendered HTML file; src/app.js
// adds behaviour. The site's shape lives in src/structure.mjs.
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'
import { marked } from 'marked'
import { SECTIONS, DEV, REDIRECTS, HIGHLIGHTS, WHO } from './src/structure.mjs'
import { esc, docPage, changelogPage, changelogFeed, downloadPage, devGatePage, redirectPage, notFoundPage, ic } from './src/render.mjs'
import { homePage } from './src/home.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '../..')
const MANUAL = path.join(REPO, 'docs/manual')
const OUT = path.resolve(process.env.OUT || path.join(REPO, 'frontend/public/docs'))
const SITE = 'https://okcheese.com'

const git = (...args) => execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8', maxBuffer: 64 << 20, env: { ...process.env, TZ: 'Asia/Shanghai' } })
const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exit(1) }
const rel = (p) => path.relative(REPO, p).split(path.sep).join('/')
const hash = (buf) => crypto.createHash('sha256').update(buf).digest('hex').slice(0, 10)

// ---------- logo: motion direction A「冒孔」 ----------
const LOGO_DIR = path.join(HERE, 'logo')
const tpl = fs.readFileSync(path.join(LOGO_DIR, 'template.html'), 'utf8')
const parts = JSON.parse(fs.readFileSync(path.join(LOGO_DIR, 'parts.json'), 'utf8'))
const GRADS = parts.grads; delete parts.grads
const cut = (a, b) => { const i = tpl.indexOf(a), j = tpl.indexOf(b, i); if (i < 0 || j < 0) fail(`logo template marker ${a}`); return tpl.slice(i, j) }
const motionCore = cut('const P = __PARTS__;', '// ---------- 四个方向').replace('__PARTS__', JSON.stringify(parts))
const bubble = cut("{\n  key:'bubble'", "{\n  key:'bite'").trim().replace(/,$/, '')
const stage = cut('function stageFor(svg){', '// 调试用')
const motionModule = `${motionCore}\nconst BUBBLE = ${bubble};\n${stage}\nexport { build, stageFor, BUBBLE };`
const LOGO_SVG = new Function(motionCore + '\nreturn build;')()('lg').replace('<defs>', '<defs>' + GRADS).replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ')

// ---------- markdown ----------
const FM = /^---\n([\s\S]*?)\n---\n/
function frontmatter(raw) {
  const m = FM.exec(raw)
  const data = {}
  if (m) {
    let list = null
    for (const line of m[1].split('\n')) {
      const item = /^\s+-\s+(.*)$/.exec(line)
      if (item && list) { data[list].push(item[1].trim()); continue }
      const kv = /^([\w-]+):\s*(.*)$/.exec(line)
      if (!kv) continue
      list = null
      if (kv[2] === '') { data[kv[1]] = []; list = kv[1] } else data[kv[1]] = kv[2].trim()
    }
  }
  return { data, body: m ? raw.slice(m[0].length) : raw }
}

// Prose names the product 知是, as the app does; 小队 is 团队 everywhere now.
const fix = (s) => s.replace(/小队/g, '团队').replace(/(?<![\w-])Cheese(?![\w-])/g, '知是')
const plain = (html) => html.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
const INFO = ic('info')

function renderMarkdown(md, { file }) {
  const toc = []
  let auto = 0
  const renderer = new marked.Renderer()
  renderer.heading = function ({ tokens, depth }) {
    let t = this.parser.parseInline(tokens), id = ''
    t = t.replace(/\s*\{#([\w-]+)\}\s*$/, (_, x) => { id = x; return '' })
    // The page title is rendered by the template; keep its anchor so /page#page links still land.
    if (depth === 1) return id ? `<span id="${id}" class="page-anchor"></span>` : ''
    id ||= `s${auto++}`
    if (depth <= 3) toc.push({ level: depth, id, text: plain(t) })
    return depth === 2
      ? `<h2 id="${id}">${t}<a class="anchor" href="#${id}" aria-label="本节链接">#</a></h2>`
      : `<h${depth} id="${id}">${t}</h${depth}>`
  }
  renderer.link = function ({ href, tokens }) {
    const t = this.parser.parseInline(tokens)
    const m = /^\/((?:dev\/)?[\w-]+)(?:\.md)?(#[\w-]+)?$/.exec(href)
    if (m) return `<a class="link" href="/docs/${m[1]}${m[2] || ''}">${t}</a>`
    if (href.startsWith('#')) return `<a class="link" href="${href}">${t}</a>`
    return `<a class="link" href="${esc(href)}" rel="noopener">${t}</a>`
  }
  renderer.image = ({ href, text }) => {
    const src = href.startsWith('/') ? `/docs${href}` : href
    return `<figure><div class="shot"><img src="${esc(src)}" alt="${esc(text)}" loading="lazy"></div>${text ? `<figcaption>${esc(text)}</figcaption>` : ''}</figure>`
  }
  renderer.blockquote = function ({ tokens }) { return `<div class="callout note">${INFO}<div>${this.parser.parse(tokens)}</div></div>` }
  renderer.code = ({ text, lang }) => `<div class="code"><div class="code-bar"><span class="code-tab on">${esc(lang || 'text')}</span><button class="copy" data-copy aria-label="复制">${ic('copy')}</button></div><pre><code>${esc(text)}</code></pre></div>`
  renderer.table = function (token) { return `<div class="table-wrap">${marked.Renderer.prototype.table.call(this, token)}</div>` }
  let html
  try { html = marked.parse(md, { renderer }) } catch (e) { fail(`${file}: ${e.message}`) }
  let lede = ''
  html = html.replace(/^\s*<p>([\s\S]*?)<\/p>/, (_, p) => { lede = p; return '' })
  // one search chunk per h2 section
  const chunks = html.split(/(?=<h2 id=")/).map((part) => {
    const h = /^<h2 id="([\w-]+)">([\s\S]*?)<a class="anchor"/.exec(part)
    return { id: h ? h[1] : '', heading: h ? plain(h[2]) : '', text: plain(part.replace(/^<h2[\s\S]*?<\/h2>/, '')).replace(/\s+/g, ' ').trim() }
  })
  return { html, lede, toc, chunks }
}

const lastChanged = (file) => git('log', '-1', '--date=format-local:%Y-%m-%d', '--format=%ad', '--', rel(file)).trim()

// ---------- pages ----------
const pages = {} // slug (user) or dev/slug → page
const userNav = {} // section key → [[group, [page]]]
const KINDS = { 流程: 'flow', 参考: 'reference', 决策: 'decision', 操作: 'howto' }

for (const [key, label, , groups] of SECTIONS) {
  userNav[key] = groups.map(([group, slugs]) => [group, slugs.map((slug) => {
    const file = path.join(MANUAL, `${slug}.md`)
    if (!fs.existsSync(file)) fail(`docs/manual/${slug}.md is in src/structure.mjs but does not exist`)
    const raw = fix(fs.readFileSync(file, 'utf8'))
    const { data, body } = frontmatter(raw)
    if (!data.title) fail(`docs/manual/${slug}.md has no title`)
    const r = renderMarkdown(body, { file: rel(file) })
    const page = { slug, section: key, sectionLabel: label, group, title: data.title, url: `/docs/${slug}`, mdUrl: `/docs/${slug}.md`, src: rel(file), updated: lastChanged(file), source: body, ...r, summary: data.summary || plain(r.lede) }
    pages[slug] = page
    return page
  })])
}
for (const f of fs.readdirSync(MANUAL)) {
  if (f.endsWith('.md') && f !== 'README.md' && !pages[f.slice(0, -3)]) fail(`docs/manual/${f} is not placed in src/structure.mjs`)
}

// developer pages, written by hand: frontmatter is the page's declared type
const devFiles = {}
for (const f of fs.readdirSync(path.join(MANUAL, 'dev'))) {
  if (!f.endsWith('.md')) continue
  const file = path.join(MANUAL, 'dev', f)
  const { data, body } = frontmatter(fs.readFileSync(file, 'utf8'))
  const where = `docs/manual/dev/${f}`
  for (const k of ['title', 'kind', 'summary']) if (!data[k]) fail(`${where}: frontmatter needs "${k}"`)
  if (!KINDS[data.kind]) fail(`${where}: kind must be one of ${Object.keys(KINDS).join(' / ')}`)
  const covers = Array.isArray(data.covers) ? data.covers : []
  if ((data.kind === '流程' || data.kind === '参考') && !covers.length) fail(`${where}: a ${data.kind} page must list the code it covers`)
  for (const c of covers) if (!fs.existsSync(path.join(REPO, c))) fail(`${where}: covers ${c}, which does not exist — update the page or the path`)
  devFiles[f.slice(0, -3)] = { data: { ...data, covers }, body, file }
}

// developer pages, generated from the code they describe
const gen = (script) => JSON.parse(execFileSync('python3', [path.join(HERE, 'gen', script)], { encoding: 'utf8', maxBuffer: 16 << 20 }))
const mdCell = (s) => String(s ?? '').replace(/\|/g, '\\|').replace(/\n+/g, ' ')
function referencePages() {
  const out = {}
  const cli = gen('cli.py')
  const connector = (/## Commands[\s\S]*?```\n([\s\S]*?)```/.exec(fs.readFileSync(path.join(REPO, 'cli/README.md'), 'utf8')) || [])[1] || ''
  out['ref-cli'] = {
    title: 'CLI 与平台工具全表', kind: '参考', covers: ['backend/sandbox/cheese', 'cli/README.md'],
    summary: '沙盒里 cheese 命令的全部子命令、会话侧的全部平台工具，以及用户电脑上连接器的命令。',
    body: `# CLI 与平台工具全表 {#ref-cli}

沙盒里 cheese 命令的全部子命令、会话侧的全部平台工具，以及用户电脑上连接器的命令。原理见 [cheese CLI 原理](/dev/cli)。

## 机器上的 cheese 子命令 {#commands}

从 \`backend/sandbox/cheese\` 的 \`build_parser()\` 读出，共 ${cli.commands.length} 条。

| 命令 | 做什么 | 参数 |
|---|---|---|
${cli.commands.map((c) => `| \`cheese ${c.name}\` | ${mdCell(c.help)} | ${c.args.map((a) => `\`${a}\``).join(' ')} |`).join('\n')}

## 会话侧的平台工具 {#tools}

从 \`PLATFORM_TOOLS\` 读出，共 ${cli.tools.length} 个。它们只需要平台 API，机器离线时照样可用。必填参数加粗。

| 工具 | 说明 | 参数 |
|---|---|---|
${cli.tools.map((t) => `| \`${t.name}\` | ${mdCell(t.description.split(/(?<=[。.])\s*/)[0])} | ${t.params.map((p) => (t.required.includes(p) ? `**\`${p}\`**` : `\`${p}\``)).join(' ')} |`).join('\n')}

## 用户电脑上的连接器 {#connector}

摘自 \`cli/README.md\`。

\`\`\`text
${connector.trim()}
\`\`\`
`,
  }
  const env = gen('env.py')
  out['ref-env'] = {
    title: '环境变量全表', kind: '参考', covers: ['backend/app/core/config.py'],
    summary: `后端读取的全部 ${env.fields.length} 个设置：环境变量名、类型、默认值和代码里的说明。`,
    body: `# 环境变量全表 {#ref-env}

后端读取的全部 ${env.fields.length} 个设置：环境变量名、类型、默认值和代码里的说明。从 \`backend/app/core/config.py\` 的 \`Settings\` 解析，不导入模块，所以不需要一个可用的环境。

| 环境变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
${env.fields.map((f) => `| ${f.env.map((e) => `\`${e}\``).join('<br>')} | \`${mdCell(f.type)}\` | \`${mdCell(f.default).slice(0, 80)}\` | ${mdCell(f.doc).slice(0, 400)} [↗](https://github.com/SageSeekerSociety/cheese/blob/main/backend/app/core/config.py#L${f.line}) |`).join('\n')}
`,
  }
  const wfDir = path.join(REPO, '.github/workflows')
  const suites = JSON.parse(fs.readFileSync(path.join(REPO, '.github/scripts/required-ci-paths.json'), 'utf8'))
  const workflows = fs.readdirSync(wfDir).filter((f) => f.endsWith('.yml')).sort().map((f) => {
    const src = fs.readFileSync(path.join(wfDir, f), 'utf8')
    const name = ((/^name:\s*(.+)$/m.exec(src) || [])[1] || f).replace(/^['"]|['"]$/g, '')
    const block = (/^on:\s*\n((?:[ \t]+.*\n|[ \t]*\n)+)/m.exec(src) || [])[1]
    const inline = (/^on:\s*(\S.*)$/m.exec(src) || [])[1]
    const triggers = block ? [...new Set([...block.matchAll(/^ {2}([a-z_]+):/gm)].map((m) => m[1]))] : (inline || '').replace(/[[\]]/g, '').split(',').map((s) => s.trim()).filter(Boolean)
    const cron = [...src.matchAll(/cron:\s*['"]([^'"]+)['"]/g)].map((m) => m[1])
    const lead = src.split('\n').filter((l) => /^#(?!!)/.test(l)).slice(0, 3).map((l) => l.replace(/^#\s?/, '')).join(' ')
    return { f, name, triggers, cron, lead }
  })
  out['ref-ci'] = {
    title: 'CI 工作流全表', kind: '参考', covers: ['.github/workflows/', '.github/scripts/required-ci-paths.json'],
    summary: `仓库全部 ${workflows.length} 个 GitHub Actions 工作流的触发方式，以及合并前必须通过的检查按改动路径怎么选。`,
    body: `# CI 工作流全表 {#ref-ci}

仓库全部 ${workflows.length} 个 GitHub Actions 工作流的触发方式，以及合并前必须通过的检查按改动路径怎么选。设计思路见 [CI 设计](/dev/ci)。

## 合并前必须通过的检查 {#required}

\`Required CI\` 按这次合并改到的路径挑出要跑的套件（\`.github/scripts/required-ci-paths.json\`）：选中的必须成功，没选中的必须是跳过，缺一个都不算通过。

| 套件 | 改到这些路径时运行 |
|---|---|
${Object.entries(suites).map(([s, pats]) => `| \`${s}\` | ${pats.map((p) => `\`${p}\``).join(' ')} |`).join('\n')}

## 全部工作流 {#workflows}

| 文件 | 名称 | 触发 | 说明 |
|---|---|---|---|
${workflows.map((w) => `| \`${w.f}\` | ${mdCell(w.name)} | ${w.triggers.map((t) => `\`${t}\``).join(' ')}${w.cron.length ? `<br>定时 ${w.cron.map((c) => `\`${c}\``).join(' ')}` : ''} | ${mdCell(w.lead).slice(0, 220)} |`).join('\n')}
`,
  }
  return out
}

// live queries: indexes computed from every developer page's declared type and coverage
function indexPages(all) {
  const byPath = {}
  for (const [slug, p] of Object.entries(all)) for (const c of p.covers || []) (byPath[c] ||= []).push([slug, p])
  const byKind = {}
  for (const [slug, p] of Object.entries(all)) (byKind[p.kind] ||= []).push([slug, p])
  return {
    'by-path': {
      title: '按代码路径查文档', kind: '参考', covers: [],
      summary: '要改哪块代码，先查这里：每个被文档覆盖的路径，列出讲它的页。',
      body: `# 按代码路径查文档 {#by-path}

要改哪块代码，先查这里：每个被文档覆盖的路径，列出讲它的页。这一页由各页开头声明的「涉及代码」自动生成；某条路径被删或改名时构建会失败，提醒更新对应的页。

| 路径 | 讲它的页 |
|---|---|
${Object.keys(byPath).sort().map((d) => `| \`${d}\` | ${byPath[d].map(([s, p]) => `[${p.title}](/dev/${s})`).join('、')} |`).join('\n')}
`,
    },
    'by-kind': {
      title: '按类型查文档', kind: '参考', covers: [],
      summary: '开发文档分四类：流程讲一件事怎么走完，参考供查阅，决策讲为什么这样，操作是照着做的步骤。',
      body: `# 按类型查文档 {#by-kind}

开发文档分四类：流程讲一件事怎么走完，参考供查阅，决策讲为什么这样，操作是照着做的步骤。每页开头必须声明类型和一句话摘要，流程和参考还要列出涉及的代码；缺了构建不通过。

${Object.keys(KINDS).filter((k) => byKind[k]).map((k) => `## ${k} {#${KINDS[k]}}\n\n${byKind[k].map(([s, p]) => `- [${p.title}](/dev/${s})：${p.summary}`).join('\n')}`).join('\n\n')}
`,
    },
  }
}

const devSources = {}
for (const [slug, f] of Object.entries(devFiles)) devSources[slug] = { ...f.data, body: f.body, file: f.file }
for (const [slug, g] of Object.entries(referencePages())) devSources[slug] = { ...g, generated: true }
for (const [slug, g] of Object.entries(indexPages(devSources))) devSources[slug] = { ...g, generated: true }

const devNav = DEV.map(([group, slugs]) => [group, slugs.map((slug) => {
  const d = devSources[slug]
  if (!d) fail(`developer page "${slug}" is in src/structure.mjs but docs/manual/dev/${slug}.md does not exist and nothing generates it`)
  const r = renderMarkdown(d.body, { file: d.file ? rel(d.file) : `generated:${slug}` })
  const page = {
    slug, section: 'dev', sectionLabel: '开发文档', group, title: d.title, url: `/docs/dev/${slug}`, mdUrl: `/docs/dev/${slug}.md`,
    src: d.file ? rel(d.file) : '', updated: d.file ? lastChanged(d.file) : '', generated: !!d.generated,
    kind: d.kind, kindKey: KINDS[d.kind], covers: d.covers, summary: d.summary, source: d.body, ...r,
  }
  pages[`dev/${slug}`] = page
  return page
})])
for (const slug of Object.keys(devFiles)) if (!pages[`dev/${slug}`]) fail(`docs/manual/dev/${slug}.md is not placed in src/structure.mjs`)

// ---------- diagrams (archify) ----------
// diagrams/<slug>.<type>.json is the source; `npm run diagrams` renders <slug>.html.
const DIAGRAMS = path.join(HERE, 'diagrams')
for (const f of fs.readdirSync(DIAGRAMS).filter((f) => f.endsWith('.json'))) {
  const slug = f.split('.')[0]
  const page = pages[`dev/${slug}`] || pages[slug]
  if (!page) fail(`diagrams/${f} names page "${slug}", which does not exist`)
  const htmlFile = path.join(DIAGRAMS, `${slug}.html`)
  if (!fs.existsSync(htmlFile)) fail(`diagrams/${slug}.html is missing; run npm run diagrams`)
  const { meta } = JSON.parse(fs.readFileSync(path.join(DIAGRAMS, f), 'utf8'))
  const [w, h] = meta.viewBox || [1200, 760]
  const url = `${page.section === 'dev' ? '/docs/dev' : '/docs'}/diagrams/${slug}.html`
  page.diagram = { url, file: htmlFile }
  page.html = `<figure class="archify"><iframe data-diagram="${url}" title="${esc(meta.title)}" loading="lazy" style="aspect-ratio:${w}/${h}"></iframe><figcaption><span>${esc(meta.title)}</span><a href="${url}" target="_blank" rel="noopener">全屏查看：可缩放、搜索、导出 ↗</a></figcaption></figure>` + page.html
}

// ---------- every internal link must land on a page and, if it names one, an anchor ----------
{
  const ids = new Map(Object.values(pages).map((p) => [p.url, new Set([...p.html.matchAll(/\sid="([\w-]+)"/g)].map((m) => m[1]))]))
  const known = new Set(['/docs/', '/docs/changelog', '/docs/download', '/docs/llms.txt', '/docs/dev/llms.txt', '/docs/manual.zip', '/docs/changelog.xml'])
  const broken = []
  for (const p of Object.values(pages)) {
    for (const [, url, anchor] of p.html.matchAll(/href="(\/docs\/[\w/.-]*)(?:#([\w-]+))?"/g)) {
      if (url.startsWith('/docs/diagrams/') || url.startsWith('/docs/dev/diagrams/') || known.has(url)) continue
      if (!ids.has(url)) broken.push(`${p.src || p.url}: ${url} is not a page`)
      else if (anchor && !ids.get(url).has(anchor)) broken.push(`${p.src || p.url}: ${url}#${anchor} has no such section`)
    }
  }
  if (broken.length) fail(`broken links:\n  ${broken.join('\n  ')}`)
}

// ---------- changelog ----------
// 455c4530 is what the 2026-09-23 "Deploy (prod RUC box)" run shipped; 0.17.0 is the last GitHub release.
const PROD_0180 = '455c453012d316506f15da35298d46a1c1d55417'
const HEAD_REF = process.env.CHANGELOG_HEAD || 'HEAD'
const prLog = (range) => git('log', '--first-parent', '--date=format-local:%Y-%m-%d', '--format=%ad%x09%s', range).trim().split('\n').filter(Boolean).map((l) => l.split('\t')).map(([d, s]) => {
  const pr = (/\(#(\d+)\)\s*$/.exec(s) || [])[1]
  return { d, s: s.replace(/\s*\(#\d+\)\s*$/, ''), pr, kind: (/^(\w+)/.exec(s) || [])[1] }
}).filter((x) => x.pr)
let unrel = [], r018 = []
try { unrel = prLog(`${PROD_0180}..${HEAD_REF}`); r018 = prLog(`0.17.0..${PROD_0180}`) } catch { fail('the changelog needs full git history (checkout fetch-depth: 0) and the 0.17.0 tag') }
const nfix = (l) => l.filter((x) => x.kind === 'fix').length
const RELEASES = [
  { ver: '未发布', id: 'unreleased', date: `${unrel.at(-1)?.d.slice(5).replace('-', '/') || '9/23'} 之后`, env: '已在测试环境，下次正式发布时带上', list: unrel, hl: HIGHLIGHTS.unreleased },
  { ver: '0.18.0', id: 'v0-18-0', date: '2026-09-23', env: '上线正式环境 · 建议版本号', list: r018, hl: { ...HIGHLIGHTS['0.18.0'], fix: [[`共 ${nfix(r018)} 项修复，集中在远端执行、预览、会话恢复和文案`, null]] } },
  { ver: '0.17.0', id: 'v0-17-0', date: '2026-07-15', env: '正式发布', list: [{ d: '2026-07-15', s: 'Fusion merge: unify the platform and the AI layer into one platform', pr: '46', kind: 'feat' }], hl: HIGHLIGHTS['0.17.0'] },
]

// ---------- FAQ, from the troubleshooting page ----------
const trouble = frontmatter(fix(fs.readFileSync(path.join(MANUAL, 'troubleshooting.md'), 'utf8'))).body
const FAQ = [...trouble.matchAll(/^## (.+?)\s*\{#([\w-]+)\}\n+([\s\S]*?)(?=\n## |$)/gm)].map((m) => ({ q: m[1], id: m[2], a: marked.parseInline(m[3].trim().split(/\n\n/)[0]) }))

// ---------- assets ----------
fs.rmSync(OUT, { recursive: true, force: true })
fs.mkdirSync(path.join(OUT, 'assets'), { recursive: true })
const write = (p, content) => { fs.mkdirSync(path.dirname(path.join(OUT, p)), { recursive: true }); fs.writeFileSync(path.join(OUT, p), content) }
const asset = (name, ext, content) => { const file = `assets/${name}-${hash(content)}.${ext}`; write(file, content); return `/docs/${file}` }

const virtual = {
  name: 'virtual',
  setup(b) {
    b.onResolve({ filter: /^virtual:motion$/ }, (a) => ({ path: a.path, namespace: 'virtual' }))
    b.onLoad({ filter: /.*/, namespace: 'virtual' }, () => ({ contents: motionModule, loader: 'js' }))
  },
}
const js = (await esbuild.build({ entryPoints: [path.join(HERE, 'src/app.js')], bundle: true, format: 'esm', minify: true, target: 'es2022', write: false, plugins: [virtual], legalComments: 'none' })).outputFiles[0].text
const css = (await esbuild.build({ entryPoints: [path.join(HERE, 'src/style.css')], bundle: true, minify: true, write: false })).outputFiles[0].text
const assets = {
  js: asset('app', 'js', js),
  css: asset('app', 'css', css),
  logo: asset('logo', 'svg', LOGO_SVG),
  room: asset('room', 'html', fs.readFileSync(path.join(HERE, 'island/room.html'))),
  // 三极行楷简体-粗 (三极字库, free for commercial use), subset to the home page's display headings by gen/font.sh.
  display: asset('display', 'woff2', fs.readFileSync(path.join(HERE, 'src/fonts/display.woff2'))),
}
for (const p of Object.values(pages)) if (p.diagram) write(p.diagram.url.replace(/^\/docs\//, ''), fs.readFileSync(p.diagram.file))
const images = path.join(MANUAL, 'public')
if (fs.existsSync(images)) fs.cpSync(images, OUT, { recursive: true })

// ---------- render ----------
const firstUrl = (key) => userNav[key][0][1][0].url
const site = {
  description: '知是 · Cheese 的使用文档、开发文档和更新日志：怎么开始、每个功能怎么用、遇到问题怎么办。',
  tabs: [
    ...SECTIONS.map(([key, label, icon]) => ({ key, label, icon, href: firstUrl(key) })),
    { key: 'dev', label: '开发文档', icon: 'code', href: '/docs/dev/overview', lock: true },
    { key: 'changelog', label: '更新日志', icon: 'log', href: '/docs/changelog' },
  ],
  userSections: SECTIONS.map(([key, label]) => ({ label, href: firstUrl(key) })),
}
const ctx = { site, assets, grads: GRADS }

const flatNav = (nav) => nav.flatMap(([, items]) => items)
for (const [key] of SECTIONS) {
  const list = flatNav(userNav[key])
  list.forEach((p, i) => write(`${p.slug}.html`, docPage(ctx, p, userNav[key], list[i - 1], list[i + 1])))
}
const devList = flatNav(devNav)
devList.forEach((p, i) => write(`dev/${p.slug}.html`, docPage(ctx, p, devNav, devList[i - 1], devList[i + 1])))
write('dev/index.html', redirectPage('/docs/dev/overview'))

const pageRefs = Object.fromEntries(Object.values(pages).filter((p) => p.section !== 'dev').map((p) => [p.slug, { url: p.url, title: p.title, sectionLabel: p.sectionLabel, summary: p.summary }]))
const devRefs = Object.fromEntries(devList.map((p) => [p.slug, { url: p.url, title: p.title, summary: p.summary }]))
pageRefs.__logo = assets.logo
const doors = SECTIONS.map(([key, label, icon]) => ({ key, label, icon, items: userNav[key].flatMap(([, items]) => items) }))
write('index.html', homePage(ctx, { releases: RELEASES, faq: FAQ, WHO, doors, pages: pageRefs, dev: devRefs }))
write('changelog.html', changelogPage(ctx, RELEASES))
write('changelog.xml', changelogFeed(RELEASES))
write('download.html', downloadPage(ctx, { base: 'https://github.com/SageSeekerSociety/cheese/releases/download/desktop-latest' }))
write('dev-gate.html', devGatePage(ctx))
write('404.html', notFoundPage(ctx))
for (const [from, to] of Object.entries(REDIRECTS)) {
  if (!pages[to]) fail(`redirect ${from} → ${to}: no such page`)
  write(`${from}.html`, redirectPage(`/docs/${to}`))
}
// The home page's interactive parts need a small map of page links and the role data.
write('home.json', JSON.stringify({ pages: pageRefs, who: WHO }))

// ---------- search indexes: public and developer, kept apart ----------
const searchIndex = (list) => JSON.stringify(list.flatMap((p) => p.chunks.map((c) => ({
  t: p.title, g: `${p.sectionLabel} · ${p.group}`, h: c.heading, u: c.id ? `${p.url}#${c.id}` : p.url, x: (c.id ? c.text : `${plain(p.lede)} ${c.text}`).slice(0, 600),
}))))
const publicPages = Object.values(pages).filter((p) => p.section !== 'dev')
write('search.json', searchIndex(publicPages))
write('dev/search.json', searchIndex(devList))
// What 问芝士 answers from (backend: app/domain/docs_site/retrieval.py). Public pages only,
// whole sections: the answer is shown to anyone signed in.
write('ask-index.json', JSON.stringify(publicPages.flatMap((p) => p.chunks.filter((c) => c.text).map((c) => ({
  title: p.title, heading: c.heading, url: c.id ? `${p.url}#${c.id}` : p.url, text: (c.id ? c.text : `${plain(p.lede)} ${c.text}`).slice(0, 4000),
})))))

// ---------- for models: llms.txt, a .md twin per page, and the whole manual ----------
const llms = (title, intro, nav) => [`# ${title}`, '', `> ${intro}`, '', ...nav.flatMap(([g, items]) => [`## ${g}`, '', ...items.map((p) => `- [${p.title}](${SITE}${p.mdUrl}): ${p.summary}`), ''])].join('\n')
const twin = (p, index) => `> ## Documentation Index\n> Fetch the complete documentation index at: ${SITE}${index}\n> Use this file to discover all available pages before exploring further.\n\n${p.source.replace(/\]\((\/[^)\s]*)\)/g, (_, u) => `](${SITE}/docs${u})`).trimStart()}`
for (const p of publicPages) write(`${p.slug}.md`, twin(p, '/docs/llms.txt'))
for (const p of devList) write(`dev/${p.slug}.md`, twin(p, '/docs/dev/llms.txt'))
write('llms.txt', llms('知是 · 使用说明', '知是是一个你和 AI 队友一起做项目的地方。这份文档讲怎么用它：从建第一个项目，到把 AI 做出来的成果合并进主分支。', SECTIONS.flatMap(([key]) => userNav[key])))
write('dev/llms.txt', llms('知是 · 开发文档', '按当前代码写的开发文档，写给改这个仓库的人和 agent。每页开头声明类型、摘要和涉及的代码。', devNav))
execFileSync('python3', ['-c', `
import sys, zipfile, pathlib
out, root = sys.argv[1], pathlib.Path(sys.argv[2])
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.glob('*.md')):
        if p.name != 'README.md':
            z.write(p, f'知是说明书/{p.name}')
`, path.join(OUT, 'manual.zip'), MANUAL])

console.log(`${rel(OUT)}: ${publicPages.length} public pages, ${devList.length} developer pages · app.js ${(js.length / 1024).toFixed(0)} KB · app.css ${(css.length / 1024).toFixed(0)} KB`)
