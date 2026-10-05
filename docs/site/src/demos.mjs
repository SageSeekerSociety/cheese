// The demo components. A page writes one in a fence:
//
//   ```demo-steps
//   title: 一条消息怎么变成芝士的一轮
//   steps:
//     - label: 发消息与寻址
//       desc: 消息落库，同时写点名通知。没点到 AI 队友就不会起一轮。
//       link: /dev/turn#address
//   ```
//
// and this file turns it into two things that never see each other:
//
//   1. the prerendered component — every word of it is in the HTML, so the page
//      reads with JavaScript off; src/demo-dom.mjs only switches state.
//   2. a short piece of prose for the versions a model reads (the `.md` twin,
//      llms.txt, manual.zip, the search and 问芝士 indexes). A demo's numbers
//      and step titles are worth a model's tokens; the component's markup, its
//      colors and its controls are not.
//
// The fence body is a deliberately small subset of YAML: `key: value` at the
// left margin, a list of `- key: value` items under a `key:` with nothing after
// it, and `key: value` lines indented under an item. Anything else fails the
// build, loudly, with the line number.

import { esc, docHref } from './render.mjs'
import { num, show, simulate, fill, evaluate, sumContext } from './demo-model.mjs'
import { selectSuites, fnmatchcase } from './ci-scope.mjs'
import { fitIndex, limitBreach, indexTextOf } from './memory-limits.mjs'
import { archSpec, archText } from './arch.mjs'
import { archBoard, archCtl, archFallback, archSide, walkOf } from './arch-view.mjs'

export const DEMO_FENCES = ['demo-steps', 'demo-timeline', 'demo-sim', 'demo-context', 'demo-ci', 'demo-flow', 'demo-memory', 'demo-arch']

// Some fences carry no numbers of their own: they point at a `source`, a blob
// the build composed from the code the page is about. `registerSource` is how
// build.mjs hands one over (the suite table from required-ci-paths.json, the
// memory limits from files.py), and a fence naming a source that does not exist
// — or a list that no longer matches it — fails the build.
const SOURCES = {}

export function registerSource(name, data) { SOURCES[name] = data }

function sourceOf(spec, where) {
  if (!spec.source) missing(where, 'this demo needs a «source» — the build-time data it shows')
  const data = SOURCES[spec.source]
  if (!data) missing(where, `no build source named «${spec.source}» — the build has ${Object.keys(SOURCES).join(', ') || '(none)'}`)
  return data
}

// Every set of changed paths a `demo-ci` fence lets a reader tick, in the order
// the fences were read. build.mjs runs them through the real
// `.github/scripts/required-ci.py` as well as through src/ci-scope.mjs and
// fails on any difference, so the demo answers with the gate's own answer.
const CI_SELECTIONS = []

// A page is parsed twice (the component and the prose for models), so the same
// fence offers its paths twice; the gate only needs to answer each set once.
export function ciSelections() { return [...new Set(CI_SELECTIONS.map((p) => JSON.stringify(p)))].map((s) => JSON.parse(s)) }

// A fence that says `data: prompt-blocks` does not carry its own numbers: the
// step is bound, by position, to a row of a dataset the build computed from the
// code. Positional binding is checked — the count must match and every step may
// assert a substring of the row it landed on — so a block that is added,
// removed or reordered fails the build instead of quietly mislabeling a step.
const DATASETS = {}

export function registerDataset(name, rows) { DATASETS[name] = rows }

// A fence that says `embed: seats` also plays on the product's own components:
// the page at /demo/seats (frontend/src/views/demo) goes in an iframe above the
// step list, and the list drives it. The scene there and the steps here are two
// files describing one demo, so the build holds them to the same step titles in
// the same order — the list stays the words, the scene stays the pictures.
const EMBEDS = {}

export function registerEmbed(name, labels) { EMBEDS[name] = labels }

// The architecture figures' constants, grepped out of the code they are about
// (`gen/arch_facts.py`) and held to src/arch.mjs by build.mjs. A fence's walks
// are written against these, so a port that moves or a path that is renamed
// fails the build with the fence's line number instead of leaving the picture
// telling the old story. See `ARCH_SAMPLES` in build.mjs for the constants and
// the code each one must still appear in.
let ARCH_FACTS = null

export function registerArchFacts(facts) { ARCH_FACTS = facts }


function embedStage(spec, steps, where) {
  if (!spec.embed) return ''
  const labels = EMBEDS[spec.embed]
  if (!labels) missing(where, `no demo scene named «${spec.embed}» — frontend/src/views/demo/scenes has ${Object.keys(EMBEDS).join(', ') || '(none)'}`)
  const mine = steps.map((s) => s.label)
  if (labels.length !== mine.length || labels.some((l, i) => l !== mine[i])) {
    missing(where, `the scene «${spec.embed}» has steps «${labels.join(' / ')}», this fence has «${mine.join(' / ')}» — change both together`)
  }
  return `<div class="dm-stage"><iframe data-dm-embed src="/demo/${esc(spec.embed)}?embed=1" title="${esc(spec.title)}（演示画面）" loading="lazy"></iframe></div>`
}

