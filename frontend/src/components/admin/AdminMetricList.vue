<script setup lang="ts">
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'

// 「一组同量纲的数」的卡片：问芝士那一页的 token 分布与耗时分布都用它。
//
// **它不是 KPI 行。** `AdminKpiCard` 回答「这一格是多少」，几张卡彼此独立；这一版
// 回答的是「同一个量的五个位置」（平均 / 中位 / 最小 / p90 / 最大），五个数缺一个
// 就读不出来分布 —— 所以它们必须挨在一起、同一把尺子，而不是铺成五张卡让眼睛再去
// 把它们拼回来。
//
// **`value` 由调用方格式化好**（同 `AdminKpiCard`）：token 和毫秒是两套写法，一处
// 定义好过让这张卡去猜。**空串画长破折号，不画 0** —— 0 是「读出来了、确实是零」，
// 空串是「这个窗口里没有一条」，两者在这张卡上必须长得不一样（同 `AdminKpiCard`
// 文件头那条）。
//
// 行本身是文字（标签和值都是真文本），不需要折叠的数据表孪生体：这张卡**就是**那张表。
defineOptions({ name: 'AdminMetricList' })

withDefaults(
  defineProps<{
    title: string
    /** 已格式化的「N 条」这类副行，没有就不画。 */
    caption?: string
    rows: { label: string; value: string }[]
    /** 口径注。给了才在标题旁画 info tip。 */
    note?: string
    loading?: boolean
    /** 加粗「平均 / 中位」那一行 —— 一屏五个数里，这两个是复述时会用到的那两个。 */
    emphasis?: string
  }>(),
  { caption: undefined, note: undefined, loading: false, emphasis: undefined }
)

const shown = (value: string) => (value.trim() === '' ? '—' : value)
</script>

<template>
  <section class="amlist">
    <header class="amlist__head">
      <h2 class="amlist__title">{{ title }}</h2>
      <AdminNoteTip v-if="note" :text="note" />
      <span v-if="caption" class="amlist__caption t-meta-read">{{ caption }}</span>
    </header>

    <div v-if="loading" class="amlist__bones">
      <span v-for="n in 3" :key="n" class="amlist__bone" />
    </div>

    <dl v-else class="amlist__rows">
      <div
        v-for="row in rows"
        :key="row.label"
        class="amlist__row"
        :class="{ 'amlist__row--on': row.label === emphasis }"
      >
        <dt class="amlist__label">{{ row.label }}</dt>
        <dd class="amlist__value t-num">{{ shown(row.value) }}</dd>
      </div>
    </dl>
  </section>
</template>

<style scoped>
.amlist {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.amlist__head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.amlist__title {
  margin: 0;
  color: var(--ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: var(--lh-13);
}

/* 副行挤在标题后面（「共 1,204 条」），不和标题各占一行：它修饰的是标题那个量。 */
.amlist__caption {
  margin-left: auto;
  color: var(--muted);
}

.amlist__rows {
  display: flex;
  flex-direction: column;
  margin: 0;
}

/* 行高固定 28px（同 §5.3）：五个数一张卡，行高抖动会让最后一行落到卡外。 */
.amlist__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 5px 0;
  border-top: 1px solid var(--line-2);
}

.amlist__row:first-child {
  border-top: 0;
}

.amlist__label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12.5px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amlist__value {
  flex: 0 0 auto;
  color: var(--text);
  font-size: 13.5px;
}

.amlist__row--on .amlist__label,
.amlist__row--on .amlist__value {
  color: var(--ink);
  font-weight: 600;
}

.amlist__bones {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
}

/* 骨架的形状和真行一样（三段等高的灰条），到货那一刻不重排。 */
.amlist__bone {
  height: 12px;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}
</style>
