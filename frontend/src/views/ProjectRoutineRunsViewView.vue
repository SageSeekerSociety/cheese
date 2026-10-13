<script setup lang="ts">
// 执行记录那一页的画面：页头一句「哪条规则 · 共几次」，顶上一排筛子，中间一页 10 次
// 执行，底下一排翻页。取数、翻页、筛子的状态全在容器 `ProjectRoutineRunsView.vue` 里，
// 这一半只收 props、只把动作抛上去（docs/manual/dev/scenes.md）。
//
// 三颗筛子只回答三个问题：倒着看还是顺着看、只看没跑成的、看最近多久。每一颗都是「此刻
// 生效的那一个说法」，点开换一个 —— 换完回到第 1 页由容器做。时间那颗的标签随时说清哪一
// 段（「最近 30 天」），所以人不用点开就知道自己此刻在看什么。
import type { NavTarget } from '@/lib/navTarget'
import type { Routine, RoutineRun } from '@/lib/routine'

import { computed, ref, watch } from 'vue'

import AppPage from '@/components/common/AppPage.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import RoutineRunRow from '@/components/routine/RoutineRunRow.vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这条规则；还没读到时是 null（页头只剩「执行记录」）。 */
  routine: Routine | null
  runs: RoutineRun[]
  /** 当前这个筛选下一共几次，不是这一页几行。 */
  total: number
  page: number
  pageCount: number
  order: 'newest' | 'oldest'
  failedOnly: boolean
  /** 最近几天；null 是全部。 */
  days: number | null
  loading: boolean
  error: string
  /** 结果文件那一行的去处。给了就照它画（这一页没有工作面板，去了频道才点得开）。 */
  roomTo?: NavTarget
}>()

const emit = defineEmits<{
  'update:page': [page: number]
  'update:order': [order: 'newest' | 'oldest']
  'update:failed-only': [failedOnly: boolean]
  'update:days': [days: number | null]
  retry: []
}>()

/**
 * 页头那句：哪条规则、一共几次 —— 「共几次」是**当前这个筛选**下的（筛子和排序都在
 * 后端做，所以「最近 30 天」和「未能执行」会把这个数一起改小）。
 *
 * 规则名不是一句话，是一段游客数据：夹在翻译句子里会被词典的检查挡住，也不该翻。所以
 * 两句分开画，中间那点由这里加，两边都读得到。
 */
const meta = computed(() => (props.routine?.title ?? ''))
const countText = computed(() => t('routines.runs.count', { count: props.total }))

/** 倒着看：两颗菜单项，此刻生效的那一颗在自己名字前挂一颗勾。 */
const orderActions = computed(() => [
  {
    key: 'newest',
    label: props.order === 'newest' ? `✓ ${t('routines.runs.newest')}` : t('routines.runs.newest'),
    icon: 'mdi-sort-clock-descending-outline',
    onSelect: () => emit('update:order', 'newest'),
  },
  {
    key: 'oldest',
    label: props.order === 'oldest' ? `✓ ${t('routines.runs.oldest')}` : t('routines.runs.oldest'),
    icon: 'mdi-sort-clock-ascending-outline',
    onSelect: () => emit('update:order', 'oldest'),
  },
])

/** 最近多久：三颗菜单项，标签就是此刻生效的那一段（「最近 30 天」）。 */
const daysActions = computed(() => [
  { key: '7', label: t('routines.runs.days7'), icon: 'mdi-calendar-week-outline', onSelect: () => emit('update:days', 7) },
  { key: '30', label: t('routines.runs.days30'), icon: 'mdi-calendar-month-outline', onSelect: () => emit('update:days', 30) },
  { key: 'all', label: t('routines.runs.daysAll'), icon: 'mdi-calendar-blank-outline', onSelect: () => emit('update:days', null) },
])

/** 时间那颗按钮此刻写的字。 */
const daysLabel = computed(() =>
  props.days === 7 ? t('routines.runs.days7') : props.days === 30 ? t('routines.runs.days30') : t('routines.runs.daysAll')
)

/**
 * 还没到能画这一页的程度：第一次读（还没有记录可铺），或者规则还没读到 —— 每一行的
 * 时区和结果文件的去处都靠它。
 *
 * 翻页时不进这里：先把上一页留着、淡一点，比整页闪成一颗转圈强。
 */
const waiting = computed(() => (props.loading && !props.runs.length) || (props.runs.length > 0 && !props.routine))

/** 「第 N 页」那个数字：点进去改，回车或失焦跳过去。 */
const jump = ref('')
watch(
  () => props.page,
  (p) => (jump.value = String(p)),
  { immediate: true }
)

function submitJump() {
  const n = Number.parseInt(jump.value, 10)
  if (Number.isFinite(n)) emit('update:page', n)
  // 填了个读不出页码的东西（或者跟当前页一样）：退回原样，不跳。
  jump.value = String(props.page)
}
</script>

