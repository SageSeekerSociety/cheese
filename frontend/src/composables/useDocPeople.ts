// 文档里点得到、认得出的人：项目名册上的人，加上项目的 AI 队友。@ 菜单的候选和
// 正文里 `<@账号>` 标签上的名字都从这里来。
import type { ProjectMemberRow } from '../cx_types'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { computed } from 'vue'

import { memberName } from '../lib/agentNames'
import { isExternalMember } from '../lib/externalMembers'
import { getAvatarUrl } from '../utils/materials'

/** 这一包递给画的那一层（props）：名册是调用方给的，面板不再自己去取。 */
export type DocPeopleBundle = ReturnType<typeof useDocPeople>

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
        // 名册这一行就带着他挑过的头像；没挑过是 null，交给首字母。
        avatar: m.avatar_id != null ? getAvatarUrl(m.avatar_id) : null,
      }))
    const handle = source.agentHandle()
    return handle ? [...humans, { handle, label: source.agentName(), agent: true, avatar: null }] : humans
  })

  const names = computed<Record<string, string>>(() => Object.fromEntries(people.value.map((p) => [p.handle, p.label])))
  // handle → 头像地址。文档评论串里画作者头像用（正文标签和姓名之外，头像也认人）。
  const avatars = computed<Record<string, string>>(() =>
    Object.fromEntries(people.value.flatMap((p) => (p.avatar ? [[p.handle, p.avatar] as const] : [])))
  )

  return { people, names, avatars }
}
