// 命令面板里的「话题」：当前项目的房间。`#` 只看这一类。
import type { PaletteItem, PaletteSource, SourceContext } from '@/commands/palette/sources'
import type { Topic } from '@/cx_types'

import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { useWorkspaceStore } from '@/stores/workspace'

function itemOf(topic: Topic, projectId: string): PaletteItem {
  const archived = topic.status === 'archived'
  return {
    id: `topic:${topic.id}`,
    title: topicTitle(topic),
    icon: archived ? 'mdi-archive-outline' : 'mdi-pound',
    badge: topic.awaits_me
      ? { text: t('navigation.palette.awaiting'), tone: 'warn' }
      : archived
        ? { text: t('navigation.palette.archived') }
        : undefined,
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
