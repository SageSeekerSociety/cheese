// 两段脚本逐行比：哪几行是新加的、哪几行删掉了，其余照旧。
import { lcsOps } from './lcs'

export interface DiffLine {
  kind: 'same' | 'added' | 'removed'
  text: string
}

const KINDS = { same: 'same', add: 'added', del: 'removed' } as const

export function lineDiff(before: string, after: string): DiffLine[] {
  return lcsOps(before.split('\n'), after.split('\n')).map(({ kind, item }) => ({ kind: KINDS[kind], text: item }))
}
