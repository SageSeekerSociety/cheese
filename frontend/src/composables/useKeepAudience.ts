// 保存到资料库之前要说清的两个数：这段对话现在有几个人看得到，保存后项目里有几个人看得到
// （#3828）。AI 队友都不算。
import { computed, getCurrentInstance } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { queryClient } from '@/query/client'
import { roomMembersQuery } from '@/query/room'
import { useWorkspaceStore } from '@/stores/workspace'

export function useKeepAudience(topicId: () => string | null, asking: () => boolean) {
  const app = getCurrentInstance()?.appContext.config.globalProperties
  const workspace = app?.$pinia ? useWorkspaceStore() : null
  const roster = useQuery(
    computed(() => ({ ...roomMembersQuery(topicId() ?? ''), enabled: !!topicId() && asking() })),
    queryClient
  )
  const people = computed(() => (roster.data.value?.data ?? []).filter((m) => !m.agent).length)
  const projectPeople = computed(() => (workspace?.members ?? []).filter((m) => !m.agent).length)
  return { people, projectPeople }
}
