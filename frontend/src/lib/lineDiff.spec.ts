// 芝士给的改法逐行标出来：加的、删的、没动的，读的人一眼看到动了哪一行。
import { describe, expect, it } from 'vitest'

import { lineDiff } from './lineDiff'

describe('逐行比两段脚本', () => {
  it('只标出加上的那一行', () => {
    const diff = lineDiff('cd frontend\npnpm install', 'cd frontend\nmise use node@22\npnpm install')
    expect(diff.filter((l) => l.kind !== 'same')).toEqual([{ kind: 'added', text: 'mise use node@22' }])
  })

  it('改掉的一行是删一行、加一行', () => {
    const diff = lineDiff('npm ci', 'pnpm install')
    expect(diff).toEqual([
      { kind: 'removed', text: 'npm ci' },
      { kind: 'added', text: 'pnpm install' },
    ])
  })
})
