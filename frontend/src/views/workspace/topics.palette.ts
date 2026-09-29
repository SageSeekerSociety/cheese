// 命令面板里的「话题」。`#` 只看这一类。
//
// 在当前项目里用侧栏那张表：它带着每个话题此刻的状态和能做的事。去掉范围（跨项目）
// 或者进到别的项目里搜的时候，侧栏的表里没有那些话题，改用只有名字的那一份。
import type { Router } from 'vue-router'
import type { TopicName } from '@/api'
import type { PaletteItem, PaletteSource, SourceContext } from '@/commands/palette/sources'
import type { Topic } from '@/cx_types'

import { ref } from 'vue'

import { listTopicNames } from '@/api'
import { copyLink, linkOf } from '@/commands/copy'
import { paletteAsk } from '@/commands/palette/state'
import { topicActions } from '@/commands/topicActions'
import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
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

// 在面板里重命名：输入框换成话题名，回车就改，不离开当前这一页。
function renameInPalette(topic: Topic) {
  paletteAsk.value = {
    title: t('work.room.menu.renameTitle'),
    placeholder: t('work.room.menu.topicName'),
    value: topic.title,
    submit(draft) {
      const next = normalizeTopicTitle(draft, topic.title, TOPIC_TITLE_MAX_LENGTH)
      if (next) void useWorkspaceStore().renameTopic(topic.id, next)
    },
  }
}

function itemOf(topic: Topic, projectId: string, router: Router): PaletteItem {
  const archived = topic.status === 'archived'
  return {
    id: `topic:${topic.id}`,
    title: topicTitle(topic),
    icon: archived ? 'mdi-archive-outline' : 'mdi-pound',
    badge: badgeOf(topic),
    awaiting: !!topic.awaits_me,
    to: { name: 'workspace-topic', params: { projectId, topicId: topic.id } },
    actions: () => topicActions(topic, router, { rename: () => renameInPalette(topic) }),
  }
}

function topicsOf(ctx: SourceContext): Topic[] {
  const store = useWorkspaceStore()
  // 侧栏那张表只装着当前项目的话题；表还是别的项目的时候宁可不给。
  return ctx.projectId && store.projectId === ctx.projectId ? store.topics : []
}

// 只有名字的那一份：用到时去问，过一会儿再用就再问一次，别处新建、改名的话题跟得上。
const FRESH_MS = 30_000
const names = ref<TopicName[]>([])
let askedAt = 0

function topicNames(): TopicName[] {
  if (Date.now() - askedAt > FRESH_MS) {
    askedAt = Date.now()
    listTopicNames()
      .then((rows) => (names.value = rows))
      .catch(() => (askedAt = 0))
  }
  return names.value
}

function nameItemOf(row: TopicName, router: Router, projectName: string | undefined): PaletteItem {
  const to = { name: 'workspace-topic', params: { projectId: row.project_id, topicId: row.id } }
  const archived = row.status === 'archived'
  return {
    id: `topic:${row.id}`,
    title: topicTitle(row),
    subtitle: projectName,
    icon: archived ? 'mdi-archive-outline' : 'mdi-pound',
    badge: archived ? { text: t('navigation.palette.archived') } : undefined,
    to,
    actions: () => [
      {
        id: 'topic.copyLink',
        title: t('work.room.menu.copyLink'),
        icon: 'mdi-link-variant',
        run: () => void copyLink(linkOf(router, to)),
      },
    ],
  }
}

function itemsOf(ctx: SourceContext): PaletteItem[] {
  const store = useWorkspaceStore()
  const here = topicsOf(ctx)
  if (here.length) return here.map((topic) => itemOf(topic, ctx.projectId!, ctx.router))
  // 不限项目：每一行下面写它在哪个项目里。当前项目的仍用侧栏那张表。
  const projectName = new Map(store.projects.map((project) => [project.id, project.name]))
  const rows = topicNames().filter((row) =>
    ctx.projectId ? row.project_id === ctx.projectId : row.project_id !== store.projectId
  )
  const current =
    !ctx.projectId && store.projectId
      ? store.topics.map((topic) => ({
          ...itemOf(topic, store.projectId!, ctx.router),
          subtitle: projectName.get(store.projectId!),
        }))
      : []
  return [
    ...current,
    ...rows.map((row) => nameItemOf(row, ctx.router, ctx.projectId ? undefined : projectName.get(row.project_id))),
  ]
}

const source: PaletteSource = {
  id: 'topics',
  label: 'navigation.palette.topics',
  order: 10,
  prefix: '#',
  items: itemsOf,
  fromRoute(route, ctx) {
    if (route.name !== 'workspace-topic' || !ctx.projectId) return null
    const topic = topicsOf(ctx).find((row) => row.id === route.params.topicId)
    return topic ? itemOf(topic, ctx.projectId, ctx.router) : null
  },
}

export default source
