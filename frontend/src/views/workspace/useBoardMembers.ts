import type { ProjectMemberRow } from '@/cx_types'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { memberName } from '@/lib/agentNames'
import { useWorkspaceStore } from '@/stores/workspace'

/** 看板卡上「谁在做」那一小块用到的名册信息。
 *
 *  「谁在做」是一个人，不是一个 handle。名册里有昵称和他自己挑的头像，卡上就该是那
 *  两样——`n1ctheboy` 这种串认得出来的只有他本人。从 RunningWorkView 里分出来，因为那
 *  一页已经贴着文件行数上限。
 */
export function useBoardMembers() {
  const store = useWorkspaceStore()
  const memberByHandle = computed(() => new Map((store.members as ProjectMemberRow[]).map((m) => [m.user_handle, m])))

  /** 名册上的昵称；名册里没有这个 handle 就把 handle 原样显示出来——退回空白等于把
   *  「这条活有主」也一起抹掉。 */
  function ownerName(handle?: string | null): string {
    if (!handle) return ''
    return memberName(memberByHandle.value.get(handle)) || handle
  }

  /** 名册上这个人挑过的头像 URL；名册里没他、或他没挑过头像（`avatar_id` 为 null）
   *  就是 null：宁可留一个按 handle 哈希、认得出是谁的色块，也不要给所有人配同一张脸。
   *
   *  「取不到就退回彩色首字母」不在这里做：失败只由 `utils/avatarFailures` 记一次、
   *  首字母只由 `UserAvatar` 画（契约 §3.14）。这里原来自己拿一个 `Set<handle>` 记破图，
   *  和那边记的是同一个事实却分成两份——同一个人在这块板失败过，在别处还会再发一次
   *  注定 404 的请求。删掉那份本地记忆，交给 `UserAvatar`。 */
  function avatarSrc(handle?: string | null): string | null {
    if (!handle) return null
    const id = memberByHandle.value.get(handle)?.avatar_id
    return id == null ? null : getAvatarUrl(id)
  }

  return { ownerName, avatarSrc }
}
