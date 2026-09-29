<script setup lang="ts">
import { computed } from 'vue'

import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import { fmtNum } from '@/lib/usageFormat'

// 一次提问花了多少 token —— **分布的形状**，不是又一个平均数。
//
// 为什么不能只有平均值：平均 3,000 token 既可能是「大家都问三千」，也可能是「九成
// 的人问三百、一成的人问三万」，而这两件事对「要不要给问芝士换个更小的模型」是相反
// 的答案。中位数和 p90 能说出这两种情况的区别，一张直方图直接**看得出**它。
//
// **横排一行一桶，不做竖向柱**。这一版和 `AdminBarChart` 选横排的理由是同一条
// （见它的文件头）：桶的区间是文字（`1,200–2,400`），横排时它是最左边一列真文本，
// 读屏读得到、长了可以省略号；竖排时它得挤在柱子底下转 45°，字号压到读不清，而
// 「哪根柱子对应哪个区间」这件事就没有别的信号了。分布的形状横过来看一样在：一根根
// 条的长度连起来就是那条曲线。
//
// 数字用 `fmtNum` 全值不用 SI 缩写：桶的边界是**精确**的读法（「2,400 到 3,600」），
// 缩写成「2.4k」在相邻两桶只差一点时会撞成同一个标签。
defineOptions({ name: 'AdminHistogram' })

const props = withDefaults(
  defineProps<{
    title: string
    /** 桶，按区间自小到大。`to` 是**开区间上界**，最后一桶含上界（见后端 `stats.histogram`）。 */
    rows: { from: number; to: number; count: number }[]
    /** 口径注。**不省略**：这张图每根条的长度是「有多少条提问落在这个区间」。 */
    note: string
    /** 每根条右边那个数的单位（「条」）。 */
    unit?: string
    loading?: boolean
  }>(),
  { unit: '', loading: false }
)

const empty = computed(() => props.rows.length === 0)

/** 条的相对长度。全空桶时取 1（否则每根都算成 NaN）。**下限 2%**：一个非零的桶
 *  必须看得见 —— 桶里有数和无桶是两件事。 */
const max = computed(() => Math.max(1, ...props.rows.map((row) => row.count)))

function widthOf(count: number): string {
  return count === 0 ? '0%' : `${Math.max(2, (count / max.value) * 100)}%`
}

/** 桶的区间标签。四舍五入到整数：token 是整数量，边界出现 `.5` 只会让人以为有半
 *  个 token 这种东西。 */
function rangeOf(row: { from: number; to: number }): string {
  return `${fmtNum(Math.round(row.from))}–${fmtNum(Math.round(row.to))}`
}
</script>

<template>
  <section class="ahist">
    <header class="ahist__head">
      <h2 class="ahist__title">{{ title }}</h2>
      <AdminNoteTip :text="note" />
    </header>

    <div v-if="loading" class="ahist__bones">
      <span v-for="n in 5" :key="n" class="ahist__bone" />
    </div>

    <p v-else-if="empty" class="ahist__none t-meta-read">—</p>

    <div v-else class="ahist__rows">
      <div v-for="row in rows" :key="rangeOf(row)" class="ahist__row">
        <span class="ahist__range t-num">{{ rangeOf(row) }}</span>
        <span class="ahist__track">
          <span class="ahist__bar" :style="{ width: widthOf(row.count) }" />
        </span>
        <span class="ahist__count t-num">{{ fmtNum(row.count) }}{{ unit }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ahist {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ahist__head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.ahist__title {
  margin: 0;
  color: var(--ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: var(--lh-13);
}

.ahist__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 三列：区间（定宽，桶与桶对齐）、轨道、条数。区间那一列定宽而不是 auto —— 十个桶
   的标签宽度本来就一样（同宽的数），定宽只是省掉一次对齐计算，也让轨道左缘齐平。 */
.ahist__row {
  display: grid;
  grid-template-columns: 124px minmax(0, 1fr) 72px;
  gap: 10px;
  align-items: center;
}

.ahist__range {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  text-align: right;
  white-space: nowrap;
}

.ahist__track {
  display: block;
  height: 10px;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}

.ahist__bar {
  display: block;
  height: 100%;
  background: var(--ink);
  border-radius: var(--radius-sm);
}

.ahist__count {
  color: var(--text);
  font-size: 12.5px;
  text-align: right;
  white-space: nowrap;
}

.ahist__none {
  margin: 0;
  color: var(--muted);
}

.ahist__bones {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ahist__bone {
  height: 10px;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}

/* 窄屏（容器 560 以下）把区间那一列压到 92px：三列在这个宽度里塞得下，只是标签短
   一点，条的长度还是相对量、不受影响。 */
@container (max-width: 559px) {
  .ahist__row {
    grid-template-columns: 92px minmax(0, 1fr) 60px;
  }
}
</style>
