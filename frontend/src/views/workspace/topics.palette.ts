// 命令面板里的「话题」：当前项目的房间。`#` 只看这一类。
import type { PaletteItem, PaletteSource, SourceContext } from '@/commands/palette/sources'
import type { Topic } from '@/cx_types'

import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { useWorkspaceStore } from '@/stores/workspace'

// 行尾写这个话题此刻走到哪：和看板同一个词，后端算好的，这里不推。只有要人动手的
// 那一列着暖色，已完成的着绿色。
function badgeOf(topic: Topic): PaletteItem['badge'] {
  const shown = topic.presentation
  if (shown)
    return {
      text: shown.display_status,
      tone: shown.column === 'needs_you' ? 'warn' : shown.column === 'done' ? 'ok' : undefined,
    }
  if (topic.awaits_me) return { text: t('navigation.palette.awaiting'), tone: 'warn' }
  if (topic.status === 'archived') return { text: t('navigation.palette.archived') }
  return undefined
}

function itemOf(topic: Topic, projectId: string): PaletteItem {
  const archived = topic.status === 'archived'
  return {
    id: `topic:${topic.id}`,
    title: topicTitle(topic),
    icon: archived ? 'mdi-archive-outline' : 'mdi-pound',
    badge: badgeOf(topic),
    awaiting: !!topic.awaits_me,
    to: { name: 'workspace-topic', params: { projectId, topicId: topic.id } },
  }
}

function topicsOf(ctx: SourceContext): Topic[] {
  const store = useWorkspaceStore()
  // 侧栏那张表只装着当前项目的话题；表还是别的项目的时候宁可不给。
  return ctx.projectId && store.projectId === ctx.projectId ? store.topics : []
}

const source: PaletteSource = {
  id: 'topics',
  label: 'navigation.palette.topics',
  order: 10,
  prefix: '#',
  items: (ctx) => topicsOf(ctx).map((topic) => itemOf(topic, ctx.projectId!)),
  fromRoute(route, ctx) {
    if (route.name !== 'workspace-topic' || !ctx.projectId) return null
    const topic = topicsOf(ctx).find((row) => row.id === route.params.topicId)
    return topic ? itemOf(topic, ctx.projectId) : null
  },
}

export default source
