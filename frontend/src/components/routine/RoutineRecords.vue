<script setup lang="ts">
// 一条规则的执行记录，整页：行里摊不开的那几十次要翻着看。
//
// 和行里那三次（`RoutineRow`）是同一件事的两种密度：这里一次一页十条，能只留没能执行的
// 那几次，也能翻页。那三行的画法本来就短，抄在这里比抽一层共用组件便宜 —— 抽出来的人
// 得在两处之间帮衬一个「整页版 / 行内版」的开关，读的人要跳一次才知道这一行长什么样。
//
// 纯展示：记录从 props 进（谁来读、第几页、筛不筛都在 `useRoutineList` 里），动作从事件
// 出。`total` 是**筛过之后的全部**，所以「共 N 次」和页数都是后端说的，这里不自己数。
import type { NavTarget } from '@/lib/navTarget'
import type { RoutineRun } from '@/lib/routine'

import { computed, ref } from 'vue'

import NavLink from '../common/NavLink.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { creditsLabel, routineFileName, routineRunLabel, routineRunTime } from '@/lib/routine'

const props = withDefaults(
  defineProps<{
    /** 看的是哪一条：这一页的抬头写着「{名字} · 执行记录」。 */
    title: string
    runs?: RoutineRun[]
    /** 筛过之后一共几次。 */
    total?: number
    /** 这条规则**本月**的点数 —— 整条规则的，不是这一页的。翻到第几页都一样。 */
    creditsThisMonth?: number
    page?: number
    pages?: number
    /** 只留没能执行的那几次。 */
    failedOnly?: boolean
    loading?: boolean
    /** 结果文件那几行整串点开去哪儿。项目总览页给（指回规则所在的房间 —— 那一页上没有
     *  能开文件的工作面板）；房间面板里那一格不给，那里的每一份都当场点开。 */
    roomTo?: NavTarget
  }>(),
  {
    runs: () => [],
    total: 0,
    creditsThisMonth: 0,
    page: 1,
    pages: 1,
    failedOnly: false,
    loading: false,
    roomTo: undefined,
  }
)

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'page', page: number): void
  (e: 'failed-only', value: boolean): void
  (e: 'open-file', path: string): void
}>()

const missed = (run: RoutineRun) => run.status === 'failed' || run.status === 'skipped'

/** 「原因 ▸」：每次执行各自收放，和行里那三次一样。 */
const whyOpen = ref<Set<string>>(new Set())
function toggleWhy(run: RoutineRun) {
  const next = new Set(whyOpen.value)
  if (next.has(run.id)) next.delete(run.id)
  else next.add(run.id)
  whyOpen.value = next
}

/** 跳页那个小框：人打进来的数字，回车或离焦就跳。没在改的时候它显示的是当前第几页。 */
const jump = ref<number | null>(null)
const jumpShown = computed({
  get: () => jump.value ?? props.page,
  set: (value: number | null) => {
    jump.value = value
  },
})
function doJump() {
  const target = jump.value
  jump.value = null
  if (target) emit('page', target)
}
</script>

