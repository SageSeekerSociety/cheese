<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import type { FeedbackStatus } from '@/cx_types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

// 看板右侧的迷你列表（§4.2 的 416px 卡、§5.3 的 28px 行）。
//
// **整行是一个 `<a>`，行里没有第二个可聚焦的东西** —— 这不是顺手这么写的，是 28px
// 这条行高能立住的前提：行内再放一个按钮（比如「指派」），它的可点区域最小 32px，
// 会把行顶到 32 以上，整张列表的行高就不再是 28（§5.3 把这件事写成了前提，
// §14 第 27 条会量）。所以推进状态那一类操作只出现在队列页，不在这里。
//
// 28px = 19（13px / `--lh-13` 的行盒）+ 4×2（上下内边距）+ 1（`--line` 分隔线）。
// 差 1px 就是一行之差 × 10，一屏看得见的东西就少一档。
//
// 焦点环画在行自己身上、`outline-offset: -2px`：行与行之间只有一条发丝线，环画在外面
// 会压进上下两行里（和 `.fb-row` 同一个理由）。
//
// 状态用 `statusMeta` 的 `label` + `ink`，不从这一层另写一份词表：状态名和颜色各只有
// 一处定义（`lib/feedbackMeta.ts`），加一个状态是那里改一行的事。

const props = withDefaults(
  defineProps<{
    /** 「需处理 · 10」这一类区块标题。 */
    title: string
    rows: { id: string; no: number; title: string; status: FeedbackStatus; updatedAt: string }[]
    /** 第 11 行「查看全部 →」。不传就不画那一行。 */
    moreTo?: RouteLocationRaw
    loading?: boolean
  }>(),
  { moreTo: undefined, loading: false }
)

const { t } = useI18n()

/** 只画 10 行。第 11 行的位置留给「查看全部」——它是**出口**，不是第 11 条数据。 */
const SHOWN = 10

const shown = computed(() =>
  props.rows.slice(0, SHOWN).map((row) => ({
    id: row.id,
    no: row.no,
    title: row.title,
    label: statusMeta(row.status).label,
    ink: statusMeta(row.status).ink,
    time: relTime(row.updatedAt),
    to: { name: 'FeedbackDetail', params: { id: row.id } } as RouteLocationRaw,
  }))
)
</script>

<template>
  <div class="anl">
    <div class="anl__head">
      <span class="t-eyebrow-read">{{ title }}</span>
    </div>

    <template v-if="loading">
      <div v-for="i in SHOWN" :key="`skel-${i}`" class="anl__row anl__row--skel">
        <v-skeleton-loader type="text" class="anl__skel anl__skel--no" />
        <v-skeleton-loader type="text" class="anl__skel anl__skel--title" />
        <v-skeleton-loader type="text" class="anl__skel anl__skel--status" />
        <v-skeleton-loader type="text" class="anl__skel anl__skel--time" />
      </div>
    </template>

    <p v-else-if="shown.length === 0" class="anl__none">
      <span class="anl__none-title">{{ t('feedback.dashboard.empty.title') }}</span>
      <span class="anl__none-desc">{{ t('feedback.dashboard.empty.desc') }}</span>
    </p>

    <template v-else>
      <router-link v-for="row in shown" :key="row.id" class="anl__row" :to="row.to">
        <span class="anl__no t-num">#{{ row.no }}</span>
        <span class="anl__title">{{ row.title }}</span>
        <span class="anl__status" :style="{ color: row.ink }">{{ row.label }}</span>
        <span class="anl__time t-num">{{ row.time }}</span>
      </router-link>

      <router-link v-if="moreTo" class="anl__row anl__more" :to="moreTo">
        <span class="anl__more-text">
          {{ t('feedback.numberList.viewAll') }}
          <span class="anl__arrow" aria-hidden="true">→</span>
        </span>
      </router-link>
    </template>
  </div>
</template>

<style scoped>
/* `overflow: hidden` 是给行底色用的：行 hover 时铺满整行，不裁的话方角会盖住卡片的
   圆角。这一层没有 sticky 表头，所以不像 `AdminGrid` 那样必须避开它。 */
.anl {
  display: flex;
  flex-direction: column;
  width: 100%;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.anl__head {
  display: flex;
  align-items: center;
  min-height: 32px;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
}

/* 列宽写在这里、只有一份：编号 64 / 标题 1fr / 状态 96 / 更新时间 72（§5.3）。
   标题那一列用 `minmax(0, 1fr)` 而不是 `1fr` —— 后者在内容超长时不收缩，整张表会被
   标题撑宽、其余三列被挤走。 */
.anl__row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 96px 72px;
  align-items: center;
  box-sizing: border-box;
  height: 28px;
  padding: 4px 12px;
  color: var(--text);
  text-decoration: none;
  border-bottom: 1px solid var(--line);
  transition: background-color 0.12s ease;
}

.anl__row:last-child {
  border-bottom: 0;
}

/* hover 只换底色、不位移（`.claude/rules/frontend.md`）。包在 `(hover: hover)` 里：
   触屏点过之后 `:hover` 会一直粘着，那一行看着像被选中了。 */
@media (hover: hover) and (pointer: fine) {
  .anl__row:hover {
    background: var(--fill);
  }
}

.anl__row:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.anl__no {
  font-size: 12.5px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.anl__title {
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 颜色由行数据给（`statusMeta(status).ink`），这里只管字形。 */
.anl__status {
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.anl__time {
  font-size: 12.5px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-align: right;
}

.anl__more {
  justify-content: center;
}

.anl__more-text {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.anl__arrow {
  color: var(--muted);
}

.anl__none {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 16px 12px;
}

.anl__none-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.anl__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* 骨架走和真行同一套 grid 与同一个 28px 高：数据到货那一刻整张列表不重排，骨架存在的
   全部意义就是这个（§14 第 11 条量的是这个差值）。 */
.anl__row--skel {
  cursor: default;
}

.anl__skel--no {
  width: 40px;
}

.anl__skel--status {
  width: 56px;
}

.anl__skel--time {
  width: 40px;
}

.anl__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}
</style>
