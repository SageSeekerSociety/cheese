// `demo-panel`: one action, shown on a simplified piece of the product.
//
//   ```demo-panel
//   title: 在频道里交给芝士
//   caption: 发出后，芝士在这条消息的支线里回答。
//   head: 综合
//   parts:
//     - kind: bars
//       lines: 2
//     - kind: msg
//       who: 你
//       say: 帮我把下周组会的报名说明整理成一页。
//       at: 2
//     - kind: composer
//       placeholder: 输入消息，@芝士 交给它处理
//       type: 帮我把下周组会的报名说明整理成一页。
//       button: 交给芝士
//       at: 1
//       press: 2
//   ```
//
// A demo is a few beats (at most four). Every part is on screen from beat 0
// unless it says `at:`, and stays unless it says `until:`; `press: n` marks the
// part's button as the thing pressed to get to beat n. A composer is the
// exception: it is always there, and its `at:` is when its `type:` is typed. The prerendered HTML is
// the LAST beat — the result — so a reader without JavaScript, with reduced
// motion, or who never scrolls it into view still sees the outcome. The browser
// side (src/panel-window.mjs) rewinds it to beat 0 when it first comes into
// view, plays it once and stops on the result.
//
// Labels that stand for product UI (a button, a status, a placeholder) must be
// strings the product actually shows: the build hands this file the zh-CN
// messages from frontend/src/i18n and a label that matches none of them fails
// the build. Message bodies (`say`, `type`, `steps`) are the demo's own content.

import { esc } from './render.mjs'
import { ic } from './content.js'

export const MAX_BEATS = 4
// Beside a step list (`walk: true` inside `:::walk`) there is one frame per step.
export const MAX_WALK_STEPS = 7

// What each kind of part may say. `ui` fields are held to the product's strings;
// a `|`-separated field is a list.
const KINDS = {
  bars: { need: [], text: ['who'], ui: [], list: [] },
  msg: { need: ['who', 'say'], text: ['who', 'say', 'to'], ui: [], list: ['steps'] },
  thread: { need: ['text'], text: ['last'], ui: ['text', 'status'], list: [] },
  typing: { need: ['text'], text: [], ui: ['text'], list: [] },
  line: { need: ['text'], text: [], ui: ['text', 'button', 'sub'], list: [] },
  // An amber strip under the header: the platform asking for something (「需要指定由谁审阅…」).
  strip: { need: ['text'], text: ['value'], ui: ['text', 'label'], list: [] },
  // A document the step is not about: its headings, each over a grey bar.
  doc: { need: ['headings'], text: [], ui: [], list: ['headings'] },
  // The 「改动」 view: which files changed, over a diff in grey bars.
  files: { need: ['files'], text: [], ui: [], list: ['files'] },
  // The right panel's tabs, one of them open.
  tabs: { need: ['items', 'active'], text: [], ui: ['active'], list: ['items'] },
  composer: { need: ['placeholder'], text: ['type'], ui: ['placeholder', 'button'], list: [] },
  head: { need: ['title'], text: ['title', 'room', 'who', 'plain'], ui: ['status', 'owner', 'button', 'note'], list: [] },
  // The acceptance item: a one-line row (`title`, `status`, `button`) or the
  // opened card (`ok`, `who`, `note`, `actions`).
  card: { need: [], text: ['title', 'note', 'pressing'], ui: ['status', 'button', 'ok', 'who'], list: ['files', 'actions'] },
  // A grey bar where the composer was (「任务已关闭」).
  notice: { need: ['text'], text: [], ui: ['text', 'button'], list: [] },
  checklist: { need: ['title', 'items'], text: ['done'], ui: ['title'], list: ['items'] },
}
const COMMON = ['kind', 'at', 'until', 'press', 'lines']

// The product's UI strings, as patterns: `交给{name}` matches 「交给芝士」.
let UI = null
export function registerUiStrings(values) {
  // A message that is nothing but placeholders («{who}{what}») would match any
  // label at all, so it vouches for none.
  UI = values.map((v) => String(v).replace(/\{'(.)'\}/g, '$1').split(/\{\w+\}/)).filter((parts) => /\p{L}/u.test(parts.join(''))).map((parts) =>
    new RegExp(`^${parts.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.+?')}$`))
}