<template>
  <AppPage :title="t('routines.runs.title')">
    <template v-if="meta" #meta>
      {{ meta }}<span class="runs-head__sep" aria-hidden="true">·</span>{{ countText }}
    </template>

    <BaseLoadError v-if="error" :error="error" :title="t('routines.runsFailed')" @retry="emit('retry')" />

    <div class="runs-filter" role="group" :aria-label="t('routines.runs.filterLabel')">
      <AdaptiveMenu :actions="orderActions" :title="t('routines.runs.orderLabel')">
        <template #activator="{ props: menuProps }">
          <button v-bind="menuProps" type="button" class="runs-chip">
            {{ order === 'newest' ? t('routines.runs.newest') : t('routines.runs.oldest') }}
          </button>
        </template>
      </AdaptiveMenu>

      <button
        type="button"
        class="runs-chip"
        :class="{ 'runs-chip--on': failedOnly }"
        :aria-pressed="failedOnly"
        @click="emit('update:failed-only', !failedOnly)"
      >
        {{ t('routines.runs.failed') }}
      </button>

      <AdaptiveMenu :actions="daysActions" :title="t('routines.runs.daysLabel')">
        <template #activator="{ props: menuProps }">
          <button v-bind="menuProps" type="button" class="runs-chip">
            {{ daysLabel }} <span aria-hidden="true">▾</span>
          </button>
        </template>
      </AdaptiveMenu>
    </div>

    <div v-if="waiting" class="runs-loading" role="status" :aria-label="t('routines.runs.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <template v-else>
      <!-- 翻页时上一步那一页淡着留在这里，别让它跳一下。 -->
      <ol v-if="runs.length" class="runs-list" :class="{ 'runs-list--stale': loading }">
        <li v-for="run in runs" :key="run.id" class="runs-list__row">
          <RoutineRunRow :run="run" :timezone="routine?.timezone" :room-to="roomTo" />
        </li>
      </ol>

      <BaseEmptyState v-else size="inline" :title="t('routines.runs.empty')" />
    </template>

    <!-- 一页放得下就整条不画：只有一页时「上一页 / 下一页」两颗都是灰的，白占一行。 -->
    <div v-if="pageCount > 1" class="runs-pager">
      <BaseButton kind="secondary" size="sm" :disabled="page <= 1" @click="emit('update:page', page - 1)">
        {{ t('routines.runs.prev') }}
      </BaseButton>
      <BaseButton kind="secondary" size="sm" :disabled="page >= pageCount" @click="emit('update:page', page + 1)">
        {{ t('routines.runs.next') }}
      </BaseButton>
      <label class="runs-pager__jump">
        <i18n-t scope="global" keypath="routines.runs.page" tag="span" class="runs-pager__text">
          <template #page>
            <input
              v-model="jump"
              class="runs-pager__input"
              type="text"
              inputmode="numeric"
              :aria-label="t('routines.runs.jumpLabel')"
              @keyup.enter="submitJump"
              @blur="submitJump"
            />
          </template>
          <template #pages>{{ pageCount }}</template>
        </i18n-t>
      </label>
    </div>
  </AppPage>
</template>

<style scoped>
/* 页头那句里规则名和「共几次」中间的那一点。 */
.runs-head__sep {
  margin: 0 6px;
  color: var(--line);
}

/* 三颗筛子都是「此刻说的是这一种」：等宽的一行，手机上并排还有富余。 */
.runs-filter {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
.runs-chip {
  padding: 5px 9px;
  border: 1px solid var(--line);
  border-radius: var(--radius-pill, 999px);
  background: var(--surface);
  color: var(--muted);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition:
    color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.runs-chip:hover {
  color: var(--ink);
}
/* 生效的那一颗：描边和字换成琥珀。 */
.runs-chip--on {
  border-color: var(--accent);
  color: var(--accent-ink);
  font-weight: 600;
}

.runs-loading {
  padding: 32px 0;
  text-align: center;
}

/* 一次执行一行，行间一条细线：和面板里那半页同一个样子，只是这一页铺满一屏 10 行。 */
.runs-list {
  list-style: none;
  padding: 0;
  margin: 0;
  border-top: 1px solid var(--line);
}
.runs-list__row {
  min-height: 40px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
/* 下一页在路上：先淡一点，读数出来再回来。 */
.runs-list--stale {
  opacity: 0.5;
  transition: opacity var(--dur-quick) var(--ease-standard);
}

.runs-pager {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.runs-pager__jump {
  margin-inline-start: auto;
  color: var(--muted);
  font-size: 13px;
}
/* 「第 [1] / 共 5 页」里那个数字：一格能改的页码，点进去填了就跳。 */
.runs-pager__input {
  width: 3em;
  padding: 2px 4px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  text-align: center;
}
</style>
