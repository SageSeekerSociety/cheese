<script setup lang="ts">
// 一件任务的状态记号：图标 + 标签。哪种状态、什么图标、什么颜色、动不动都由
// `lib/taskState.ts` 算好传进来，这一份只管画；频道里的任务卡、全部任务、待处理清单、
// 任务页头和侧栏的任务行用的都是它，同一件任务在几处长得一样。
//
// 会动的只有两种，都在说正在发生的事（design-system §9.6）：
// - 「在做」且会话真的在跑：图标慢慢转。会话停着时换一个静止的图标，所以关掉动画
//   （§9.5）之后，「在跑」和「停着」照样分得开。
// - 「卡住」「待回答」第一次出现在这一页时闪一下，只一下。看没看过记在
//   `firstSight` 里（任务 + 进这个状态的时刻），列表刷新、组件重画都不再闪。
import type { TaskState } from '@/lib/taskState'

import { ref, watch } from 'vue'

import { firstSight } from '@/lib/taskState'

const props = withDefaults(
  defineProps<{
    state: TaskState
    /** 图标多大（px）。字跟着它所在的那一行。 */
    size?: number
    /** 只画图标：标签进 `title` 和读屏的名字（侧栏行、待处理清单行首）。 */
    iconOnly?: boolean
    /** 这一件在等看的人：标签加粗。 */
    emphasis?: boolean
  }>(),
  { size: 14, iconOnly: false, emphasis: false }
)

const attend = ref(false)
watch(
  () => props.state.attention,
  (key) => {
    attend.value = !!key && firstSight(key)
  },
  { immediate: true }
)
</script>

<template>
  <span
    class="task-state"
    :class="{
      'task-state--live': state.live,
      'task-state--attend': attend,
      'task-state--strong': emphasis,
    }"
    :data-kind="state.kind"
    :data-tone="state.tone"
    :data-live="state.live ? '' : undefined"
    :role="iconOnly ? 'img' : undefined"
    :aria-label="iconOnly ? state.label : undefined"
    :title="iconOnly ? state.label : undefined"
    data-testid="task-state"
  >
    <v-icon class="task-state__icon" :size="size" aria-hidden="true">{{ state.icon }}</v-icon>
    <span v-if="!iconOnly" class="task-state__label">{{ state.label }}</span>
  </span>
</template>

<style scoped>
.task-state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  color: var(--muted);
  white-space: nowrap;
}
.task-state__icon {
  flex: none;
  color: var(--faint);
}
.task-state__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-state--strong .task-state__label {
  font-weight: 600;
}
/* 颜色按 §1.5 的三件套：图标用记号色，字用 -ink。 */
.task-state[data-tone='ok'] {
  color: var(--ok-ink);
}
.task-state[data-tone='ok'] .task-state__icon,
.task-state[data-tone='okMark'] .task-state__icon {
  color: var(--ok);
}
.task-state[data-tone='neutral'] .task-state__icon {
  color: var(--muted);
}
.task-state[data-tone='warn'] {
  color: var(--warn-ink);
}
.task-state[data-tone='warn'] .task-state__icon {
  color: var(--warn);
}
.task-state[data-tone='ink'],
.task-state[data-tone='ink'] .task-state__icon {
  color: var(--ink);
}
.task-state[data-tone='danger'] {
  color: var(--danger-ink);
}
.task-state[data-tone='danger'] .task-state__icon {
  color: var(--danger);
}
/* 会话在跑：真的旋转，所以是匀速（§9.3）。 */
.task-state--live .task-state__icon {
  animation: task-state-turn 2.4s linear infinite;
}
/* 第一次看到卡住、待回答：放大一下再落回原处，只播一次。 */
.task-state--attend .task-state__icon {
  animation: task-state-attend calc(var(--dur-slow) * 2) var(--ease-out) 1;
}
@keyframes task-state-turn {
  to {
    transform: rotate(360deg);
  }
}
@keyframes task-state-attend {
  0%,
  100% {
    transform: scale(1);
  }
  35% {
    transform: scale(1.35);
  }
}
@media (prefers-reduced-motion: reduce) {
  .task-state--live .task-state__icon,
  .task-state--attend .task-state__icon {
    animation: none;
  }
}
</style>
