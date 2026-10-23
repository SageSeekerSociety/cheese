/**
 * 项目侧栏那几件：一行话题、它右边那几位成员、⋯ 里那几项、项目本体那一行、
 * 项目名下那两页，以及壳换词之后的词汇表。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册。条目在 `../catalogRail.ts`。
 */
import type { MenuCommand } from '@/commands'
import type { Topic } from '@/cx_types'
import type { RailMemberMark } from '@/lib/memberActivity'
import type { VisibleRow } from '@/lib/topicTree'

import { AGENT_NAME } from './common'

// 侧栏拆成几个只管画的组件之后，这几件都能单独立着。它们吃的数据不少（一行的
// `VisibleRow` 有十几个字段），所以这里造的是**产品里真会出现的那几格**：在跑的、
// 等你拍板的、收起来的、出了故障的、在等合并的——而不是「随便来一条」。

/** 一行话题的完整形状（`lib/topicTree.ts` 的 `VisibleRow`）：话题 + 缩进 + 折叠
 *  开关要的那几个数（收起来了几个、里面有几条未读、里面有没有动静）。 */
function railRow(
  topic: Partial<Topic> & { id: string; title: string },
  visible: Partial<VisibleRow<Topic>> = {}
): VisibleRow<Topic> {
  return {
    topic: {
      project_id: 'p1',
      parent_id: null,
      kind: 'topic',
      status: 'active',
      created_at: '2026-09-24T09:00:00Z',
      ...topic,
    },
    depth: 0,
    hasChildren: false,
    collapsed: false,
    hiddenCount: 0,
    hiddenUnread: 0,
    unreadTotal: 0,
    hiddenLight: null,
    hiddenStalled: false,
    ...visible,
  }
}

export const RAIL_ROWS = {
  /** 一位队友正在这个话题里干活：右边它的小头像带一颗绿点。 */
  working: railRow({
    id: 't-1',
    title: '写第 4 章的教案',
    activity: [{ member: 'cheese-a1', kind: 'working', since: 1790845200 }],
  }),
  /** 有事等你拍板：行尾一盏黄灯（未读的 @ 不点这盏灯，所以未读和它是两回事）。 */
  awaits: railRow({ id: 't-2', title: '决定这学期用哪本教材', awaits_me: true, joined: true }),
  /** 收起来的父话题：开关自己带聚合色（里面收着的那几行里有事等你），右边是聚上来的未读。 */
  collapsed: railRow(
    { id: 't-3', title: '期末复习' },
    {
      hasChildren: true,
      collapsed: true,
      hiddenCount: 12,
      hiddenUnread: 4,
      unreadTotal: 4,
      hiddenLight: { kind: 'yellow' },
    }
  ),
  /** 子话题：缩进一级，左边一条竖向引导线。 */
  sub: railRow({ id: 't-4', title: '第 3 题：为什么天空是蓝的', parent_id: 't-3' }, { depth: 1, unreadTotal: 2 }),
  /** 房间在等的一位队友：它最近一轮报错了。不靠数据变——钟走到哪儿它都红着。 */
  stalled: railRow({
    id: 't-5',
    title: '把成绩单导出成 CSV',
    waits: [{ member: 'cheese-a1', reason: 'failed', since: '2026-09-29T08:41:00Z' }],
  }),
}

/** 侧栏一行右边那几位成员（`useTopicRail.memberMarks` 在真环境里给的就是这个形状）。 */
export const RAIL_MARKS = {
  working: [
    {
      handle: 'cheese-a1',
      name: AGENT_NAME,
      avatar: null,
      agent: true,
      title: `${AGENT_NAME}正在这里工作`,
    },
    { handle: 'louis', name: '娄宇', avatar: null, agent: false, title: '娄宇正在这里工作' },
  ],
} satisfies Record<string, RailMemberMark[]>

/** 一行的 ⋯ 里那几项（`commands/topicActions.ts` 在真环境里给的就是这个形状）。 */
export const RAIL_ACTIONS: MenuCommand[] = [
  { id: 'topic.rename', title: '重命名', icon: 'mdi-pencil', run: () => {} },
  { id: 'topic.copyLink', title: '复制链接', icon: 'mdi-link-variant', run: () => {} },
  { id: 'topic.archive', title: '归档', icon: 'mdi-archive-outline', run: () => {} },
]

/** 项目本体（全局房间）：置顶那一行，也是项目名的落点。 */
export const RAIL_ROOT_TOPIC: Topic = {
  id: 't-root',
  project_id: 'p1',
  parent_id: null,
  title: '课程项目 · 项目总览',
  kind: 'root',
  status: 'active',
  created_at: '2026-09-20T08:00:00Z',
}

/** 项目名下那一行（总览、资料库，见 `lib/shell.ts` 的 `projectPageLayout`）。 */
export const RAIL_PAGES = [
  { key: 'workspace-overview', label: 'navigation.project.overview', icon: 'mdi-view-dashboard-outline' },
  { key: 'project-library', label: 'navigation.project.library', icon: 'mdi-folder-outline' },
]

/** 壳换了词之后的项目词汇表（「{project}文档」靠它渲染）。 */
export const RAIL_TERMS = { project: '项目', topic: '话题' }
