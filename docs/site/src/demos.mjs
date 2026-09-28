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
import { num, show, simulate, fill, evaluate } from './demo-model.mjs'

export const DEMO_FENCES = ['demo-steps', 'demo-timeline', 'demo-sim']

// A fence that says `data: prompt-blocks` does not carry its own numbers: the
// step is bound, by position, to a row of a dataset the build computed from the
// code. Positional binding is checked — the count must match and every step may
// assert a substring of the row it landed on — so a block that is added,
// removed or reordered fails the build instead of quietly mislabeling a step.
const DATASETS = {}

export function registerDataset(name, rows) { DATASETS[name] = rows }

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
const PALETTE = ['--info', '--ok', '--warn', '--sec', '--accent', '--accent-3']
const tone = (i) => `var(${PALETTE[i % PALETTE.length]})`

export function renderDemo(lang, body, where) {
  const spec = parseFence(body, where)
  if (!spec.title) missing(where, 'a demo needs a «title»')
  if (lang === 'demo-sim') return renderSim(spec, where)
  return renderSteps(spec, where, lang === 'demo-timeline')
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
      <span class="dm-rail"><i class="dm-dot" style="--c:${tone(i)}"></i></span>
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
    return `<button class="dm-seg" data-dm-jump="${i}" style="--w:${w.toFixed(3)}%;--c:${tone(i)}" aria-label="跳到第 ${i + 1} 步：${esc(s.label || '')}"><i></i></button>`
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
