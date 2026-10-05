<script setup lang="ts">
// 「现场」那一格的接线外壳。
//
// 面板里那一半（`components/panels/PanelSite.vue`）只吃 props：场景棘轮认的「场景」是
// `components/panels/**` 下每个 SFC，A 档的意思是「给一组 props 就能单独出画面」，取数
// 一滴都不能漏进去。所以这一格所有的取数——这一窗 transcript、按队友筛的名册、每一轮的
// 起始时间、顶上的会话栏、摊开一步之后那一段输出——都在
// `composables/usePanelSite.ts` 里调一次，结果原样递下去。
//
// 外壳只能待在这儿：`components/panels/**` 底下每个 SFC 都是场景、包括外壳自己，所以它
// 得站在场景之外；`components/**` 又不许直接 import 接口层（`pnpm run lint:boundary`），
// 所以取数走 `composables/`。同一条理由见 `components/work/PanelChangesHost.vue`。
import type { AgentControlState } from '../../cx_types'
import type { MemberActivityLine } from '../../lib/memberActivity'

import { usePanelSite } from '../../composables/usePanelSite'
import PanelSite from '../panels/PanelSite.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    // 这段对话的 id：房间的，或者任务的。
    topicId: string | null
    // 会话栏里「由 X 授权」那颗 chip 去哪：项目 ID 换来项目里的成员页。
    projectId?: string | null
    // This tab is the one on screen. Load happens on the rising edge, exactly
    // like opening the old drawer did.
    active?: boolean
    // 房间名册 handle → 名字。这一栏给每一行署的是它的作者，和对话栏一个规矩：
    // 一个房间可以先后交给两个队友，各自的话各自署名，不能写死「芝士」。
    memberNames?: Record<string, string>
    // 这个房间现在有没有活在跑。现场自己听不到轮次帧（WS 在对话栏那边），而
    // 「最后一组还没完」和「最后一组是上一轮留下的」看起来一模一样。
    working?: boolean
    // 房间 socket 上最近一帧会话状态（对话栏收到，经 TopicView 转过来）。
    agentControl?: AgentControlState | null
    // 在跑的轮次 id → 开始时间（毫秒），对话栏从 socket 上算的。哪一组「进行中」读它。
    runningTurns?: Record<string, number>
    // 一轮结束时加一：趁这时把这一段安静地重读一遍，补上 socket 断开时漏掉的行。
    refreshTick?: number
    /** 名册里查不到名字的 AI 发言按这个名字称呼（项目 AI 队友的名字）。 */
    agentName?: string
    /** 此刻谁在这个房间里忙（`MemberActivity` 那一份）。 */
    activity?: MemberActivityLine[]
  }>(),
  {
    projectId: null,
    active: false,
    memberNames: () => ({}),
    working: false,
    agentControl: null,
    runningTurns: () => ({}),
    refreshTick: 0,
    agentName: () => t('work.room.defaultAgentName'),
    activity: () => [],
  }
)

const emit = defineEmits<{
  (e: 'open-file', path: string, taskId: string | null): void
  (e: 'open-topic', id: string): void
  (e: 'mention-click', handle: string): void
}>()

// 取数那一层要跟着这几个 prop 变（换个房间、切到这一格、socket 上来一行），所以递进去
// 的是取值的那几个函数，不是抄出来的一份值。
const site = usePanelSite({
  topicId: () => props.topicId,
  active: () => props.active,
  refreshTick: () => props.refreshTick,
  runningTurns: () => props.runningTurns,
  agentControl: () => props.agentControl,
})

// 对话栏的 socket 上来了现场的一行：WorkPanel 拿着这一只往里递（房间页和任务页都是它，
// 演示里是剧本一帧一帧喂）。名册上的名字由那一层给，这一只只把它转下去。
defineExpose({ receive: site.receive })
</script>

<template>
  <PanelSite
    :topic-id="props.topicId"
    :project-id="props.projectId"
    :active="props.active"
    :member-names="props.memberNames"
    :working="props.working"
    :agent-control="props.agentControl"
    :running-turns="props.runningTurns"
    :refresh-tick="props.refreshTick"
    :agent-name="props.agentName"
    :activity="props.activity"
    :site="site"
    @open-file="(path: string, taskId: string | null) => emit('open-file', path, taskId)"
    @open-topic="(id: string) => emit('open-topic', id)"
    @mention-click="(handle: string) => emit('mention-click', handle)"
  />
</template>
