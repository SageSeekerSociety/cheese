<script setup lang="ts">
// 可用 tokens 额度 —— 这个项目还能跑多少。
//
// 它是项目的一个事实，读它的人问的是「还够不够用、用完了找谁」，所以它属于项目
// 设置那一页，和运行环境、仓库连接并排。拿不到额度信息时这一块说「暂无额度信
// 息」而不是消失：分不出「没有上限」和「读不到」的话，一个用完了的项目看起来和
// 一个不限量的项目一模一样。
import type { ProjectCredits } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { getProjectCredits } from '@/api'

const props = defineProps<{ projectId: string }>()

const credits = ref<ProjectCredits | null>(null)

const usedPct = computed<number>(() => {
  const c = credits.value
  if (!c || c.unlimited || c.credits_total <= 0) return 0
  return Math.min(100, (c.credits_used / c.credits_total) * 100)
})
const exhausted = computed<boolean>(() => {
  const c = credits.value
  return !!c && !c.unlimited && c.credits_remaining <= 0
})

/** 额度是小数（1 额度 = 1 万 tokens），所以精度跟着量级走。 */
function fmt(n: number): string {
  if (Math.abs(n) >= 100) return n.toFixed(0)
  if (Math.abs(n) >= 1) return n.toFixed(2)
  return n.toFixed(4)
}

async function load() {
  const projectId = props.projectId
  try {
    const got = await getProjectCredits(projectId)
    if (props.projectId !== projectId) return
    credits.value = got
  } catch {
    if (props.projectId === projectId) credits.value = null
  }
}

watch(
  () => props.projectId,
  () => {
    credits.value = null
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-gauge</v-icon>
      <span class="page-section-title">额度</span>
      <v-spacer />
      <span v-if="credits && !credits.unlimited" class="ln-num c-muted">
        1 额度 = {{ credits.tokens_per_credit.toLocaleString() }} tokens
      </span>
    </div>
    <div class="page-section-body">
      <div v-if="!credits" class="t-body c-muted py-2">暂无额度信息</div>
      <div v-else-if="credits.unlimited" class="t-body c-muted py-2">当前未设置额度上限</div>
      <template v-else>
        <div class="credit-remaining" :class="{ 'credit-remaining--empty': exhausted }">
          {{ fmt(credits.credits_remaining) }}
          <span class="credit-remaining__unit">额度剩余</span>
        </div>
        <div class="credit-bar mb-2">
          <div
            class="credit-bar__used"
            :class="{ 'credit-bar__used--empty': exhausted }"
            :style="{ width: usedPct + '%' }"
          />
        </div>
        <div class="ln-row">
          <span class="ln-row-title">已使用 / 共发放</span>
          <v-spacer />
          <span class="ln-num"> {{ fmt(credits.credits_used) }} / {{ fmt(credits.credits_total) }} </span>
        </div>
        <div v-if="exhausted" class="credit-exhausted mt-1">额度已用完，请联系团队管理员或额度发放方补充</div>
        <div v-else class="t-meta c-muted mt-2">包含团队共享额度与本项目定向额度；优先使用定向额度</div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.credit-remaining {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 26px;
  font-weight: 600;
  color: var(--ink);
  margin: 6px 0 10px;
}
/* 剩余额度是一个要读的数字，不是一个记号 —— 浅色主题下 `--warn` 在白底上只有
   2.34:1，读不出来。 */
.credit-remaining--empty {
  color: var(--warn-ink);
}
.credit-remaining__unit {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 400;
  color: var(--faint);
  margin-left: 6px;
}
.credit-bar {
  height: 10px;
  /* 10px 高的进度条，两端本来就该是半圆。 */
  border-radius: var(--radius-pill);
  overflow: hidden;
  background: var(--fill);
}
.credit-bar__used {
  height: 100%;
  background: var(--ink);
  transition: width 0.3s ease;
}
.credit-bar__used--empty {
  background: var(--warn);
}
/* 用完了是一句要读的话，所以用墨色那一档，不是记号色。 */
.credit-exhausted {
  font-size: 12.5px;
  color: var(--warn-ink);
}
</style>
