<script setup lang="ts">
import type { FeedbackStatus, FeedbackTimelineEntry } from '@/cx_types'

import { computed } from 'vue'

import { statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

// 反馈详情右侧那根竖着的时间线：已收录 → 评估中 → 计划中 → 处理中 → 已解决。
//
// 它画的是**梯子**，不是 `timeline` 数组本身 —— 数组里只有已经发生过的几步，梯子是
// 全部五步。少了这个区分，一条刚收录的反馈右侧只会有一个孤零零的点，读的人看不出
// 「后面还有几关」，而这正是这块要回答的问题。
//
// 梯子从**服务端**来（`GET /feedback/meta` 的 `status_ladder`，页面透过
// `store.statusLadder` 递进来）：加一档状态是后端改一处，前端不该有一份要发版才能
// 跟上的副本。`ladder` 是必填的 prop，没有默认值 —— 默认值会让「忘了传」变成一条
// 悄悄画错的线。
//
// 走过的步骤用实心点 + 状态色，当前这一步额外用实心底；还没到的用空心。**形状和
// 颜色一起变**：只靠颜色的话，灰度截图和色觉障碍的读者看到的是五个一样的点。
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
  done: boolean
  current: boolean
}

const steps = computed<Step[]>(() => {
  const currentIndex = props.ladder.indexOf(props.status)
  return props.ladder.map((status, index) => {
    // 同一状态可能被推进过两次（回退再推进），取**最早**那一次：时间线记的是
    // 「什么时候到过这里」，不是「最后一次是什么时候改回来的」。
    const entry = props.timeline.find((e) => e.status === status)
    return {
      status,
      label: statusMeta(status).label,
      at: entry?.at ?? null,
      by: entry?.by_handle ?? null,
      done: index < currentIndex,
      current: index === currentIndex,
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
        <div v-if="step.at" class="t-meta">
          {{ relTime(step.at) }}<template v-if="step.by"> · {{ step.by }}</template>
        </div>
        <div v-else class="t-meta">未开始</div>
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
.fb-step__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.5;
}
</style>
