// The context-window demo (fence `demo-context`), after Claude Code's «Explore
// the context window»: one turn of 芝士 played from an empty window, with the
// whole window as a bar, a timeline of what went in, and a side panel that
// explains whatever you point at.
//
// Unlike the other demos this one builds its own DOM: it is an instrument, not
// a list with a highlight. The prerendered list stays in the figure as the
// narrow-screen and no-script version, and the build still puts the same rows
// into the text a model reads.
import { contextSegments } from './demo-model.mjs'

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
// `code` in the prose, the way the fences write it.
const prose = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, '')}K` : String(Math.round(n)))

export const KINDS = {
  auto: { badge: '自动', detail: '开场自动装入' },
  you: { badge: '人', detail: '房间里有人说的' },
  platform: { badge: '平台', detail: '平台投递的' },
  cheese: { badge: '芝士', detail: '芝士干活' },
  sub: { badge: '分身', detail: '在分身自己的窗口里' },
  compact: { badge: '压缩', detail: '骨架自动压缩' },
}
export const SEEN = {
  chat: { mark: '●', label: '对话里看得见', sub: '房间对话里原样出现。' },
  site: { mark: '◐', label: '现场里有一行', sub: '「现场」里记成一行动作，内容本身不摊开。' },
  none: { mark: '○', label: '房间里看不见', sub: '只在模型的窗口里，房间里没有任何痕迹。' },
}

export function mountContextWindow(fig) {
  const cfg = JSON.parse(fig.querySelector('[data-cw]')?.textContent || 'null')
  if (!cfg) return
  const { rows, cats, window: MAX, line, title, note } = cfg
  const data = rows.map((r) => ({ cat: r.cat, v: r.value, keeps: r.keeps || null }))

  let pos = 0 // rows revealed
  let passed = new Set() // gates answered
  let playing = false
  let timer = 0
  let pinned = null
  let hovered = null
  let hotCat = null
  let touched = false

  const root = document.createElement('div')
  root.className = 'cw'
  root.innerHTML = `
    <div class="cw-head">
      <div class="cw-titles"><b class="cw-title">${esc(title)}</b><span class="cw-sub">${esc(note || '')}</span></div>
      <div class="cw-total"><b data-total>0</b><span> tokens</span><small>/ ${fmt(MAX)} · 示意</small></div>
    </div>
    <div class="cw-bars">
      <div class="cw-thin"><i data-thin></i></div>
      <div class="cw-bar" data-bar></div>
      <div class="cw-line" style="left:${(line * 100).toFixed(2)}%" title="骨架的自动压缩线（示意）"><span>自动压缩线</span></div>
      <div class="cw-legend">
        <div class="cw-keys">${cats.map((c) => `<span class="cw-key" data-cat="${c.key}"><i style="background:var(${c.color})"></i>${esc(c.label)}</span>`).join('')}</div>
        <span class="cw-seenkey">● 对话里看得见 · ◐ 现场里一行 · ○ 看不见</span>
      </div>
    </div>
    <div class="cw-body">
      <div class="cw-list" data-list>
        <div class="cw-start" data-start>
          <div class="cw-prompt-line"><span>#</span> ${esc(cfg.topic || '新话题')}<i class="cw-caret"></i></div>
          <button type="button" class="cw-go" data-go>▶ 开始这一轮</button>
          <p>看一轮里芝士的窗口装进了什么：从会话开场、有人说话、它干活，一直到窗口快满时骨架自动压缩。</p>
        </div>
        <div class="cw-card cw-after" data-after hidden></div>
        <div class="cw-rows" data-rows></div>
        <div data-gate></div>
      </div>
      <aside class="cw-side">
        <div class="cw-detail" data-detail></div>
        <div class="cw-box cw-box-key"><small>要点</small><p data-take></p></div>
        <div class="cw-box"><small>房间里你看到的是</small><p data-room></p></div>
      </aside>
    </div>
    <div class="cw-foot">
      <button type="button" class="cw-play" data-play aria-label="播放">▶</button>
      <div class="cw-track"><i data-progress></i></div>
      <span class="cw-pct" data-pct>0%</span>
      <button type="button" class="cw-fs" data-fs aria-label="全屏" title="全屏">⛶</button>
    </div>`
  fig.appendChild(root)
  fig.classList.add('cw-live')

  const $ = (s) => root.querySelector(s)
  const bar = $('[data-bar]')
  const list = $('[data-list]')
  const rowsEl = $('[data-rows]')
  const gateEl = $('[data-gate]')

  // One element per row, built once; paint() only toggles what is shown.
  const els = rows.map((r, i) => {
    const el = document.createElement('div')
    const k = KINDS[r.kind]
    const color = `var(${cats.find((c) => c.key === r.cat)?.color || '--faint'})`
    el.className = `cw-row cw-k-${r.kind}`
    el.dataset.i = String(i)
    el.style.setProperty('--c', color)
    const size = r.kind === 'sub' ? r.value : r.value
    el.innerHTML = `
      ${r.phase ? `<div class="cw-phase">${esc(r.phase)}</div>` : ''}
      ${r.subStart ? `<div class="cw-subhead">分身自己的窗口</div>` : ''}
      <div class="cw-item">
        <span class="cw-rail"><i></i></span>
        <span class="cw-badge">${k.badge}</span>
        <span class="cw-label">${esc(r.label)}</span>
        ${r.kind === 'compact' ? '<span class="cw-tok" data-ctok></span>' : `<span class="cw-tok">+${fmt(size)}</span>`}
        ${r.kind === 'compact' ? '' : `<span class="cw-mini"><i style="width:${Math.min((size / 5000) * 100, 100)}%"></i></span>`}
        <span class="cw-eye" title="${SEEN[r.seen].label}">${SEEN[r.seen].mark}</span>
      </div>
      ${r.subEnd ? `<div class="cw-subfoot" data-subfoot></div>` : ''}`
    rowsEl.appendChild(el)
    return el
  })

  const compactAt = rows.findIndex((r) => r.kind === 'compact')
  const compacted = () => compactAt >= 0 && pos > compactAt

  function segments() {
    return contextSegments(data, pos)
  }
  function total(n = pos) {
    return contextSegments(data, n).reduce((a, s) => a + s.v, 0)
  }

  function paint() {
    const segs = segments()
    const sum = segs.reduce((a, s) => a + s.v, 0)
    const pct = (sum / MAX) * 100
    const tone = pct > 75 ? '--danger' : pct > 50 ? '--warn' : '--ok'
    const active = pinned ?? hovered
    bar.innerHTML = segs.map((s) => {
      const on = s.i === active || (hotCat && rows[s.i].cat === hotCat)
      const dim = hotCat ? rows[s.i].cat !== hotCat : active !== null && s.i !== active
      const color = cats.find((c) => c.key === rows[s.i].cat)?.color || '--faint'
      return `<i data-seg="${s.i}" class="${on ? 'on' : dim ? 'dim' : ''}" style="width:${Math.max((s.v / MAX) * 100, 0.18).toFixed(3)}%;background:var(${color})"></i>`
    }).join('')
    $('[data-total]').textContent = `~${fmt(sum)}`
    $('[data-total]').style.color = `var(${tone}-ink)`
    $('[data-thin]').style.cssText = `width:${pct.toFixed(2)}%;background:var(${tone})`

    const kept = compacted() ? rows[compactAt].keeps || [] : null
    els.forEach((el, i) => {
      const r = rows[i]
      let show = i < pos
      if (show && kept && i < compactAt && !kept.includes(r.cat)) show = false
      el.hidden = !show
      el.classList.toggle('on', i === active)
      el.classList.toggle('dim', !!hotCat && r.cat !== hotCat)
      el.classList.toggle('now', i === pos - 1)
    })
    // The subagent's footer: how much stayed on its side.
    root.querySelectorAll('[data-subfoot]').forEach((f) => {
      const end = Number(f.closest('.cw-row').dataset.i)
      let n = 0
      for (let j = end; j >= 0 && rows[j].kind === 'sub'; j--) n += rows[j].value
      f.textContent = `↓ ${fmt(n)} tokens 留在分身的窗口里 · 回到这里的只有总结`
    })
    const ctok = root.querySelector('[data-ctok]')
    if (ctok && compactAt >= 0) ctok.textContent = `${fmt(total(compactAt))} → ${fmt(total(compactAt + 1))}`
    const after = $('[data-after]')
    after.hidden = !compacted()
    if (compacted()) {
      const before = total(compactAt)
      const now = total(compactAt + 1)
      after.innerHTML = `<b>自动压缩之后</b><p class="cw-mono">${fmt(before)} → ${fmt(now)} tokens · 腾出 ${fmt(before - now)}</p><p>${prose(rows[compactAt].after || '')}</p>`
    }
    $('[data-start]').hidden = pos > 0 || playing

    // Side panel: the row you point at, else the latest row's story.
    const focus = active ?? (pos > 0 ? pos - 1 : null)
    let story = null
    for (let j = focus ?? -1; j >= 0; j--) if (rows[j].takeaway) { story = rows[j]; break }
    $('[data-take]').innerHTML = prose(story?.takeaway || cfg.takeaway || '')
    $('[data-room]').innerHTML = prose(story?.room || cfg.room || '')
    const d = active !== null ? rows[active] : null
    $('[data-detail]').innerHTML = d ? detail(d) : `<div class="cw-empty"><span>☝</span><b>悬停或点一行</b><small>悬停看一眼，点一下钉住，好滚动着读。</small></div>`

    const progress = rows.length ? pos / rows.length : 0
    $('[data-progress]').style.width = `${(progress * 100).toFixed(1)}%`
    $('[data-pct]').textContent = `${Math.round(progress * 100)}%`
    const play = $('[data-play]')
    play.textContent = pos >= rows.length ? '↺' : playing ? '⏸' : '▶'
    play.setAttribute('aria-label', pos >= rows.length ? '重来' : playing ? '暂停' : '播放')
    gate()
  }

  function detail(r) {
    const k = KINDS[r.kind]
    const s = SEEN[r.seen]
    const color = cats.find((c) => c.key === r.cat)?.color || '--faint'
    return `<div class="cw-d-head"><i style="background:var(${color})"></i><b>${esc(r.label)}</b></div>
      <span class="cw-d-kind">${k.detail}</span>
      ${r.kind !== 'compact' ? `<p class="cw-mono cw-d-tok">${fmt(r.value)} tokens${r.kind === 'sub' ? ' · 不占这一格' : ''}</p>` : ''}
      <p>${prose(r.desc)}</p>
      <div class="cw-d-seen cw-seen-${r.seen}"><b>${s.mark} ${s.label}</b><small>${s.sub}</small></div>
      ${r.tip ? `<div class="cw-d-tip"><b>省上下文</b><p>${prose(r.tip)}</p></div>` : ''}
      ${r.link ? `<a class="cw-d-link" href="${esc(r.link)}">看这一节 →</a>` : ''}`
  }

  // The turn waits here for a person: the message box, or the compaction card.
  function waitingGate() {
    const r = rows[pos]
    return r && r.gate && !passed.has(pos) ? r : null
  }
  function gate() {
    const r = waitingGate()
    if (!r) { gateEl.innerHTML = ''; return }
    if (r.kind === 'compact') {
      gateEl.innerHTML = `<div class="cw-card cw-gate-compact"><p>窗口到了 <b class="cw-mono">${fmt(total())} tokens</b>，碰到压缩线了。${prose(r.gate)}</p><button type="button" data-send>继续 ↵</button></div>`
    } else {
      gateEl.innerHTML = `<div class="cw-gate"><small>${esc(r.who || '有人')}在房间里发</small><div class="cw-gate-box"><span>❯</span><p>${prose(r.gate)}<i class="cw-caret"></i></p><button type="button" data-send>发送 ↵</button></div></div>`
    }
    list.scrollTop = list.scrollHeight
  }

  function stop() { playing = false; clearTimeout(timer); paint() }
  function step() {
    timer = 0
    if (pos >= rows.length) return stop()
    if (waitingGate()) { playing = false; return paint() }
    pos++
    paint()
    if (compacted() && pos === compactAt + 1) list.scrollTop = 0
    else list.scrollTop = list.scrollHeight
    const next = rows[pos]
    if (!next) return stop()
    const wait = next.kind === 'auto' ? 140 : next.kind === 'sub' ? 500 : next.big ? 1600 : 800
    timer = setTimeout(step, reduced ? 0 : wait)
  }
  function play() {
    if (playing) return stop()
    if (pos >= rows.length) { pos = 0; passed = new Set(); pinned = null }
    playing = true
    paint()
    timer = setTimeout(step, 250)
  }
  function send() {
    passed.add(pos)
    playing = true
    step()
  }

  root.addEventListener('click', (e) => {
    touched = true
    const t = e.target
    if (t.closest('[data-go]') || t.closest('[data-play]')) return play()
    if (t.closest('[data-send]')) return send()
    if (t.closest('[data-fs]')) {
      if (document.fullscreenElement) document.exitFullscreen()
      else root.requestFullscreen?.().catch(() => {})
      return
    }
    const row = t.closest('.cw-item')?.parentElement
    const seg = t.closest('[data-seg]')
    const i = row ? Number(row.dataset.i) : seg ? Number(seg.dataset.seg) : null
    if (i !== null) { pinned = pinned === i ? null : i; paint() }
  })
  root.addEventListener('mouseover', (e) => {
    const row = e.target.closest('.cw-item')?.parentElement
    const seg = e.target.closest('[data-seg]')
    const key = e.target.closest('.cw-key')
    const i = row ? Number(row.dataset.i) : seg ? Number(seg.dataset.seg) : null
    const cat = key ? key.dataset.cat : null
    if (i !== hovered || cat !== hotCat) { hovered = i; hotCat = cat; paint() }
  })
  root.addEventListener('mouseleave', () => { hovered = null; hotCat = null; paint() })
  // Space plays, sends and restarts — only once you have clicked into it, and only while it is on screen.
  window.addEventListener('keydown', (e) => {
    if (!touched || e.code !== 'Space') return
    if (/INPUT|TEXTAREA|SELECT|BUTTON/.test(e.target.tagName)) return
    const box = root.getBoundingClientRect()
    if (box.bottom < 0 || box.top > innerHeight) return
    e.preventDefault()
    if (waitingGate()) send(); else play()
  })

  paint()
}
