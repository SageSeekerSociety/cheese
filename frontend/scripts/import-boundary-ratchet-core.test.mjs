// The failure report, and the gate refusing a planted violation.
//
// The counting and the rule driven through the real eslint config are covered
// by import-boundary-ratchet.test.mjs; what is asserted here is the half a fix
// depends on. The reader of a boundary failure is whoever has to land the
// change — an agent as often as a person — and the report is the whole of what
// they get. So the report has to name the fix and the one-file command that
// proves it, and these tests hold it to that.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { BOUNDARY_RULE_ID, formatBoundaryReport } from './import-boundary-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const RATCHET = resolve(HERE, 'import-boundary-ratchet.mjs')

/** One violation, the shape parseEslintJson returns. */
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
    const text = formatBoundaryReport([violation(file, 7)])

    assert.match(text, /src\/components\/panels\/Chat\.vue:7:1 {2}boundary\/no-api-or-router/)
    assert.match(text, /@\/api,/)
    assert.match(text, /@\/services\/\*/)
    assert.match(text, /src\/composables\//)
    assert.match(text, /src\/stores\//)
    assert.match(text, /src\/lib\/navTarget\.ts/)
    assert.match(text, /\.claude\/rules\/architecture\.md/)
  })

  // The narrow run is the point: the whole-tree check is what CI already ran
  // while the reader waits, and the file is named in the report so the command
  // is copy-pasteable as printed. Once per file, however many violations it has.
  it('prints the one-file check once for each file with a violation', () => {
    const text = formatBoundaryReport([
      violation('src/components/A.vue', 1),
      violation('src/components/B.vue', 2),
      violation('src/components/B.vue', 9),
    ])

    assert.match(text, /import-boundary-ratchet\.mjs --files src\/components\/A\.vue/)
    assert.equal(text.match(/--files src\/components\/B\.vue/g)?.length, 1)
  })

  it('says nothing about fixing when there is nothing to fix', () => {
    const text = formatBoundaryReport([])

    assert.doesNotMatch(text, /Sink the dependency/)
    assert.doesNotMatch(text, /--files/)
  })
})

// ---------------------------------------------------------------------------
// The gate, on a real file in the real tree. A boundary nobody has watched
// fail is not a boundary, and a check that quietly stopped scanning would
// still exit 0 on a clean tree.
// ---------------------------------------------------------------------------

describe('the gate on a planted violation', () => {
  const PROBE = resolve(ROOT, 'src/components/__boundaryCoreProbe.vue')
  const RELATIVE = 'src/components/__boundaryCoreProbe.vue'

  after(() => rmSync(PROBE, { force: true }))

  const plant = () =>
    writeFileSync(
      PROBE,
      `<script setup lang="ts">\nimport { api } from '@/api'\nconst x = api\n</script>\n<template><div /></template>\n`
    )

  it('refuses it, and prints the fix and the narrow command on the way out', () => {
    try {
      plant()
      const run = spawnSync(process.execPath, [RATCHET, '--files', RELATIVE], { cwd: ROOT, encoding: 'utf8' })

      assert.equal(run.status, 1, `${run.stdout}${run.stderr}`)
      assert.match(run.stdout, /__boundaryCoreProbe\.vue:\d+:\d+ {2}boundary\/no-api-or-router/)
      assert.match(run.stdout, /Sink the dependency/)
      // The path in the command is the one this run used, not an absolute one:
      // the reader runs it from frontend/ exactly as printed.
      assert.match(run.stdout, /--files src\/components\/__boundaryCoreProbe\.vue/)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  // The command the report tells the reader to run has to be a real one, or the
  // shortest path it points at is a dead end. So this reads the line out of the
  // report instead of rebuilding it, and runs exactly what it prints, so a
  // script name that is not on disk or a file argument that is not the one
  // scanned fails here.
  it('runs the command the report prints, and it refuses the violation', () => {
    try {
      plant()
      const printed = formatBoundaryReport([violation(RELATIVE, 2)])
        .split('\n')
        .filter((line) => line.trim().startsWith('node scripts/'))
      assert.equal(printed.length, 1, 'one file, one printed command')

      const [, script, ...argv] = printed[0].trim().split(/\s+/)
      assert.equal(resolve(ROOT, script), RATCHET, `the report printed a different script: ${script}`)
      const run = spawnSync(process.execPath, [resolve(ROOT, script), ...argv], { cwd: ROOT, encoding: 'utf8' })

      assert.equal(run.status, 1, `${run.stdout}${run.stderr}`)
      assert.match(run.stdout, new RegExp(BOUNDARY_RULE_ID.replace('/', '\\/')))
    } finally {
      rmSync(PROBE, { force: true })
    }
  })
})
