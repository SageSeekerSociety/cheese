<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtNum } from '@/lib/usageFormat'

// 看板上的「最花 token 的项目」。
//
// 这一张一度是**竖向柱状图**（手写 SVG，项目名横排在每根柱子底下）。那一版在真浏览器里
// 是坏的：名字是长短不一的中文，每个名字只分到约 65px 的一格，字号压到 9px 之后仍然
// **相邻重叠**（量到过 9×9 像素；1100px 宽下 26×17）。名字横着排不下这件事，换个方向
// 就没了：
//
//   * **横向**：名字在左（可以长、装不下走省略号并挂 `title`），条在中间，数值在最右。
//   * **排行**：这一份数据本来就是 top-N，**顺序本身是信息**，横排正好把它读出来。
//   * 名字和数值都用真尺寸（12–13px），不为塞下而缩字号。
//
// 于是它也不再需要「只有 ≤12 行才画标签」那条妥协，也不再需要一张折叠的数据表孪生体：
// 这一版的**每一行本身就是文字**（名字和数值都是真文本，条是装饰），读屏直接读得到 ——
// 上一版整块 SVG 是 `aria-hidden`，数据只能从那张表里拿。
//
// 手写 DOM 而不是 SVG：这一版没有任何需要按坐标算的东西（条长就是百分比），而 DOM 里的
// 文字可以省略号、可以换行、可以被读屏读到，SVG 里的 `<text>` 三样都做不到。
//
// 行可以带 `to`（top_projects 下钻到项目页）：**整行一个链接** —— 行内再摆第二
// 个可点的东西，一行就有了两个 Tab 站。没有 `to` 的行就是静的（cursor/hover/Tab
// 三样一样都不给，同 `AdminKpiCard` 文件头那条纪律）。
const props = withDefaults(
  defineProps<{
    title: string
    rows: {
      /** 行的**身份**（key 用它不用索引 —— 名字不唯一，两个同名项目不能合成一行）。 */
      id?: string
      label: string
      value: number
      /** 有去向整行才是链接（见文件头）。 */
      to?: RouteLocationRaw
    }[]
    loading?: boolean
  }>(),
  { loading: false }
)

const { t } = useI18n()

const empty = computed(() => props.rows.length === 0)

/** 条的相对长度。全 0 时取 1（否则每一根都算成 NaN）。 */
const max = computed(() => Math.max(1, ...props.rows.map((row) => row.value)))

/** 条占轨道的百分比。**下限 2%**：一个 0（或者极小值）会让「这一项在榜上」这件事在
 *  屏幕上消失，而它确实在 —— 榜上最后一名和「没有这一项」是两件事。 */
function widthOf(value: number): string {
  return `${Math.max(2, (value / max.value) * 100)}%`
}
</script>

<template>
  <div class="abr">
    <div class="abr__head">
      <span class="abr__title t-eyebrow-read">{{ title }}</span>
    </div>

    <div v-if="loading" class="abr__skeleton">
      <v-skeleton-loader type="text" class="abr__skel abr__skel--title" />
      <v-skeleton-loader type="image" class="abr__skel abr__skel--plot" />
    </div>

    <p v-else-if="empty" class="abr__none">
      <span class="abr__none-title">{{ t('feedback.dashboard.empty.title') }}</span>
      <span class="abr__none-desc">{{ t('feedback.dashboard.empty.desc') }}</span>
    </p>

    <ol v-else class="abr__rows">
      <li v-for="(row, i) in rows" :key="row.id ?? i" class="abr__item">
        <!-- 名字可能很长（项目名是用户起的）。省略号 + `title`：屏幕上先保住「这是哪几个
             项目」的可扫读性，完整名字鼠标停一下就有，读屏念的是全文。 -->
        <router-link v-if="row.to" :to="row.to" class="abr__row abr__row--link">
          <span class="abr__label" :title="row.label">{{ row.label }}</span>
          <span class="abr__track" aria-hidden="true">
            <span class="abr__bar" :style="{ width: widthOf(row.value) }" />
          </span>
          <span class="abr__value t-num">{{ fmtNum(row.value) }}</span>
        </router-link>
        <div v-else class="abr__row">
          <span class="abr__label" :title="row.label">{{ row.label }}</span>
          <span class="abr__track" aria-hidden="true">
            <span class="abr__bar" :style="{ width: widthOf(row.value) }" />
          </span>
          <span class="abr__value t-num">{{ fmtNum(row.value) }}</span>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.abr {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.abr__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.abr__item {
  min-width: 0;
}

/* 每一行：名字 | 轨道 | 数值。三列用 grid 而不是 flex：名字和数值都要能对齐成一条竖线，
   而 flex 的 `auto` 宽度会让每一行各算一遍，长名字那一行的数值就跑到别处去了。 */
.abr__row {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  height: 22px;
  color: inherit;
  text-decoration: none;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

/* 整行是链接才有的三件事（cursor/hover/焦点环）；hover 只改底色、不改位置，
   焦点环收进 -2px —— 行高只有 22px，外扩的焦点环会画到隔壁行上。 */
.abr__row--link {
  cursor: pointer;
  transition: background-color 0.12s ease;
}

@media (hover: hover) and (pointer: fine) {
  .abr__row--link:hover {
    background: var(--fill);
  }
}

.abr__row--link:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.abr__label {
  min-width: 0;
  overflow: hidden;
  font-size: 12.5px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 轨道只画底色，不画刻度：这一栏要比的是条与条的长短，不是绝对值（绝对值在右边）。 */
.abr__track {
  display: block;
  min-width: 0;
  height: 10px;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  overflow: hidden;
}

.abr__bar {
  display: block;
  height: 100%;
  background: var(--text);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

/* 数值右对齐、等宽字：一列数字竖着看要对得上位。 */
.abr__value {
  font-size: 12.5px;
  line-height: var(--lh-12);
  color: var(--ink);
  text-align: right;
  white-space: nowrap;
}

.abr__none {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
}

.abr__none-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.abr__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.abr__skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.abr__skel--title {
  width: 96px;
}

.abr__skel--plot {
  width: 100%;
}

/* `v-skeleton-loader` 的骨头默认带 16px 外边距和 12px 高，在一张 16px 内边距的卡里会
   把两条挤成一条；尺寸改成本地的。底色 `--fill-2` 是 §7.1 给骨架条指定的那一档，
   Vuetify 默认的 `--v-theme-on-surface` 在深浅两个主题里都不是这一档。 */
.abr__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

.abr__skel--plot :deep(.v-skeleton-loader__image) {
  height: 180px;
  margin: 0;
  background: var(--fill-2);
}
</style>
