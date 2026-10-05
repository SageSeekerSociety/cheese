import type { ProjectMemberRow } from '@/cx_types'

import { computed, ref } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { memberName } from '@/lib/agentNames'
import { useWorkspaceStore } from '@/stores/workspace'

/** 看板卡上「谁在做」那一小块用到的名册信息。
 *
 *  「谁在做」是一个人，不是一个 handle。名册里有昵称和他自己挑的头像，卡上就该是那
 *  两样——`n1ctheboy` 这种串认得出来的只有他本人。真头像加载失败过的那几个退回彩色
 *  首字母，不留破图。从 RunningWorkView 里分出来，因为那一页已经贴着文件行数上限。
 */
export function useBoardMembers() {
  const store = useWorkspaceStore()
  const memberByHandle = computed(() => new Map((store.members as ProjectMemberRow[]).map((m) => [m.user_handle, m])))
  const avatarBroken = ref<Set<string>>(new Set())

  /** 名册上的昵称；名册里没有这个 handle 就把 handle 原样显示出来——退回空白等于把
   *  「这条活有主」也一起抹掉。 */
  function ownerName(handle?: string | null): string {
    if (!handle) return ''
    return memberName(memberByHandle.value.get(handle)) || handle
  }

  function avatarSrc(handle?: string | null): string | null {
    if (!handle || avatarBroken.value.has(handle)) return null
    const id = memberByHandle.value.get(handle)?.avatar_id
    // 名册上没这个人、或这行没有头像时返回 null：宁可留一个按 handle 哈希、认得出
    // 是谁的色块，也不要 getAvatarUrl(undefined) 给所有没挑过头像的人配同一张脸。
    return id == null ? null : getAvatarUrl(id)
  }

  function onAvatarError(handle?: string | null): void {
    if (!handle || avatarBroken.value.has(handle)) return
    avatarBroken.value = new Set(avatarBroken.value).add(handle)
  }

  return { ownerName, avatarSrc, onAvatarError }
}
