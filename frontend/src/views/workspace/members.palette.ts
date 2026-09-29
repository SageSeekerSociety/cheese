// 命令面板里的「成员」：当前项目的名册，人和 AI 队友都在。`@` 只看这一类。
import type { PaletteSource } from '@/commands/palette/sources'

import { useWorkspaceStore } from '@/stores/workspace'

const source: PaletteSource = {
  id: 'members',
  label: 'navigation.palette.members',
  order: 30,
  prefix: '@',
  items(ctx) {
    const store = useWorkspaceStore()
    if (!ctx.projectId || store.projectId !== ctx.projectId) return []
    return store.members.map((member) => ({
      id: `member:${member.user_handle}`,
      title: member.name || member.user_handle,
      subtitle: `@${member.user_handle}`,
      icon: member.agent ? 'mdi-robot-outline' : 'mdi-account-outline',
      keywords: [member.user_handle],
      to: { name: 'member', params: { projectId: ctx.projectId!, handle: member.user_handle } },
    }))
  },
}

export default source
