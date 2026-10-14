// The rendered-contrast rule and its ratchet arithmetic, for the public pages
// in contrast-baseline.json. Pure logic — no I/O, no process — so it can be
// tested directly (`node --test`, see contrast-ratchet-core.test.mjs).
//
// The driver is e2e/tests/contrast-ratchet.spec.ts, which is where it has to be:
// the measurement needs a real browser, and Playwright lives under e2e/. The
// rule and the baseline it is judged against stay here, next to the other
// ratchets, so `fe-contrast` is the same shape as `fe-storage` and the
// import-boundary, palette, a11y, stylelint and tsc ratchets beside it.
//
// WHY A RENDERED CHECK. Every other colour gate in this repo reads source: the
// palette ratchet reads template attributes, stylelint reads CSS, and the a11y
// ratchet says in its own header that it cannot see colour contrast. What a
// reader actually gets depends on the cascade — an inherited colour over a
// translucent wash over whatever is behind that — and only the compositor knows
// the answer. #1328 shipped a dark theme with text on a near-white shell and
// #3691 found readable text sitting at 4.33:1 on public pages; both looked
// correct in source.
//
// WHY NOT axe-core. Tried first, and it is the standard implementation, but its
// color-contrast rule reads real text nodes only: generated content is never
// evaluated, and anything outside the accessibility tree is skipped. Measured
// while this was being written, the landing's amber CTA arrow — an `::before`
// glyph at 2.65:1 against a 3:1 floor, one of the two classes this check exists
// to catch — did not appear in axe's report at all. A rule that cannot see one
// of its two subjects is the wrong engine, so the painted runs are measured
// here. That also makes this check and a11y-ratchet.mjs complements rather than
// duplicates.
//
// WHY A RATCHET. The pages start with real sub-floor runs. The amber CTA labels
// at 2.65:1 are a deliberate trade — the brand colour is frozen and only the
// label colour can move — so blocking on zero would mean restyling first or
// leaving the check off. Freeze what is there, block what is new.

/**
 * WCAG relative luminance of an `[r, g, b]` triple, channels 0-255.
 * @param {[number, number, number]} rgb
 */
