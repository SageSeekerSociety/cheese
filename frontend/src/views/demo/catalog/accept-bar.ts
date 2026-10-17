/**
 * 输入框上方那一条采纳横条（`TopicAcceptCard`）那一册。这张卡自己去接口取数，所以
 * 它是全册里 `needs` 最多的一件（vuetify、pinia、router、i18n）。
 *
 * 卡上那条活的 id 从 `../catalogFixtures.ts` 的 `ACCEPT_CARD` 来。
 */
import type { CatalogEntry } from './types'

import { ACCEPT_CARD } from '../catalogFixtures'

import TopicAcceptCard from '@/components/TopicAcceptCard.vue'

export const ACCEPT_BAR_ENTRIES: CatalogEntry[] = [
  {
    id: 'accept-card',
    title: 'TopicAcceptCard',
    about: '输入框上方那一条采纳横条：现在在等什么，轮到人时在这里退回或采纳。详情在「改动」页顶部。',
    file: 'src/components/TopicAcceptCard.vue',
    component: TopicAcceptCard,
    // 这张卡自己去接口取数（`getAcceptCards` / `getPrChecks`），也读工作区 store。
    needs: ['vuetify', 'pinia', 'router', 'i18n'],
    states: [
      {
        name: '贴在输入框上方',
        note: '一行：待谁审阅，以及退回和采纳两颗按钮。交的是什么不在这一行上。',
        // 卡上带着哪一条活的 id：不传就只看「不属于任何一条活」的那些卡（见组件里那个 filter）。
        // 看卡的是卡上点名的审阅人：采纳和退回只给他。
        props: {
          topicId: 'demo',
          topicStatus: 'active',
          taskId: ACCEPT_CARD?.task_id,
          viewer: ACCEPT_CARD?.reviewer_handle,
        },
        expect: '采纳并完成任务',
      },
    ],
  },
]
