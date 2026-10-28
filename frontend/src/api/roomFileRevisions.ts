// 房间文件的草稿历史：每一次保存留下的一版，读清单、取某一版的字节、下载、恢复。
//
// 从 `./roomFiles` 拆出来：那一份管房间里的文件本身（读写、编辑器、摆出来的东西），这一份
// 管它们存过的每一版。预览顶上的版本行、两版对比、卡片上的「改了 N 格」都只读这一份。

import { t } from '../i18n'
import { announceDownload, announceDownloadFailure, downloadBytes, saveBlob } from '../lib/download'

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

/** 某一版的原始字节：卡片拿它和现在那份逐格比，预览里比两版改了什么也读它。 */
export async function roomFileRevisionBytes(topicId: string, revisionId: string): Promise<ArrayBuffer> {
  // 裸 fetch 不经过 `request()`，不会自己刷新令牌（同 `./roomFiles` 的 `rawImageUrl`、
  // `previewFileBytes`）：打开文件就自动比两版，标签页闲了一会儿之后这一份会拿着快过期的
  // 令牌发出去，卡片会静默地少掉「改了 N 格」。
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
  const leaf = revision.path.split('/').pop() ?? 'file'
  const dot = leaf.lastIndexOf('.')
  const [base, ext] = dot > 0 ? [leaf.slice(0, dot), leaf.slice(dot)] : [leaf, '']
  try {
    const blob = await downloadBytes(
      `${BASE}/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revision.id)}/raw`,
      authHeaders()
    )
    saveBlob(blob, t('global.request.revisionFileName', { name: base, seq: revision.seq, ext }))
    announceDownload()
  } catch (cause) {
    announceDownloadFailure(cause)
    throw cause
  }
}
