// The CI scope demo (fence `demo-ci`): tick the paths a merge touches, watch
// the ten suites light up — which run, which skip, and which path matched which
// pattern. The selection is src/ci-scope.mjs, the same code build.mjs checked
// against `.github/scripts/required-ci.py`.
//
// Like the context window this one builds its own DOM: it is an instrument,
// not a list with a highlight. The prerendered list stays in the figure as the
// narrow-screen and no-script version, and the build still puts the same facts
// into the text a model reads.
import { selectSuites, fnmatchcase } from './ci-scope.mjs'

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

export function mountCiScope(fig) {
  const cfg = JSON.parse(fig.querySelector('[data-ci]')?.textContent || 'null')
  if (!cfg) return
  const { suites, scenarios, title, note } = cfg
  const patterns = Object.fromEntries(cfg.suites.map((s) => [s.key, s.patterns]))
  const paths = cfg.paths.map((p) => ({ ...p }))
  let chosen = new Set(scenarios[0] ? scenarios[0].paths : [])
  let hot = null // the row/column the pointer is on
  let hotPath = null

  const root = document.createElement('div')
  root.className = 'cix'
  root.innerHTML = `
    <div class="cix-head">
      <div class="cix-titles"><b>${esc(title)}</b><span>${esc(note || '')}</span></div>
      <div class="cix-total"><b data-count>0</b><span> 个套件要跑</span></div>
    </div>
    <div class="cix-body">
      <div class="cix-left">
        <div class="cix-label">这次改到了哪些路径</div>
        <div class="cix-scen" data-scen role="group" aria-label="预设场景"></div>
        <div class="cix-paths" data-paths></div>
        <form class="cix-add" data-add>
          <input data-input type="text" aria-label="再加一条路径" placeholder="再加一条，比如 deploy/deploy-docker.sh">
          <button type="submit">加</button>
        </form>
      </div>
      <div class="cix-right">
        <div class="cix-label">这次会跑的套件</div>
        <div class="cix-suites" data-suites></div>
      </div>
    </div>
    <div class="cix-foot" data-verdict></div>`
  fig.appendChild(root)
  fig.classList.add('cix-live')

  const $ = (s) => root.querySelector(s)
  const scenEl = $('[data-scen]')
  const pathsEl = $('[data-paths]')
  const suitesEl = $('[data-suites]')

  scenEl.innerHTML = scenarios.map((s) => `<button type="button" class="cix-scenario" data-scen-key="${esc(s.key)}">${esc(s.label)}</button>`).join('')
  suitesEl.innerHTML = suites.map((s) => `
    <div class="cix-suite" data-suite="${esc(s.key)}"${s.patterns.length > 8 ? ` title="pattern：${esc(s.patterns.join(' '))}"` : ''}>
      <div class="cix-suite-top"><b>${esc(s.key)}</b><span class="cix-state" data-state></span></div>
      <div class="cix-why" data-why></div>
      <div class="cix-meta">${esc(s.desc)}${s.workflow ? ` · <code>${esc(s.workflow)}</code>` : ''}</div>
    </div>`).join('')

  function read() {
    return [...chosen]
  }
  // The rows are built when the list of paths grows and merely touched up
  // afterwards: rebuilding them on every hover would yank the row out from
  // under the pointer.
  let painted = -1
  function syncPaths() {
    if (painted !== paths.length) {
      pathsEl.innerHTML = paths.map((p, i) => `
        <label class="cix-path" data-path="${esc(p.path)}">
          <input type="checkbox" data-i="${i}">
          <code>${esc(p.path)}</code>${p.label ? `<span>${esc(p.label)}</span>` : ''}
        </label>`).join('')
      painted = paths.length
    }
    for (const el of pathsEl.querySelectorAll('.cix-path')) {
      const path = el.dataset.path
      // Pointing at a path lights the suites it starts; pointing at a suite
      // lights the paths that reach it. The question is either one.
      el.classList.toggle('hot', hotPath ? hotPath === path : !!hot && (patterns[hot] || []).some((p) => fnmatchcase(path, p)))
      el.querySelector('input').checked = chosen.has(path)
    }
  }

  function paint() {
    const picked = read()
    const sel = selectSuites(picked, patterns)
    const running = suites.filter((s) => sel[s.key].run)
    const hotSel = hotPath ? selectSuites([hotPath], patterns) : null

    $('[data-count]').textContent = String(running.length)
    root.querySelectorAll('[data-scen-key]').forEach((b) => {
      const s = scenarios.find((x) => x.key === b.dataset.scenKey)
      const same = s && s.paths.length === picked.length && s.paths.every((p) => chosen.has(p))
      b.classList.toggle('on', !!same)
    })
    root.querySelectorAll('.cix-suite').forEach((el) => {
      const s = sel[el.dataset.suite]
      const why = el.querySelector('[data-why]')
      const state = el.querySelector('[data-state]')
      el.classList.toggle('run', s.run)
      el.classList.toggle('skip', !s.run)
      if (hotSel) el.classList.toggle('dim', !hotSel[el.dataset.suite].run)
      else el.classList.remove('dim')
      state.textContent = s.run ? '跑' : '跳过'
      if (s.why === 'gate') why.textContent = '改到这套检查本身，所有套件都跑'
      else if (s.why === 'always') why.textContent = '每次都跑'
      else if (s.hits.length) why.innerHTML = s.hits.slice(0, 3).map(([p, pat]) => `<code>${esc(p)}</code> ← <code>${esc(pat)}</code>`).join('<br>') + (s.hits.length > 3 ? `<br>还有 ${s.hits.length - 3} 条命中` : '')
      else why.innerHTML = `没有一条路径命中它的 pattern：<code>${esc((patterns[el.dataset.suite] || []).slice(0, 3).join(' '))}</code>${(patterns[el.dataset.suite] || []).length > 3 ? ' …' : ''}`
    })
    syncPaths()
    const skipped = suites.length - running.length
    const list = running.map((s) => s.key).join(' · ') || '（没有）'
    $('[data-verdict]').innerHTML = `
      <div><b>CI required</b> 只要求这一个检查：上面选中的 <b class="ok">${running.length}</b> 个套件必须 <b class="ok">success</b>，没选中的 <b class="warn">${skipped}</b> 个必须是 <b class="warn">skipped</b>。</div>
      <div class="cix-verdict-line">这次要跑：<code>${esc(list)}</code></div>
      <div class="cix-verdict-line">缺一个、或没选中的那个跑了，这一条就判失败。</div>`
  }

  root.addEventListener('change', (e) => {
    const box = e.target.closest('input[type=checkbox]')
    if (!box) return
    const p = paths[Number(box.dataset.i)].path
    if (box.checked) chosen.add(p)
    else chosen.delete(p)
    paint()
  })
  root.addEventListener('click', (e) => {
    const b = e.target.closest('[data-scen-key]')
    if (!b) return
    const s = scenarios.find((x) => x.key === b.dataset.scenKey)
    for (const p of s.paths) if (!paths.some((x) => x.path === p)) paths.push({ path: p, label: '' })
    chosen = new Set(s.paths)
    paint()
  })
  root.querySelector('[data-add]').addEventListener('submit', (e) => {
    e.preventDefault()
    const input = $('[data-input]')
    const value = input.value.trim()
    if (!value) return
    if (!paths.some((p) => p.path === value)) paths.push({ path: value, label: '自己加的' })
    chosen.add(value)
    input.value = ''
    paint()
  })
  // Whichever side the pointer is on, the other side answers: see syncPaths.
  root.addEventListener('mouseover', (e) => {
    const path = e.target.closest('[data-path]')?.dataset.path || null
    const suite = e.target.closest('.cix-suite')?.dataset.suite || null
    if (path === hotPath && suite === hot) return
    hotPath = path
    hot = suite
    paint()
  })
  root.addEventListener('mouseleave', () => { hotPath = null; hot = null; paint() })

  paint()
}
