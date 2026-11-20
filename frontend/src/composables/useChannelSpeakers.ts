// 频道主线只让部分角色发言时（#4795），我在这里能不能说话、不能时输入框那一行写什么。
// 所有者总能说；支线留着时，人人能在支线和任务里回。后端发消息时按同一条规则再判一次。
import type { Topic } from '@/cx_types'
import type { ChannelSpeakers } from '@/types/channels'

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { t } from '@/i18n'
import { joined } from '@/lib/noticeText'
import { roleName } from '@/lib/projectPermissions'
import { queryClient } from '@/query/client'
import { projectQuery, projectRolesQuery } from '@/query/project'

export function useChannelSpeakers(room: () => Topic | null) {
  const limited = computed(() => (room() as (Topic & ChannelSpeakers) | null)?.speaker_roles ?? null)
  const threadsOpen = computed(() => (room() as (Topic & ChannelSpeakers) | null)?.thread_replies_open !== false)
  const projectId = computed(() => room()?.project_id ?? '')
  const projectRead = useQuery(
    computed(() => ({ ...projectQuery(projectId.value), enabled: !!projectId.value && !!limited.value })),
    queryClient
  )
  const rolesRead = useQuery(
    computed(() => ({ ...projectRolesQuery(projectId.value), enabled: !!projectId.value && !!limited.value })),
    queryClient
  )
  const mine = computed(() => {
    const project = projectRead.data.value as { my_role?: string | null; my_role_key?: string | null } | undefined
    return { seat: project?.my_role ?? null, key: project?.my_role_key ?? null }
  })
  const kept = computed(() => {
    const keys = limited.value
    if (!keys || !projectRead.data.value) return false
    return mine.value.seat !== 'owner' && !keys.includes(mine.value.key ?? '')
  })
  const names = computed(() =>
    joined((rolesRead.data.value ?? []).filter((r) => limited.value?.includes(r.key)).map((r) => roleName(r)))
  )

  /** 输入框那一行：主线（`thread` 为假）或支线、任务里。能说话时为 null。 */
  function notice(thread: boolean): string | null {
    if (!kept.value) return null
    if (thread) return threadsOpen.value ? null : t('work.channel.speakersOnly', { roles: names.value })
    return threadsOpen.value
      ? t('work.channel.speakersOnlyMain', { roles: names.value })
      : t('work.channel.speakersOnly', { roles: names.value })
  }

  return { notice }
}
