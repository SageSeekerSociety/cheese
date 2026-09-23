<script setup lang="ts">
import { computed } from 'vue'

import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'

// 「值对上限」的一根条：额度燃尽、磁盘占用、覆盖率都用它。
//
// **`limit` 为空 = 不限量**，画的是**空心虚线槽 + 「不限量」**，不是一根满条：
// 一根满条在视觉上就是「100% 用掉了」，而 unlimited 的意思是「没有上限这件事」。
//
// 状态色只在 `tone` 是 warn / danger 时上（额度快烧完、磁盘吃紧）。正常档走中性阶。
const props = withDefaults(
  defineProps<{
    label: string
    /** 已用 / 已覆盖的那个数。展示前由调用方格式化。 */
    valueText: string
    /** 上限。`null` = 不限量。 */
    limit: number | null
    /** 已用占上限的比例（0–1）。`limit` 为 null 时忽略。 */
    ratio?: number
    tone?: 'ink' | 'ok' | 'warn' | 'danger'
    /** 右侧那句「不限量 / 12%」。 */
    hint?: string
    loading?: boolean
    /** 口径注。给了就在 label 旁画 info tip（磁盘/预览的口径注脚走这里，见 §8 收编）。 */
    note?: string
  }>(),
  { ratio: 0, tone: 'ink', loading: false }
)

const unlimited = computed(() => props.limit === null)
const widthText = computed(() => `${Math.max(2, Math.min(100, (props.ratio ?? 0) * 100))}%`)
</script>

<template>
  <div class="amb">
    <div class="amb__row">
      <span class="amb__label t-eyebrow-read">{{ label }}</span>
      <AdminNoteTip v-if="note" :text="note" />
      <span class="amb__value t-num t-dense">{{ valueText }}</span>
      <span v-if="hint" class="amb__hint t-meta-read">{{ hint }}</span>
    </div>

    <div v-if="loading" class="amb__skel">
      <v-skeleton-loader type="text" class="amb__skel-bar" />
    </div>

    <!-- unlimited：空心虚线槽。**不画满条**，见文件头。 -->
    <div v-else-if="unlimited" class="amb__track amb__track--unlimited" aria-hidden="true" />

    <div v-else class="amb__track" aria-hidden="true">
      <span class="amb__fill" :class="`amb__fill--${tone}`" :style="{ width: widthText }" />
    </div>
  </div>
</template>

<style scoped>
.amb {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.amb__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

/* label 不吃满整行（`flex: 0 1 auto`）：口径 tip 要紧挨着它，而不是被推到右端；
   右对齐交给 value 的 `margin-left: auto` —— 没有 tip 时布局与原来逐像素相同。 */
.amb__label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amb__value {
  margin-left: auto;
  color: var(--ink);
}

.amb__hint {
  color: var(--faint);
}

.amb__track {
  display: block;
  height: 8px;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

/* unlimited 的槽：空心 + 虚线边 —— 「这里没有上限」，不是「用满了」。 */
.amb__track--unlimited {
  background: transparent;
  border: 1px dashed var(--line-2);
}

.amb__fill {
  display: block;
  height: 100%;
  background: var(--muted);
  border-radius: var(--radius-pill);
}

/* 只有 warn/danger 才上状态色（项目约定：状态色留给「要人管」）。 */
.amb__fill--warn {
  background: var(--warn);
}
.amb__fill--danger {
  background: var(--danger);
}
.amb__fill--ok {
  background: var(--ok);
}

.amb__skel-bar {
  height: 8px;
}
</style>
