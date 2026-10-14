import type { TopicMemberRow } from '@/cx_types'

import { computed, type Ref } from 'vue'

import { agentNames, memberName } from '@/lib/agentNames'
import { memberFacesOf } from '@/lib/memberFaces'
import { useWorkspaceStore } from '@/stores/workspace'

// 名册上的两种身份，都按 handle 索引：叫得出名字的那一份，和画得出脸的那一份。
// 放在一处是因为它们读的是同一份名册（房间 + 项目），分开就会各读一次、也各自漏一次。
//
// 名字按房间名册来，项目名册再补上已离席的队友（`agentNames`）；名册拉不到就退回 handle
// ——比空白好，也比报错好。脸（头像 id、是不是队友）只从项目名册来，不拿名字反查。
export function useMemberIdentity(roomMembers: Ref<TopicMemberRow[]>) {
  const store = useWorkspaceStore()
  const memberNames = computed<Record<string, string>>(() => ({
    ...Object.fromEntries(roomMembers.value.map((m) => [m.member_handle, memberName(m) || m.member_handle])),
    ...Object.fromEntries(agentNames(roomMembers.value, store.members)),
  }))
  const memberFaces = computed(() => memberFacesOf(store.members))
  return { memberNames, memberFaces }
}
