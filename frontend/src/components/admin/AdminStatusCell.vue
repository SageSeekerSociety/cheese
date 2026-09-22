<script setup lang="ts">
/**
 * AdminStatusCell.vue — 管理列表里那一格「状态」。
 *
 * 它替掉的是「6px 圆点 + 两个字」：那个画法在一屏十七行的密集列表里不够用 ——
 * 圆点只有三档颜色（灰 / 琥珀 / 绿），而状态有四档，于是「已修复」和「已上线」
 * 长得一模一样，而这两级恰恰是**必须分开**的（一个是代码改完了，一个是部署出去
 * 了；把这两件事画成一个样子，读的人会以为修复就等于自己这边能用了）。
 *
 * 改成**四格梯子**：填几格 = 这个状态在服务端给的梯子上第几格。
 *   已收录 [▰▱▱▱]  处理中 [▰▰▱▱]  已修复 [▰▰▰▱]  已上线 [▰▰▰▰]↑
 * 于是形状（填几格）、字形（上线多一个箭头）、文字（标签）三重信号各自独立，
 * 颜色只是第四重 —— 只靠颜色的话色觉障碍的读者分不出「处理中」和「已修复」。
 *
 * 梯子长度和位置都取自 `store.statusLadder`（服务端 `meta.status_ladder`），不写死
 * 四格：服务端将来插一级，这里是跟着变的。本地那份 `STATUS_LADDER` 只在 meta 还没
 * 到的那一帧顶上。
 *
 * 那几根小条是 `aria-hidden` 的：读屏拿到的应该是「状态：处理中」这样一句话，
 * 而不是四根没有名字的方块。所以标签文本本身就在，不靠 `title` 才读得出来。
 */

import type { FeedbackStatus } from '@/cx_types'

import { computed } from 'vue'

import { statusMeta } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

const props = defineProps<{ status: FeedbackStatus }>()

const store = useFeedbackStore()

const meta = computed(() => statusMeta(props.status))

/** 一共几格。服务端给几条梯子就画几格。 */
const rungs = computed(() => Math.max(1, store.statusLadder.length))

/** 填几格。**取不到位置时给 1 格而不是 0**：0 格读起来是「什么都没发生」，而这
 *  条反馈确实在这里、状态确实有值 —— 服务端多出一级新状态时，一格亮着加上它自己
 *  的标签，是「有这个状态、但它在这条梯子上的位置我不认识」的诚实画法。 */
const filled = computed(() => {
  const i = store.statusLadder.indexOf(props.status)
  return i >= 0 ? i + 1 : 1
})

/** 悬停时的那句话。状态名 + 它在梯子上的位置，**不编造逐级时间**（服务端没给）。 */
const hint = computed(() => {
  const { label } = meta.value
  if (filled.value > 1) return `${label} · 已走到第 ${filled.value}/${rungs.value} 步`
  return label
})
</script>

<template>
  <span class="fbst" :title="hint">
    <span class="fbst__rungs" aria-hidden="true">
      <i
        v-for="i in rungs"
        :key="i"
        class="fbst__rung"
        :class="{ 'fbst__rung--on': i <= filled }"
        :style="i <= filled ? { background: meta.dot } : undefined"
      />
    </span>
    <span class="fbst__label" :style="{ color: meta.ink }">{{ meta.label }}</span>
    <!-- 上线是这条梯子的最后一格，也是唯一「用户这边真的能用了」的那一格。
         多一个向上的箭头，是为了让它在余光里和「已修复」分开。 -->
    <v-icon v-if="props.status === 'deployed'" icon="mdi-arrow-up" size="12" class="fbst__up" />
  </span>
</template>

<style scoped>
.fbst {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.fbst__rungs {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 2px;
}

/* 8×3 的小条。填色那几根的颜色从 `statusMeta().dot` 来，最淡的一档是 `--faint`
   （`#9aa0a8`，在 `--surface` 上 2.64:1）。够不着 WCAG 1.4.11 那条 3:1，这是
   **有意的**：这几根条是第二重信号，不是唯一一重。真正承载信息的是旁边那行
   标签，它落在 `--ink` 一档上；把条本身调到 3:1 就得让「已收录」看起来和
   「处理中」差不多重，那是拿层级换一个不必要的形式合规。
   （别把这个数当成 `--faint` 的定义：它随 token 走，[#1328] 那一版调暗之后
   会过线。条是不是唯一信号才是这段要说的那件事。） */
.fbst__rung {
  width: 8px;
  height: 3px;
  border-radius: var(--radius-pill);
  /* 没填的那几格是轨道，不是内容：用 `--line-2`，保证「4 格里的第 1 格」看得出来
     是「四分之一」而不是「一根孤零零的条」。 */
  background: var(--line-2);
}

.fbst__label {
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.fbst__up {
  flex: 0 0 auto;
  color: var(--ok-ink);
}
</style>
