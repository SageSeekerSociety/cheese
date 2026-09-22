<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

// 看板上的「按 X 拆」小表（用量那两块：按模型、按通路）。
//
// 它和 `AdminBarChart` 的分工是**一行要不要带第二三个数**：柱状图那一版刻意只画一根
// 条（长度是一个量纲，再叠钱就是两根条叠在一起，长短就没法比了），而这一版要回答的
// 正是「贵的是模型还是计费方式」—— 所以每行除了条还有「钱」和「算不出价」两列，缺
// 任何一个都答不了那个问题。
//
// **每一行本身是文字**（名字、条、三个数都是真文本），读屏直接读得到，所以这里不要
// 那张折叠的数据表孪生体 —— `AdminLineChart` 要那张表是因为整块 SVG 是 `aria-hidden`。
//
// 「算不出价」那列空的时候画长破折号不画 0：0 是「确实没有未定价的部分」，破折号是
// 「这一格不适用」。两者混在一起的话，「订阅那条通路的未定价是 0」会被读成订阅定价了。
const props = withDefaults(
  defineProps<{
    title: string
    /** 表底那句口径。**不省略**：这三列的含义不写出来，读者会把「算不出价」当成「没花钱」。 */
    note: string
    rows: { label: string; value: number; cost: string; unpriced: string }[]
    loading?: boolean
  }>(),
  { loading: false }
)

const { t } = useI18n()

const empty = computed(() => props.rows.length === 0)

const max = computed(() => Math.max(1, ...props.rows.map((row) => row.value)))

function widthOf(value: number): string {
  return `${Math.max(2, (value / max.value) * 100)}%`
}
</script>

<template>
  <div class="abt">
    <div class="abt__head">
      <span class="abt__title t-eyebrow-read">{{ title }}</span>
    </div>

    <div v-if="loading" class="abt__skeleton">
      <v-skeleton-loader type="text" class="abt__skel abt__skel--title" />
      <v-skeleton-loader type="image" class="abt__skel abt__skel--plot" />
    </div>

    <p v-else-if="empty" class="abt__none">
      <span class="abt__none-title">{{ t('feedback.dashboard.empty.title') }}</span>
      <span class="abt__none-desc">{{ t('feedback.dashboard.empty.desc') }}</span>
    </p>

    <template v-else>
      <ol class="abt__rows">
        <li v-for="(row, i) in rows" :key="i" class="abt__row">
          <span class="abt__label" :title="row.label">{{ row.label }}</span>
          <span class="abt__track" aria-hidden="true">
            <span class="abt__bar" :style="{ width: widthOf(row.value) }" />
          </span>
          <span class="abt__num t-num">{{ row.cost }}</span>
          <span class="abt__num abt__num--muted t-num">{{ row.unpriced || '—' }}</span>
        </li>
      </ol>
      <p class="abt__note t-meta-read">{{ note }}</p>
    </template>
  </div>
</template>

<style scoped>
.abt {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.abt__title {
  color: var(--muted);
}

.abt__rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.abt__row {
  display: grid;
  grid-template-columns: minmax(72px, 1.1fr) minmax(48px, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  font-size: 12.5px;
  line-height: var(--lh-12);
}

.abt__label {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.abt__track {
  display: block;
  height: 6px;
  overflow: hidden;
  background: var(--fill);
  border-radius: var(--radius-pill);
}

.abt__bar {
  display: block;
  height: 100%;
  background: var(--muted);
  border-radius: var(--radius-pill);
}

.abt__num {
  color: var(--ink);
  text-align: right;
  white-space: nowrap;
}

.abt__num--muted {
  color: var(--muted);
}

.abt__note {
  margin: 0;
  color: var(--faint);
  line-height: var(--lh-12);
}

.abt__none {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
}

.abt__none-title {
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.abt__none-desc {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.abt__skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.abt__skel--title {
  width: 96px;
}

.abt__skel--plot {
  width: 100%;
}

.abt__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

.abt__skel--plot :deep(.v-skeleton-loader__image) {
  height: 120px;
  margin: 0;
  background: var(--fill-2);
}
</style>
