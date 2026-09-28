// The memory-limit demo (fence `demo-memory`): drag the index and watch which
// limit fires.
//
// Three things are drawn at once, because on this page they are three different
// limits: the injection budget (how much of the index reaches the model), the
// single-line limit on an index entry, and the length limit on a body. The
// numbers come from the build (gen/memory_limits.py reads them out of
// `backend/app/domain/memory/files.py`); this file only moves sliders and
// prints what memory-limits.mjs computes — the same port build.mjs checks
// against the real Python.
import { fitIndex, limitBreach, indexTextOf } from './memory-limits.mjs'

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
const kb = (n) => `${(n / 1024).toFixed(1)}KB`

export function mountMemory(fig) {
  const cfg = JSON.parse(fig.querySelector('[data-mem]')?.textContent || 'null')
  if (!cfg) return
  const c = cfg.limits
  const indexName = c.indexName || 'MEMORY.md'
  const state = {
    lines: Math.round(c.INDEX_MAX_LINES * 1.1),
    lineBytes: Math.min(128, Math.max(c.linePrefix.length + 8, 128)),
    newLine: Math.round(c.INDEX_LINE_MAX * 0.8),
    existing: false,
    body: Math.round(c.BODY_MAX * 0.6),
  }

  const root = document.createElement('div')
  root.className = 'mem'
  const slider = (id, label, hint, max, step, value) => `
    <label class="mem-slider">
      <span class="mem-sl">${esc(label)}<output data-out="${id}">${esc(value)}</output></span>
      <input type="range" data-in="${id}" min="0" max="${max}" step="${step}" value="${value}">
      <small>${esc(hint)}</small>
    </label>`
  root.innerHTML = `
    <div class="mem-head"><b>${esc(cfg.title)}</b>${cfg.note ? `<span>${esc(cfg.note)}</span>` : ''}</div>
    <div class="mem-grid">
      <div class="mem-controls">
        <div class="mem-group"><b>索引长什么样</b>
          ${slider('lines', '索引行数', `上限 ${c.INDEX_MAX_LINES} 行`, c.INDEX_MAX_LINES * 2, 1, state.lines)}
          ${slider('lineBytes', '每行字节数', `一行的前缀固定是 ${c.linePrefix.length} 字节`, 400, 8, state.lineBytes)}
        </div>
        <div class="mem-group"><b>这一版新写的一条</b>
          ${slider('newLine', '索引那一行的字符数', `超过 ${c.INDEX_LINE_MAX} 字符就拒收`, c.INDEX_LINE_MAX * 2, 1, state.newLine)}
          <label class="mem-check"><input type="checkbox" data-in="existing"><span>这一行早先就在索引里（不是这一版加的）</span></label>
          ${slider('body', '正文的字数', `超过 ${c.BODY_MAX} 字就拒收`, c.BODY_MAX * 2, 10, state.body)}
        </div>
      </div>
      <div class="mem-reads">
        <div class="mem-read" data-read="inject">
          <b>注入给模型的索引</b>
          <div class="mem-bar"><i data-bar></i><s></s><b class="mem-cap" data-cap></b></div>
          <p data-inject></p>
        </div>
        <div class="mem-read" data-read="index">
          <b>写索引那一行</b>
          <p data-indexline></p>
        </div>
        <div class="mem-read" data-read="body">
          <b>写一条正文</b>
          <p data-body></p>
        </div>
        <div class="mem-verdict" data-verdict></div>
      </div>
    </div>`
  fig.appendChild(root)
  fig.classList.add('mem-live')

  const $ = (s) => root.querySelector(s)
  const budget = () => {
    const text = indexTextOf(c, state.lines, state.lineBytes)
    return { text, fit: fitIndex(c, text) }
  }

  function paint() {
    for (const key of ['lines', 'lineBytes', 'newLine', 'body']) $(`[data-out="${key}"]`).textContent = state[key]
    $('[data-in="existing"]').checked = state.existing

    const { fit } = budget()
    const over = fit.overLines || fit.overBytes
    $('[data-inject]').innerHTML = over
      ? `索引超了上限，注入时在行边界截断：<b>只注入前 ${fit.keptLines} 行 · ${kb(fit.keptBytes)}</b>，后面的 ${fit.oldLines - fit.keptLines} 行不进去，并附一句警告。`
      : `没超上限，<b>整份注入：${fit.keptLines} 行 · ${kb(fit.keptBytes)}</b>，不附警告。`
    // The bar is drawn against what is there, not against the cap: the kept
    // part, then what the cap cut off, with the cap itself ticked so it is
    // visible that the cut is where the tick is.
    const span = Math.max(fit.oldBytes, c.INDEX_MAX_BYTES)
    $('[data-bar]').style.width = `${((fit.keptBytes / span) * 100).toFixed(2)}%`
    $('[data-bar]').className = over ? 'over' : ''
    $('[data-cap]').style.left = `${((c.INDEX_MAX_BYTES / span) * 100).toFixed(2)}%`
    // A cut index is a warning, not a refusal: the write went through.
    $('[data-read="inject"]').className = `mem-read ${over ? 'warn' : 'ok'}`

    const lineBreach = limitBreach(c, { name: indexName, newLineChars: state.newLine, alreadyInIndex: state.existing })
    $('[data-indexline]').innerHTML = lineBreach
      ? `${state.newLine} 字符 > ${c.INDEX_LINE_MAX} 字节上限：<b>拒收</b>，这一版原样存成 <code>MEMORY.md.rejected.md</code>，索引里不留。`
      : state.existing
        ? `这行早先就在索引里，长度不再重算：<b>收下</b>。`
        : `${state.newLine} 字符 ≤ ${c.INDEX_LINE_MAX} 字节上限：<b>收下</b>。`
    $('[data-read="index"]').className = `mem-read ${lineBreach ? 'bad' : 'ok'}`

    const bodyBreach = limitBreach(c, { name: 'note.md', bodyChars: state.body })
    $('[data-body]').innerHTML = bodyBreach
      ? `${state.body} 字 > ${c.BODY_MAX} 字上限：<b>拒收</b>，存成 <code>note.md.rejected.md</code>，正文不落盘。`
      : `${state.body} 字 ≤ ${c.BODY_MAX} 字上限：<b>收下</b>。`
    $('[data-read="body"]').className = `mem-read ${bodyBreach ? 'bad' : 'ok'}`

    const refused = lineBreach || bodyBreach
    $('[data-verdict]').className = `mem-verdict ${refused ? 'bad' : over ? 'warn' : 'ok'}`
    $('[data-verdict]').innerHTML = refused
      ? `<b>接口回 422</b>，收下的那些照常落盘，被拒的那一版进 .rejected.md，会话对账时也一样拦。`
      : over
        ? `<b>接口回 200</b>，附一句索引截断的警告。`
        : `<b>接口回 200</b>，什么都不用提示。`
  }

  // `input` for the sliders as they are dragged, `change` for the checkbox —
  // which some browsers only report once the box has been clicked.
  for (const type of ['input', 'change']) root.addEventListener(type, (e) => {
    const el = e.target.closest('[data-in]')
    if (!el) return
    const key = el.dataset.in
    state[key] = el.type === 'checkbox' ? el.checked : Number(el.value)
    paint()
  })
  paint()
}
