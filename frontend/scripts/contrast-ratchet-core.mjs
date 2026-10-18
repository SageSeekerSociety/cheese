// The rendered-contrast rule and its verdict, for the public pages. Pure logic —
// no I/O, no process — so it can be tested directly (`node --test`, see
// contrast-ratchet-core.test.mjs).
//
// The driver is e2e/tests/contrast-ratchet.spec.ts, which is where it has to be:
// the measurement needs a real browser, and Playwright lives under e2e/. The
// rule stays here, next to the other frontend checks.
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
// EVERY RUN, NO ALLOWANCE. Every run on the three pages must clear its floor,
// and every run must be one the walker can measure; there is no baseline of
// accepted debt. The amber CTA labels that once sat at 2.65:1 — the brand colour
// is fixed, so only the label colour can move — were paid off by putting dark
// ink on the amber (LandingShell.vue).
//
// WHY THE RUNGS HAVE NAMES, AND ONE TOKEN HAS ONE. The floors are WCAG's, and
// they are reached by the run's size, weight and font — never by the colour it
// was written with. Naming them (TIERS, tierOf) is what lets a failure say
// which rung it is on and what that rung owes. One token is named too:
// `--faint` is the project's decorative rung — a deliberate 3:1 for icons,
// dividers and placeholder shapes, one step off --muted in the neutral ramp. A
// run painted with it that lands below its floor is reported with the fix that
// fits that rung — put the words on --muted, or move the token — rather than
// the generic one, because the answer the ramp forbids is deepening `--faint`
// until words clear 4.5:1, which is what would collapse its step against
// --muted. docs/design-system.md §2.6 and #3691 are where that ruling was made;
// this is the check saying it out loud.

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
 * The rungs of the WCAG AA ladder, by name, and the floor each one owes: text
 * owes 4.5:1, and the 3:1 rung covers large text and graphics. The names are
 * what a failure quotes, so a reader gets "the graphic rung" and not a bare
 * number.
 */
export const TIERS = { body: 4.5, large: 3, graphic: 3 }

/**
 * Which rung a run is on, by name. Deliberately a function of the run's size,
 * weight and font alone — the colour that painted it is not an input. A run too
 * close to its ground to read is not made readable by the token it was written
 * with; that is what makes `--faint` a fix rather than a verdict (#3691,
 * docs/design-system.md §2.6).
 * @param {{ size: number, weight: number, icon: boolean }} run
 * @returns {'body' | 'large' | 'graphic'}
 */
export function tierOf({ size, weight, icon }) {
  if (icon) return 'graphic'
  if (size >= 24 || (size >= 18.66 && weight >= 700)) return 'large'
  return 'body'
}

/**
 * The floor a run has to clear. The same rule is applied inside collectRuns,
 * which runs in the page and cannot import this; the driver asserts the two
 * agree for every run it measures, so a change here that is not mirrored there
 * fails immediately rather than silently.
 * @param {{ size: number, weight: number, icon: boolean }} run
 */
