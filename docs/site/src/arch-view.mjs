// The 原理分解 figure, drawn twice from the same strings.
//
// One fence (`demo-arch`) becomes a map of stations, a packet that walks them,
// and an inspector that opens whichever station the packet is standing on. All
// of it is geometry and text, so it is built here, in pure functions of the
// config: build.mjs calls them to prerender the figure (a reader with no
// JavaScript gets the picture, the fallback list and every word), and
// src/arch-window.mjs calls the very same ones in the browser on every click.
// Two renderings, one set of strings — they cannot drift apart, and a test can
// compare them.
//
// Nothing here touches the DOM, so both sides can import it.

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

// The board. Arithmetic, so a test can assert where a station landed.
export const VIEW = { w: 980, h: 540 }
export const CELL = { w: 162, h: 100 }
export const ORIGIN = { x: 86, y: 66 }
export const BOX = { w: 146, h: 58 }

export const colX = (col) => ORIGIN.x + col * CELL.w
export const rowY = (row) => ORIGIN.y + row * CELL.h

export const stationAt = (cfg, key) => cfg.stations.find((s) => s.key === key)
export const wireKey = (a, b) => `${a}|${b}`

// ---------- one walk, as it stands at `pos` ----------
// `pos` counts stops: 1 = the packet is standing on the first one.
export function walkOf(cfg, entry, scene) {
  return cfg.walks[`${entry}/${scene}`] || Object.values(cfg.walks)[0]
}

// What every station is doing right now, and which wire is lit. A station this
// walk never visits is `skip` — that is the point of the Codex entry, where the
// meter and admission are not in the picture at all.
export function stateOf(cfg, walk, pos) {
  const stops = walk.stops
  const on = stops[pos - 1] || {}
  const visited = new Set(stops.slice(0, pos).map((s) => s.at))
  const ahead = new Set(stops.slice(pos).map((s) => s.at))
  const stations = {}
  for (const st of cfg.stations) {
    if (st.key === on.at) stations[st.key] = on.block ? 'block' : on.soft ? 'soft' : 'on'
    else if (visited.has(st.key)) stations[st.key] = 'done'
    else if (ahead.has(st.key)) stations[st.key] = 'next'
    else stations[st.key] = 'skip'
  }
  const wires = {}
  for (const [a, b] of cfg.wires) wires[wireKey(a, b)] = 'idle'
  for (let i = 0; i + 1 < stops.length; i++) {
    // A wire is one line, whichever way the packet crosses it: a walk that goes
    // back the way it came (`exec → result → screen`) lights the same line.
    const k = wireKey(stops[i].at, stops[i + 1].at)
    const key = k in wires ? k : wireKey(stops[i + 1].at, stops[i].at)
    if (!(key in wires)) continue
    wires[key] = i + 1 < pos ? 'done' : i + 1 === pos ? 'live' : 'idle'
  }
  return { stations, wires, on, pos, n: stops.length }
}

export const CHIP = { skip: '没经过', block: '拦在这里', soft: '软放行', on: '正在这一站', done: '', next: '' }

// ---------- the map ----------
export function archBoard(cfg, walk, pos) {
  const st = stateOf(cfg, walk, pos)
  const wires = cfg.wires.map(([a, b]) => {
    const A = stationAt(cfg, a), B = stationAt(cfg, b)
    if (!A || !B) return ''
    return `<line class="ar-wire" data-wire="${esc(wireKey(a, b))}" data-state="${esc(st.wires[wireKey(a, b)])}" x1="${colX(A.col)}" y1="${rowY(A.row)}" x2="${colX(B.col)}" y2="${rowY(B.row)}"/>`
  }).join('')
  const bands = (cfg.bands || []).map((band) => {
    const cols = band.cells.map(([c]) => c), rws = band.cells.map(([, r]) => r)
    const x = colX(Math.min(...cols)) - BOX.w / 2 - 16, y = rowY(Math.min(...rws)) - BOX.h / 2 - 22
    const w = (Math.max(...cols) - Math.min(...cols)) * CELL.w + BOX.w + 32
    const h = (Math.max(...rws) - Math.min(...rws)) * CELL.h + BOX.h + 32
    return `<g class="ar-band"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="14"/><text x="${x + 12}" y="${y + 17}">${esc(band.label)}</text></g>`
  }).join('')
  const cards = cfg.stations.map((s) => {
    const state = st.stations[s.key]
    return `<g class="ar-st" data-station="${esc(s.key)}" data-state="${esc(state)}" style="--tone:var(${s.tone})" transform="translate(${colX(s.col)},${rowY(s.row)})">
      <rect x="${-BOX.w / 2}" y="${-BOX.h / 2}" width="${BOX.w}" height="${BOX.h}" rx="10"/>
      <text class="ar-name" x="0" y="${s.sub ? -5 : 4}">${esc(s.label)}</text>
      ${s.sub ? `<text class="ar-sub" x="0" y="13">${esc(s.sub)}</text>` : ''}
      <text class="ar-chip" data-chip x="${BOX.w / 2 - 7}" y="${-BOX.h / 2 + 15}" text-anchor="end">${esc(CHIP[state] || '')}</text>
    </g>`
  }).join('')
  // The packet is a `g` moved by a CSS transform: that transitions, where
  // animating `cx`/`cy` does not everywhere.
  const here = stationAt(cfg, st.on.at) || cfg.stations[0]
  const packet = `<g class="ar-pkt" data-pkt style="transform:translate(${colX(here.col)}px,${rowY(here.row)}px)"><circle r="8"/></g>`
  return `<svg class="ar-svg" viewBox="0 0 ${VIEW.w} ${VIEW.h}" role="img" aria-label="${esc(cfg.title)}：现在的第 ${pos} / ${st.n} 站是${esc((stationAt(cfg, st.on.at) || {}).label || '')}">
    ${bands}${wires}${cards}${packet}
  </svg>`
}

