// Tests for the accessibility ratchet. Run by `pnpm run test:ratchet`
// (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
//   1. the counting and what must not be counted — pure functions on fixtures;
//   2. what the rules fire on, through eslint's own API against the real
//      eslint.a11y.config.mjs, so a config that stopped applying the plugin
//      fails here rather than going quietly green;
//   3. a plant — a file that breaks a rule is written into the tree and the
//      ratchet must go red on it, name the file and the rule, refuse to freeze
//      it on a plain --update, and go green once it is frozen on purpose;
//   4. a split — the same plant moved from one file into another, and the
//      re-key that follows it: the counts move, the total does not, and every
//      way of getting that wrong is refused, naming the file and the number
//      that did not add up.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import { A11Y_RULE_PREFIX, countsByRule, formatMove, movedBaseline, parseEslintJson } from './a11y-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.a11y.config.mjs')
const RATCHET = resolve(HERE, 'a11y-ratchet.mjs')
const BASELINE = resolve(ROOT, 'a11y-baseline.json')

// ---------------------------------------------------------------------------
// 1. Counting.
// ---------------------------------------------------------------------------

describe('parseEslintJson', () => {
  it('counts only the accessibility rules, per file, relative to the root', () => {
    const { counts, violations, unjudged } = parseEslintJson(
      JSON.stringify([
        {
          filePath: '/repo/frontend/src/views/A.vue',
          messages: [
            { ruleId: 'vuejs-accessibility/no-autofocus', line: 3, column: 5, message: 'autofocus' },
            { ruleId: 'vuejs-accessibility/label-has-for', line: 9, column: 1, message: 'label' },
            { ruleId: 'prettier/prettier', line: 1, column: 1, message: 'formatting' },
          ],
        },
        { filePath: '/repo/frontend/src/views/B.vue', messages: [{ ruleId: 'vue/no-unused-vars', message: 'x' }] },
      ]),
      '/repo/frontend'
    )
    assert.deepEqual(counts, { 'src/views/A.vue': 2 })
    assert.deepEqual(
      violations.map((v) => `${v.file}:${v.line} ${v.rule}`),
      ['src/views/A.vue:3 vuejs-accessibility/no-autofocus', 'src/views/A.vue:9 vuejs-accessibility/label-has-for']
    )
    assert.deepEqual(unjudged, [])
    assert.deepEqual(countsByRule(violations), {
      'vuejs-accessibility/no-autofocus': 1,
      'vuejs-accessibility/label-has-for': 1,
    })
  })

  it('does not count a file eslint could not read as clean', () => {
    const { counts, unjudged } = parseEslintJson(
      JSON.stringify([
        {
          filePath: '/repo/frontend/src/Broken.vue',
          fatal: true,
          messages: [{ ruleId: null, message: 'Parsing error: Unexpected token' }],
        },
      ]),
      '/repo/frontend'
    )
    assert.deepEqual(counts, {})
    assert.match(unjudged[0], /src\/Broken\.vue: Parsing error/)
  })

  it('refuses output that is not a JSON array', () => {
    assert.throws(() => parseEslintJson('', '/r'), /JSON/)
    assert.throws(() => parseEslintJson('{"error": "boom"}', '/r'), /not a JSON array/)
  })
})

// ---------------------------------------------------------------------------
// 2. The rules, through the real config.
// ---------------------------------------------------------------------------

async function a11yRules(eslint, template) {
  const code = `<script setup lang="ts">\nconst go = () => 1\n</script>\n\n<template>\n${template}\n</template>\n`
  const [result] = await eslint.lintText(code, { filePath: 'src/components/A11yProbe.vue', warnIgnored: false })
  assert.equal(result.fatal ?? false, false, JSON.stringify(result.messages))
  return result.messages.filter((m) => String(m.ruleId).startsWith(A11Y_RULE_PREFIX)).map((m) => m.ruleId)
}

