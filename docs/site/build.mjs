// Builds the documentation site into frontend/public/docs (served at /docs/).
//
//   cd docs/site && npm ci && npm run build          # OUT=<dir> to build elsewhere
//   DOCS_BASE= OUT=../../frontend/docs-host npm run build   # the docs host's copy, served at its root
//
// src/where.mjs says what DOCS_BASE changes; the frontend image carries both builds.
//
// Content is docs/manual/*.md (user docs, public) and docs/manual/dev/*.md
// (developer docs, platform admins only — nginx asks the backend before serving
// anything under dev/). Every URL is a prerendered HTML file; src/app.js
// adds behaviour. The site's shape lives in src/structure.mjs.
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'
import { marked } from 'marked'
import { SECTIONS, DEV, REDIRECTS, HIGHLIGHTS } from './src/structure.mjs'
import { esc, docHref, docPage, changelogPage, changelogFeed, downloadPage, devGatePage, redirectPage, notFoundPage, ic } from './src/render.mjs'
import { DEMO_FENCES, renderDemo, demoText, replaceFences, countFences, registerDataset, registerEmbed, registerSource, registerArchFacts, ciSelections } from './src/demos.mjs'
import { homePage } from './src/home.mjs'
import { BASE, SITE, PLATFORM } from './src/where.mjs'
import { selectSuites } from './src/ci-scope.mjs'
import { fitIndex, limitBreach, indexTextOf } from './src/memory-limits.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '../..')
const MANUAL = path.join(REPO, 'docs/manual')
const OUT = path.resolve(process.env.OUT || path.join(REPO, 'frontend/public/docs'))

// The demo scenes a fence can embed (`embed: <name>`), by their step titles.
const SCENES = path.join(REPO, 'frontend/src/views/demo/scenes')
for (const f of fs.readdirSync(SCENES).filter((f) => f.endsWith('.json'))) {
  registerEmbed(f.replace(/\.json$/, ''), JSON.parse(fs.readFileSync(path.join(SCENES, f), 'utf8')).steps.map((s) => s.label))
}

const git = (...args) => execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8', maxBuffer: 64 << 20, env: { ...process.env, TZ: 'Asia/Shanghai' } })
const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exit(1) }
const rel = (p) => path.relative(REPO, p).split(path.sep).join('/')
const hash = (buf) => crypto.createHash('sha256').update(buf).digest('hex').slice(0, 10)

// ---------- brand ----------
// The product's own files (docs/brand.md): the brand-colour mark for the tab icon
// and 芝士's avatar, and the in-product lockup — the single-colour mark and the
// 知是 wordmark, both in the text colour of whatever holds them, so one copy is
// right on either theme. Proportions are brand.md §4's, set in the stylesheet.
const BRAND = path.join(REPO, 'frontend/src/assets')
const LOGO_SVG = fs.readFileSync(path.join(BRAND, 'logo.svg'), 'utf8')
const inlineSvg = (file, cls) => fs.readFileSync(path.join(BRAND, file), 'utf8').trim()
  .replace('<svg xmlns="http://www.w3.org/2000/svg" ', `<svg class="${cls}" aria-hidden="true" fill="currentColor" `).replace(/ role="img" aria-label="[^"]*"/, '')
const LOCKUP = `<span class="brand-lockup">${inlineSvg('logo-plain.svg', 'brand-lockup-mark')}${inlineSvg('brand/wordmark-zh.svg', 'brand-lockup-word')}</span>`