// ---------- the inspector ----------
const rows = (pairs) => pairs.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')

export function archSide(cfg, walk, pos) {
  const st = stateOf(cfg, walk, pos)
  const on = st.on
  const at = stationAt(cfg, on.at) || { label: '' }
  const card = (key, title, body) => (body ? `<div class="ar-card" data-card="${key}"><b>${title}</b>${body}</div>` : '')
  const quota = on.quota
    ? `<div class="ar-card ar-quota" data-card="quota"><b>额度读数（示意）</b>
        <div class="ar-bar"><i class="ar-bar-from" data-quota-from style="--w:${esc(on.quota.from)}"></i><i class="ar-bar-to" data-quota-to style="--w:${esc(on.quota.to)}"></i></div>
        <span class="ar-q-note">${esc(on.quota.label)}：${esc(on.quota.from)} → ${esc(on.quota.to)}</span>
      </div>`
    : ''
  return `<div class="ar-side">
    <div class="ar-now">
      <b data-now-station>${esc(at.label)}</b>
      <span data-now-pos>第 ${pos} / ${st.n} 站${on.act ? `｜${esc(on.act)}` : ''}</span>
    </div>
    <p class="ar-head" data-head>${esc(on.head)}</p>
    ${on.block ? '<p class="ar-stop" data-stop="block">请求在这一站被拦下</p>' : on.soft ? '<p class="ar-stop" data-stop="soft">这一站是软的：放行</p>' : ''}
    ${card('see', '它看到了什么', on.see ? `<dl>${rows(on.see)}</dl>` : '')}
    ${card('out', '它送出去了什么', on.out ? `<dl>${rows(on.out)}</dl>` : '')}
    ${card('say', '它的判定', on.say ? `<dl>${rows(on.say)}</dl>` : '')}
    ${quota}
    <p class="ar-note" data-note>${esc(on.note || '')}</p>
    ${on.link ? `<a class="link" href="${esc(on.link)}">看这一节</a>` : ''}
  </div>`
}

// ---------- the controls ----------
export function archCtl(cfg, entry, scene, pos) {
  const sc = cfg.scenes.filter((s) => cfg.matrix[entry].includes(s.key))
  // A group with one choice is not a choice: the machines figure has one entry
  // («一次工具调用»), and one pressed button that does nothing reads as broken.
  const picks = (what, label, one, many) => (many.length < 2 ? '' : `<div class="ar-picks" data-picks="${what}" role="group" aria-label="${label}">
      ${many.map(one).join('')}
    </div>`)
  return `<div class="ar-ctl">
    ${picks('entry', '入口', (e) => `<button type="button" class="ar-pick" data-entry="${esc(e.key)}" aria-pressed="${e.key === entry}">${esc(e.label)}</button>`, cfg.entries)}
    ${picks('scene', '场景', (s) => `<button type="button" class="ar-pick" data-scene="${esc(s.key)}" aria-pressed="${s.key === scene}">${esc(s.label)}</button>`, sc)}
    <div class="ar-nav">
      <button type="button" class="ar-btn" data-prev aria-label="上一站">←</button>
      <button type="button" class="ar-btn ar-play" data-play aria-pressed="false"><span data-play-label>播放</span></button>
      <button type="button" class="ar-btn" data-next aria-label="下一站">→</button>
      <span class="ar-count" data-count aria-live="polite">${pos} / ${walkOf(cfg, entry, scene).stops.length}</span>
    </div>
  </div>`
}

// What a narrow screen (and a reader with JavaScript off) gets instead: every
// walk, station by station, in the order the packet walks them — and where it
// stops. Real HTML in the page, so it is also what a search index reads.
export function archFallback(cfg) {
  const list = cfg.entries.map((e) => cfg.matrix[e.key].map((k) => {
    const walk = cfg.walks[`${e.key}/${k}`]
    const scene = cfg.scenes.find((s) => s.key === k) || { label: k }
    const stops = walk.stops.map((s) => {
      const at = stationAt(cfg, s.at) || { label: s.at }
      return `${esc(at.label)}${s.block ? '（拦在这里）' : s.soft ? '（软放行）' : ''}`
    }).join(' → ')
    const blocked = walk.stops.find((s) => s.block)
    const end = blocked
      ? `停在第 ${walk.stops.indexOf(blocked) + 1} 站：${esc(blocked.head)}`
      : `走完，共 ${walk.stops.length} 站`
    return `<li${blocked ? ' class="block"' : ''}><b>${esc(e.label)} × ${esc(scene.label)}</b><p>${stops}</p><p class="ar-f-end">${end}</p></li>`
  }).join('')).join('')
  return `<ol class="ar-fallback">${list}</ol>`
}