export function floorFor(run) {
  return TIERS[tierOf(run)]
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

/** Ratios are reported at two decimals. */
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
 * One recorded run per key — the worst instance of it, since a key that
 * appears nine times on a page is usually the same element repeated, and it is
 * the instance closest to unreadable that the verdict has to answer for.
 *
 * A key is a selector, and a selector is not always one element. `/solutions`
 * has plain paragraphs on a wash and plain paragraphs on a picture, and only the
 * first kind can be measured; the event is common enough that dropping the
 * second kind would leave the check blind exactly where a page would hide text
 * from it. So the readable instance is kept for its ratio and the ones the
 * walker could not read are counted on it as `unreadable` — one entry, both
 * answers, and a report can say that the ratio does not speak for the whole
 * key.
 * @param {object[]} runs records from collectRuns(), each already carrying
 *                      `path` and `theme`
 * @returns {Map<string, object>}
 */
export function worstOf(runs) {
  const worst = new Map()
  const unreadable = new Map()
  for (const run of runs) {
    const key = keyOf(run)
    if (run.unjudged) {
      unreadable.set(key, (unreadable.get(key) || 0) + 1)
      if (!worst.has(key)) worst.set(key, run)
      continue
    }
    const seen = worst.get(key)
    if (!seen || seen.unjudged || run.ratio < seen.ratio) worst.set(key, run)
  }
  const kept = new Map()
  for (const [key, run] of worst) kept.set(key, { ...run, unreadable: unreadable.get(key) || 0 })
  return kept
}

/**
 * The verdict on one walk. Two ways to fail:
 *   below     a run whose readable instances fall below its floor;
 *   unjudged  a key the walker cannot measure, wholly or in part — a picture or
 *             a gradient between some instance of it and its ground. A page
 *             that puts text behind a picture would otherwise pass this check
 *             by hiding the text from it, so that is a failure, not a note.
 * A key can be in both lists: its readable instance is below its floor and a
 * sibling of the same selector sits on a picture.
 * @param {Map<string, object>} worst from worstOf()
 */
export function judge(worst) {
  const below = []
  const unjudged = []
  for (const [key, run] of [...worst].sort(([a], [b]) => a.localeCompare(b))) {
    if (run.unjudged || run.unreadable) unjudged.push({ key, run })
    if (!run.unjudged && run.ratio < run.need) below.push({ key, run })
  }
  return { below, unjudged, ok: !below.length && !unjudged.length }
}

/**
 * The fix for the one token the rule knows by name, printed under a failing
 * run painted with it. Not "make it darker": `--faint` is the decorative rung —
 * a deliberate 3:1 for icons, dividers and placeholder shapes — and it sits one
 * step off `--muted` in the neutral ramp, so darkening it far enough for words
 * to clear 4.5:1 would collapse that step. The ruling
 * (docs/design-system.md §2.6, #3691) is that the words move off the token.
 */
const FAINT_FIX = [
  '',
  '    --faint is the decorative rung — a deliberate 3:1 for icons, dividers and',
  '    placeholder shapes, not a colour for words. Do not deepen the token: it',
  '    sits one step off --muted, and that step is what the ramp is made of. Put',
  '    the words on --muted (.t-meta-read / .t-eyebrow-read); a decoration that',
  '    reads below 3:1 needs the token itself moved.',
].join('\n')

/**
 * The human half: what went wrong, where, which rung it is on, and which of the
 * named colour rules it is.
 * @param {ReturnType<typeof judge>} result
 */
export function formatContrastReport(result) {
  const lines = []
  const detail = ({ key, run }, answerable) => {
    const where = `${run.selector}${run.pseudo || ''} "${run.text}"`
    const measured = run.unjudged
      ? ` — ${run.unjudged}`
      : ` — ${run.fg} on ${run.bg} at ${run.size}px/${run.weight}, ${run.ratio}:1, needs ${run.need}:1 (the ${tierOf(run)} rung)`
    const blind = run.unreadable ? `, and ${run.unreadable} instance(s) of it the walker cannot read` : ''
    // Only where the run is reported for its own ratio. Under "the check cannot
    // measure" a tagged run is there for the instances the ratio does not speak
    // for — the run itself, or a sibling of the same selector on a picture — and
    // repainting the text is not the answer to that: it leaves the gate red.
    const faint = answerable && run.faint && !run.unjudged && run.ratio < run.need ? FAINT_FIX : ''
    return `  ${key}\n    ${where}${measured}${blind}${faint}`
  }
  for (const [title, rows, answerable] of [
    ['Runs below their contrast floor:', result.below, true],
    ['Runs the check cannot measure:', result.unjudged, false],
  ]) {
    if (!rows.length) continue
    lines.push(title)
    for (const row of rows) lines.push(detail(row, answerable))
    lines.push('')
  }
  if (!result.ok) {
    lines.push('The floors are 4.5:1 for text and 3:1 for large text (24px, or 18.66px')
    lines.push('from weight 700) and for icon glyphs. The two usual causes have names')
    lines.push('here: --accent is a fill and not a text colour (write with --accent-ink),')
    lines.push('and --faint is decoration (write with --muted). See docs/design-system.md.')
  }
  if (!lines.length) lines.push('every run clears its floor')
  lines.push(`total: ${result.below.length} run(s) below floor`)
  return lines.join('\n')
}

/**
 * The in-page half: walks the rendered document and returns one record per
 * painted text run. Runs in the browser — Playwright serialises this function
 * into the page, so it must stay self-contained: no imports, no closure over
 * anything in this module, and no helper it does not define itself. The floor
 * it applies is floorFor() above, which it cannot import; the driver asserts
 * they agree. It also tags the runs painted with `--faint` — the one token the
 * rule knows by name — by resolving it in the document it is walking, which is
 * the only thing it reads from outside the run itself.
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

  // The one token the rule knows by name: `--faint` is the deliberate 3:1
  // decorative rung (docs/design-system.md §2.6), so a run painted with it is
  // tagged as it is recorded and the report answers a failing one with the fix
  // that fits that rung instead of the generic one. Read off the document
  // element of the theme being measured, and compared on the same rounded
  // channels `show()` prints, so both themes are recognised and a colour that
  // merely resembles the token is not. Nothing is tagged when the document does
  // not declare it (no app stylesheet) or declares it in a syntax the two
  // branches below do not read — hex and rgb()/rgba() are how this repo writes
  // tokens, and a guess would tag the wrong runs.
  const faintToken = (() => {
    const declared = getComputedStyle(document.documentElement).getPropertyValue('--faint').trim()
    const hex = declared.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
    if (hex) {
      const full = hex[1].length === 3 ? [...hex[1]].map((c) => c + c).join('') : hex[1]
      return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)).join(',')
    }
    const parsed = parse(declared)
    return parsed ? show(parsed) : ''
  })()

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
      // the report could use — the selector already names it. Generated
      // content arrives quoted, and the quotes are not part of it.
      text: icon && pseudo ? '' : String(text).replace(/\s+/g, ' ').trim().slice(0, 24),
      fg: show(fg),
      bg: show(ground),
      size,
      weight,
      icon,
      need,
      // Whether the run is painted with the named decorative token at full
      // strength, which changes the fix a failure quotes and nothing else. A run
      // dimmed by its own opacity, or painted in a translucent colour that
      // rounds to the token's channels, is not the token reading too faint, so
      // it is not tagged and no fix can blame the token for it.
      faint: alpha === 1 && show(fg) === faintToken,
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
