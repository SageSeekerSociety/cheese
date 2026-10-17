// 这一栏里坐着谁、界面上怎么称呼他：名册、座位、显示名、头像，和 chips 读的那两张表。
//
// 名册本身见 room/composables/useRoomRoster，「谁在这间房说话、@ 渲染成谁」见
// useAgentNaming。两块都要按 handle 索引的那几张表，所以在这里一起建出来交给调用方：
// `refMaps` 就是行组件画 chips 时读的那一份（`reachable` 由名册定）。
import type { ProjectMemberRow, Topic } from '../../cx_types'

import { reactive } from 'vue'

import { useRoomRoster } from '../../components/room/composables/useRoomRoster'
import { useAgentNaming } from '../useAgentNaming'

/** 这一栏问得出的「谁是谁」：名册、称呼，加上 chips 读的那两张表。 */
export function usePanelPeople(deps: {
  /** 这一栏读的那间房：名册是房间的，不是某一个任务的。 */
  topic: () => Topic | null
  members: () => ProjectMemberRow[]
  /** 我自己的 handle。 */
  author: string
  /** 名册读不到时说一句（面板把这句话挂成错误横幅）。 */
  onError: (message: string) => void
  /** 这一栏说给谁听（opts.agentHandle）。 */
  agentHandle?: () => string | null
  alwaysSummon: () => boolean
}) {
  // Chips are drawn in the row components (../lib/renderMessage); here we only fill the
  // maps they draw with (`refMaps`, below the roster, which decides `reachable`).
  const mentionNames = reactive<Record<string, string>>({})
  const mentionTitles = reactive<Record<string, string>>({})
  const topicTitles = reactive<Record<string, string>>({})

  // 名册、座位、显示名、头像 —— 见 room/composables/useRoomRoster。
  // 房间名册和项目名册是两份，因为 AI 队友的座位只在前者上。
  const roster = useRoomRoster({
    topic: deps.topic,
    members: deps.members,
    author: deps.author,
    onError: deps.onError,
  })
  const refMaps = reactive({ mentionNames, mentionTitles, topicTitles, reachable: roster.reachable })

  // 谁在这间房说话、界面上怎么称呼它、@ 渲染成谁的名字 —— 见 useAgentNaming。
  const naming = useAgentNaming({
    roomSeat: roster.agentSeat,
    pool: roster.mentionPool,
    nameOf: roster.agentNameOf,
    names: mentionNames,
    titles: mentionTitles,
    agentHandle: deps.agentHandle,
    alwaysSummon: deps.alwaysSummon,
  })

  return { refMaps, mentionNames, mentionTitles, topicTitles, roster, naming }
}