export function luminance([r, g, b]) {
  const lin = (c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

/**
 * WCAG 2.1 contrast ratio between two opaque colours.
 * @param {[number, number, number]} fg
 * @param {[number, number, number]} bg
 */
export function contrastRatio(fg, bg) {
  const a = luminance(fg)
  const b = luminance(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/**
 * The floor a run has to clear. The same rule is applied inside collectRuns,
 * which runs in the page and cannot import this; the driver asserts the two
 * agree for every run it measures, so a change here that is not mirrored there
 * fails immediately rather than silently.
 * @param {{ size: number, weight: number, icon: boolean }} run
 */
export function floorFor({ size, weight, icon }) {
  if (icon) return 3
  if (size >= 24) return 3
  if (size >= 18.66 && weight >= 700) return 3
  return 4.5
}

/**
 * How a run is identified across two measurements: which page, which theme,
 * which element, which pseudo-element. The measured numbers stay out of it —
 * a font that shifts a glyph by a hair must not read as a different run.
 * @param {{ path: string, theme: string, selector: string, pseudo?: string }} run
 */
export function keyOf({ path, theme, selector, pseudo }) {
  return [path, theme, selector, pseudo || ''].join('|')
}

/**
 * Ratios are reported and frozen at two decimals, so re-measuring the same
 * pixels cannot churn the baseline file.
 */
export function roundRatio(ratio) {
  return Math.round(ratio * 100) / 100
}

/** The public pages this gate covers, by the URL a visitor lands on. The
 * visitor shell wraps their content (src/lib/publicVisitor.ts). `/` and
 * `/about` render the same component and produce the same document, so the
 * landing is listed once — at the URL the site's own links point to. */
export const PAGES = ['/', '/solutions', '/download']

export const THEMES = ['light', 'dark']

/**
 * A re-measure of unchanged pixels moves a ratio by a hundredth at most. The
 * tolerance is what separates that from a real change, so the check does not
 * alternate between "regressed" and "improved" on a run that sits a hair from
 * its floor — `span.scene-avatar`'s "周" at 4.44:1 against a 4.5 floor is one.
 * It cannot hide new debt: this only widens the band around a value that is
 * already frozen, and anything not yet frozen is judged against the floor
 * itself.
 */
export const TOLERANCE = 0.05

/**
 * One recorded run per key — the worst instance of it, since a key that
 * appears nine times on a page is usually the same element repeated, and it is
 * the instance closest to unreadable that the baseline has to answer for. If a
 * worse instance appears later, this is what moves.
 * @param {object[]} runs records from collectRuns(), each already carrying
 *                      `path` and `theme`
 * @returns {Map<string, object>}
 */
export function worstOf(runs) {
  const worst = new Map()
  for (const run of runs) {
    const key = keyOf(run)
    const seen = worst.get(key)
    if (!seen) worst.set(key, run)
    else if (seen.unjudged && !run.unjudged) worst.set(key, run)
    else if (!!seen.unjudged === !!run.unjudged && run.ratio < seen.ratio) worst.set(key, run)
  }
  return worst
}

/** The baseline record for a run: what it measured, and what it was, in the
 * page, so a reader of the JSON knows what the key is without a browser. */
export function entryFor(run) {
  return {
    ratio: run.unjudged ? null : roundRatio(run.ratio),
    need: run.need,
    unjudged: run.unjudged || '',
    sample: run.text,
  }
}

/**
 * Compare a run against the baseline.
 *
 * Three ways to fail, all of them new information:
 *   added    a run below its floor that the baseline has never seen;
 *   worse    a frozen run that got further below its floor, and a run frozen as
 *            unmeasurable that the walker can now read and that turns out to be
 *            below its floor by more than the tolerance — the check just
 *            learned something bad;
 *   unjudged a run the walker cannot measure that was not already frozen as
 *            unmeasurable. A check that quietly ignores what it cannot measure
 *            is worse than no check, so this is reported rather than dropped;
 *   (a frozen run that was measured and is now unmeasurable lands here too —
 *    the ground behind it became a picture.)
 *
 * Anything that improved is reported but does not fail: a baseline that keeps
 * freezing runs that no longer need it stops being able to show a regression,
 * so `improved` has to be paid off with an update, not just noted.
 * @param {Record<string, object>} baseline
 * @param {Map<string, object>} worst from worstOf()
 */
export function compare(baseline, worst) {
  const added = []
  const worse = []
  const unjudged = []
  const improved = []
  const passes = (run) => !run.unjudged && run.ratio >= run.need
  for (const [key, run] of [...worst].sort(([a], [b]) => a.localeCompare(b))) {
    const frozen = baseline[key]
    if (run.unjudged) {
      if (!frozen || !frozen.unjudged) unjudged.push({ key, run })
      continue
    }
    if (!frozen) {
      if (run.ratio < run.need) added.push({ key, run })
      continue
    }
    if (frozen.unjudged) {
      // Frozen as unmeasurable, and the walker can read it now. There is no
      // frozen number to compare against, so the floor is the reference — and
      // the same tolerance, or a run sitting one part in a hundred either side
      // of its floor would turn the gate red and green on alternate runs, which
      // is the flapping `TOLERANCE` exists to stop. Clearing the floor is an
      // improvement; below it by more than the tolerance is a failure like any
      // other, which `tightenedBaseline` then freezes as a number; in between is
      // neither, exactly as it is for a frozen number.
      if (passes(run)) improved.push({ key, was: frozen, run })
      else if (run.ratio < run.need - TOLERANCE) worse.push({ key, was: frozen, run })
      continue
    }
    if (passes(run)) {
      improved.push({ key, was: frozen, run })
      continue
    }
    if (run.ratio < frozen.ratio - TOLERANCE) worse.push({ key, was: frozen, run })
    else if (run.ratio > frozen.ratio + TOLERANCE) improved.push({ key, was: frozen, run })
  }
  for (const key of Object.keys(baseline).sort()) {
    if (!worst.has(key)) improved.push({ key, was: baseline[key], run: null })
  }
  return {
    added,
    worse,
    unjudged,
    improved,
    belowFloor: [...worst.values()].filter((run) => !run.unjudged && run.ratio < run.need).length,
    ok: !added.length && !worse.length && !unjudged.length,
  }
}

/**
 * The baseline the repo should now record. Never looser than reality, so an
 * update after a genuine regression cannot be used to launder it in: a frozen
 * run keeps the worse of the two ratios, and a key that is not in the baseline
 * is only added when `freezeNew` — a person deciding, in the commit, that this
 * is debt the project accepts. That holds for a run the walker cannot measure
 * too: an unfrozen unmeasurable run is new debt, not a free pass. A run that
 * now passes is dropped.
 * @param {Record<string, object>} baseline
 * @param {Map<string, object>} worst from worstOf()
 * @param {{ freezeNew?: boolean }} [options]
 */
export function tightenedBaseline(baseline, worst, { freezeNew = false } = {}) {
  const next = {}
  const keys = new Set([...Object.keys(baseline), ...worst.keys()])
  for (const key of [...keys].sort()) {
    const run = worst.get(key)
    const frozen = baseline[key]
    if (!run) continue // gone from the page — the debt is gone with it
    if (!frozen && !freezeNew) continue // new debt needs --freeze-new, measurable or not
    if (run.unjudged) {
      next[key] = entryFor(run)
      continue
    }
    if (run.ratio >= run.need) continue // clears its floor now
    const ratio =
      frozen && typeof frozen.ratio === 'number' ? Math.min(frozen.ratio, roundRatio(run.ratio)) : roundRatio(run.ratio)
    next[key] = { ...entryFor(run), ratio }
  }
  return next
}

/**
 * The human half: what went wrong, where, and which of the two named colour
 * rules it probably is.
 * @param {ReturnType<typeof compare>} result
 */
export function formatContrastReport(result) {
  const lines = []
  const detail = ({ key, was, run }) => {
    const sample = run ? run.text : was && was.sample
    const where = run ? `${run.selector}${run.pseudo || ''} "${sample}"` : key
    const measured = run
      ? run.unjudged
        ? ` — ${run.unjudged}`
        : ` — ${run.fg} on ${run.bg} at ${run.size}px/${run.weight}, ${run.ratio}:1, needs ${run.need}:1`
      : ''
    const from = was && typeof was.ratio === 'number' ? ` (baseline froze ${was.ratio}:1)` : ''
    return `  ${key}\n    ${where}${measured}${from}`
  }
  for (const [title, rows] of [
    ['New runs below their contrast floor:', result.added],
    ['Frozen runs that are below their floor:', result.worse],
    ['Runs the check cannot measure:', result.unjudged],
  ]) {
    if (!rows.length) continue
    lines.push(title)
    for (const row of rows) lines.push(detail(row))
    lines.push('')
  }
  if (!result.ok) {
    lines.push('The floors are 4.5:1 for text and 3:1 for large text (24px, or 18.66px')
    lines.push('from weight 700) and for icon glyphs. The two usual causes have names')
    lines.push('here: --accent is a fill and not a text colour (write with --accent-ink),')
    lines.push('and --faint is decoration (write with --muted). See docs/design-system.md.')
    lines.push('A run that is deliberately below its floor goes in')
    lines.push('frontend/contrast-baseline.json as accepted debt — not around the check.')
  }
  if (result.improved.length) {
    lines.push(result.ok ? 'Runs that no longer need freezing — tighten the baseline:' : 'Also improved:')
    for (const { key, run } of result.improved) {
      lines.push(`  ${key}${run && typeof run.ratio === 'number' ? ` (now ${run.ratio}:1)` : ' (gone from the page)'}`)
    }
    if (result.ok) {
      lines.push('')
      lines.push('Run: CONTRAST_UPDATE=1 pnpm --dir e2e exec playwright test contrast-ratchet')
      lines.push('Then commit frontend/contrast-baseline.json. It will not add a run the')
      lines.push('baseline has never seen unless CONTRAST_FREEZE_NEW=1 is set too.')
    }
  }
  if (!lines.length) lines.push('every run clears its floor')
  lines.push(`total: ${result.belowFloor} run(s) below floor`)
  return lines.join('\n')
}

/**
 * The in-page half: walks the rendered document and returns one record per
 * painted text run. Runs in the browser — Playwright serialises this function
 * into the page, so it must stay self-contained: no imports, no closure over
 * anything in this module, and no helper it does not define itself. The floor
 * it applies is floorFor() above, which it cannot import; the driver asserts
 * they agree.
 */
export function collectRuns() {
  const parse = (value) => {
    const match = String(value).match(/rgba?\(([^)]+)\)/)
    if (!match) return null
    const parts = match[1].split(',').map((n) => parseFloat(n))
    if (parts.slice(0, 3).some((n) => Number.isNaN(n))) return null
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 }
  }
  const lin = (c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  }
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b)
  const ratioOf = (a, b) => {
    const la = lum(a)
    const lb = lum(b)
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
  }
  const show = (c) => `${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)}`

  // The ground a run sits on, composited bottom-up: the public pages paint
  // translucent washes over each other, and a thin one has to be laid over what
  // is behind it. A background-image or a gradient anywhere between the run and
  // the opaque layer below it means the real ground is a picture this walker
  // cannot read, and those runs come back `unjudged` rather than measured
  // against a colour that is not there.
  const groundOf = (el) => {
    const layers = []
    let blind = false
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node)
      if (style.backgroundImage && style.backgroundImage !== 'none') blind = true
      const bg = parse(style.backgroundColor)
      if (bg && bg.a > 0) layers.push(bg)
      if (bg && bg.a >= 1) break
    }
    let ground = { r: 255, g: 255, b: 255, a: 1 }
    for (let i = layers.length - 1; i >= 0; i -= 1) {
      const layer = layers[i]
      ground = {
        r: layer.r * layer.a + ground.r * (1 - layer.a),
        g: layer.g * layer.a + ground.g * (1 - layer.a),
        b: layer.b * layer.a + ground.b * (1 - layer.a),
        a: 1,
      }
    }
    return { ground, blind }
  }

  // Whether a run paints cannot be read off one box. An element can measure
  // 0×0 and still paint its text — Vuetify's `.v-btn__content`, which holds the
  // amber CTA label, is one of those — so a rect test on the element drops real
  // runs. Hide a run only when an ancestor says so, and otherwise ask the
  // text's own range, which follows the glyphs wherever they land.
  const hidden = new WeakMap()
  const hiddenByAncestor = (el) => {
    if (!el || el.nodeType !== 1) return false
    const cached = hidden.get(el)
    if (cached !== undefined) return cached
    const style = getComputedStyle(el)
    const off =
      hiddenByAncestor(el.parentElement) ||
      style.display === 'none' ||
      style.visibility === 'hidden' ||
      style.contentVisibility === 'hidden' ||
      parseFloat(style.opacity) === 0
    hidden.set(el, off)
    return off
  }
  const textShown = (node) => {
    const range = document.createRange()
    range.selectNodeContents(node)
    const box = range.getBoundingClientRect()
    return box.width > 0 && box.height > 0
  }
  // Generated content has no node to range over, so the fallback for it is
  // "something in this element's box tree has area".
  const elementShown = (el) => {
    if (el.getClientRects().length) return true
    for (const child of el.querySelectorAll('*')) if (child.getClientRects().length) return true
    return false
  }
  const selectorOf = (el) => {
    const classes = [...el.classList].slice(0, 3)
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (classes.length ? '.' + classes.join('.') : '')
  }

  const runs = []
  const record = (el, style, pseudo, text) => {
    const fg = parse(style.color)
    if (!fg) return
    const { ground, blind } = groundOf(el)
    // An element opacity below 1 dims the run against its ground. Folding it
    // into the foreground's alpha is an approximation — it also dims whatever
    // the element covers — but it never overstates contrast the way ignoring
    // it would.
    const alpha = fg.a * parseFloat(getComputedStyle(el).opacity || '1')
    const size = parseFloat(style.fontSize)
    const weight = parseInt(style.fontWeight, 10) || 400
    const icon = /material design icons|mdi/i.test(style.fontFamily)
    const need = icon || size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5
    const capped = Math.min(alpha, 1)
    const painted = {
      r: fg.r * capped + ground.r * (1 - capped),
      g: fg.g * capped + ground.g * (1 - capped),
      b: fg.b * capped + ground.b * (1 - capped),
    }
    runs.push({
      selector: selectorOf(el),
      pseudo,
      // An icon glyph is a private-use character, so it says nothing a reader of
      // the baseline could use — the selector already names it. Generated
      // content arrives quoted, and the quotes are not part of it.
      text: icon && pseudo ? '' : String(text).replace(/\s+/g, ' ').trim().slice(0, 24),
      fg: show(fg),
      bg: show(ground),
      size,
      weight,
      icon,
      need,
      ratio: Math.round(ratioOf(painted, ground) * 100) / 100,
      unjudged: blind ? 'a background image or gradient sits between the run and its ground' : '',
    })
  }

  for (const el of document.querySelectorAll('body *')) {
    if (hiddenByAncestor(el)) continue
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim() && textShown(n))
    if (own.length) record(el, getComputedStyle(el), '', own.map((n) => n.textContent).join(''))
    if (!elementShown(el)) continue
    for (const pseudo of ['::before', '::after']) {
      const style = getComputedStyle(el, pseudo)
      const content = style.content
      if (!content || content === 'none' || content === 'normal') continue
      if (/^["']\s*["']$/.test(content)) continue
      record(el, style, pseudo, content.replace(/^(['"])([\s\S]*)\1$/, '$2'))
    }
  }
  return runs
}
