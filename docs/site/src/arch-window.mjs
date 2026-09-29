// The 原理分解 figure's behaviour: pick an entry, pick a scene, walk the stops.
//
// The picture is already in the page when this runs (build.mjs prerendered it
// from the same functions used below), so nothing here invents content: it
// moves the packet, recolours the map from `stateOf`, and swaps the inspector
// for the stop the packet now stands on. Every string it shows was in the
// figure's JSON to begin with.
import { archBoard, archCtl, archSide, stateOf, walkOf, stationAt, colX, rowY, CHIP } from './arch-view.mjs'

const $$ = (s, r) => [...r.querySelectorAll(s)]
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches

// Long enough to read a station's card before the packet moves on.
const GAP = 2600

export function mountArch(fig) {
  const cfg = JSON.parse(fig.querySelector('[data-arch]')?.textContent || 'null')
  if (!cfg || !cfg.entries?.length) return
  const board = fig.querySelector('[data-ar-board]')
  const side = fig.querySelector('[data-ar-side]')
  const ctl = fig.querySelector('[data-ar-ctl]')
  if (!board || !side || !ctl) return

  let entry = cfg.entries[0].key
  let scene = cfg.matrix[entry][0]
  // Reduced motion gets the whole walk at once and never plays itself.
  let pos = reduced ? walkOf(cfg, entry, scene).stops.length : 1
  let playing = false
  let timer = 0

  const walk = () => walkOf(cfg, entry, scene)

  function paintCtl() {
    for (const b of $$('[data-picks="entry"] .ar-pick', ctl)) b.setAttribute('aria-pressed', String(b.dataset.entry === entry))
    for (const b of $$('[data-picks="scene"] .ar-pick', ctl)) b.setAttribute('aria-pressed', String(b.dataset.scene === scene))
    const count = ctl.querySelector('[data-count]')
    if (count) count.textContent = `${Math.min(pos, walk().stops.length)} / ${walk().stops.length}`
    const play = ctl.querySelector('[data-play]')
    if (play) {
      play.setAttribute('aria-pressed', String(playing))
      const label = play.querySelector('[data-play-label]')
      if (label) label.textContent = playing ? '暂停' : '播放'
    }
  }

  function paint() {
    const w = walk()
    pos = Math.max(1, Math.min(w.stops.length, pos))
    const st = stateOf(cfg, w, pos)
    for (const g of $$('[data-station]', board)) {
      const state = st.stations[g.dataset.station]
      g.dataset.state = state
      const chip = g.querySelector('[data-chip]')
      if (chip) chip.textContent = CHIP[state] || ''
    }
    for (const line of $$('[data-wire]', board)) line.dataset.state = st.wires[line.dataset.wire] || 'idle'
    const pkt = board.querySelector('[data-pkt]')
    const here = stationAt(cfg, st.on.at) || cfg.stations[0]
    if (pkt) {
      pkt.style.transform = `translate(${colX(here.col)}px,${rowY(here.row)}px)`
      pkt.dataset.state = st.on.block ? 'block' : st.on.soft ? 'soft' : 'on'
    }
    side.innerHTML = archSide(cfg, w, pos)
    // Data attributes are the figure's public state: a test (and anything else
    // watching the page) reads the demo without reaching into its DOM.
    fig.dataset.entry = entry
    fig.dataset.scene = scene
    fig.dataset.pos = String(pos)
    fig.dataset.stops = String(w.stops.length)
    fig.dataset.block = String(w.stops.filter((s) => s.block).map((s) => s.at).join(' ') || '')
    paintCtl()
  }

  function stop() {
    playing = false
    clearTimeout(timer)
    timer = 0
    paintCtl()
  }

  function tick() {
    timer = 0
    if (pos >= walk().stops.length) return stop()
    pos++
    paint()
    if (pos < walk().stops.length) timer = setTimeout(tick, reduced ? 0 : GAP)
    else stop()
  }

  function play() {
    if (playing) return stop()
    if (pos >= walk().stops.length) { pos = 1; paint() }
    playing = true
    paintCtl()
    timer = setTimeout(tick, 320)
  }

  function reseat(nextEntry, nextScene) {
    const changed = nextEntry !== entry || nextScene !== scene
    entry = nextEntry
    scene = nextScene
    if (changed) pos = reduced ? walk().stops.length : 1
    stop()
    ctl.innerHTML = archCtl(cfg, entry, scene, pos)
    board.innerHTML = archBoard(cfg, walk(), pos)
    paint()
  }

  const goTo = (n) => { stop(); pos = n; paint() }

  ctl.addEventListener('click', (e) => {
    // Only the picker buttons themselves. The figure carries `data-entry` and
    // `data-scene` as its published state, so `closest('[data-entry]')` from one
    // of the nav buttons climbs all the way up and matches the whole figure —
    // which swallowed every 上一站 / 播放 / 下一站 click.
    const pick = e.target.closest('.ar-pick')
    if (pick) {
      const k = pick.dataset.entry
      if (k && k !== entry) {
        // A scene the new entry does not offer falls back to its first.
        const scenes = cfg.matrix[k] || []
        reseat(k, scenes.includes(scene) ? scene : scenes[0])
        return
      }
      const s = pick.dataset.scene
      if (s && s !== scene) reseat(entry, s)
      return
    }
    if (e.target.closest('[data-play]')) return play()
    if (e.target.closest('[data-prev]')) return goTo(pos - 1)
    if (e.target.closest('[data-next]')) return goTo(pos + 1)
  })

  // Arrow keys walk the stops while the focus is inside the figure.
  fig.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    if (!fig.contains(document.activeElement)) return
    e.preventDefault()
    goTo(e.key === 'ArrowRight' ? pos + 1 : pos - 1)
  })

  fig.classList.add('ar-live')
  fig.dataset.kind = cfg.kind
  reseat(entry, scene)
}
