// 后端把卡在途中的阶段信息/故障写在卡的 `note` 上，并随卡下发一个 `note_level`。
//
// 这里以前是自己按 emoji 开头猜的：一份写死的 `['⚠️','❌','🚫','✋']`。它漏过东西
// —— `🌿 本地分支与 PR 分支已分叉` 是后来加的，卡在那个状态上是「停住了、等人动
// 手」，却因为不在这份列表里而和「还在等检查」渲染成同一个颜色。分级现在住在拥有
// 那些前缀的地方 (backend domain/review/notes.py)，这里只把码画成颜色。
import type { AcceptCard } from '../cx_types'

export function noteTone(note: Pick<AcceptCard, 'note' | 'note_level'>): 'error' | 'info' | null {
  if (!note.note?.trim()) return null
  return note.note_level === 'error' ? 'error' : 'info'
}