function uiCheck(label, where, fail) {
  if (!UI) fail(where, 'the build has no product strings — build.mjs must register frontend/src/i18n/messages/zh-CN')
  if (!UI.some((re) => re.test(label))) fail(where, `«${label}» is not a string the product shows (frontend/src/i18n/messages/zh-CN) — copy it from the interface`)
}

let AGENT_AVATAR = ''
export function registerAgentAvatar(url) { AGENT_AVATAR = url }

const listOf = (v) => String(v ?? '').split('|').map((x) => x.trim()).filter(Boolean)

export function panelSpec(spec, where, fail) {
  const agent = spec.agent || '芝士'
  const parts = (spec.parts || []).map((p, i) => {
    const at = `${where}: part ${i + 1}`
    const k = KINDS[p.kind]
    if (!k) fail(at, `«kind» is one of ${Object.keys(KINDS).join(', ')}`)
    for (const f of Object.keys(p)) if (![...COMMON, ...k.need, ...k.text, ...k.ui, ...k.list].includes(f)) fail(at, `a «${p.kind}» has no «${f}»`)
    for (const f of k.need) if (p[f] === undefined || p[f] === '') fail(at, `a «${p.kind}» needs «${f}»`)
    for (const f of ['at', 'until', 'press']) if (p[f] !== undefined && !(Number.isInteger(p[f]) && p[f] >= 0)) fail(at, `«${f}» is a beat number`)
    if (p.until !== undefined && p.until <= (p.at || 0)) fail(at, '«until» comes after «at»')
    if (p.press !== undefined) {
      if (!(p.button || p.pressing)) fail(at, '«press» needs the «button» (or, on a card, «pressing») it presses')
      if (p.press <= (p.at || 0)) fail(at, '«press» is the beat the press leads to, after the part is on screen')
    }
    if (p.kind === 'composer' && p.type && p.press === undefined) fail(at, 'a composer that types needs «press»: the beat it is sent on')
    const out = { ...p, at: p.at || 0 }
    for (const f of k.list) if (p[f] !== undefined) out[f] = listOf(p[f])
    for (const f of k.ui) for (const label of (k.list.includes(f) ? out[f] : p[f] === undefined ? [] : [p[f]])) uiCheck(String(label), at, fail)
    if (p.kind === 'card' && p.pressing && ![...(out.actions || []), p.button].includes(p.pressing)) fail(at, `«pressing: ${p.pressing}» is not one of the card's «actions»`)
    return out
  })
  if (!parts.length) fail(where, 'a panel needs «parts»')
  const beats = Math.max(0, ...parts.flatMap((p) => [p.at, p.until ?? 0, p.press ?? 0]))
  if (beats < 1) fail(where, 'a panel with nothing that changes is a picture — give a part «at:», «until:» or «press:»')
  const walk = spec.walk === true
  if (walk) {
    // Frame 0 is the screen before step 1; frame n is the screen after step n.
    if (beats > MAX_WALK_STEPS) fail(where, `${beats} steps; a step list beside one panel has at most ${MAX_WALK_STEPS} — split the task`)
    if (spec.caption) fail(where, 'a panel beside a step list has no «caption»: the step text is the caption')
  } else {
    if (beats > MAX_BEATS) fail(where, `${beats} beats; a demo shows one action in at most ${MAX_BEATS} — split it and put each next to its step`)
    if (!spec.caption) fail(where, 'a panel needs a «caption»: the one line that says what it shows')
  }
  for (const p of parts) if (p.kind === 'tabs' && !p.items.includes(p.active)) fail(where, `«active: ${p.active}» is not one of the tabs`)
  return { title: spec.title, caption: spec.caption || '', head: spec.head || '', agent, beats, parts, walk, top: spec.align === 'top' }
}

