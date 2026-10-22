/**
 * 项目总览上的两件：最上面那块「现状」，和项目目标还空着时让 AI 队友起草的那一小块。
 *
 * 单独一份，和 `catalogDoc.ts` 同一个理由：`catalog.ts` 已经顶到一千行的上限。
 */
import type { SituationPart } from '@/api/projectProgress'
import type { CatalogEntry } from './catalog'

import { AGENT_NAME } from './catalogFixtures'

import OverviewDraftForm from '@/components/overview/OverviewDraftForm.vue'
import SituationCard from '@/components/overview/SituationCard.vue'

const SITUATION: SituationPart[] = [
  {
    name: '活动发布与审批',
    text: '支持发布活动、审批和生成招募文案',
    status: 'usable',
    sources: [
      { kind: 'delivery', card_id: 'c1', task_id: 't1', room_id: 'r1', title: '活动发布与审批流程', quote: '' },
      { kind: 'delivery', card_id: 'c2', task_id: 't2', room_id: 'r1', title: '招募文案生成', quote: '' },
    ],
  },
  {
    name: '内测与上线',
    text: '数据迁移已接入，正在接统一身份认证',
    status: 'underway',
    sources: [
      { kind: 'delivery', card_id: 'c3', task_id: 't3', room_id: 'r1', title: '接入数据迁移', quote: '' },
      { kind: 'task', card_id: null, task_id: 't4', room_id: 'r1', title: '接入统一身份认证', quote: '' },
    ],
  },
  {
    name: '数据统计',
    text: '志愿时长统计和导出',
    status: 'not_started',
    sources: [{ kind: 'goal', card_id: null, task_id: null, room_id: null, title: '', quote: '志愿时长统计与导出' }],
  },
]

export const OVERVIEW_ENTRIES: CatalogEntry[] = [
  {
    id: 'overview-situation-card',
    title: 'SituationCard',
    about: '项目总览最上面的「现状」：项目分成的几块，每块的状态由记录定，下面一行是点得开的出处。',
    file: 'src/components/overview/SituationCard.vue',
    component: SituationCard,
    needs: ['vuetify', 'i18n'],
    args: { agentName: AGENT_NAME, agentHandle: 'cheese' },
    states: [
      {
        name: '写成过',
        note: '右上角写着谁、多久以前整理的；状态标签和文档里同一个样子，出处点开去那件任务或项目目标。',
        props: { parts: SITUATION, writtenAt: new Date(Date.now() - 2 * 3600_000).toISOString(), writing: false },
        expect: '正在接统一身份认证',
      },
      {
        name: '正在重写',
        note: '上一版照旧显示，右上角说正在整理。',
        props: { parts: SITUATION, writtenAt: null, writing: true },
        expect: '正在整理',
      },
    ],
  },
  {
    id: 'overview-draft-form',
    title: 'OverviewDraftForm',
    about: '项目目标还空着时，让 AI 队友起草之前可以补充几句、加几份材料；两样都可以不给。',
    file: 'src/components/overview/OverviewDraftForm.vue',
    component: OverviewDraftForm,
    needs: ['vuetify', 'i18n'],
    args: { agentName: AGENT_NAME, busy: false, error: null },
    states: [
      {
        name: '刚展开',
        note: '补充说明是选填；材料会存进资料库。',
        props: {},
        expect: '开始起草',
      },
      {
        name: '材料没传上去',
        note: '起草没开始，说清为什么，补充的话还在。',
        props: { error: '上传失败' },
        expect: '上传失败',
      },
    ],
  },
]