// ---------- the fence body ----------
function scalar(v) {
  const s = v.trim()
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) return s.slice(1, -1)
  if (s === 'true') return true
  if (s === 'false') return false
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s)
  return s
}

export function parseFence(body, where) {
  const root = {}
  let list = null
  let item = null
  const lines = body.split('\n')
  lines.forEach((raw, n) => {
    const line = raw.replace(/\s+$/, '')
    const at = `${where}: fence line ${n + 1}`
    if (!line.trim() || line.trim().startsWith('#')) return
    const m = /^(\s*)(-\s+)?([A-Za-z_][\w-]*):\s?(.*)$/.exec(line)
    if (!m) throw new Error(`${at}: cannot read «${line.trim()}» — it wants to be «key: value»`)
    const [, indent, dash, key, val] = m
    if (dash) {
      if (!list) throw new Error(`${at}: «${key}» is an item, but no list is open above it`)
      item = {}
      list.push(item)
      if (val !== '') item[key] = scalar(val)
      return
    }
    if (indent) {
      if (!item) throw new Error(`${at}: «${key}» is indented, but no item is open above it`)
      item[key] = scalar(val)
      return
    }
    item = null
    if (val === '') { list = root[key] = []; return }
    list = null
    root[key] = scalar(val)
  })
  return root
}

export function missing(where, msg) { throw new Error(`${where}: ${msg}`) }

// ---------- binding a step to a dataset row ----------
function bound(spec, where) {
  if (!spec.data) {
    if (spec.then) missing(where, '«then» only means something together with «data»')
    return (spec.steps || []).map((s) => ({ ...s }))
  }
  const rows = DATASETS[spec.data]
  if (!rows) missing(where, `no dataset named «${spec.data}» — the build knows ${Object.keys(DATASETS).join(', ') || '(none)'}`)
  const declared = spec.steps || []
  if (declared.length !== rows.length) missing(where, `«${spec.data}» has ${rows.length} rows now, this fence describes ${declared.length} — read the list again and add or drop a step`)
  const per = spec.per_token || 1.6
  const out = declared.map((s, i) => {
    const row = rows[i]
    if (!s.desc) missing(where, `step ${i + 1} has no «desc»`)
    if (s.check && !String(row.title).includes(s.check)) missing(where, `step ${i + 1} says «check: ${s.check}» but row ${i + 1} is «${row.title}» — the order of the rows moved`)
    return {
      ...s,
      chars: row.chars,
      label: s.label || shortTitle(row.title),
      value: Math.round(row.chars / per),
      valueNote: `${num(row.chars)} 字符 ÷ ${per} = ${num(Math.round(row.chars / per))}`,
    }
  })
  for (const s of spec.then || []) {
    if (!s.desc) missing(where, `a «then» step has no «desc»`)
    out.push({ ...s })
  }
  return out
}

// The block's own heading, cut at the parenthesis that starts its explanation.
const shortTitle = (t) => String(t).replace(/（.*$/, '').replace(/^\s+|\s+$/g, '')

// ---------- prerender ----------

export function renderDemo(lang, body, where) {
  const spec = parseFence(body, where)
  if (!spec.title) missing(where, 'a demo needs a «title»')
  if (lang === 'demo-sim') return renderSim(spec, where)
  if (lang === 'demo-context') return renderContext(spec, where)
  if (lang === 'demo-ci') return renderCi(spec, where)
  if (lang === 'demo-flow') return renderFlow(spec, where)
  if (lang === 'demo-memory') return renderMemory(spec, where)
  if (lang === 'demo-arch') return renderArch(spec, where)
  return renderSteps(spec, where, lang === 'demo-timeline')
}

// ---------- demo-arch ----------
// 一次经过，一站一站地看：会话进程怎么把包交给计量代理、每一站看见了什么、
// 又送出去了什么、哪一站把它拦下。地图、站点、每一步的事实都在 src/arch.mjs
// 里，数字来自 gen/arch_facts.py（从代码里 grep 出来的常量，build.mjs 比对）。
// 这一段只把数据摆成页面：地图（build 和浏览器用同一份 src/arch-view.mjs）、
// 右侧的检视面板、上面的两组按钮，外加一份窄屏和无脚本能读的清单。
export function archData(spec, where) {
  if (!ARCH_FACTS) missing(where, 'the build has no architecture facts — build.mjs must register them from gen/arch_facts.py')
  const cfg = archSpec(spec, where, missing, ARCH_FACTS)
  for (const walk of Object.values(cfg.walks)) for (const stop of walk.stops) if (stop.link) stop.link = docHref(stop.link)
  return cfg
}

function renderArch(spec, where) {
  const cfg = archData(spec, where)
  const entry = cfg.entries[0].key
  const scene = cfg.matrix[entry][0]
  const walk = walkOf(cfg, entry, scene)
  return `<figure class="demo demo-arch" data-demo="arch" data-kind="${esc(cfg.kind)}" data-entry="${esc(entry)}" data-scene="${esc(scene)}" data-pos="1" aria-label="${esc(cfg.title)}">
  ${head(cfg.title, cfg.note)}
  <div class="ar-board" data-ar-board>${archBoard(cfg, walk, 1)}</div>
  <div class="ar-stage">
    <div class="ar-side-wrap" data-ar-side>${archSide(cfg, walk, 1)}</div>
    <div class="ar-ctl-wrap" data-ar-ctl>${archCtl(cfg, entry, scene, 1)}</div>
  </div>
  ${archFallback(cfg)}
  <script type="application/json" data-arch>${JSON.stringify(cfg).replace(/</g, '\\u003c')}</script>
</figure>`
}