<template>
  <div class="records">
    <div class="records__head">
      <BaseButton
        kind="ghost"
        icon="mdi-arrow-left"
        size="sm"
        class="tap-target"
        :aria-label="t('routines.records.back')"
        @click="emit('back')"
      />
      <div class="records__id">
        <div class="t-body records__title">{{ t('routines.records.title', { title }) }}</div>
        <div class="t-meta c-faint">{{ t('routines.records.count', { n: total }) }}</div>
      </div>
    </div>

    <!-- 这条规则本月合计。摆在明细上面、抬头下面：它是这一整条规则的数，翻页不变，
         读的人先看到它再往下看是哪几次花的。 -->
    <p class="records__sum t-body" :title="t('routines.spend.hint')" data-testid="routine-records-spend">
      {{ t('routines.spend.recordsTotal', { points: creditsLabel(creditsThisMonth) }) }}
    </p>

    <button
      type="button"
      class="records__filter"
      :class="{ 'records__filter--on': failedOnly }"
      :aria-pressed="failedOnly ? 'true' : 'false'"
      @click="emit('failed-only', !failedOnly)"
    >
      {{ t('routines.records.missedOnly') }}
    </button>

    <div v-if="loading && !runs.length" class="records__loading" role="status" :aria-label="t('routines.loading')">
      <v-progress-circular indeterminate size="24" color="primary" />
    </div>
    <p v-else-if="!runs.length" class="t-meta c-faint records__none">
      {{ failedOnly ? t('routines.records.noneMissed') : t('routines.neverRun') }}
    </p>
    <ul v-else class="records__list">
      <li v-for="run in runs" :key="run.id" class="records__row">
        <span class="t-meta c-faint">{{ routineRunTime(run.scheduled_for || run.created_at) }}</span>
        <span class="records__what">
          <span class="t-body records__status">{{ routineRunLabel(run.status) }}</span>
          <span v-if="run.credits != null" class="t-meta c-faint records__spend" data-testid="routine-run-spend">
            {{ creditsLabel(run.credits) }}
          </span>
          <template v-if="missed(run)">
            <button
              type="button"
              class="records__why"
              :aria-expanded="whyOpen.has(run.id) ? 'true' : 'false'"
              @click="toggleWhy(run)"
            >
              {{ t('routines.records.reason') }}
              <span class="records__caret" aria-hidden="true">{{ whyOpen.has(run.id) ? '▾' : '▸' }}</span>
            </button>
            <span v-if="whyOpen.has(run.id)" class="t-meta c-danger">{{ run.error || t('routines.noReason') }}</span>
          </template>
          <template v-else-if="run.outputs.length">
            <NavLink v-if="roomTo" :to="roomTo" class="records__file">
              {{ run.outputs.map(routineFileName).join(t('routines.listSeparator')) }}
            </NavLink>
            <template v-else>
              <button
                v-for="path in run.outputs"
                :key="path"
                type="button"
                class="records__file"
                :title="path"
                @click="emit('open-file', path)"
              >
                {{ routineFileName(path) }}
              </button>
            </template>
          </template>
          <span v-else-if="run.summary" class="t-meta c-muted">{{ run.summary }}</span>
        </span>
      </li>
    </ul>

    <div class="records__pager">
      <BaseButton kind="ghost" size="sm" :disabled="page <= 1" @click="emit('page', page - 1)">
        {{ t('routines.records.prev') }}
      </BaseButton>
      <BaseButton kind="ghost" size="sm" :disabled="page >= pages" @click="emit('page', page + 1)">
        {{ t('routines.records.next') }}
      </BaseButton>
      <span class="t-meta c-faint records__page">
        {{ t('routines.records.pageBefore') }}
        <input
          v-model.number="jumpShown"
          type="number"
          inputmode="numeric"
          min="1"
          :max="pages"
          class="records__page-input"
          :aria-label="t('routines.records.pageInput')"
          @change="doJump"
          @blur="doJump"
        />
        {{ t('routines.records.pageAfter', { pages }) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.records__head {
  display: flex;
  align-items: center;
  gap: 4px;
}
.records__id {
  min-width: 0;
}
.records__title {
  color: var(--text);
}
/* 「只看未能执行」：一颗自己画的胶囊（选中了就上琥珀色），按下去就是筛。 */
.records__filter {
  margin-top: 12px;
  padding: 4px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: none;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
  transition:
    color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.records__filter--on {
  border-color: var(--accent);
  color: var(--accent-ink);
  background: var(--accent-wash);
}
.records__loading {
  padding: 24px 0;
  text-align: center;
}
.records__none {
  margin: 12px 0 0;
}
/* 两列：左边「哪一次的」，右边「那一次怎么了」。列宽由最长的那一格定（见 RoutineRow）。 */
.records__list {
  display: grid;
  grid-template-columns: max-content 1fr;
  align-items: baseline;
  gap: 12px 16px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.records__row {
  display: contents;
}
.records__what {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  min-width: 0;
}
.records__status {
  color: var(--text);
}
/* 这一整条规则本月合计的那一行：摆在明细上面，和数字一样对齐读。 */
.records__sum {
  margin: 12px 0 0;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
/* 一次执行花了多少点：和「已完成」并排，安静一档。 */
.records__spend {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.records__why {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
}
.records__why:hover {
  color: var(--ink);
}
.records__caret {
  font-size: 12px;
}
.records__file {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--accent);
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent) 40%, transparent);
  text-underline-offset: 3px;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.records__file:hover {
  color: var(--accent-ink);
}
.records__pager {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.records__page {
  margin-inline-start: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
/* 跳到第几页：一个小方框。数字右对齐，位数变了框不变。 */
.records__page-input {
  width: 48px;
  padding: 2px 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font: inherit;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  text-align: center;
}
</style>