describe('the accessibility config', () => {
  const eslint = new ESLint({ cwd: ROOT, overrideConfigFile: CONFIG })

  it('flags a clickable <div> that a keyboard cannot reach', async () => {
    const rules = await a11yRules(eslint, '  <div @click="go">open</div>')
    assert.ok(rules.includes('vuejs-accessibility/click-events-have-key-events'), rules.join())
    assert.ok(rules.includes('vuejs-accessibility/no-static-element-interactions'), rules.join())
  })

  it('leaves a <button> doing the same thing alone', async () => {
    assert.deepEqual(await a11yRules(eslint, '  <button type="button" @click="go">open</button>'), [])
  })

  it('flags an image with no alt text', async () => {
    assert.deepEqual(await a11yRules(eslint, '  <img src="/x.png" />'), ['vuejs-accessibility/alt-text'])
  })
})

// ---------------------------------------------------------------------------
// 3. The plant.
// ---------------------------------------------------------------------------

describe('the ratchet on a planted violation', () => {
  const PROBE_REL = 'src/components/__a11yProbe.vue'
  const PROBE = resolve(ROOT, PROBE_REL)
  const sandbox = mkdtempSync(join(tmpdir(), 'a11y-ratchet-'))

  after(() => {
    rmSync(PROBE, { force: true })
    rmSync(sandbox, { recursive: true, force: true })
  })

  const runRatchet = (...args) => spawnSync(process.execPath, [RATCHET, ...args], { cwd: ROOT, encoding: 'utf8' })
  // Line 6, column 3: a clickable <div> — two violations, one element.
  const CLICKABLE_DIV =
    '<script setup lang="ts">\nconst go = () => 1\n</script>\n\n<template>\n  <div @click="go">x</div>\n</template>\n'
  const frozen = (path) => JSON.parse(readFileSync(path, 'utf8')).files

  it('blocks it by name and rule, will not launder it, and accepts it only when frozen on purpose', () => {
    const scratch = join(sandbox, 'baseline.json')
    try {
      writeFileSync(PROBE, CLICKABLE_DIV)

      const blocked = runRatchet('--files', PROBE_REL, '--baseline', scratch)
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__a11yProbe\.vue: 0 -> 2/)
      assert.match(blocked.stdout, /__a11yProbe\.vue:6:3 {2}vuejs-accessibility\/click-events-have-key-events/)

      // A plain --update must not swallow it.
      const laundered = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update')
      assert.equal(laundered.status, 1, `${laundered.stdout}${laundered.stderr}`)
      assert.match(laundered.stderr, /wrote nothing/)
      assert.throws(() => readFileSync(scratch), /ENOENT/)

      const frozenNew = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update', '--freeze-new')
      assert.equal(frozenNew.status, 0, `${frozenNew.stdout}${frozenNew.stderr}`)
      assert.match(frozenNew.stdout, /freezing src\/components\/__a11yProbe\.vue: 0 -> 2/)
      assert.deepEqual(frozen(scratch), { [PROBE_REL]: 2 })

      const green = runRatchet('--files', PROBE_REL, '--baseline', scratch)
      assert.equal(green.status, 0, `${green.stdout}${green.stderr}`)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  it('--update lowers a count that went down and never raises one', () => {
    const scratch = join(sandbox, 'baseline-shrink.json')
    try {
      // Frozen at 5, now 2: the update writes 2.
      writeFileSync(PROBE, CLICKABLE_DIV)
      writeFileSync(scratch, JSON.stringify({ files: { [PROBE_REL]: 5, 'src/gone.vue': 3 } }))
      const lowered = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update')
      assert.equal(lowered.status, 0, `${lowered.stdout}${lowered.stderr}`)
      assert.deepEqual(frozen(scratch), { [PROBE_REL]: 2 })

      // Frozen at 1, now 2: the update refuses and leaves the file as it was.
      writeFileSync(scratch, JSON.stringify({ files: { [PROBE_REL]: 1 } }))
      const raised = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update')
      assert.equal(raised.status, 1, `${raised.stdout}${raised.stderr}`)
      assert.deepEqual(frozen(scratch), { [PROBE_REL]: 1 })
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  it('the committed baseline is keyed by paths that match on any machine', () => {
    const files = frozen(BASELINE)
    for (const [key, count] of Object.entries(files)) {
      assert.match(key, /^src\/.+\.vue$/, `baseline key is not a src/ .vue path: ${key}`)
      assert.ok(Number.isInteger(count) && count > 0, `${key}: ${count}`)
    }
  })
})

// ---------------------------------------------------------------------------
// 4. The move: re-keying a split.
// ---------------------------------------------------------------------------

describe('movedBaseline', () => {
  const sum = (counts) => Object.values(counts).reduce((a, b) => a + b, 0)
  const FROM = 'src/views/Whole.vue'
  const TO = 'src/components/Half.vue'
  const OTHER = 'src/views/Other.vue'
  // Four frozen on the file that splits, two on a file the split does not touch.
  const baseline = { [FROM]: 4, [OTHER]: 2 }
  // The split itself: two of the four travelled, so the tree measures two on
  // each side. Fractions differ on purpose — equal numbers hide which side a
  // count in the message came from.
  const split = { [FROM]: 2, [TO]: 2, [OTHER]: 2 }

  it('re-keys the count between the two files and leaves the total alone', () => {
    const plan = movedBaseline(baseline, split, { from: FROM, to: TO, count: 2 })
    assert.deepEqual(plan.next, { [FROM]: 2, [TO]: 2, [OTHER]: 2 })
    assert.equal(plan.total, 6)
    assert.equal(sum(plan.next), sum(baseline))
    assert.deepEqual(plan.source, { file: FROM, frozen: 4, after: 2, measured: 2 })
    assert.deepEqual(plan.target, { file: TO, frozen: 0, after: 2, measured: 2 })
  })

  it('drops the source key when the whole allowance moves', () => {
    const plan = movedBaseline({ [FROM]: 2 }, { [TO]: 2 }, { from: FROM, to: TO, count: 2 })
    assert.deepEqual(plan.next, { [TO]: 2 })
    assert.equal(plan.total, 2)
  })

  it('adds to a target that was already frozen', () => {
    // Into an existing sibling rather than a brand new file: one frozen there
    // plus two moved, and the tree measures three.
    const plan = movedBaseline({ [FROM]: 4, [TO]: 1 }, { [FROM]: 2, [TO]: 3 }, { from: FROM, to: TO, count: 2 })
    assert.deepEqual(plan.next, { [FROM]: 2, [TO]: 3 })
    assert.deepEqual(plan.target, { file: TO, frozen: 1, after: 3, measured: 3 })
  })

  it('refuses a count the source never froze — the total would rise', () => {
    assert.throws(
      () => movedBaseline({ [FROM]: 2 }, { [TO]: 3 }, { from: FROM, to: TO, count: 3 }),
      /the frozen total would rise from 2 to 3/
    )
  })

  it('refuses a count larger than what actually left the source', () => {
    // The count says three travelled; the tree still measures three in the
    // source, so only one did.
    assert.throws(
      () => movedBaseline({ [FROM]: 4 }, { [FROM]: 3, [TO]: 3 }, { from: FROM, to: TO, count: 3 }),
      /the tree still shows 3 violation\(s\) in src\/views\/Whole\.vue/
    )
  })

  it('refuses a move that empties the source while the tree still measures in it', () => {
    // The whole allowance moves, so the source key is dropped — and the tree
    // still shows one violation there. The check has to compare against the
    // allowance the source keeps (zero), not against the dropped key: a number
    // read back off `next` would be `undefined`, and every comparison with it
    // is false, which is how this one silently stopped firing.
    assert.throws(
      () => movedBaseline({ [FROM]: 2 }, { [FROM]: 1, [TO]: 2 }, { from: FROM, to: TO, count: 2 }),
      /the tree still shows 1 violation\(s\) in src\/views\/Whole\.vue, more than the 0 the move/
    )
  })

  it('refuses when the target does not measure what the move would freeze there', () => {
    // One more in the target than travelled: that one was written, not moved.
    assert.throws(
      () => movedBaseline({ [FROM]: 4 }, { [FROM]: 2, [TO]: 3 }, { from: FROM, to: TO, count: 2 }),
      /measures 3 violation\(s\) in src\/components\/Half\.vue, but the move would freeze 2/
    )
    // One fewer: the target would sit frozen above what the tree shows, and the
    // slack is what a later violation would spend.
    assert.throws(
      () => movedBaseline({ [FROM]: 4 }, { [FROM]: 2, [TO]: 1 }, { from: FROM, to: TO, count: 2 }),
      /measures 1 violation\(s\) in src\/components\/Half\.vue, but the move would freeze 2/
    )
  })

  it('refuses counts and paths that are not a move at all', () => {
    const ok = { from: FROM, to: TO, count: 2 }
    for (const count of [0, -1, 1.5, Number.NaN]) {
      assert.throws(() => movedBaseline(baseline, split, { ...ok, count }), /positive whole number/, `${count}`)
    }
    assert.throws(() => movedBaseline(baseline, split, { ...ok, to: FROM }), /the same file/)
    assert.throws(() => movedBaseline(baseline, split, { ...ok, to: 'src/components/Half.ts' }), /not a baseline key/)
    assert.throws(
      () => movedBaseline(baseline, split, { ...ok, from: '/repo/src/views/Whole.vue' }),
      /not a baseline key/
    )
  })
})

describe('formatMove', () => {
  const plan = () =>
    movedBaseline(
      { 'src/views/Whole.vue': 4 },
      { 'src/views/Whole.vue': 3, 'src/components/Half.vue': 1 },
      { from: 'src/views/Whole.vue', to: 'src/components/Half.vue', count: 1 }
    )

  it('names both files, both sides of the re-key, and what the target measures', () => {
    const text = formatMove(plan(), [
      { file: 'src/components/Half.vue', rule: 'vuejs-accessibility/no-static-element-interactions' },
      { file: 'src/views/Whole.vue', rule: 'vuejs-accessibility/click-events-have-key-events' },
    ])
    assert.match(text, /^moved 1 violation\(s\): src\/views\/Whole\.vue -> src\/components\/Half\.vue/)
    assert.match(text, /src\/views\/Whole\.vue: frozen 4 -> 3, the tree measures 3/)
    assert.match(text, /src\/components\/Half\.vue: frozen 0 -> 1, the tree measures 1/)
    assert.match(text, /check these are what moved\): no-static-element-interactions 1/)
    assert.match(text, /frozen total: 4 -> 4/)
  })

  it('lists the rules measured in the target, not the ones left behind', () => {
    const text = formatMove(plan(), [
      { file: 'src/components/Half.vue', rule: 'vuejs-accessibility/no-static-element-interactions' },
      { file: 'src/views/Whole.vue', rule: 'vuejs-accessibility/click-events-have-key-events' },
    ])
    assert.doesNotMatch(text, /click-events-have-key-events/)
  })
})

// ---------------------------------------------------------------------------
// 5. The move through the CLI, against a split planted in the tree.
// ---------------------------------------------------------------------------

describe('the ratchet on a split', () => {
  const FROM_REL = 'src/components/__a11ySplitWhole.vue'
  const TO_REL = 'src/components/__a11ySplitHalf.vue'
  const STRAY_REL = 'src/components/__a11ySplitStray.vue'
  const PROBES = [FROM_REL, TO_REL, STRAY_REL].map((rel) => resolve(ROOT, rel))
  const sandbox = mkdtempSync(join(tmpdir(), 'a11y-split-'))

  after(() => {
    for (const probe of PROBES) rmSync(probe, { force: true })
    rmSync(sandbox, { recursive: true, force: true })
  })

  const runRatchet = (...args) => spawnSync(process.execPath, [RATCHET, ...args], { cwd: ROOT, encoding: 'utf8' })
  const frozen = (path) => JSON.parse(readFileSync(path, 'utf8')).files
  // `--files` takes one path per occurrence, so two files are two flags.
  const files = (...rels) => rels.flatMap((rel) => ['--files', rel])
  // One clickable <div> is two violations, so a file's count is twice its divs.
  const withDivs = (n) =>
    `<script setup lang="ts">\nconst go = () => 1\n</script>\n\n<template>\n${'  <div @click="go">x</div>\n'.repeat(n)}</template>\n`
  const move = (from, to, count) => ['--move-from', from, '--move-to', to, '--move-count', String(count)]

  // The split under test: four violations in one file become two and two.
  const splitInTree = () => {
    writeFileSync(resolve(ROOT, FROM_REL), withDivs(1))
    writeFileSync(resolve(ROOT, TO_REL), withDivs(1))
  }

  it('re-keys the counts, and the ratchet is green on the split afterwards', () => {
    const scratch = join(sandbox, 'baseline.json')
    try {
      writeFileSync(scratch, JSON.stringify({ files: { [FROM_REL]: 4 } }))
      splitInTree()

      // What the split does to the ratchet today: the new key reads as new debt.
      const blocked = runRatchet(...files(FROM_REL, TO_REL), '--baseline', scratch)
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__a11ySplitHalf\.vue: 0 -> 2/)

      // The move reads the tree and, without --update, writes nothing.
      const dry = runRatchet(...files(FROM_REL, TO_REL), '--baseline', scratch, ...move(FROM_REL, TO_REL, 2))
      assert.equal(dry.status, 0, `${dry.stdout}${dry.stderr}`)
      assert.match(dry.stdout, /^moved 2 violation\(s\): .*__a11ySplitWhole\.vue -> .*__a11ySplitHalf\.vue/m)
      assert.match(dry.stdout, /frozen total: 4 -> 4/)
      // The rule line is the evidence: one clickable <div> carried over, which is
      // one violation of each of the two rules it breaks. The summary line below
      // counts both files (two divs), so it is not this number.
      assert.match(dry.stdout, /check these are what moved\): .*no-static-element-interactions 1/)
      assert.deepEqual(frozen(scratch), { [FROM_REL]: 4 }, 'the dry run wrote the baseline')

      // --update writes the re-keyed baseline, and nothing else changes.
      const written = runRatchet(
        ...files(FROM_REL, TO_REL),
        '--baseline',
        scratch,
        ...move(FROM_REL, TO_REL, 2),
        '--update'
      )
      assert.equal(written.status, 0, `${written.stdout}${written.stderr}`)
      assert.deepEqual(frozen(scratch), { [FROM_REL]: 2, [TO_REL]: 2 })

      const green = runRatchet(...files(FROM_REL, TO_REL), '--baseline', scratch)
      assert.equal(green.status, 0, `${green.stdout}${green.stderr}`)
    } finally {
      for (const probe of PROBES) rmSync(probe, { force: true })
    }
  })

  it('refuses a move the tree does not show, and writes nothing', () => {
    const scratch = join(sandbox, 'refusals.json')
    // A third key the probes never write, so "the frozen total" is not just the
    // one file under test.
    const before = { [FROM_REL]: 4, 'src/gone.vue': 3 }
    const cases = [
      [
        'the count is one short of what the target measures',
        move(FROM_REL, TO_REL, 1),
        /measures 2 violation\(s\) in .*__a11ySplitHalf\.vue, but the move would freeze 1/,
      ],
      [
        'the count is one more than what left the source',
        move(FROM_REL, TO_REL, 3),
        /still shows 2 violation\(s\) in .*__a11ySplitWhole\.vue/,
      ],
      // `before` also freezes three in a file no probe wrote, so the total is 7.
      ['more than the source ever froze', move(FROM_REL, TO_REL, 5), /frozen total would rise from 7 to 8/],
      // The whole allowance of four moves, so the source key is dropped — while
      // the tree still measures two there. Takes the branch where the source
      // key is gone, which is where the check used to stop firing.
      [
        'the whole allowance moves but the source still measures',
        move(FROM_REL, TO_REL, 4),
        /still shows 2 violation\(s\) in .*__a11ySplitWhole\.vue, more than the 0 the move/,
      ],
      [
        'a source the baseline never froze',
        move('src/components/__a11ySplitElsewhere.vue', TO_REL, 2),
        /freezes 0 violation\(s\) in/,
      ],
      ['a count that is not a count', move(FROM_REL, TO_REL, 0), /positive whole number/],
      // The flag is read as text and must be a plain decimal, so forms a bare
      // Number() would accept are refused at the flag, before eslint runs.
      ['a count written as something Number() would take', move(FROM_REL, TO_REL, '0x2'), /positive whole number/],
      ['a target outside the tree', move(FROM_REL, 'src/components/Half.ts', 2), /not a baseline key/],
      ['--freeze-new beside a move', [...move(FROM_REL, TO_REL, 2), '--freeze-new'], /--freeze-new freezes every file/],
      ['one flag of the three', ['--move-from', FROM_REL], /go together, once each/],
    ]
    try {
      splitInTree()
      for (const [why, args, match] of cases) {
        writeFileSync(scratch, JSON.stringify({ files: before }))
        const run = runRatchet(...files(FROM_REL, TO_REL), '--baseline', scratch, '--update', ...args)
        assert.equal(run.status, 1, `${why}: ${run.stdout}${run.stderr}`)
        assert.match(`${run.stdout}${run.stderr}`, match, why)
        assert.deepEqual(frozen(scratch), before, `${why}: the baseline changed anyway`)
      }
    } finally {
      for (const probe of PROBES) rmSync(probe, { force: true })
    }
  })

  it('names the baseline it refused to write, not the default one', () => {
    // The refusal below names the file it left alone. With --baseline pointing
    // elsewhere, the default name would be a lie about which file it touched.
    const scratch = join(sandbox, 'named-elsewhere.json')
    try {
      splitInTree()
      writeFileSync(scratch, JSON.stringify({ files: { [FROM_REL]: 4 } }))
      const run = runRatchet(
        ...files(FROM_REL, TO_REL),
        '--baseline',
        scratch,
        '--update',
        ...move(FROM_REL, TO_REL, 1)
      )
      assert.equal(run.status, 1, `${run.stdout}${run.stderr}`)
      assert.match(run.stderr, /wrote nothing; .*named-elsewhere\.json is unchanged/)
      assert.deepEqual(frozen(scratch), { [FROM_REL]: 4 })
    } finally {
      for (const probe of PROBES) rmSync(probe, { force: true })
    }
  })

  it('does not excuse a violation the split did not move', () => {
    const scratch = join(sandbox, 'stray.json')
    try {
      splitInTree()
      // A third file picks up a clickable <div> of its own while the split happens.
      writeFileSync(resolve(ROOT, STRAY_REL), withDivs(1))
      writeFileSync(scratch, JSON.stringify({ files: { [FROM_REL]: 4 } }))

      const dry = runRatchet(...files(FROM_REL, TO_REL, STRAY_REL), '--baseline', scratch, ...move(FROM_REL, TO_REL, 2))
      assert.equal(dry.status, 1, `${dry.stdout}${dry.stderr}`)
      assert.match(dry.stdout, /__a11ySplitStray\.vue: 0 -> 2/)
      // And the move was read, not ignored: ignoring the flags also exits 1 here
      // (on the stray alone), so without this line the test would stay green
      // with --move-* wired to nothing.
      assert.match(dry.stdout, /^moved 2 violation\(s\): .*__a11ySplitWhole\.vue -> .*__a11ySplitHalf\.vue/m)

      const written = runRatchet(
        ...files(FROM_REL, TO_REL, STRAY_REL),
        '--baseline',
        scratch,
        ...move(FROM_REL, TO_REL, 2),
        '--update'
      )
      assert.equal(written.status, 1, `${written.stdout}${written.stderr}`)
      assert.deepEqual(frozen(scratch), { [FROM_REL]: 4 }, 'the move laundered the stray violation')
    } finally {
      for (const probe of PROBES) rmSync(probe, { force: true })
    }
  })
})
