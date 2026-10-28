// 房间文件的版本历史：存过的每一版、取某一版的字节、恢复到某一版。
//
// 从 roomFiles.ts 拆出来：那个文件管附件与预览的字节，这里管同一份文件随时间存下的那几版。
import { t } from '../i18n'

import { authHeaders, BASE, ensureFreshToken, request } from './http'

/** 房间文件保存过的一版。`source` 说字节从哪条路进来：编辑器、芝士、恢复、上传。 */
export interface RoomFileRevision {
  id: string
  path: string
  seq: number
  version: string
  size: number
  author: string
  author_kind: 'human' | 'agent' | 'unknown'
  source: 'baseline' | 'upload' | 'template' | 'ai' | 'editor' | 'restore' | 'scheduled'
  note: string | null
  /** 存下这一版的那次编辑会话；和自己打开时的那个相同，就是自己刚存的。 */
  editor_key: string | null
  created_at: string
}

export function roomFileRevisions(
  topicId: string,
  path: string
): Promise<{ data: RoomFileRevision[]; total: number; path: string; version: string | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/revisions?path=${encodeURIComponent(path)}`)
}

export function restoreRoomFileRevision(topicId: string, revisionId: string): Promise<RoomFileRevision> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revisionId)}/restore`, {
    method: 'POST',
  })
}

/** 某一版的字节（卡片拿它和现在那份逐格比）。 */
export async function roomFileRevisionBytes(topicId: string, revisionId: string): Promise<ArrayBuffer> {
  // 裸 fetch 不经过 `request()`：token 快过期时得先自己换一次，否则页面放了一阵再滚到
  // 一张表格卡，这一下会 401（同 `rawImageUrl` / `previewFileBytes` 的理由）。
  await ensureFreshToken()
  const res = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revisionId)}/raw`,
    { headers: authHeaders() }
  )
  if (!res.ok) throw new Error(t('global.request.fileReadFailed', { status: res.status }))
  return res.arrayBuffer()
}

export async function downloadRoomFileRevision(topicId: string, revision: RoomFileRevision): Promise<void> {
  // 同上：裸 fetch，先自己换一次 token。版本列表是打开面板时取的，点下载之前可能已经
  // 放了很久。
  await ensureFreshToken()
  const res = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revision.id)}/raw`,
    { headers: authHeaders() }
  )
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  const blob = await res.blob()
  const leaf = revision.path.split('/').pop() ?? 'file'
  const dot = leaf.lastIndexOf('.')
  const [base, ext] = dot > 0 ? [leaf.slice(0, dot), leaf.slice(dot)] : [leaf, '']
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = t('global.request.revisionFileName', { name: base, seq: revision.seq, ext })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
