// 文档里点得到、认得出的人：项目名册上的人，加上项目的 AI 队友。@ 菜单的候选和
// 正文里 `<@账号>` 标签上的名字都从这里来。
import type { ProjectMemberRow } from '../cx_types'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { computed } from 'vue'

import { memberName } from '../lib/agentNames'
import { isExternalMember } from '../lib/externalMembers'

export function useDocPeople(source: {
  members: () => ProjectMemberRow[]
  agentHandle: () => string | null | undefined
  agentName: () => string
}) {
  const people = computed<MentionPoolEntry[]>(() => {
    const humans = source
      .members()
      .filter((m) => !m.agent)
      .map((m) => ({
        handle: m.user_handle,
        label: memberName(m) || m.user_handle,
        agent: false,
        external: isExternalMember(m),
      }))
    const handle = source.agentHandle()
    return handle ? [...humans, { handle, label: source.agentName(), agent: true }] : humans
  })

  const names = computed<Record<string, string>>(() => Object.fromEntries(people.value.map((p) => [p.handle, p.label])))

  return { people, names }
}
