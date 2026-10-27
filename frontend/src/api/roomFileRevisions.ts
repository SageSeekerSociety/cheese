// 房间文件的草稿历史：每一次保存留下的一版，读清单、取某一版的字节、下载、恢复。
//
// 从 `./roomFiles` 拆出来：那一份管房间里的文件本身（读写、编辑器、摆出来的东西），这一份
// 管它们存过的每一版。预览顶上的版本行、两版对比都只读这一份。

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

/** 某一版的原始字节：下载它、预览里比两版改了什么，都读这一份。 */
export async function roomFileRevisionBytes(topicId: string, revisionId: string): Promise<ArrayBuffer> {
  // 裸 fetch 不会自己刷新令牌（同 `./roomFiles` 的 `rawImageUrl`）：打开文件就自动比两版，
  // 标签页闲了一会儿之后这一份会拿着快过期的令牌发出去。
  await ensureFreshToken()
  const res = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revisionId)}/raw`,
    { headers: authHeaders() }
  )
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  return res.arrayBuffer()
}

export async function downloadRoomFileRevision(topicId: string, revision: RoomFileRevision): Promise<void> {
  const blob = new Blob([await roomFileRevisionBytes(topicId, revision.id)])
  const leaf = revision.path.split('/').pop() ?? 'file'
  const dot = leaf.lastIndexOf('.')
  const [base, ext] = dot > 0 ? [leaf.slice(0, dot), leaf.slice(dot)] : [leaf, '']
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = t('global.request.revisionFileName', { name: base, seq: revision.seq, ext })
  // 挂进文档再点：脱离文档的锚点在某些浏览器里点了不下载（同 `./roomFiles` 的 `downloadFile`）。
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
