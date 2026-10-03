// 命令面板里的「成员」：当前项目的名册，人和 AI 队友都在。`@` 只看这一类。
import type { PaletteSource } from '@/commands/palette/sources'

import { copyText } from '@/commands/copy'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { agentDmKey } from '@/lib/dm'
import { myHandle } from '@/me'
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
      title: memberName(member) || member.user_handle,
      subtitle: `@${member.user_handle}`,
      icon: member.agent ? 'mdi-robot-outline' : 'mdi-account-outline',
      keywords: [member.user_handle],
      to: { name: 'member', params: { projectId: ctx.projectId!, handle: member.user_handle } },
      actions: () => [
        // 自己不给自己发私信。
        ...(member.user_handle === myHandle()
          ? []
          : [
              {
                id: 'member.dm',
                title: t('navigation.palette.dm'),
                icon: 'mdi-message-outline',
                to: {
                  name: 'workspace-dm',
                  params: {
                    projectId: ctx.projectId!,
                    peer: member.agent ? agentDmKey(member.user_handle) : member.user_handle,
                  },
                },
              },
            ]),
        {
          id: 'member.copyHandle',
          title: t('navigation.palette.copyHandle', { handle: member.user_handle }),
          icon: 'mdi-at',
          run: () => void copyText(`@${member.user_handle}`, t('navigation.copy.done')),
        },
      ],
    }))
  },
}

export default source