// ---------- demo-context ----------
// One context window filling up over a turn, after Claude Code's «Explore the
// context window». The startup rows are bound to the prompt-blocks dataset like
// demo-timeline; `before:` rows come ahead of them (the harness's own prompt),
// `then:` rows after. A `kind: sub` row lives in a subagent's own window and
// does not count; the `kind: compact` row keeps only the categories in its
// `keeps:` and adds its `value` as the summary. A row with `gate:` waits for a
// click before it goes in. The interactive part is src/context-window.mjs.
export const CONTEXT_CATS = {
  harness: { label: '骨架自带', c: '--faint' },
  rules: { label: '平台规则', c: '--chart-1' },
  state: { label: '项目状态', c: '--chart-4' },
  memory: { label: '记忆', c: '--chart-5' },
  you: { label: '人和平台的话', c: '--chart-2' },
  work: { label: '文件和输出', c: '--chart-6' },
  say: { label: '芝士发言', c: '--chart-3' },
  compact: { label: '压缩摘要', c: '--text' },
  sub: { label: '分身', c: '--chart-4' },
}
const KIND_NAMES = ['auto', 'you', 'platform', 'cheese', 'sub', 'compact']
const SEEN = {
  chat: { label: '对话里看得见' },
  site: { label: '现场里有一行' },
  none: { label: '房间里看不见' },
}

export function contextRows(spec, where) {
  const before = (spec.before || []).map((s) => ({ ...s, cat: s.cat || 'harness', kind: s.kind || 'auto' }))
  const bound_ = bound(spec, where).map((s) => ({ ...s, cat: s.cat || 'rules', kind: s.kind || 'auto' }))
  // `skip: true` keeps a bound row checked against the dataset but out of this window (a block only some projects get).
  const rows = [...before, ...bound_].filter((s) => s.skip !== true).map((s) => ({ ...s, seen: s.seen || 'none', value: s.value ?? 0 }))
  rows.forEach((r, i) => {
    if (r.kind === 'sub') r.cat = 'sub'
    if (r.kind === 'compact') r.cat = 'compact'
    if (!KIND_NAMES.includes(r.kind)) missing(where, `row «${r.label}»: «kind» is one of ${KIND_NAMES.join(', ')}`)
    if (!CONTEXT_CATS[r.cat]) missing(where, `row «${r.label}»: no category «${r.cat}» — use one of ${Object.keys(CONTEXT_CATS).join(', ')}`)
    if (!SEEN[r.seen]) missing(where, `row «${r.label}»: «seen» is chat, site or none`)
    if (typeof r.value !== 'number') missing(where, `row «${r.label}»: «value» must be a number of tokens`)
    if (r.keeps) r.keeps = String(r.keeps).split(',').map((x) => x.trim())
    r.subStart = r.kind === 'sub' && rows[i - 1]?.kind !== 'sub'
    r.subEnd = r.kind === 'sub' && rows[i + 1]?.kind !== 'sub'
    if (r.link) r.link = docHref(r.link)
  })
  if (rows.filter((r) => r.kind === 'compact').length > 1) missing(where, 'one compaction per demo')
  return rows
}

function renderContext(spec, where) {
  const rows = contextRows(spec, where)
  const window_ = spec.window || 200000
  const used = new Set(rows.map((r) => r.cat))
  const cats = Object.entries(CONTEXT_CATS).filter(([k]) => used.has(k) && k !== 'sub').map(([key, v]) => ({ key, label: v.label, color: v.c }))
  const cfg = { title: spec.title, note: spec.note, topic: spec.topic, window: window_, line: spec.compact_line || 0.9, takeaway: spec.takeaway, room: spec.room, cats, rows }
  // Narrow screens and no script get the rows as a plain list.
  const li = rows.map((r) => `<li><b>${esc(r.label)}</b>${r.kind === 'compact' ? '' : `<span class="cw-f-tok">${num(r.value)}${r.kind === 'sub' ? '（在分身的窗口里）' : ''}</span>`}<span class="cw-f-seen">${SEEN[r.seen].label}</span><p>${esc(r.desc || '')}</p></li>`).join('')
  return `<figure class="demo demo-cw" data-demo="context" aria-label="${esc(spec.title)}">
  ${head(spec.title, spec.note)}
  <ol class="cw-fallback">${li}</ol>
  <script type="application/json" data-cw>${JSON.stringify(cfg).replace(/</g, '\\u003c')}</script>
</figure>`
}


