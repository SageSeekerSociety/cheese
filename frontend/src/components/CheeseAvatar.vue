<script setup lang="ts">
// AI 队友的头像 — one identity everywhere (spec §8.4 人格唯一)：一块超椭圆，里面
// 一双胶囊眼，一大一小，整体偏右，像把脸转向了说话的一方。样子和由来见
// docs/brand.md「AI 队友头像」。
//
// 形状：人是圆的，团队、空间、项目是圆角方块（squareRadius），AI 队友单独是超椭圆，
// 形状本身就把三者分开。底色不用琥珀（琥珀留给主操作）。
//
// 一个项目可以有好几个 AI 队友，它们的脸一样，靠底色分：底色按 handle 从一组深色里
// 取一档（agentTone），不按名字——队友改名之后颜色不能跟着变。没有 handle 的地方
// （官网示例、演示房间）拿名字当种子。
//
// 表情（state）：队友在干活时，对话里它最近出现的那个头像跟着状态动（lib/agentFace）。
// 在想、在干活、等机器一直循环，节奏放慢；卡住、做完了只在变的那一刻播一次。每种
// 表情先是一个静止的样子（眼睛往上看、眯眼、一只眼眯起、笑眼），动画叠在上面——系统
// 关了动效时动画全停，靠这个样子也分得出是哪一种。
import type { FaceState } from '../lib/agentFace'

import { computed } from 'vue'

import { t } from '../i18n'
import { agentTone } from '../utils/avatar'

const props = withDefaults(
  defineProps<{ size?: number | string; name?: string; handle?: string | null; state?: FaceState | null }>(),
  {
    size: 28,
    name: undefined,
    handle: null,
    state: null,
  }
)

// 没给名字时是默认的那一位：读屏读它的默认名，底色也按这个名字取。
const shownName = computed(() => props.name || t('work.agentAvatar.defaultName'))

const px = computed(() => Number(props.size))
const tone = computed(() => agentTone(props.handle || shownName.value))
</script>

<template>
  <svg
    class="cheese-avatar"
    :width="px"
    :height="px"
    viewBox="0 0 100 100"
    role="img"
    :aria-label="shownName"
    :data-user-content="name || undefined"
    :data-tone="tone"
    :data-state="state ?? undefined"
  >
    <g class="cheese-avatar__body">
      <path class="cheese-avatar__tile" d="M50 0C88 0 100 12 100 50S88 100 50 100S0 88 0 50S12 0 50 0Z" />
      <g class="cheese-avatar__eyes">
        <rect class="cheese-avatar__eye" x="42.5" y="44.5" width="13" height="21" rx="6.5" />
        <rect class="cheese-avatar__eye cheese-avatar__eye--far" x="66" y="46" width="10" height="16" rx="5" />
      </g>
      <g v-if="state === 'done'" class="cheese-avatar__smile">
        <path d="M40 60Q49 46 58 60" stroke-width="5.5" />
        <path d="M65.5 58Q71.5 48 77.5 58" stroke-width="4.5" />
      </g>
      <template v-if="state === 'think'">
        <circle v-for="n in 3" :key="n" class="cheese-avatar__bubble" cx="82" cy="34" r="5" />
      </template>
    </g>
  </svg>
</template>

<style scoped>
.cheese-avatar {
  display: block;
  flex: 0 0 auto;
  overflow: visible;
  user-select: none;
}
.cheese-avatar__eye,
.cheese-avatar__bubble {
  fill: var(--agent-ink);
}
.cheese-avatar__smile {
  fill: none;
  stroke: var(--agent-ink);
  stroke-linecap: round;
}
.cheese-avatar__body,
.cheese-avatar__eyes,
.cheese-avatar__eye,
.cheese-avatar__bubble {
  transform-box: fill-box;
  transform-origin: center;
}
.cheese-avatar[data-tone='0'] .cheese-avatar__tile {
  fill: var(--agent-tone-0);
}
.cheese-avatar[data-tone='1'] .cheese-avatar__tile {
  fill: var(--agent-tone-1);
}
.cheese-avatar[data-tone='2'] .cheese-avatar__tile {
  fill: var(--agent-tone-2);
}
.cheese-avatar[data-tone='3'] .cheese-avatar__tile {
  fill: var(--agent-tone-3);
}
.cheese-avatar[data-tone='4'] .cheese-avatar__tile {
  fill: var(--agent-tone-4);
}

