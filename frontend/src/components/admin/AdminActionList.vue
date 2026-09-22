<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

// 「等你处理 / 卡住了」那一列。整行是一个目的地 —— 这一块的全部用处就是让人点进去。
//
// **一行只有一个焦点元素**（整行的 router-link）：行内再放一个「去处理」按钮会让一行
// 有两个 Tab 站，读屏要在同一行里听两遍同一个目的地（`AdminNumberList` 同一条纪律）。
//
// 空态是一句**邀请**（「今天没有卡住的事」），不是一句道歉。错误和空屏要说怎么恢复，
// 不是 mood（frontend-design 的 writing 那节）。
const props = withDefaults(
  defineProps<{
    title: string
    rows: {
      id: string
      title: string
      subtitle?: string
      statusLabel?: string
      /** 只有 `warn` / `danger` 会用到状态色；`ink` 是中性阶（默认）。 */
      tone?: 'ink' | 'ok' | 'warn' | 'danger'
      age?: string
      to: RouteLocationRaw
    }[]
    moreTo?: RouteLocationRaw
    loading?: boolean
    /** 空屏的那句邀请。**必填** —— 不写就只能画一个没有字的框。 */
    empty: string
  }>(),
  { loading: false }
)

const { t } = useI18n()
const SHOWN = 10
const shown = computed(() => props.rows.slice(0, SHOWN))
const rest = computed(() => Math.max(0, props.rows.length - SHOWN))
</script>

<template>
  <div class="aal">
    <div class="aal__head">
      <span class="aal__title t-eyebrow-read">{{ title }}</span>
      <span v-if="rows.length" class="aal__count t-meta-read">{{ rows.length }}</span>
    </div>

    <div v-if="loading" class="aal__skeleton">
      <v-skeleton-loader v-for="i in 3" :key="i" type="text" class="aal__skel" />
    </div>

    <p v-else-if="!rows.length" class="aal__none t-meta-read">{{ empty }}</p>

    <ol v-else class="aal__rows">
      <li v-for="row in shown" :key="row.id" class="aal__row">
        <router-link :to="row.to" class="aal__link">
          <span class="aal__bar" :class="`aal__bar--${row.tone ?? 'ink'}`" aria-hidden="true" />
          <span class="aal__body">
            <span class="aal__rowtitle t-body">{{ row.title }}</span>
            <span v-if="row.subtitle" class="aal__sub t-meta-read">{{ row.subtitle }}</span>
          </span>
          <span v-if="row.statusLabel" class="aal__status t-dense">{{ row.statusLabel }}</span>
          <span v-if="row.age" class="aal__age t-meta-read t-num">{{ row.age }}</span>
        </router-link>
      </li>
    </ol>

    <router-link v-if="moreTo && (rows.length || rest)" :to="moreTo" class="aal__more t-meta-read">
      {{ t('feedback.dashboard.list.all', { n: rows.length }) }}
    </router-link>
  </div>
</template>

<style scoped>
.aal {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.aal__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.aal__count {
  color: var(--faint);
}

.aal__rows {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.aal__link {
  display: grid;
  grid-template-columns: 3px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 8px 6px;
  border-radius: var(--radius-sm);
  text-decoration: none;
  color: inherit;
}

/* hover 只改底色，不改位置。按项目约定包在 (hover:hover) 里 —— 触屏不会留下
   粘住的「选中」观感。 */
@media (hover: hover) and (pointer: fine) {
  .aal__link:hover {
    background: var(--fill);
  }
}

.aal__bar {
  width: 3px;
  height: 22px;
  border-radius: 2px;
  background: var(--line-2);
}

/* 状态色只在 warn / danger 上出现（这是「要人动」的那一类）。ok/ink 走中性阶。 */
.aal__bar--warn {
  background: var(--warn);
}
.aal__bar--danger {
  background: var(--danger);
}

.aal__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}

.aal__rowtitle {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aal__sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aal__status {
  color: var(--muted);
}

.aal__age {
  color: var(--faint);
}

.aal__more {
  margin-top: 8px;
  color: var(--muted);
  text-decoration: none;
}

.aal__none {
  margin: 0;
  padding: 12px 6px;
  color: var(--muted);
}

.aal__skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.aal__skel {
  height: 28px;
}
</style>