// ---------- demo-ci ----------
// A merge diff goes in, the suites the gate would run come out. The suites and
// their patterns are the build's (`.github/scripts/required-ci-paths.json`);
// what the fence declares is what a reader may tick, and its `expect:` — the
// suite names this page describes — is held to the file, so adding a suite to
// the gate fails the build here until the page is read again.
export function ciData(spec, where) {
  const src = sourceOf(spec, where)
  const suites = (src.suites || []).map((s) => ({ key: s.key, desc: s.desc || '', workflow: s.workflow || '', patterns: s.patterns || [] }))
  const expect = String(spec.expect || '').split(',').map((x) => x.trim()).filter(Boolean)
  const have = suites.map((s) => s.key)
  if (expect.length !== have.length || expect.some((k) => !have.includes(k)))
    missing(where, `this fence describes the suites «${expect.join(' · ')}», the repository has «${have.join(' · ')}» — read required-ci-paths.json again and update the fence and the prose together`)
  if (!suites.length) missing(where, 'required-ci-paths.json names no suites')
  const patterns = Object.fromEntries(suites.map((s) => [s.key, s.patterns]))
  const check = (path, who) => {
    if (!Object.values(patterns).some((pats) => pats.some((p) => fnmatchcase(path, p))))
      missing(where, `${who} «${path}» matches no pattern in required-ci-paths.json — a path that selects nothing here teaches nothing`)
  }
  const paths = (spec.paths || []).map((p) => {
    if (!p.path) missing(where, 'every «paths» entry needs a «path»')
    check(p.path, 'path')
    return { path: p.path, label: p.label || '' }
  })
  if (!paths.length) missing(where, 'a «demo-ci» needs a «paths» list — the paths a reader can tick')
  const scenarios = (spec.scenarios || []).map((s) => {
    if (!s.key || !s.label) missing(where, 'every scenario needs a «key» and a «label»')
    const list = String(s.paths || '').split(',').map((x) => x.trim()).filter(Boolean)
    if (!list.length) missing(where, `scenario «${s.key}» needs «paths»`)
    for (const p of list) {
      check(p, `scenario «${s.key}»`)
      if (!paths.some((x) => x.path === p)) paths.push({ path: p, label: '' })
    }
    return { key: s.key, label: s.label, paths: list }
  })
  if (!scenarios.length) missing(where, 'a «demo-ci» needs at least one scenario')
  // What a reader can end up asking about: each scenario, plus the ticked paths
  // themselves. build.mjs answers all of these with the real script too.
  CI_SELECTIONS.push(paths.map((p) => p.path), ...scenarios.map((s) => s.paths))
  return { title: spec.title, note: spec.note, suites, paths, scenarios }
}

export function ciRuns(cfg) {
  return cfg.scenarios.map((s) => {
    const sel = selectSuites(s.paths, Object.fromEntries(cfg.suites.map((x) => [x.key, x.patterns])))
    return { ...s, run: cfg.suites.filter((x) => sel[x.key].run).map((x) => x.key) }
  })
}

function renderCi(spec, where) {
  const cfg = ciData(spec, where)
  const runs = ciRuns(cfg)
  // Narrow screens and no script: the suites, their patterns, and what each
  // scenario would select — all of it computed here, all of it in the HTML.
  const li = cfg.suites.map((s) => `<li><b>${esc(s.key)}</b>${s.workflow ? `<span class="cix-f-wf">${esc(s.workflow)}</span>` : ''}<p>${esc(s.desc)}</p><p class="cix-f-pats">${s.patterns.map((p) => esc(p)).join(' ')}</p></li>`).join('')
  const scen = runs.map((s) => `<li><b>${esc(s.label)}</b><span class="cix-f-run">跑 ${esc(s.run.join(' · '))}</span></li>`).join('')
  return `<figure class="demo demo-cix" data-demo="ci" aria-label="${esc(spec.title)}">
  ${head(spec.title, spec.note)}
  <ol class="cix-fallback">${li}</ol>
  <ul class="cix-fallback cix-f-scen">${scen}</ul>
  <script type="application/json" data-ci>${JSON.stringify(cfg).replace(/</g, '\\u003c')}</script>
</figure>`
}

// ---------- demo-flow ----------
// One request walked lane by lane: the actors are columns, each step is an
// arrow between two of them, and a route picks which steps belong to this
// telling. A step marked `block:` is where this route is refused. `resident:`
// names what is NOT in the picture — the layer a rollout does not touch, drawn
// as a band that stays put while the lanes above it change.
const FLOW_TONES = ['ok', 'bad', 'warn']