/* ---- 表情：先是静止的样子，再叠动画 ---------------------------------------- */

/* 在想：眼睛往右上看，孔一个接一个冒出来（标志里那几个孔）。 */
[data-state='think'] .cheese-avatar__eyes {
  transform: translate(7px, -10px);
  animation: cheese-ponder 4.8s ease-in-out infinite;
}
[data-state='think'] .cheese-avatar__eye {
  animation: cheese-blink 4.8s infinite;
}
[data-state='think'] .cheese-avatar__bubble {
  opacity: 0;
  animation: cheese-rise 2.4s ease-out infinite;
}
[data-state='think'] .cheese-avatar__bubble:nth-of-type(2) {
  animation-delay: 0.8s;
}
[data-state='think'] .cheese-avatar__bubble:nth-of-type(3) {
  animation-delay: 1.6s;
}
@keyframes cheese-ponder {
  0%,
  100% {
    transform: translate(7px, -10px);
  }
  50% {
    transform: translate(9px, -11px);
  }
}
@keyframes cheese-blink {
  0%,
  94%,
  100% {
    transform: scaleY(1);
  }
  96.5% {
    transform: scaleY(0.1);
  }
}
@keyframes cheese-rise {
  0% {
    transform: translate(0, 0) scale(0.3);
    opacity: 0;
  }
  20% {
    opacity: 0.9;
  }
  100% {
    transform: translate(5px, -24px) scale(1.15);
    opacity: 0;
  }
}

/* 在干活：眯起眼，慢慢左右来回扫，像在读东西。 */
[data-state='work'] .cheese-avatar__eye {
  transform: scaleY(0.72);
}
[data-state='work'] .cheese-avatar__eyes {
  transform: translate(-7px, 2px);
  animation: cheese-scan 3.4s ease-in-out infinite;
}
@keyframes cheese-scan {
  0%,
  40%,
  100% {
    transform: translate(-7px, 2px);
  }
  50%,
  90% {
    transform: translate(4px, 2px);
  }
}

/* 等机器：转过来正对着，慢慢呼吸。 */
[data-state='wait'] .cheese-avatar__eyes {
  transform: translate(-7px, 0);
}
[data-state='wait'] .cheese-avatar__body {
  animation: cheese-breathe 3.6s ease-in-out infinite;
}
@keyframes cheese-breathe {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.035);
  }
}

/* 卡住了：一只眼眯起来，歪一下头，停在歪着的样子。只播一次。 */
[data-state='stuck'] .cheese-avatar__eye--far {
  transform: scaleY(0.5);
}
[data-state='stuck'] .cheese-avatar__eyes {
  transform: translate(-3px, 3px);
}
[data-state='stuck'] .cheese-avatar__body {
  transform: rotate(-5deg);
  animation: cheese-tilt 0.9s var(--ease-standard) 1;
}
@keyframes cheese-tilt {
  0% {
    transform: rotate(0);
  }
  55% {
    transform: rotate(-7deg);
  }
  100% {
    transform: rotate(-5deg);
  }
}

/* 做完了：眼睛弯成笑眼，小跳一下。只播一次，之后头像回到静止。 */
[data-state='done'] .cheese-avatar__eyes {
  opacity: 0;
}
[data-state='done'] .cheese-avatar__body {
  animation: cheese-hop 0.7s var(--ease-standard) 1;
}
@keyframes cheese-hop {
  0%,
  100% {
    transform: translateY(0);
  }
  40% {
    transform: translateY(-8%);
  }
  70% {
    transform: translateY(0) scale(1.03, 0.97);
  }
}

/* 系统关了动效：一帧都不动，停在上面那几个静止的样子；在想留一个孔。 */
@media (prefers-reduced-motion: reduce) {
  .cheese-avatar * {
    animation: none !important;
  }
  [data-state='think'] .cheese-avatar__bubble:first-of-type {
    opacity: 0.9;
    transform: translate(2px, -10px) scale(0.9);
  }
}
</style>
