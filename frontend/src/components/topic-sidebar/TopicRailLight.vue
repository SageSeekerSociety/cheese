<script setup lang="ts">
// 行上那盏灯：颜色 + 动不动。
//
// 画哪一盏由父级按 `lib/roomLight.ts` 算好传进来（状态那几条判据的事实都在后端，
// 蓝灯是父级按「这条归谁」盖上的），这一份只管画 —— 频道行和任务行两处都用它，
// 免得同一盏灯在两处画成两个样子。
//
// 灯是**状态**，不是控件：不给 hover 反馈，点它也没有事发生。说不出是什么的那一行
// 干脆不画（父级传下来的就是 null，那种行不渲染这个组件）。
import type { RowLight } from '@/lib/roomLight'

import { lightTitle } from '@/lib/roomLight'

defineProps<{ light: RowLight }>()
</script>

<template>
  <span
    class="row-light"
    :class="[`row-light--${light.kind}`, { 'row-light--breathe': light.breathe }]"
    role="img"
    :aria-label="lightTitle(light)"
    :title="lightTitle(light)"
  />
</template>

<style scoped>
/* 8px 实心点：行里别的小记号（未读丸、成员点）也是这个尺寸。 */
.row-light {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}
/* 蓝：别人的活。红黄绿说的是「这条现在怎么了」，蓝说的是「这条不归我」——所以它
   自己一个色相，不借红黄绿里的任何一个，也不和任务行右边别的蓝（链接、选中态）
   混：那是一处 --signal-blue，和 --signal-yellow 同样的用法。 */
.row-light--blue {
  background: var(--signal-blue);
}
.row-light--red {
  background: var(--danger);
}
/* 黄不用琥珀/橙：右边的未读数字就是琥珀色，同色会让人把「有新消息」和「有事等你」
   读成一回事（和下面 `.subtree-toggle--awaits` 同一条理由）。 */
.row-light--yellow {
  background: var(--signal-yellow);
}
.row-light--green {
  background: var(--ok);
}
/* 呼吸绿：这里有一轮在跑（或活在交付链路上），所以这盏灯自己在动。这个动是**信息
   本身**，不是装饰——但系统设了「减少动态」时它必须停下来（docs/design-system.md
   §9.5：每一处 infinite 动画自己带一条 reduced-motion 规则，而且信息要在动画停掉
   之后仍然说得出）。
   所以呼吸的那一盏除了闪，还常驻一圈光晕：动画停掉之后，它和长绿那盏（同样的绿、
   没有光晕）仍然分得开，差别只是从「动」变成「亮一圈」。 */
.row-light--breathe {
  animation: row-light-breathe 1.6s var(--ease-standard) infinite;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 22%, transparent);
}
@keyframes row-light-breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
@media (prefers-reduced-motion: reduce) {
  .row-light--breathe {
    animation: none;
  }
}
</style>