export function flowData(spec, where) {
  const actors = (spec.actors || []).map((a) => {
    if (!a.key || !a.label) missing(where, 'every actor needs a «key» and a «label»')
    return { key: a.key, label: a.label, sub: a.sub || '' }
  })
  if (actors.length < 2) missing(where, 'a flow needs at least two actors')
  const keys = actors.map((a) => a.key)
  if (new Set(keys).size !== keys.length) missing(where, 'two actors share a «key»')
  const routes = (spec.routes || []).map((r) => {
    if (!r.key || !r.label) missing(where, 'every route needs a «key» and a «label»')
    if (r.tone && !FLOW_TONES.includes(r.tone)) missing(where, `route «${r.key}»: «tone» is one of ${FLOW_TONES.join(', ')}`)
    return { key: r.key, label: r.label, note: r.note || '', result: r.result || '', tone: r.tone || 'ok' }
  })
  if (!routes.length) missing(where, 'a flow needs at least one «routes» entry')
  const routeKeys = routes.map((r) => r.key)
  const steps = (spec.steps || []).map((s, i) => {
    const at = `${where} step ${i + 1}`
    if (!s.from || !s.to || !s.label) missing(where, `${at}: every step needs «from», «to» and «label»`)
    for (const end of [s.from, s.to]) if (!keys.includes(end)) missing(where, `${at}: «${end}» is not one of the actors (${keys.join(', ')})`)
    const on = String(s.routes || '').split(',').map((x) => x.trim()).filter(Boolean)
    for (const r of on) if (!routeKeys.includes(r)) missing(where, `${at}: «routes: ${s.routes}» names «${r}», which is not a route (${routeKeys.join(', ')})`)
    if (s.link) s.link = docHref(s.link)
    return { i, routes: on.length ? on : routeKeys, phase: s.phase || '', from: s.from, to: s.to, label: s.label, desc: s.desc || '', ref: s.ref || '', block: s.block === true, link: s.link || '' }
  })
  if (!steps.length) missing(where, 'a flow needs at least one step')
  for (const r of routeKeys) {
    if (!steps.some((s) => s.routes.includes(r))) missing(where, `route «${r}» has no steps — every route must show something`)
  }
  const resident = (spec.resident || []).map((x) => x.label).filter(Boolean)
  return { title: spec.title, note: spec.note, actors, routes, steps, resident }
}

function renderFlow(spec, where) {
  const cfg = flowData(spec, where)
  const fallback = cfg.routes.map((r) => {
    const mine = cfg.steps.filter((s) => s.routes.includes(r.key))
    const li = mine.map((s) => `<li${s.block ? ' class="block"' : ''}><b>${esc(cfg.actors.find((a) => a.key === s.from).label)} → ${esc(cfg.actors.find((a) => a.key === s.to).label)}</b><span>${esc(s.label)}</span>${s.ref ? `<code>${esc(s.ref)}</code>` : ''}<p>${esc(s.desc)}</p></li>`).join('')
    return `<li><b>${esc(r.label)}</b>${r.note ? `<span class="fl-f-note">${esc(r.note)}</span>` : ''}<ol class="fl-f-steps">${li}</ol>${r.result ? `<p class="fl-f-result">${esc(r.result)}</p>` : ''}</li>`
  }).join('')
  return `<figure class="demo demo-fl" data-demo="flow" aria-label="${esc(spec.title)}">
  ${head(spec.title, spec.note)}
  <ol class="fl-fallback">${fallback}</ol>
  ${cfg.resident.length ? `<p class="fl-f-resident">常驻、不随发版替换：${cfg.resident.map((x) => esc(x)).join(' · ')}</p>` : ''}
  <script type="application/json" data-fl>${JSON.stringify(cfg).replace(/</g, '\\u003c')}</script>
</figure>`
}

// ---------- demo-memory ----------
// The two single-entry limits and the injection budget, dragged instead of
// described. The numbers are the build's (gen/memory_limits.py reads them out
// of `backend/app/domain/memory/files.py`); the fence names the ones it shows,
// so a constant that is renamed fails the build instead of quietly disappearing.
export function memoryData(spec, where) {
  const src = sourceOf(spec, where)
  const wanted = String(spec.limits || '').split(',').map((x) => x.trim()).filter(Boolean)
  if (!wanted.length) missing(where, 'a «demo-memory» needs a «limits» list — the constant names this page shows')
  for (const name of wanted) {
    if (typeof src.constants[name] !== 'number') missing(where, `no constant «${name}» in ${src.origin} — the page names ${Object.keys(src.constants).join(', ')}`)
  }
  return {
    title: spec.title,
    note: spec.note,
    limits: { ...src.constants, linePrefix: src.linePrefix },
    shown: wanted,
  }
}

function renderMemory(spec, where) {
  const cfg = memoryData(spec, where)
  const c = cfg.limits
  const facts = [
    `索引进上下文前按行截断：超过 ${num(c.INDEX_MAX_LINES)} 行或 ${num(Math.round(c.INDEX_MAX_BYTES / 1024))}KB 就只注入前面的部分，并附一句警告。`,
    `索引里新写的一行超过 ${num(c.INDEX_LINE_MAX)} 字符就拒收，那一版存成 .rejected.md。`,
    `一条记忆的正文超过 ${num(c.BODY_MAX)} 字就拒收，那一版存成 .rejected.md。`,
    `这两条在会话对账时和写接口上都拦，接口回 422。`,
  ]
  return `<figure class="demo demo-mem" data-demo="memory" aria-label="${esc(spec.title)}">
  ${head(spec.title, spec.note)}
  <ul class="mem-fallback">${facts.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
  <script type="application/json" data-mem>${JSON.stringify(cfg).replace(/</g, '\\u003c')}</script>
</figure>`
}

function head(title, note) {
  return `<div class="dm-head"><b class="dm-title">${esc(title)}</b>${note ? `<span class="dm-note">${esc(note)}</span>` : ''}</div>`
}

