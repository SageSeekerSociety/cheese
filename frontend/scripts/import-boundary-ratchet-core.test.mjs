// The failure report, and the gate refusing a planted violation.
//
// The counting, the rule driven through the real eslint config and the ratchet
// arithmetic are covered by import-boundary-ratchet.test.mjs; what is asserted
// here is the half a fix depends on. The reader of a boundary failure is
// whoever has to land the change — an agent as often as a person — and the
// report is the whole of what they get. A report that only counted ("Chat.vue:
// 0 -> 1") leaves them to grep for what a component is allowed to do, and the
// cheapest thing they can do alone is add a line to import-boundary-baseline
// .json. So the report has to name the fix and the one-file command that proves
// it, and these tests hold it to that.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { BOUNDARY_RULE_ID, compare, formatBoundaryReport } from './import-boundary-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const RATCHET = resolve(HERE, 'import-boundary-ratchet.mjs')
const CONFIG = resolve(ROOT, 'eslint.boundary.config.mjs')
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/eslint')

/** A regressed file's own diagnostic, the shape parseEslintJson returns. */
const violation = (file, line) => ({
  file,
  line,
  column: 1,
  rule: BOUNDARY_RULE_ID,
  message: 'A component under src/components must not call the API layer.',
})

describe('the boundary failure report', () => {
  // The four principles live in .claude/rules/architecture.md, and the fix is a
  // landing point per half of the rule: fetching in a composable or store the
  // view calls, the route behind NavTarget / useNavigation. A report that ends
  // at "move it to the view" sends the reader back to the chapter to find out
  // which directory.
  it('names where the fix lands, for both halves of the rule', () => {
    const file = 'src/components/panels/Chat.vue'
    const text = formatBoundaryReport(compare({}, { [file]: 1 }), [violation(file, 7)])

    assert.match(text, /@\/api, @\/services\/\*/)
    assert.match(text, /src\/composables\//)
    assert.match(text, /src\/stores\//)
    assert.match(text, /src\/lib\/navTarget\.ts/)
    assert.match(text, /\.claude\/rules\/architecture\.md/)
  })

  // The narrow run is the point: the whole-tree check is what CI already ran
  // while the reader waits, and the file is named in the report so the command
  // is copy-pasteable as printed.
  it('prints the one-file check for each regressed file', () => {
    const text = formatBoundaryReport(compare({}, { 'src/components/A.vue': 1, 'src/components/B.vue': 2 }))

    assert.match(text, /pnpm exec eslint --config eslint\.boundary\.config\.mjs src\/components\/A\.vue/)
    assert.match(text, /pnpm exec eslint --config eslint\.boundary\.config\.mjs src\/components\/B\.vue/)
    assert.match(text, /import-boundary-ratchet\.mjs --files src\/components\/A\.vue/)
    assert.match(text, /import-boundary-ratchet\.mjs --files src\/components\/B\.vue/)
  })

  it('says nothing about fixing when nothing regressed', () => {
    const text = formatBoundaryReport(compare({ 'src/components/A.vue': 2 }, { 'src/components/A.vue': 1 }))

    assert.doesNotMatch(text, /Sink the dependency/)
    assert.doesNotMatch(text, /pnpm exec eslint/)
  })

  // A baseline key is a file path and the count is per file, so moving or
  // splitting a component renames its key: the old one drops to 0 and the new
  // path starts at 0. Reported as a plain "new violation", that sends the
  // reader to fix a component that imports nothing new — and the stale key
  // stays in the baseline. The run has both halves, so the report can say so.
  it('explains a moved or split component instead of calling it a new import', () => {
    const result = compare({ 'src/components/chat/Chat.vue': 1 }, { 'src/components/panels/Chat.vue': 1 })
    const text = formatBoundaryReport(result)

    assert.match(text, /src\/components\/chat\/Chat\.vue: 1 -> 0/)
    assert.match(text, /moving or splitting a component changes its key/)
    assert.match(text, /pnpm run lint:boundary:update/)
    assert.match(text, /same count under the new key/)
  })

  // The other side of that: a genuinely new import against a baseline that
  // never held the path is the common case, and it must not be buried under an
  // explanation about file paths that does not apply to it.
  it('does not invent a move when a new file is the whole story', () => {
    const text = formatBoundaryReport(compare({}, { 'src/components/A.vue': 1 }))

    assert.doesNotMatch(text, /moving or splitting a component changes its key/)
    assert.match(text, /src\/components\/A\.vue: 0 -> 1/)
  })
})

// ---------------------------------------------------------------------------
// The gate, on a real file in the real tree. A boundary nobody has watched
// fail is not a boundary, and with today's empty baseline a check that quietly
// stopped scanning would still exit 0 on a clean tree.
// ---------------------------------------------------------------------------

describe('the gate on a planted violation', () => {
  const PROBE = resolve(ROOT, 'src/components/__boundaryCoreProbe.vue')
  const RELATIVE = 'src/components/__boundaryCoreProbe.vue'
  const sandbox = mkdtempSync(join(tmpdir(), 'boundary-core-'))
  const scratchBaseline = join(sandbox, 'baseline.json')

  after(() => {
    rmSync(PROBE, { force: true })
    rmSync(sandbox, { recursive: true, force: true })
  })

  const plant = () =>
    writeFileSync(
      PROBE,
      `<script setup lang="ts">\nimport { api } from '@/api'\nconst x = api\n</script>\n<template><div /></template>\n`
    )

  it('refuses it, and prints the fix and the narrow command on the way out', () => {
    try {
      plant()
      const run = spawnSync(process.execPath, [RATCHET, '--files', RELATIVE, '--baseline', scratchBaseline], {
        cwd: ROOT,
        encoding: 'utf8',
      })

      assert.equal(run.status, 1, `${run.stdout}${run.stderr}`)
      assert.match(run.stdout, /__boundaryCoreProbe\.vue: 0 -> 1/)
      assert.match(run.stdout, /Sink the dependency/)
      // The path in the command is the one this run used, not an absolute one:
      // the reader runs it from frontend/ exactly as printed.
      assert.match(run.stdout, /--files src\/components\/__boundaryCoreProbe\.vue/)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  // The command the report tells the reader to run has to be a real one, or the
  // shortest path it points at is a dead end and the reader falls back to the
  // baseline. So this reads the line out of the report instead of rebuilding
  // it, and runs exactly what it prints — through the binary resolved here, but
  // with the printed arguments — so a config name that is not on disk, a
  // dropped `eslint`, or a file argument that is not the one scanned fails here.
  it('runs the eslint command the report prints, and it reports the violation', () => {
    try {
      plant()
      const printed = formatBoundaryReport(compare({}, { [RELATIVE]: 1 }))
        .split('\n')
        .filter((line) => line.trim().startsWith('pnpm exec eslint'))
      assert.equal(printed.length, 1, 'one regressed file, one printed eslint command')

      const tokens = printed[0].trim().split(/\s+/)
      assert.deepEqual(tokens.slice(0, 3), ['pnpm', 'exec', 'eslint'])
      const argv = tokens.slice(3)
      assert.equal(argv[0], '--config')
      assert.equal(resolve(ROOT, argv[1]), CONFIG, `the report printed a different config: ${argv[1]}`)
      assert.equal(existsSync(CONFIG), true, 'the boundary eslint config is not on disk')
      assert.equal(argv[argv.length - 1], RELATIVE)
      assert.equal(existsSync(LOCAL_BIN), true, 'eslint is not installed under node_modules/.bin')

      const run = spawnSync(LOCAL_BIN, argv, {
        cwd: ROOT,
        encoding: 'utf8',
        shell: process.platform === 'win32',
      })

      assert.match(`${run.stdout}${run.stderr}`, new RegExp(BOUNDARY_RULE_ID.replace('/', '\\/')))
    } finally {
      rmSync(PROBE, { force: true })
    }
  })
})
