<script setup lang="ts">
import type { FeedbackStatus } from '@/cx_types'

import { computed } from 'vue'

import { STATUS_SEGMENTS, statusMeta } from '@/lib/feedbackMeta'

/**
 * StatusRail.vue — 队列行首那根竖向阶梯条，4px × 40px。
 *
 * 它是「状态不靠颜色单独承载」这条规则的第一重信号：段数就是这条反馈在梯子上走到
 * 了第几级，形状在 1x 屏、黑白打印和色觉障碍下都还活着，而 4px 宽的色块会先糊掉。
 * 所以**段数和颜色都不在这里写第二份** —— 段数取 `STATUS_SEGMENTS`，已填段的颜色取
 * `statusMeta().dot`，两处都在 `lib/feedbackMeta.ts`。
 *
 * 段数**不**跟着服务端那几条梯子走：`RUNGS` 是 4，因为这是这一段的几何（40px ÷ 4 =
 * 10px，前三段各带一条 1px 的缝）。服务端哪天插进第五级，这里要的是重新裁一次这 40px，
 * 而不是悄悄多画一格 —— 8px 的段和 10px 的段在余光里分不出来，「填了几格」这条主通道
 * 就没了（要加的是位置，不是把已有的格子压扁）。
 *
 * 整根条 `aria-hidden`：读屏该念的是旁边那行 meta 里的状态字，而不是一根没有名字的
 * 色块（三处冗余里的另外两处是状态字和状态芯片）。
 */
defineOptions({ name: 'StatusRail' })

const props = defineProps<{ status: FeedbackStatus }>()

/** 梯子一共几格。见上面：这是这个组件的几何，不是数据的形状。 */
const RUNGS = 4

const meta = computed(() => statusMeta(props.status))

/** 填几格。`STATUS_SEGMENTS` 存的已经是「下标 + 1」，认不出的状态它自己兜 1 段。 */
const filled = computed(() => STATUS_SEGMENTS[props.status] ?? 1)
</script>

<template>
  <span class="srail" :style="{ '--srail-fill': meta.dot }" aria-hidden="true">
    <i
      v-for="i in RUNGS"
      :key="i"
      class="srail__seg"
      :class="{ 'srail__seg--on': i <= filled, 'srail__seg--seam': i < RUNGS }"
    />
  </span>
</template>

<style scoped>
.srail {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  width: 4px;
  height: 40px;
}

/* 每段 10px（四段等高）。`box-sizing: border-box` 在这里是**载荷**，不是习惯：
   缝要从那 10px 里扣，否则四段加上三条 1px 是 43px，而这一根必须正好 40px ——
   它和 61px 的队列行（9 + 20 + 4 + 18 + 9 + 1）对齐，行高是钉死的。 */
.srail__seg {
  box-sizing: border-box;
  flex: 1 1 0;
  background: var(--line);
}

/* 缝画在**上一段**的下沿，颜色是行底色（`--canvas`），于是它读起来是一道缺口而不是
   一条线。最后一段不带：它下面就是行的下边线，两条线叠在一起只会变粗。 */
.srail__seg--seam {
  border-bottom: 1px solid var(--canvas);
}

/* 已填段的颜色从根元素的 `--srail-fill` 来（源头是 `statusMeta().dot`）。 */
.srail__seg--on {
  background: var(--srail-fill);
}
</style>