function renderSteps(spec, where, timeline) {
  const steps = bound(spec, where)
  if (!steps.length) missing(where, 'a demo needs at least one step')
  const unit = spec.unit || ''
  const estimate = spec.estimate === true
  const total = steps.reduce((s, x) => s + (x.value || 0), 0)
  const max = spec.max || total || steps.length
  for (const s of steps) if (s.value !== undefined && typeof s.value !== 'number') missing(where, `step «${s.label}»: «value» must be a number, got ${JSON.stringify(s.value)}`)

  const li = steps.map((s, i) => {
    const bits = []
    if (s.label) bits.push(`<b>${esc(s.label)}</b>`)
    if (s.value !== undefined) bits.push(`<span class="dm-val"${s.valueNote ? ` title="${esc(s.valueNote)}"` : ''}>${estimate ? '≈' : ''}${num(s.value)}${unit ? ` ${esc(unit)}` : ''}</span>`)
    return `<li class="dm-step" data-dm-step="${i}"${s.value !== undefined ? ` data-value="${s.value}"` : ''}>
      <span class="dm-rail"><i class="dm-dot"></i></span>
      <div class="dm-body">
        ${bits.length ? `<div class="dm-line">${bits.join('')}</div>` : ''}
        <p class="dm-desc">${esc(s.desc)}</p>
        ${s.link ? `<a class="link dm-link" href="${esc(docHref(s.link))}">${esc(s.linkText || '看这一节')}</a>` : ''}
        ${s.gate ? `<button class="dm-go" data-dm-go hidden>${esc(s.go || '继续')} <span aria-hidden="true">▶</span></button>` : ''}
      </div>
    </li>`
  }).join('\n')

  const segs = steps.map((s, i) => {
    const w = s.value !== undefined ? Math.max((s.value / max) * 100, 0.6) : 100 / steps.length
    return `<button class="dm-seg" data-dm-jump="${i}" style="--w:${w.toFixed(3)}%" aria-label="跳到第 ${i + 1} 步：${esc(s.label || '')}"><i></i></button>`
  }).join('')

  return `<figure class="demo demo-steps${timeline ? ' demo-tl' : ''}" data-demo="steps" aria-label="${esc(spec.title)}">
  ${head(spec.title, spec.note)}
  <div class="dm-ctl" role="group" aria-label="演示控制">
    <button class="dm-btn" data-dm-prev aria-label="上一步">←</button>
    <button class="dm-btn dm-play" data-dm-play aria-pressed="false"><span data-dm-play-label>播放</span></button>
    <button class="dm-btn" data-dm-next aria-label="下一步">→</button>
    <input class="dm-range" type="range" data-dm-range min="1" max="${steps.length}" step="1" value="1" aria-label="进度">
    <span class="dm-count" data-dm-count aria-live="polite">1 / ${steps.length}</span>
  </div>
  <div class="dm-bar">
    <div class="dm-track"><span class="dm-fill" data-dm-fill style="--p:100%"></span></div>
    <div class="dm-segs" role="group" aria-label="跳到某一步">${segs}</div>
  </div>
  ${embedStage(spec, steps, where)}
  <ol class="dm-list">
${li}
  </ol>
</figure>`
}