// ---------- prerender: the last beat ----------
const visibleAtEnd = (p, n) => p.until === undefined || p.until > n
const attrs = (p, n) => `${p.at ? ` data-at="${p.at}"` : ''}${p.until !== undefined ? ` data-until="${p.until}"` : ''}${visibleAtEnd(p, n) ? '' : ' hidden'}`

function avatar(who, agent) {
  if (who === agent) return `<span class="dp-av dp-av-agent" aria-hidden="true">${AGENT_AVATAR ? `<img src="${esc(AGENT_AVATAR)}" alt="">` : ''}</span>`
  return `<span class="dp-av" aria-hidden="true">${esc([...String(who)][0] || '')}</span>`
}

const button = (label, p, cls = 'dp-btn') => `<span class="${cls}"${p.press !== undefined && (!p.pressing || p.pressing === label) ? ` data-press="${p.press}"` : ''}>${esc(label)}</span>`

function partHtml(p, cfg) {
  // A composer is always on screen; its `at` is when the typing starts.
  const a = p.kind === 'composer' ? '' : attrs(p, cfg.beats)
  switch (p.kind) {
    case 'bars': {
      const lines = Math.max(1, Math.min(3, p.lines || 2))
      const bars = Array.from({ length: lines }, (_, i) => `<i style="--w:${[78, 56, 38][i]}%"></i>`).join('')
      return `<div class="dp-msg dp-skel"${a}><span class="dp-av" aria-hidden="true"></span><div class="dp-main">${p.who ? `<b class="dp-who">${esc(p.who)}</b>` : '<i class="dp-name-bar"></i>'}<div class="dp-bars">${bars}</div></div></div>`
    }
    case 'msg':
      return `<div class="dp-msg"${a}>${avatar(p.who, cfg.agent)}<div class="dp-main"><b class="dp-who">${esc(p.who)}</b><p class="dp-say">${p.to ? `<span class="dp-at">@${esc(p.to)}</span> ` : ''}${esc(p.say)}</p>${p.steps ? `<ol class="dp-steps">${p.steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>` : ''}</div></div>`
    case 'thread':
      return `<div class="dp-thread"${a}><div class="dp-thread-top"><span class="dp-thread-n">${esc(p.text)}</span>${p.status ? `<span class="dp-thread-status">${esc(p.status)}</span>` : ''}</div>${p.last ? `<p class="dp-thread-last">${esc(p.last)}</p>` : ''}</div>`
    case 'typing':
      return `<div class="dp-typing"${a}><span class="dp-dots" aria-hidden="true"><i></i><i></i><i></i></span>${esc(p.text)}</div>`
    case 'line':
      return p.sub
        ? `<div class="dp-empty"${a}><b>${esc(p.text)}</b><span>${esc(p.sub)}</span></div>`
        : `<div class="dp-line"${a}><span>${esc(p.text)}</span>${p.button ? button(p.button, p, 'dp-link') : ''}</div>`
    case 'strip':
      return `<div class="dp-strip"${a}><span>${esc(p.text)}</span>${p.label ? `<span class="dp-strip-pick">${esc(p.label)}<span class="dp-select">${esc(p.value || '')}</span></span>` : ''}</div>`
    case 'doc':
      return `<div class="dp-doc"${a}>${p.headings.map((h, i) => `<b>${esc(h)}</b><i style="--w:${[92, 74, 84, 66][i % 4]}%"></i>`).join('')}</div>`
    case 'tabs':
      return `<div class="dp-tabs"${a}>${p.items.map((x) => `<span${x === p.active ? ' class="on"' : ''}>${esc(x)}</span>`).join('')}</div>`
    case 'composer':
      return `<div class="dp-composer"${a}><span class="dp-input">${p.type ? `<span class="dp-typed" data-at="${p.at}" data-until="${p.press}" data-type hidden>${esc(p.type)}</span>` : ''}<span class="dp-ph">${esc(p.placeholder)}</span></span>${p.button ? button(p.button, p, 'dp-btn dp-btn-quiet') : ''}</div>`
    case 'head':
      return `<div class="dp-head"${a}><span class="dp-title">${p.room ? `<span class="dp-room"># ${esc(p.room)} /</span>` : ''}${esc(p.title)}</span>${p.status ? `<span class="dp-chip">${esc(p.status)}</span>` : ''}<span class="dp-head-end">${p.owner ? `<span class="dp-owner">${avatar(p.who || '你', cfg.agent)}${esc(p.owner)}</span>` : ''}${p.note ? `<span class="dp-note">${esc(p.note)}</span>` : ''}${p.button ? button(p.button, p, p.plain === true ? 'dp-btn' : 'dp-btn dp-btn-primary') : ''}</span></div>`
    case 'files':
      return `<div class="dp-changes"${a}><ul class="dp-files">${fileRows(p.files)}</ul><div class="dp-diff" aria-hidden="true"><i style="--w:70%"></i><i class="add" style="--w:86%"></i><i class="add" style="--w:62%"></i><i class="add" style="--w:78%"></i><i style="--w:54%"></i></div></div>`
    case 'card': {
      const files = (p.files || []).map((f) => {
        const m = /^(.*?)\s+(\+\d+)\s+([−-]\d+)$/.exec(f)
        return m ? `<li><span class="dp-file">${esc(m[1])}</span><span class="dp-add">${esc(m[2])}</span><span class="dp-del">${esc(m[3].replace('-', '−'))}</span></li>` : `<li><span class="dp-file">${esc(f)}</span></li>`
      }).join('')
      const actions = (p.actions || []).map((x, i) => button(x, p, i === 0 ? 'dp-btn dp-btn-primary' : 'dp-btn dp-btn-plain')).join('')
      const row = p.title ? `<div class="dp-card-row">${ic('git')}<b class="dp-card-title">${esc(p.title)}</b>${p.status ? `<span class="dp-card-status">${esc(p.status)}</span>` : ''}${p.button ? button(p.button, p, 'dp-btn dp-btn-primary') : ''}</div>` : ''
      const open = p.ok || p.who || p.note || files || actions
      return `<div class="dp-card${open ? ' dp-card-open' : ''}"${a}>${p.ok ? `<div class="dp-card-ok">${ic('check')}${esc(p.ok)}</div>` : ''}${p.who ? `<div class="dp-card-who">${esc(p.who)}</div>` : ''}${p.note ? `<p class="dp-card-note">${esc(p.note)}</p>` : ''}${files ? `<ul class="dp-files">${files}</ul>` : ''}${actions ? `<div class="dp-actions">${actions}</div>` : ''}${row}</div>`
    }
    case 'notice':
      return `<div class="dp-notice"${a}><span>${esc(p.text)}</span>${p.button ? `<span class="dp-notice-go">${esc(p.button)}</span>` : ''}</div>`
    case 'checklist': {
      const done = Number(p.done ?? p.items.length)
      return `<div class="dp-check"${a}><div class="dp-check-top"><span>${esc(p.title)}</span><span>${done}/${p.items.length}</span></div><ul>${p.items.map((x, i) => `<li${i < done ? ' class="on"' : ''}>${i < done ? ic('check') : '<i class="dp-ring" aria-hidden="true"></i>'}${esc(x)}</li>`).join('')}</ul></div>`
    }
  }
  return ''
}

function fileRows(files) {
  return files.map((f) => {
    const m = /^(.*?)\s+(\+\d+)\s+([−-]\d+)$/.exec(f)
    return m ? `<li><span class="dp-file">${esc(m[1])}</span><span class="dp-add">${esc(m[2])}</span><span class="dp-del">${esc(m[3].replace('-', '−'))}</span></li>` : `<li><span class="dp-file">${esc(f)}</span></li>`
  }).join('')
}

// The window: a header row (a head part replaces it), a feed that fills from the
// bottom like the product's conversation, and a dock for the composer and cards.
const DOCK = ['composer', 'card', 'notice']
// Under the header, above the feed.
const TOP = ['strip', 'tabs']

export function panelHtml(cfg) {
  const heads = cfg.parts.filter((p) => p.kind === 'head')
  const top = cfg.parts.filter((p) => TOP.includes(p.kind))
  const feed = cfg.parts.filter((p) => p.kind !== 'head' && !DOCK.includes(p.kind) && !TOP.includes(p.kind))
  const dock = cfg.parts.filter((p) => DOCK.includes(p.kind))
  const bar = heads.length ? heads.map((p) => partHtml(p, cfg)).join('') : cfg.head ? `<div class="dp-head"><span class="dp-title"># ${esc(cfg.head)}</span></div>` : ''
  return `<figure class="demo-panel${cfg.walk ? ' dp-walk' : ''}" data-demo="panel" data-beats="${cfg.beats}"${cfg.walk ? ' data-walk-panel' : ''} aria-label="${esc(cfg.title)}">
  <div class="dp-stage">
    <div class="dp-win">
      ${bar}
      ${top.map((p) => partHtml(p, cfg)).join('')}
      ${feed.length ? `<div class="dp-feed${cfg.top ? ' dp-feed-top' : ''}">${feed.map((p) => partHtml(p, cfg)).join('')}</div>` : ''}
      ${dock.length ? `<div class="dp-dock">${dock.map((p) => partHtml(p, cfg)).join('')}</div>` : ''}
    </div>
    ${cfg.walk ? '' : '<button type="button" class="dp-replay" data-dp-replay hidden>重播</button>'}
  </div>
  ${cfg.caption ? `<figcaption class="dp-cap">${esc(cfg.caption)}</figcaption>` : ''}
</figure>`
}

// ---------- what a model reads instead ----------
function partText(p) {
  switch (p.kind) {
    case 'msg': return `${p.who}：${p.to ? `@${p.to} ` : ''}${p.say}${p.steps ? `（${p.steps.map((s, i) => `${i + 1}. ${s}`).join('；')}）` : ''}`
    case 'thread': return `消息下面出现「${p.text}」${p.last ? `：${p.last}` : ''}`
    case 'typing': case 'line': case 'notice': return `「${p.text}」${p.sub ? `：${p.sub}` : ''}`
    case 'strip': return `「${p.text}」${p.label ? `，「${p.label}」` : ''}`
    case 'doc': return `文档：${p.headings.join('、')}`
    case 'tabs': return `右侧切到「${p.active}」`
    case 'files': return `改动的文件：${p.files.join('、')}`
    case 'composer': return p.type ? `在输入框里写「${p.type}」` : ''
    case 'head': return `页头：${p.room ? `# ${p.room} / ` : ''}${p.title}${p.status ? `，状态「${p.status}」` : ''}${p.note ? `，「${p.note}」` : ''}`
    case 'card': return `输入框上方的验收条${p.title ? `「${p.title}」` : ''}${[p.status, p.ok, p.who].filter(Boolean).map((x) => `「${x}」`).join('')}${p.note ? `，${p.note}` : ''}${p.actions ? `，按钮 ${p.actions.map((x) => `「${x}」`).join('')}` : ''}${p.button ? `，按钮「${p.button}」` : ''}`
    case 'checklist': return `${p.title}：${p.items.join('；')}`
  }
  return ''
}

export function panelText(cfg) {
  const lines = []
  for (let b = 0; b <= cfg.beats; b++) {
    for (const p of cfg.parts) {
      if (p.press === b) lines.push(`点「${p.pressing || p.button}」`)
    }
    for (const p of cfg.parts) {
      if (p.at === b && p.kind !== 'bars') {
        const t = partText(p)
        if (t) lines.push(t)
      }
    }
  }
  if (cfg.walk) return `**${cfg.title}**（网页上，上面每一步旁边都有这一步之后的画面；这里是文字版。）\n\n${lines.map((l) => `- ${l}`).join('\n')}`
  return `**${cfg.title}**（网页上是一段几秒的演示画面，播完停在结果上；这里是文字版。${cfg.caption}）\n\n${lines.map((l) => `- ${l}`).join('\n')}`
}
