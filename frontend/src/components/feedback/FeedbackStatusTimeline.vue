<script setup lang="ts">
import type { FeedbackStatus, FeedbackTimelineEntry } from '@/cx_types'

import { computed } from 'vue'

import { passedStatusLabel, pendingStatusLabel, statusLabel } from './feedbackLabels'

import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

// 反馈详情右侧那根竖着的时间线：已收录 → 处理中 → 已修复 → 已上线。
//
// 它画的是**梯子**，不是 `timeline` 数组本身 —— 数组里只有已经发生过的几步，梯子是
// 全部四级。少了这个区分，一条刚收录的反馈右侧只会有一个孤零零的点，读的人看不出
// 「后面还有几关」，而这正是这块要回答的问题。
//
// 梯子从**服务端**来（`GET /feedback/meta` 的 `status_ladder`，页面透过
// `store.statusLadder` 递进来）：加一档状态是后端改一处，前端不该有一份要发版才能
// 跟上的副本。`ladder` 是必填的 prop，没有默认值 —— 默认值会让「忘了传」变成一条
// 悄悄画错的线。
//
// 走过的步骤用实心点 + 状态色，当前这一步额外用实心底；还没到的用空心。**形状和
// 颜色一起变**：只靠颜色的话，灰度截图和色觉障碍的读者看到的是四个一样的点。
//
// 说明栏上「没有记录」分两种，说的都是**记录**，不是这件事做没做。当前这一步**之后**
// 的，是真的还没轮到，写「未开始」。当前这一步**之前**的，是这一步已经过去了、却
// 没有单独留下时间：`STATUS_LADDER` 允许直达，直达不补写中间那几行 —— 管理端按键
// 今天仍能一步跳到后面，更新之前的数据多半就是这么留下的。所以那里两个词都不能用
// ——「未开始」在说它还没到，可它已经过去了；「跳过」在说有人绕开了流程，可它多半
// 只是没被单独按一下。能说的只有记录本身：无记录。
//
// 档位的名字分三副面孔：还没轮到的不能顶着「已修复」—— 中文的「已」和英文的过去分词
// （Resolved）都在说「已经」，而这一步还没发生，所以用 `pendingStatusLabel`
// （修复 / Resolve）；正在这一步的用 `statusLabel`（已修复 / Resolved）；已经走过去
// 的用 `passedStatusLabel`，它只在「处理中」这一档和上一副不同 —— 有人正在弄的那一
// 档，走过去了就该是「已处理」。
const props = defineProps<{
  timeline: FeedbackTimelineEntry[]
  status: FeedbackStatus
  /** 服务端给的梯子（有序的状态列表）。 */
  ladder: FeedbackStatus[]
}>()

interface Step {
  status: FeedbackStatus
  label: string
  at: string | null
  by: string | null
  note: NotePart[]
  done: boolean
  current: boolean
}

interface NotePart {
  text: string
  href: string | null
}

// 说明里的链接（部署管线写的 PR 地址）画成可点的 `<a>`，其余原样是文本。只认
// http(s)：说明是服务端写的，但一个能渲染任意 scheme 的 href 不该靠「写的人可信」撑着。
function noteParts(note: string | null | undefined): NotePart[] {
  if (!note) return []
  return note
    .split(/(https?:\/\/[^\s]+)/)
    .filter((part) => part !== '')
    .map((part) => ({ text: part, href: /^https?:\/\//.test(part) ? part : null }))
}

// 「不修复」不在梯子上：它是另一种结局，不是「已上线」之后的一级。这时梯子画到它就停 ——
// 走过的那几级照画，后面的「已修复 / 已上线」不画，免得读的人以为还会轮到它们。
const rungs = computed<FeedbackStatus[]>(() =>
  props.ladder.includes(props.status)
    ? props.ladder
    : [...props.ladder.filter((s) => props.timeline.some((e) => e.status === s)), props.status]
)

const steps = computed<Step[]>(() => {
  const currentIndex = rungs.value.indexOf(props.status)
  return rungs.value.map((status, index) => {
    // 同一状态可能被推进过两次（回退再推进），取**最早**那一次：时间线记的是
    // 「什么时候到过这里」，不是「最后一次是什么时候改回来的」。
    const entry = props.timeline.find((e) => e.status === status)
    const done = index < currentIndex
    const current = index === currentIndex
    return {
      status,
      // 三副面孔：还没轮到的、正在这一步的、已经走过去的。「处理中」走过去之后要
      // 变成「已处理」—— 它说的是「有人正在弄」，可这一步已经过去了。
      label: current ? statusLabel(status) : done ? passedStatusLabel(status) : pendingStatusLabel(status),
      at: entry?.at ?? null,
      by: entry?.by_handle ?? null,
      note: noteParts(entry?.note),
      done,
      current,
    }
  })
})
</script>

<template>
  <ol class="fb-timeline">
    <li v-for="step in steps" :key="step.status" class="fb-step" :class="{ 'fb-step--current': step.current }">
      <span class="fb-step__rail" aria-hidden="true">
        <span
          class="fb-step__dot"
          :class="{ 'fb-step__dot--todo': !step.done && !step.current }"
          :style="{ '--fb-dot': statusMeta(step.status).dot }"
        />
      </span>
      <div class="fb-step__body">
        <div class="fb-step__title" :class="{ 'c-muted': !step.done && !step.current }">
          {{ step.label }}
        </div>
        <div v-if="step.at" class="t-meta-read t-num">
          {{ relTime(step.at) }}<template v-if="step.by"> · <UserRef :handle="step.by" /></template>
        </div>
        <div v-else class="t-meta-read t-num">
          {{ t(step.done ? 'feedback.timeline.noRecord' : 'feedback.timeline.notStarted') }}
        </div>
        <div v-if="step.note.length" class="t-meta-read fb-step__note">
          <template v-for="(part, i) in step.note" :key="i">
            <a v-if="part.href" :href="part.href" target="_blank" rel="noopener noreferrer">{{ part.text }}</a>
            <template v-else>{{ part.text }}</template>
          </template>
        </div>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.fb-timeline {
  margin: 0;
  padding: 0;
  list-style: none;
}
.fb-step {
  display: flex;
  gap: 10px;
  min-height: 46px;
}
/* 竖线画在点的**下半截**：最后一步不能拖一条线到空白里，所以用 last-child 收掉。 */
.fb-step__rail {
  position: relative;
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  width: 10px;
}
.fb-step__rail::after {
  content: '';
  flex: 1;
  width: 1px;
  background: var(--line-2);
}
.fb-step:last-child .fb-step__rail::after {
  background: none;
}
/* 走到过和当前这一步的颜色由模板给（它来自 STATUS_META，是 JS 里的值），所以
   走自定义属性而不是直接写 background —— 直接写就成了行内样式，--todo 那条
   透明背景压不过它，只能靠 !important。 */
.fb-step__dot {
  flex: none;
  width: 10px;
  height: 10px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--fb-dot, var(--faint));
}
/* 还没到的步骤：空心。颜色和形状各说一遍同一件事，去掉任何一边都还读得出来。 */
.fb-step__dot--todo {
  background: transparent;
  border: 1.5px solid var(--line-2);
}
.fb-step--current .fb-step__dot {
  box-shadow: 0 0 0 3px var(--fill-2);
}
.fb-step__note {
  overflow-wrap: anywhere;
}
.fb-step__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.5;
}
</style>