function renderSim(spec, where) {
  if (!Array.isArray(spec.vars) || !spec.vars.length) missing(where, 'a simulation needs a «vars» list')
  const vars = spec.vars.map((v) => {
    if (!v.key) missing(where, 'every var needs a «key»')
    if (!v.label) missing(where, `var «${v.key}» needs a «label»`)
    const kind = ['toggle', 'choice'].includes(v.type) ? v.type : 'range'
    // «label=value | label=value» on one line: a choice's options are short
    // enough that nesting a list under every var would only cost indentation.
    const options = String(v.options || '').split('|').map((piece) => piece.trim()).filter(Boolean).map((piece) => {
      const [label, value] = piece.split('=')
      return { value: (value ?? label).trim(), label: label.trim() }
    })
    if (kind === 'choice' && !options.length) missing(where, `var «${v.key}» is a choice, so it needs «options»`)
    const value = kind === 'toggle' ? (v.on === true || v.value === true) : kind === 'choice' ? String(v.value ?? options[0].value) : (v.value ?? v.min ?? 0)
    if (kind === 'choice' && !options.some((o) => o.value === value)) missing(where, `var «${v.key}»: «value: ${value}» is not one of its options`)
    return {
      key: v.key, label: v.label, kind, options, unit: v.unit || '',
      min: kind === 'range' ? (v.min ?? 0) : undefined,
      max: kind === 'range' ? (v.max ?? 100) : undefined,
      step: kind === 'range' ? (v.step ?? 1) : undefined,
      value,
    }
  })
  for (const v of vars) {
    if (v.kind !== 'range') continue
    if (typeof v.min !== 'number' || typeof v.max !== 'number') missing(where, `var «${v.key}»: «min» and «max» must be numbers`)
  }
  const derived = (spec.derived || []).map((d) => {
    if (!d.key || !d.expr) missing(where, 'every «derived» entry needs a «key» and an «expr»')
    return { key: d.key, expr: String(d.expr) }
  })
  const defaults = Object.fromEntries(vars.map((v) => [v.key, v.value]))
  let state
  try { state = simulate(spec, defaults) } catch (e) { missing(where, `a rule does not evaluate: ${e.message}`) }
  const broken = state.out.find((o) => o.error)
  if (broken) missing(where, `«${broken.label}» 的算式在默认参数下算不出来：${broken.error}`)

  const varHtml = vars.map((v) => {
    if (v.kind === 'toggle') {
      return `<label class="sm-var sm-toggle">
      <input type="checkbox" data-sm-var="${esc(v.key)}" data-sm-kind="toggle"${v.value ? ' checked' : ''}>
      <span class="sm-name">${esc(v.label)}</span>
      <output class="sm-val" data-sm-show="${esc(v.key)}">${show(v.value)}</output>
    </label>`
    }
    if (v.kind === 'choice') {
      return `<label class="sm-var sm-choice">
      <span class="sm-name">${esc(v.label)}</span>
      <select data-sm-var="${esc(v.key)}" data-sm-kind="choice">${v.options.map((o) => `<option value="${esc(o.value)}"${o.value === v.value ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}</select>
    </label>`
    }
    return `<label class="sm-var">
      <span class="sm-name">${esc(v.label)}</span>
      <output class="sm-val"><span data-sm-show="${esc(v.key)}">${show(v.value)}</span>${v.unit ? ` ${esc(v.unit)}` : ''}</output>
      <input type="range" data-sm-var="${esc(v.key)}" data-sm-kind="range" min="${v.min}" max="${v.max}" step="${v.step}" value="${v.value}">
    </label>`
  }).join('\n')

  const ruleHtml = state.rules.length ? `<ul class="sm-rules">${state.rules.map((r, i) => `<li class="sm-rule${r.hit ? ' hit' : ''}" data-sm-rule="${i}"${r.tone ? ` data-tone="${esc(r.tone)}"` : ''}><b>${esc(r.label || '')}</b><span data-sm-rule-text>${esc(r.text)}</span></li>`).join('')}</ul>` : ''

  const outHtml = state.out.length ? `<dl class="sm-facts">${state.out.map((o, i) => `<div><dt>${esc(o.label)}</dt><dd data-sm-fact="${i}" data-sm-unit="${esc(o.unit)}">${esc(o.text)}${o.unit ? ` ${esc(o.unit)}` : ''}</dd></div>`).join('')}</dl>` : ''

  // The rules are re-evaluated in the browser from the same data, so a rule's
  // condition and its template travel with the element that shows it.
  const rulesJson = JSON.stringify(state.rules.length ? spec.rules.map((r) => ({ when: r.when === undefined ? null : String(r.when), text: String(r.text ?? ''), label: r.label || '', tone: r.tone || '' })) : [])
  const outJson = JSON.stringify(state.out.length ? spec.out.map((o) => ({ label: o.label, expr: String(o.expr), unit: o.unit || '' })) : [])

  return `<figure class="demo demo-sim${spec.wide ? ' demo-wide' : ''}" data-demo="sim" aria-label="${esc(spec.title)}">
  ${head(spec.title, spec.note)}
  <div class="sm-grid">
    <div class="sm-vars">
${varHtml}
    </div>
    <div class="sm-out">
      ${ruleHtml}
      ${outHtml}
      <p class="sm-nojs">打开 JavaScript 就能拖动上面的参数，这里的结论会跟着变。</p>
    </div>
  </div>
  <script type="application/json" data-sm-rules>${rulesJson.replace(/</g, '\\u003c')}</script>
  <script type="application/json" data-sm-out>${outJson.replace(/</g, '\\u003c')}</script>
  <script type="application/json" data-sm-derived>${JSON.stringify(derived).replace(/</g, '\\u003c')}</script>
</figure>`
}

// ---------- what a model reads instead ----------
// The same demo, said in prose: the steps and their numbers, the parameters and
// what the default position concludes. No markup, no colors, no controls.
export function demoText(lang, body, { where }) {
  const spec = parseFence(body, where)
  if (lang === 'demo-arch') return archText(archData(spec, where))
  if (lang === 'demo-sim') {
    const vars = (spec.vars || []).map((v) => {
      if (v.type === 'toggle') return `${v.label}（开关，默认${v.on === true || v.value === true ? '开' : '关'}）`
      if (v.type === 'choice') return `${v.label}（可选 ${String(v.options || '').split('|').map((x) => x.split('=')[0].trim()).join(' / ')}，默认 ${String(v.value).split('=').pop()}）`
      return `${v.label}（${num(v.min ?? 0)} – ${num(v.max ?? 100)}，默认 ${num(v.value ?? 0)}）`
    })
    const state = simulate(spec, {})
    const rules = state.rules.length ? `\n\n判定规则（默认可调参数下命中的是第 ${state.hit + 1} 条）：\n${state.rules.map((r, i) => `${i + 1}. ${r.label ? `${r.label}：` : ''}${r.text}${r.hit ? '（默认命中）' : ''}`).join('\n')}` : ''
    const out = state.out.length ? `\n\n默认位置的结果：\n${state.out.map((o) => `- ${o.label}：${o.text}${o.unit ? ` ${o.unit}` : ''}`).join('\n')}` : ''
    return `**${spec.title}**（网页上是一个可以拖动参数的模拟器；这里是它的文字版：${vars.join('、')}。）${rules}${out}`
  }
  if (lang === 'demo-context') {
    const rows = contextRows(spec, where)
    const list = rows.map((r) => `- ${r.label}（${CONTEXT_CATS[r.cat].label}，${SEEN[r.seen].label}${r.cat === 'compact' ? '' : `，约 ${num(r.value)} tokens`}）：${r.desc || ''}`).join('\n')
    return `**${spec.title}**（网页上是一根随时间填满的上下文窗口，这里是它的文字版，窗口 ${num(spec.window || 200000)} tokens。）\n\n${list}`
  }
  if (lang === 'demo-ci') {
    const cfg = ciData(spec, where)
    const suites = cfg.suites.map((s) => `- ${s.key}${s.workflow ? `（${s.workflow}）` : ''}：${s.desc}｜改到 ${s.patterns.join(' ')} 时运行`).join('\n')
    const runs = ciRuns(cfg).map((s) => `- ${s.label}（${s.paths.join('、')}）：${s.run.join(' · ')}`).join('\n')
    return `**${spec.title}**（网页上可以勾选改动路径，看哪些套件会跑；这里是文字版。）\n\n改动改到这些路径时运行：\n\n${suites}\n\n选中路径后要跑的套件：\n\n${runs}\n\n未被选中的套件必须是跳过，选中的必须成功，这一个检查（CI required）才算通过。`
  }
  if (lang === 'demo-flow') {
    const cfg = flowData(spec, where)
    const label = (k) => cfg.actors.find((a) => a.key === k).label
    const routes = cfg.routes.map((r) => {
      const steps = cfg.steps.filter((s) => s.routes.includes(r.key)).map((s) => `${s.block ? '（被拦）' : ''}${label(s.from)} → ${label(s.to)}：${s.label}${s.ref ? `（${s.ref}）` : ''}${s.desc ? `。${s.desc}` : ''}`).join('\n  ')
      return `- ${r.label}${r.note ? `（${r.note}）` : ''}：\n  ${steps}${r.result ? `\n  结果：${r.result}` : ''}`
    }).join('\n')
    const resident = cfg.resident.length ? `\n\n常驻、不随发版替换：${cfg.resident.join(' · ')}。` : ''
    return `**${spec.title}**（网页上是一条按参与方排开的时序演示，可以切换路线一步步走；这里是文字版。）\n\n参与方：${cfg.actors.map((a) => a.label).join(' · ')}。\n\n${routes}${resident}`
  }
  if (lang === 'demo-memory') {
    const cfg = memoryData(spec, where)
    const c = cfg.limits
    const sample = fitIndex(c, indexTextOf(c, c.INDEX_MAX_LINES + 40, 128))
    return `**${spec.title}**（网页上是一组可以拖的参数，看截断和拒收发生在哪；这里是文字版。）
- 索引进上下文前按行截断：超过 ${num(c.INDEX_MAX_LINES)} 行或 ${num(c.INDEX_MAX_BYTES)} 字节（${Math.round(c.INDEX_MAX_BYTES / 1024)}KB）时只注入前面的部分，并附一句警告；照每行 128 字节算，${num(c.INDEX_MAX_LINES + 40)} 行的索引只注入 ${num(sample.keptLines)} 行。
- 索引里新写的一行超过 ${num(c.INDEX_LINE_MAX)} 字符就拒收，那一版存成 .rejected.md；早先就有的一行不挡这一次。
- 一条记忆的正文超过 ${num(c.BODY_MAX)} 字就拒收，那一版存成 .rejected.md。
- 两条单条上限在会话对账时和写接口上都拦，接口回 422；总长没有写入闸，只截断。`
  }
  const steps = bound(spec, where)
  const list = steps.map((s) => {
    const v = s.value === undefined ? '' : `（${spec.estimate === true ? '约 ' : ''}${num(s.value)}${spec.unit ? ` ${spec.unit}` : ''}）`
    return `- ${s.label ? `${s.label}：` : ''}${s.desc}${v}${s.gate ? '｜演示在这里停下，等人点继续' : ''}`
  }).join('\n')
  return `**${spec.title}**（网页上是可以逐步播放的演示，这里是同一份内容的文字版。）\n\n${list}`
}

// ---------- the fence as markdown sees it ----------
// Used by build.mjs for the pass that produces the text a model reads; the same
// fences are expanded to components by the renderer's `code` hook.
export const FENCE = /^```(demo-[\w-]+)[ \t]*\n([\s\S]*?)\n```[ \t]*$/gm

export function replaceFences(md, fn) {
  return md.replace(FENCE, (whole, lang, body) => {
    if (!DEMO_FENCES.includes(lang)) return whole
    return fn(lang, body)
  })
}

export function countFences(md) {
  return [...md.matchAll(FENCE)].filter((m) => DEMO_FENCES.includes(m[1])).length
}

export { show, num, simulate, fill, evaluate }
