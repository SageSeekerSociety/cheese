/// <reference types="node" />
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

// Each of these modules had no importer and no barrel re-export anywhere in the
// tree, so it was deleted rather than left for the next reader to guess whether
// something calls it. Restoring one brings back dead code with no caller; this
// test is what notices.
const HERE = dirname(fileURLToPath(import.meta.url))

const RETIRED = ['composables/useWindowSize.ts', 'utils/dateTime.ts', 'events/core/plugin.ts', 'utils/format.ts']

describe('retired modules', () => {
  it.each(RETIRED)('%s stays deleted', (path) => {
    expect(existsSync(join(HERE, path))).toBe(false)
  })
})
