/**
 * 任务的卡片（`TaskMapCard`）在预览站里的条目：任务概览最上面那一块，项目地图上
 * 每个任务也是这一张。规矩见 `catalog.ts`，单放一份是因为 `catalog.ts` 顶着一千行的
 * 上限。`CatalogEntry` 是 type-only 引用，运行时不构成循环。
 */
import type { MapCardView } from '@/types/taskMapCard'
import type { CatalogEntry, CatalogNeed } from './catalog'

import TaskMapCard from '@/components/task/TaskMapCard.vue'

const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

const NAMES = { bob: '波比', 'cheese-topica': '芝士' }
const PEOPLE = [
  { handle: 'alice', name: 'Alice' },
  { handle: 'bob', name: '波比' },
]

const FULL: MapCardView = {
  card: {
    what: '丢东西的人点「是我的」，就能联系到捡到的人',
    progress: '表设计好了，在写认领这一步',
    waiting_on: [{ handle: 'bob', ask: '看一下表结构' }],
    terms: [{ term: '认领', meaning: '丢东西的人确认这件东西是自己的' }],
    updated_by: 'cheese-topica',
    updated_at: '2026-10-11T08:00:00Z',
  },
  can_edit: true,
}

export const TASK_MAP_ENTRIES: CatalogEntry[] = [
  {
    id: 'task-map-card',
    title: 'TaskMapCard',
    about:
      '任务的卡片：是什么、到哪了、等谁、名词，每栏一两句，没内容的栏不画。AI 队友每轮干完按需更新，做这件任务的人可以直接改。来源一栏是紧挨着它的 TaskSourceLine。',
    file: 'src/components/task/TaskMapCard.vue',
    component: TaskMapCard,
    needs: UI_T,
    args: { memberNames: NAMES, people: PEOPLE, saving: false, error: null },
    states: [
      {
        name: '写满了',
        note: '四栏都有：在等人时「到哪了」前面是黄点。能改的人看得到「编辑」。',
        props: { view: FULL },
        expect: '看一下表结构',
      },
      {
        name: '还没写',
        note: '老任务、芝士还没写过：一句「暂无任务卡片」，下面的概览不受影响。',
        props: { view: { card: null, can_edit: false } },
        expect: '暂无任务卡片',
      },
    ],
  },
]
