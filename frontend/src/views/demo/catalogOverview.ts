/**
 * 项目总览上的两件：最上面那块「现状」，和项目目标还空着时让 AI 队友起草的那一小块。
 *
 * 单独一份，和 `catalogDoc.ts` 同一个理由：`catalog.ts` 已经顶到一千行的上限。
 */
import type { CatalogEntry } from './catalog'

import { AGENT_NAME } from './catalogFixtures'

import OverviewDraftForm from '@/components/overview/OverviewDraftForm.vue'
import SituationCard from '@/components/overview/SituationCard.vue'

const SITUATION = `活动发布到时长核准的流程已可用，当前重点是准备内测

- 活动发布与审批 {✓ 可用}：发布、审批和招募文案已实现
- 报名与候补 {✓ 可用}：候补按顺序自动递补
- 内测与上线 {! 进行中}：数据迁移已接入，统一身份认证仍走演示模式
- 数据统计 {✗ 未开始}：仅有导出`

export const OVERVIEW_ENTRIES: CatalogEntry[] = [
  {
    id: 'overview-situation-card',
    title: 'SituationCard',
    about: '项目总览最上面的「现状」：AI 队友读项目写下的一句判断和各块的状态。',
    file: 'src/components/overview/SituationCard.vue',
    component: SituationCard,
    needs: ['vuetify', 'i18n'],
    args: { agentName: AGENT_NAME, agentHandle: 'cheese' },
    states: [
      {
        name: '写成过',
        note: '右上角写着谁、多久以前整理的；正文和文档里的状态标签同一个样子。',
        props: { content: SITUATION, writtenAt: new Date(Date.now() - 2 * 3600_000).toISOString(), writing: false },
        expect: '当前重点是准备内测',
      },
      {
        name: '正在重写',
        note: '上一版照旧显示，右上角说正在整理。',
        props: { content: SITUATION, writtenAt: null, writing: true },
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