// ---------- design tokens ----------
// Colours, radii, shadows, type and motion are the product's: the `:root` block
// and the dark block of frontend/src/style.css, copied in front of the docs
// stylesheet at build time, so a value lives in one place.
const TOKEN_BLOCKS = [':root {', ":root[data-theme='dark'] {"]
function productTokens() {
  const css = fs.readFileSync(path.join(REPO, 'frontend/src/style.css'), 'utf8')
  return TOKEN_BLOCKS.map((opener) => {
    const at = css.indexOf(`\n${opener}\n`)
    const end = css.indexOf('\n}\n', at)
    if (at < 0 || end < 0) fail(`frontend/src/style.css has no top-level «${opener} … }» block — the docs take their tokens from it`)
    return css.slice(at + 1, end + 2)
  }).join('\n')
}

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
  let collecting = true
  const renderer = new marked.Renderer()
  renderer.heading = function ({ tokens, depth }) {
    let t = this.parser.parseInline(tokens), id = ''
    t = t.replace(/\s*\{#([\w-]+)\}\s*$/, (_, x) => { id = x; return '' })
    // The page title is rendered by the template; keep its anchor so /page#page links still land.
    if (depth === 1) return id ? `<span id="${id}" class="page-anchor"></span>` : ''
    id ||= `s${auto++}`
    if (depth <= 3 && collecting) toc.push({ level: depth, id, text: plain(t) })
    return depth === 2
      ? `<h2 id="${id}">${t}<a class="anchor" href="#${id}" aria-label="本节链接">#</a></h2>`
      : `<h${depth} id="${id}">${t}</h${depth}>`
  }
  renderer.link = function ({ href, tokens }) {
    const t = this.parser.parseInline(tokens)
    if (!href.startsWith('#') && docHref(href) !== href) return `<a class="link" href="${docHref(href)}">${t}</a>`
    if (href.startsWith('#')) return `<a class="link" href="${href}">${t}</a>`
    return `<a class="link" href="${esc(href)}" rel="noopener">${t}</a>`
  }
  renderer.image = ({ href, text }) => {
    if (href.startsWith('/images/') && !fs.existsSync(path.join(MANUAL, 'public', href))) fail(`${file}: picture ${href} is not in docs/manual/public/images`)
    const src = href.startsWith('/') ? `${BASE}${href}` : href
    return `<figure><div class="shot"><img src="${esc(src)}" alt="${esc(text)}" loading="lazy"></div>${text ? `<figcaption>${esc(text)}</figcaption>` : ''}</figure>`
  }
  renderer.blockquote = function ({ tokens }) { return `<div class="callout note">${INFO}<div>${this.parser.parse(tokens)}</div></div>` }
  let demos = 0
  renderer.code = ({ text, lang }) => {
    // A demo fence is expanded here and nowhere else: the prerendered component
    // is what a browser gets, and the prose below is what a model gets.
    if (DEMO_FENCES.includes(lang)) return renderDemo(lang, text, `${file}: demo ${++demos}`)
    return `<div class="code"><div class="code-bar"><span class="code-lang">${esc(lang || 'text')}</span><button class="copy" data-copy aria-label="复制">${ic('copy')}</button></div><pre><code>${esc(text)}</code></pre></div>`
  }
  renderer.table = function (token) { return `<div class="table-wrap">${marked.Renderer.prototype.table.call(this, token)}</div>` }
  let html
  try { html = marked.parse(md, { renderer }) } catch (e) { fail(`${file}: ${e.message}`) }
  const fences = countFences(md)
  if (fences !== demos) fail(`${file}: ${fences} demo fences in the source but ${demos} expanded — the renderer only sees a fence at the top level`)
  let lede = ''
  // The first paragraph is the lede (after the title's anchor, which stays in place).
  html = html.replace(/^(\s*(?:<span [^>]*class="page-anchor"><\/span>)?\s*)<p>([\s\S]*?)<\/p>/, (_, anchor, p) => { lede = p; return anchor })

  // The same page again, with each demo cut down to a short piece of prose: this
  // is what the search and 问芝士 indexes are built from, so a model never pays
  // for the component's markup. The headings are the same, with the same ids.
  const text = replaceFences(md, (lang, body) => `\n${demoText(lang, body, { where: `${file}: demo` })}\n`)
  collecting = false
  auto = 0
  let textHtml
  try { textHtml = marked.parse(text, { renderer }) } catch (e) { fail(`${file}: ${e.message}`) }
  // one search chunk per h2 section
  const chunks = textHtml.split(/(?=<h2 id=")/).map((part) => {
    const h = /^<h2 id="([\w-]+)">([\s\S]*?)<a class="anchor"/.exec(part)
    return { id: h ? h[1] : '', heading: h ? plain(h[2]) : '', text: plain(part.replace(/^<h2[\s\S]*?<\/h2>/, '')).replace(/\s+/g, ' ').trim() }
  })
  return { html, lede, toc, chunks, text }
}

const lastChanged = (file) => git('log', '-1', '--date=format-local:%Y-%m-%d', '--format=%ad', '--', rel(file)).trim()

// ---------- pages ----------
const pages = {} // slug (user) or dev/slug → page
const userNav = {} // section key → [[group, [page]]]
const KINDS = { 流程: 'flow', 概念: 'concept', 参考: 'reference', 决策: 'decision', 操作: 'howto' }

for (const [key, label, , groups] of SECTIONS) {
  userNav[key] = groups.map(([group, slugs]) => [group, slugs.map((slug) => {
    const file = path.join(MANUAL, `${slug}.md`)
    if (!fs.existsSync(file)) fail(`docs/manual/${slug}.md is in src/structure.mjs but does not exist`)
    const raw = fix(fs.readFileSync(file, 'utf8'))
    const { data, body } = frontmatter(raw)
    if (!data.title) fail(`docs/manual/${slug}.md has no title`)
    const r = renderMarkdown(body, { file: rel(file) })
    const page = { slug, section: key, sectionLabel: label, group, title: data.title, path: `/${slug}`, url: `${BASE}/${slug}`, mdUrl: `${BASE}/${slug}.md`, src: rel(file), updated: lastChanged(file), ...r, source: r.text, summary: data.summary || plain(r.lede) }
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
  if (data.kind !== '决策' && data.kind !== '操作' && !covers.length) fail(`${where}: a ${data.kind} page must list the code it covers`)
  for (const c of covers) if (!fs.existsSync(path.join(REPO, c))) fail(`${where}: covers ${c}, which does not exist — update the page or the path`)
  devFiles[f.slice(0, -3)] = { data: { ...data, covers }, body, file }
}

// ---------- system prompt reference: the blocks, from the code that builds them ----------
// gen/prompt.py runs `build_system_prompt` and `build_session_opening` with one
// sample per switch and splits what comes back; this only lays it out, so a
// block that is added or renamed shows up here by itself.
const PROMPT_SRC = 'backend/app/domain/agent/harness/prompt.py'
const GITHUB = 'https://github.com/SageSeekerSociety/cheese'
const blob = (path, line) => `${GITHUB}/blob/main/${path}${line ? `#L${line}` : ''}`

// A fence wide enough to hold the text: a block may quote three backticks at the
// agent, and an ordinary fence would end right there.
const fenceFor = (text) => {
  let width = 3
  for (const run of text.match(/`+/g) || []) width = Math.max(width, run.length + 1)
  return '`'.repeat(width)
}
const code = (text, lang = 'text') => `${fenceFor(text)}${lang}\n${text}\n${fenceFor(text)}`
const fold = (summary, body) => `<details class="fold"><summary>${summary}</summary>\n\n${body}\n\n</details>`
// <summary> is HTML, not markdown, so inline `code` is turned into an element by hand.
const inline = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
const cell = (s) => mdCell(inline(s))
// A block is normally a `## ` part; the `skills` argument is the exception — it
// is a skill body with a `# ` heading of its own, so the page says so.
const blockLabel = (b) => (b.base ? '底稿（`base` 参数）' : b.headline ? b.title : `（不带标题的一段：${b.title}）`)

function promptReference() {
  const shell = devFiles['ref-prompt']
  if (!shell) fail('docs/manual/dev/ref-prompt.md is missing — gen/prompt.py embeds its sections into it')
  const p = gen('prompt.py')
  const out = []

  if (p.mode !== 'exec') out.push(`> 这一页这次是**静态解析**出来的：${p.note}。下面标着「动态生成」的块是源码里的表达式，不是渲染后的文本。`)
  if (p.unknown_params?.length) out.push(`> \`build_system_prompt\` 或 \`build_session_opening\` 有了生成器还不认识的参数：${p.unknown_params.map((name) => inline(name)).join('、')}——它们的样本值是按类型补的，见[样本参数](#samples)。` + `参数是自己加的，就把 \`docs/site/gen/prompt.py\` 里对应的一组样本补上。`)
  if (p.failed_samples?.length) out.push(`> 有样本跑不出来，它们没有出现在下面的表里：${p.failed_samples.map((f) => `${inline(f.name)}（${inline(f.error)}）`).join('、')}。`)

  out.push(`## 装配顺序 {#blocks}

一个新会话开场时芝士读到的就是这些块，顺序就是这个顺序。到「你的专家角色」为止是系统提示词（\`build_system_prompt\`），只有规矩，整个会话里一字不变；从「本话题现在的情况」起是第一条消息前面的开场快照（\`build_session_opening\`），会话接着跑时只把变了的那几段再说一次。「出现条件」是这一块的开关：不满足就整块不出现，一个字都不加。

| # | 块 | 出现条件 | 预算 |
|---|---|---|---|
${p.blocks.map((b, i) => `| ${i + 1} | ${cell(blockLabel(b))}${b.origin === 'dynamic' ? ' · 动态生成' : ''} | ${cell(b.condition)} | ${b.budget ? `${inline(b.budget.const)} = ${b.budget.value} 字符` : '—'} |`).join('\n')}`)

  out.push(`## 每一块的原文 {#texts}

按装配顺序逐块展开。\`{…}\` 是只有到运行时才有的值（参数、函数返回值）。

${p.blocks.map((b) => fold(`${inline(blockLabel(b))} · ${b.chars} 字符`, [
    `条件：${inline(b.condition)}`,
    b.budget ? `预算：${inline(b.budget.const)} = ${b.budget.value} 字符，超了按丢弃顺序压缩，并在末尾附一行说明` : '',
    b.origin === 'dynamic' ? `动态生成：这一块来自源码里的表达式，行号 ${b.source_line ? `[${b.source_line}](${blob(PROMPT_SRC, b.source_line)})` : '见上'}` : '',
    code(b.text),
    ...b.variants.map(([name, text]) => `**${inline(name)} 时的另一种形态**\n\n${code(text)}`),
  ].filter(Boolean).join('\n\n'))).join('\n\n')}`)

  if (p.samples.length) out.push(`## 样本参数 {#samples}

原文不是「大概长这样」写的：它是用下面这些参数真跑一遍 \`build_system_prompt\` 和 \`build_session_opening\` 得到的输出。「最小」是除底稿外什么都不传的样子，「全部打开」是每个开关都给值。

| 样本 | 参数 | 说明 |
|---|---|---|
${p.samples.map((s) => `| ${s.name} | ${s.params.map((x) => inline(x)).join('<br>')} | ${cell(s.about)} |`).join('\n')}`)

  out.push(`## 平台说明库 {#library}

\`skill_library/\` 里是平台自己的几份说明。\`chat.md\` 就是上面「在房间里说话」那一块（\`skills\` 参数），\`doc_form.md\` 拼进「当前话题的实况文档」；\`doc_writing.md\` 和 \`doc_blocks.md\` 不进系统提示词，作为 \`cheese-docs\` 技能发给会话，用到才读。

| 文件 | 名称 | 说明 |
|---|---|---|
${p.library.files.map((f) => `| [\`${f.file}\`](${blob(f.file)}) | ${cell(f.title)} | ${cell(f.description)} |`).join('\n')}

正文：

${p.library.files.map((f) => fold(`${inline(f.title)} · ${f.chars} 字符`, code(f.body))).join('\n\n')}`)

  out.push(`## 相关常量 {#constants}

\`prompt.py\` 里模块级的东西：预算、上限，和那些整段拼进提示词的文本。行号链到 GitHub 上的源码。

| 常量 | 类型 | 值 | 代码里的说明 |
|---|---|---|---|
${p.constants.map((c) => {
    const shown = c.kind === 'int' ? `\`${c.value}\`` : c.value.length <= 40 ? `\`${c.value}\`` : `${c.value.length} 字符${c.in_blocks.length ? '，见上面「' + inline(c.in_blocks[0]) + '」那一块' : ''}`
    return `| [\`${c.name}\`](${blob(PROMPT_SRC, c.line)}) | ${c.kind === 'int' ? '整数' : '文本'} | ${shown} | ${cell(c.comment || '—')} |`
  }).join('\n')}`)

  return {
    title: shell.data.title,
    kind: shell.data.kind,
    summary: shell.data.summary,
    covers: shell.data.covers,
    body: `${shell.body.trimEnd()}\n\n${out.join('\n\n')}\n`,
  }
}

// developer pages, generated from the code they describe
const genCache = {}
const gen = (script, input) => (genCache[script] ??= JSON.parse(execFileSync('python3', [path.join(HERE, 'gen', script)], { encoding: 'utf8', maxBuffer: 64 << 20, input })))
// The blocks of the system prompt, in the order build_system_prompt adds them.
// The context page's timeline is bound to this: the numbers it shows are the
// character counts of the text that function really produced.
registerDataset('prompt-blocks', gen('prompt.py').blocks.map((b) => ({ title: b.title, chars: b.chars })))
const mdCell = (s) => String(s ?? '').replace(/\|/g, '\\|').replace(/\n+/g, ' ')

// ---------- what the interactive demos show ----------
// A demo fence declares what it draws; the numbers behind it are read here, out
// of the code the page is about. Two runners below check the browser's copy of
// a rule against the real one and fail the build on any difference — a page
// that drifts from the code it describes breaks the site instead of quietly
// teaching the old rule.
const CI_PATHS = '.github/scripts/required-ci-paths.json'
const CI_WORKFLOW = '.github/workflows/required-ci.yml'

// suite -> the reusable workflow that runs it, read off the job gated on
// `needs.scope.outputs.<suite>`, plus that workflow's own name for the page.
function ciSource() {
  const patterns = JSON.parse(fs.readFileSync(path.join(REPO, CI_PATHS), 'utf8'))
  const yml = fs.readFileSync(path.join(REPO, CI_WORKFLOW), 'utf8')
  const marks = [...yml.matchAll(/^ {2}([a-z0-9_]+):$/gm)]
  const jobs = new Map(marks.map((m, i) => [m[1], yml.slice(m.index, marks[i + 1]?.index ?? yml.length)]))
  const declared = [...(jobs.get('scope') || '').matchAll(/^ {6}([a-z0-9_]+): \$\{\{ steps\.scope\.outputs\.\1 \}\}$/gm)].map((m) => m[1])
  const runs = {}
  for (const [job, body] of jobs) {
    const gate = /^ {4}if: needs\.scope\.outputs\.([a-z0-9_]+) == 'true'$/m.exec(body)
    const uses = /^ {4}uses: \.\/\.github\/workflows\/([\w.-]+)$/m.exec(body)
    if (gate && uses) runs[gate[1]] = uses[1]
  }
  const suites = Object.entries(patterns).map(([key, pats]) => {
    if (!declared.includes(key)) fail(`${CI_WORKFLOW}: the scope job has no «${key}» output, but ${CI_PATHS} calls it a suite`)
    if (!runs[key]) fail(`${CI_WORKFLOW}: no job runs when the scope selects «${key}» — the demo would have no workflow to name`)
    const name = ((/^name:\s*(.+)$/m.exec(fs.readFileSync(path.join(REPO, '.github/workflows', runs[key]), 'utf8')) || [])[1] || runs[key]).replace(/^['"]|['"]$/g, '')
    return { key, patterns: pats, workflow: `.github/workflows/${runs[key]}`, desc: name }
  })
  for (const key of Object.keys(runs)) if (!(key in patterns)) fail(`${CI_WORKFLOW}: the «${key}» job has no entry in ${CI_PATHS}`)
  return { origin: CI_PATHS, suites }
}

// Paths run through both the JavaScript port (src/ci-scope.mjs) and the real
// `.github/scripts/required-ci.py`: the gate itself, a doc, a suite's own
// workflow, a glob that stops at one level, a path in no list at all.
const CI_SAMPLES = [
  ['docs/manual/dev/ci.md'],
  ['README.md'],
  ['backend/app/main.py'],
  ['frontend/src/views/Room.vue'],
  ['deploy/deploy-docker.sh'],
  ['cli/cheese'],
  ['scripts/remote_execution/seed.py'],
  ['backend/app/domain/agent/executor_transport.py'],
  ['.pre-commit-config.yaml'],
  ['.github/workflows/deploy.yml'],
  ['backend/tests/fixtures/wire/x.json'],
  ['docs/manual/dev/ci.md', 'backend/app/main.py'],
  ['.github/scripts/required-ci.py'],
  ['.github/scripts/test_required_ci.py'],
  ['.github/workflows/required-ci.yml'],
  ['backend/deploy/not-a-path'],
]

const ci = ciSource()
registerSource('ci-scope', ci)
const CI_PATTERNS = Object.fromEntries(ci.suites.map((s) => [s.key, s.patterns]))

// Every one of these path sets is answered twice — by src/ci-scope.mjs in the
// browser and by the real `.github/scripts/required-ci.py` here — and any
// difference stops the build. This is the only guarantee that the demo a reader
// clicks through picks the same suites the merge gate would.
function checkCi(pathSets) {
  const real = JSON.parse(execFileSync('python3', [path.join(HERE, 'gen', 'required_ci.py')], { encoding: 'utf8', maxBuffer: 64 << 20, input: JSON.stringify(pathSets) }))
  pathSets.forEach((paths, i) => {
    const mine = selectSuites(paths, CI_PATTERNS)
    const got = real[i] || {}
    for (const key of new Set([...Object.keys(mine), ...Object.keys(got)])) {
      const what = `[${paths.join(', ') || '（空）'}]`
      if (!(key in mine)) fail(`CI scope: for ${what} required-ci.py selects «${key}», which ${CI_PATHS} does not list — the page would not know that suite`)
      if (mine[key].run !== got[key]) fail(`CI scope: for ${what} the page says «${key}» is ${mine[key].run ? 'selected' : 'not selected'}, required-ci.py says ${got[key] ? 'selected' : 'not selected'} — src/ci-scope.mjs must match select()`)
    }
  })
}
checkCi(CI_SAMPLES)

// The memory limits, straight out of `backend/app/domain/memory/files.py`.
const memory = gen('memory_limits.py')
registerSource('memory-limits', {
  origin: 'backend/app/domain/memory/files.py',
  constants: memory.constants,
  linePrefix: memory.linePrefix,
})
{
  const limits = { ...memory.constants, linePrefix: memory.linePrefix }
  for (const c of memory.indexCases) {
    const mine = fitIndex(limits, indexTextOf(limits, c.lines, c.lineBytes))
    for (const key of ['keptLines', 'keptBytes', 'truncated', 'oldLines', 'oldBytes']) {
      if (mine[key] !== c[key]) fail(`Memory limits: an index of ${c.lines} lines × ${c.lineBytes} bytes — the page says ${key}=${mine[key]}, fit_index() says ${c[key]} (src/memory-limits.mjs)`)
    }
  }
  const say = (what, mine, real) => { if (!!mine !== real) fail(`Memory limits: ${what} — the page says ${mine ? 'refused' : 'accepted'}, limit_breach() says ${real ? 'refused' : 'accepted'} (src/memory-limits.mjs)`) }
  const indexName = memory.constants.INDEX_NAME || 'MEMORY.md'
  for (const c of memory.lineCases) say(`an index line of ${c.chars} characters, ${c.alreadyInIndex ? 'already in the index' : 'new'},`, limitBreach(limits, { name: indexName, newLineChars: c.chars, alreadyInIndex: c.alreadyInIndex }), c.rejected)
  for (const c of memory.bodyCases) say(`a body of ${c.chars} characters,`, limitBreach(limits, { name: 'a-thing.md', bodyChars: c.chars, indexName }), c.rejected)
}

// The 原理分解 figures' constants, grepped out of the code that enforces them by
// `gen/arch_facts.py` and pinned here. Two checks, both of them load-bearing:
//
//   1. every value the generator read is written down below with the value the
//      picture is drawn against — so a port renumbered or a status changed in
//      the code fails the build instead of quietly redrawing the map;
//   2. the exact source text each value came from must still be in its file —
//      so a value that only survives in a comment or another page is not a fact.
//      A fact assembled from two greps («the prefix» + «the route») carries one
//      piece of source per grep, and every piece has to still be there.
//
// Every fact must be pinned: an unpinned one fails too, so a constant cannot
// arrive in the figure without someone writing down here what it should be.
// src/arch.mjs draws the stations from these; the walks name them by dotted key.
const ARCH_SAMPLES = {
  'paths.admission': '/llm/admission',
  'paths.tunnel': '/llm/tunnel',
  'paths.catch_all': '/llm/v1',
  'ports.reverse': '443',
  'ports.connect': '8444',
  'budget.status': '429',
  'budget.type': 'billing_error',
  'budget.prefix': 'cheese project budget: ',
  'budget.allow_reason': 'admitted',
  'budget.refusal_reason': '本月额度已用完，11月1日重置。',
  'binding.status': '400',
  'binding.type': 'invalid_request_error',
  'connect_refusal': '407',
  'placeholder_token': 'sk-ant-oat01-cheese-no-claude-login-on-this-host',
  'sub_model.id': 'sonnet',
  'sub_model.wire': 'claude-sonnet-5',
  'admission.fail_open_reason': 'admission unreachable (fail-open)',
  'probe_seconds': '15',
  'failure_threshold': '2',
  'quarantine_minutes': '30',
  'footprint_root': '.cheese',
  'dispatch_log': 'dispatch_log.py',
}
{
  const arch = gen('arch_facts.py')
  if (arch.error) fail(`Architecture facts: ${arch.error} (gen/arch_facts.py reads the code the figures are about)`)
  const facts = arch.facts || {}
  const sourceOf = new Map()
  for (const [key, f] of Object.entries(facts)) {
    if (!(key in ARCH_SAMPLES)) fail(`Architecture facts: ${key} = ${f.value} is not pinned in build.mjs' ARCH_SAMPLES — write down what the figure should say before it says it`)
    if (String(f.value) !== ARCH_SAMPLES[key]) fail(`Architecture facts: the code says ${key} = ${f.value}, ARCH_SAMPLES says ${ARCH_SAMPLES[key]} (${f.file}) — the figure and the code have parted; update both`)
    for (const piece of f.sources) {
      if (!sourceOf.has(piece.file)) sourceOf.set(piece.file, fs.readFileSync(path.join(REPO, piece.file), 'utf8'))
      if (!sourceOf.get(piece.file).includes(piece.text)) fail(`Architecture facts: ${key} was read from ${piece.file}, but «${piece.text.trim()}» is gone from it — gen/arch_facts.py is reading a stale copy`)
    }
  }
  for (const key of Object.keys(ARCH_SAMPLES)) if (!(key in facts)) fail(`Architecture facts: ARCH_SAMPLES pins ${key}, and gen/arch_facts.py did not find it — the constant it names has moved or been renamed`)
  const nested = {}
  for (const [key, f] of Object.entries(facts)) {
    const parts = key.split('.')
    let at = nested
    while (parts.length > 1) at = at[parts.shift()] ??= {}
    at[parts[0]] = f.value
  }
  registerArchFacts(nested)
}
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
  out['ref-prompt'] = promptReference()
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
      summary: '开发文档分五类：流程讲一件事怎么走完，概念讲背后的道理，参考供查阅，决策讲为什么这样，操作是照着做的步骤。',
      body: `# 按类型查文档 {#by-kind}

开发文档分五类：流程讲一件事怎么走完，概念讲背后的道理，参考供查阅，决策讲为什么这样，操作是照着做的步骤。每页开头必须声明类型和一句话摘要，流程、概念和参考还要列出涉及的代码；缺了构建不通过。

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
    slug, section: 'dev', sectionLabel: '开发文档', group, title: d.title, path: `/dev/${slug}`, url: `${BASE}/dev/${slug}`, mdUrl: `${BASE}/dev/${slug}.md`,
    src: d.file ? rel(d.file) : '', updated: d.file ? lastChanged(d.file) : '', generated: !!d.generated,
    kind: d.kind, kindKey: KINDS[d.kind], covers: d.covers, summary: d.summary, source: r.text, ...r,
  }
  pages[`dev/${slug}`] = page
  return page
})])
for (const slug of Object.keys(devFiles)) if (!pages[`dev/${slug}`]) fail(`docs/manual/dev/${slug}.md is not placed in src/structure.mjs`)

// Every page is parsed by now, so the path sets the CI demo's own fences offer
// a reader go through the real script too — the demo cannot drift from the gate
// even by way of the data written into a page.
checkCi(ciSelections())

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
  const url = `${BASE}${page.section === 'dev' ? '/dev' : ''}/diagrams/${slug}.html`
  page.diagram = { url, file: htmlFile }
  page.html = `<figure class="archify"><iframe data-diagram="${url}" title="${esc(meta.title)}" loading="lazy" style="aspect-ratio:${w}/${h}"></iframe><figcaption><span>${esc(meta.title)}</span><a href="${url}" target="_blank" rel="noopener">全屏查看：可缩放、搜索、导出 ↗</a></figcaption></figure>` + page.html
}

// ---------- every internal link must land on a page and, if it names one, an anchor ----------
{
  const ids = new Map(Object.values(pages).map((p) => [p.url, new Set([...p.html.matchAll(/\sid="([\w-]+)"/g)].map((m) => m[1]))]))
  const known = new Set(['/', '/changelog', '/download', '/llms.txt', '/dev/llms.txt', '/manual.zip', '/changelog.xml'].map((u) => BASE + u))
  const broken = []
  for (const p of Object.values(pages)) {
    for (const [, url, anchor] of p.html.matchAll(new RegExp(`href="(${BASE}/[\\w/.-]*)(?:#([\\w-]+))?"`, 'g'))) {
      if (url.startsWith(`${BASE}/diagrams/`) || url.startsWith(`${BASE}/dev/diagrams/`) || known.has(url)) continue
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
const asset = (name, ext, content) => { const file = `assets/${name}-${hash(content)}.${ext}`; write(file, content); return `${BASE}/${file}` }

// src/where.mjs reads the environment; the browser gets this build's answers as constants.
const define = Object.fromEntries(Object.entries({ DOCS_BASE: BASE, DOCS_SITE: SITE, DOCS_PLATFORM: PLATFORM }).map(([k, v]) => [`process.env.${k}`, JSON.stringify(v)]))
const js = (await esbuild.build({ entryPoints: [path.join(HERE, 'src/app.js')], bundle: true, format: 'esm', minify: true, target: 'es2022', write: false, define, legalComments: 'none' })).outputFiles[0].text
const css = (await esbuild.build({ stdin: { contents: `${productTokens()}\n${fs.readFileSync(path.join(HERE, 'src/style.css'), 'utf8')}`, loader: 'css', resolveDir: path.join(HERE, 'src') }, bundle: true, minify: true, write: false })).outputFiles[0].text
const assets = {
  js: asset('app', 'js', js),
  css: asset('app', 'css', css),
  logo: asset('logo', 'svg', LOGO_SVG),
}
for (const p of Object.values(pages)) if (p.diagram) write(p.diagram.url.slice(BASE.length + 1), fs.readFileSync(p.diagram.file))
const images = path.join(MANUAL, 'public')
if (fs.existsSync(images)) fs.cpSync(images, OUT, { recursive: true })

// ---------- render ----------
const firstUrl = (key) => userNav[key][0][1][0].url
const site = {
  description: '知是 · Cheese 的使用文档、开发文档和更新日志：怎么开始、每个功能怎么用、遇到问题怎么办。',
  tabs: [
    ...SECTIONS.map(([key, label, icon]) => ({ key, label, icon, href: firstUrl(key) })),
    { key: 'dev', label: '开发文档', icon: 'code', href: `${BASE}/dev/overview`, lock: true },
    { key: 'changelog', label: '更新日志', icon: 'log', href: `${BASE}/changelog` },
  ],
  userSections: SECTIONS.map(([key, label]) => ({ label, href: firstUrl(key) })),
}
const ctx = { site, assets, lockup: LOCKUP }

const flatNav = (nav) => nav.flatMap(([, items]) => items)
for (const [key] of SECTIONS) {
  const list = flatNav(userNav[key])
  list.forEach((p, i) => write(`${p.slug}.html`, docPage(ctx, p, userNav[key], list[i - 1], list[i + 1])))
}
const devList = flatNav(devNav)
devList.forEach((p, i) => write(`dev/${p.slug}.html`, docPage(ctx, p, devNav, devList[i - 1], devList[i + 1])))
write('dev/index.html', redirectPage(`${BASE}/dev/overview`))

const doors = SECTIONS.map(([key, label, icon]) => ({ key, label, icon, items: userNav[key].flatMap(([, items]) => items) }))
write('index.html', homePage(ctx, { releases: RELEASES, faq: FAQ, doors }))
write('changelog.html', changelogPage(ctx, RELEASES))
write('changelog.xml', changelogFeed(RELEASES))
write('download.html', downloadPage(ctx, { base: 'https://github.com/SageSeekerSociety/cheese/releases/download/desktop-latest' }))
write('dev-gate.html', devGatePage(ctx))
write('404.html', notFoundPage(ctx))
for (const [from, to] of Object.entries(REDIRECTS)) {
  if (!pages[to]) fail(`redirect ${from} → ${to}: no such page`)
  write(`${from}.html`, redirectPage(`${BASE}/${to}`))
}

// ---------- search indexes: public and developer, kept apart ----------
const searchIndex = (list) => JSON.stringify(list.flatMap((p) => p.chunks.map((c) => ({
  t: p.title, g: `${p.sectionLabel} · ${p.group}`, h: c.heading, u: c.id ? `${p.url}#${c.id}` : p.url, x: (c.id ? c.text : `${plain(p.lede)} ${c.text}`).slice(0, 600),
}))))
const publicPages = Object.values(pages).filter((p) => p.section !== 'dev')
write('search.json', searchIndex(publicPages))
write('dev/search.json', searchIndex(devList))
// What 问芝士 and the agents' cheese_docs_search read (backend: app/domain/docs_site/
// retrieval.py), whole sections. Public pages in one file, developer pages in another
// behind the dev/ gate: 问芝士's answers are shown to anyone signed in, and only
// agents in the platform's own project may search developer pages.
// Its urls are paths within the site (/accept#is-merge), the same in either build:
// the backend puts the docs' public address in front (docs_site/site.py).
const askIndex = (list) => JSON.stringify(list.flatMap((p) => p.chunks.filter((c) => c.text).map((c) => ({
  title: p.title, heading: c.heading, url: c.id ? `${p.path}#${c.id}` : p.path, text: (c.id ? c.text : `${plain(p.lede)} ${c.text}`).slice(0, 4000),
}))))
write('sections.json', askIndex(publicPages))
write('dev/sections.json', askIndex(devList))

// ---------- for models: llms.txt, a .md twin per page, and the whole manual ----------
const llms = (title, intro, nav) => [`# ${title}`, '', `> ${intro}`, '', ...nav.flatMap(([g, items]) => [`## ${g}`, '', ...items.map((p) => `- [${p.title}](${SITE}${p.mdUrl}): ${p.summary}`), ''])].join('\n')
const twin = (p, index) => `> ## Documentation Index\n> Fetch the complete documentation index at: ${SITE}${index}\n> Use this file to discover all available pages before exploring further.\n\n${p.source.replace(/\]\((\/[^)\s]*)\)/g, (_, u) => `](${SITE}${BASE}${u})`).trimStart()}`
for (const p of publicPages) write(`${p.slug}.md`, twin(p, `${BASE}/llms.txt`))
for (const p of devList) write(`dev/${p.slug}.md`, twin(p, `${BASE}/dev/llms.txt`))
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
