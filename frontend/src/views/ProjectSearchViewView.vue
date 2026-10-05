<script setup lang="ts">
// 搜索结果页的画面：搜索框、栏目页签、每一段结果、底部的加载 / 空 / 出错三种状态。
// 取数、地址（`?q=…&kind=…`）和分页都在容器 `ProjectSearchView.vue` 里 —— 这一半只
// 收 props，只把「输入停了」「点了某一栏」「清掉词」「重试」「滚到底了」抛上去。
import type { PaletteItem } from '@/commands/palette/sources'
import type { ContentKind } from '@/views/workspace/search/projectSearch'

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { firstWord } from '@/commands/palette/results'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'

const props = defineProps<{
  query: string
  kinds: ContentKind[]
  /** 选中那一栏的 id；`null` 是「全部」。 */
  kindId: string | null
  counts: Record<string, number> | null
  sections: { kind: ContentKind; items: PaletteItem[] }[]
  loading: boolean
  failed: boolean
  moreFailed: boolean
  errorReason: string
  exhausted: boolean
}>()

const emit = defineEmits<{
  'query-change': [q: string]
  'select-kind': [id: string]
  'clear-query': []
  load: []
  'load-more': []
}>()

// 手机上带着词进来时不自动聚焦：键盘一弹就把结果挡住了。
const { mdAndUp } = useDisplay()

const kind = computed(() => props.kinds.find((k) => k.id === props.kindId) ?? null)
const countOf = (k: ContentKind) =>
  props.counts ? k.only.reduce((sum, key) => sum + (props.counts![key] ?? 0), 0) : null
const total = computed(() => (props.counts ? props.kinds.reduce((sum, k) => sum + (countOf(k) ?? 0), 0) : null))

// ---- 输入：停一下再让容器改地址，地址一变容器重新搜 ---------------------------
const draft = ref(props.query)
watch(
  () => props.query,
  (next) => {
    if (next !== draft.value.trim()) draft.value = next
  }
)
let typing: ReturnType<typeof setTimeout> | undefined
watch(draft, (next) => {
  clearTimeout(typing)
  typing = setTimeout(() => {
    const q = next.trim()
    if (q === props.query) return
    emit('query-change', q)
  }, 300)
})
onBeforeUnmount(() => clearTimeout(typing))

function clearQuery() {
  draft.value = ''
  emit('clear-query')
}

// ---- 滚到列表底下那一截露出来，让容器接着加载下一页 ---------------------------
const sentinel = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | undefined
watch(sentinel, (el) => {
  observer?.disconnect()
  if (!el || typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) emit('load-more')
  })
  observer.observe(el)
})
onBeforeUnmount(() => observer?.disconnect())

function segments(item: PaletteItem): { text: string; hit: boolean }[] {
  const range = firstWord(item.title, props.query)
  if (!range) return [{ text: item.title, hit: false }]
  const [from, to] = range
  return [
    { text: item.title.slice(0, from), hit: false },
    { text: item.title.slice(from, to), hit: true },
    { text: item.title.slice(to), hit: false },
  ].filter((part) => part.text)
}
</script>

<template>
  <AppPage :title="t('navigation.search.title')">
    <div class="search-page">
      <v-text-field
        v-model="draft"
        type="search"
        variant="outlined"
        density="comfortable"
        prepend-inner-icon="mdi-magnify"
        :aria-label="t('navigation.search.placeholder')"
        :placeholder="t('navigation.search.placeholder')"
        autocomplete="off"
        hide-details
        :autofocus="mdAndUp || !query"
      />

      <v-tabs
        v-if="query"
        :model-value="kind?.id ?? 'all'"
        density="compact"
        color="on-surface"
        slider-color="primary"
        show-arrows
        class="search-tabs"
        @update:model-value="(next) => $emit('select-kind', String(next))"
      >
        <v-tab value="all" class="text-none">
          {{ t('navigation.search.all') }}<span v-if="total !== null" class="search-tabs__count">{{ total }}</span>
        </v-tab>
        <v-tab v-for="k in kinds" :key="k.id" :value="k.id" class="text-none">
          {{ t(k.label) }}<span v-if="countOf(k) !== null" class="search-tabs__count">{{ countOf(k) }}</span>
        </v-tab>
      </v-tabs>

      <!-- Search failed to load: replace the results block in place with an error
           and a retry, not the whole page — the search box and tabs stay put
           (docs/design-system.md §3.10). -->
      <BaseLoadError
        v-if="failed"
        class="search-page__load-error"
        :title="t('navigation.search.failed')"
        :error="errorReason || null"
        @retry="$emit('load')"
      />
      <BaseEmptyState
        v-else-if="query && !loading && !sections.length"
        size="inline"
        align="center"
        class="search-page__empty"
        :title="t('navigation.palette.empty')"
        :action="t('navigation.search.clear')"
        @action="clearQuery"
      />

      <!-- In "all" mode each kind gets its own section with a "see all" head; a
           single-kind query is one section with no head. -->
      <section v-for="group in sections" :key="group.kind.id" class="search-group">
        <div v-if="!kind" class="search-group__head">
          <h2 class="t-eyebrow-read">{{ t(group.kind.label) }}</h2>
          <button
            v-if="(countOf(group.kind) ?? 0) > group.items.length"
            type="button"
            class="search-group__more t-meta"
            @click="$emit('select-kind', group.kind.id)"
          >
            {{ t('navigation.search.viewAll', { n: countOf(group.kind) }) }}
          </button>
        </div>
        <ul class="search-list">
          <li v-for="item in group.items" :key="item.id">
            <router-link :to="item.to!" class="search-row">
              <v-icon :icon="item.icon" size="18" class="search-row__icon" />
              <span class="search-row__text">
                <span class="search-row__title">
                  <template v-for="(part, p) in segments(item)" :key="p">
                    <mark v-if="part.hit">{{ part.text }}</mark>
                    <template v-else>{{ part.text }}</template>
                  </template>
                </span>
                <span v-if="item.subtitle" class="search-row__subtitle">{{ item.subtitle }}</span>
              </span>
              <span v-if="item.badge" class="search-row__badge">{{ item.badge.text }}</span>
            </router-link>
          </li>
        </ul>
      </section>
      <!-- Loading the next page failed: keep what we already have, append the
           error and a retry under it, and do not blank the page. -->
      <BaseLoadError
        v-if="moreFailed"
        class="search-page__load-error"
        :title="t('navigation.search.failed')"
        :error="errorReason || null"
        @retry="$emit('load-more')"
      />
      <div v-if="kind && !exhausted && !failed && !moreFailed" ref="sentinel" class="search-page__sentinel" />

      <div v-if="loading" class="d-flex justify-center py-6" role="status" :aria-label="t('navigation.search.loading')">
        <v-progress-circular indeterminate size="24" color="primary" />
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.search-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 32px;
}
.search-tabs {
  border-bottom: 1px solid var(--line);
}
.search-tabs__count {
  margin-left: 6px;
  color: var(--faint);
  font-size: 13px;
}
.search-page__empty {
  padding: 24px 0;
  text-align: center;
}
.search-page__load-error {
  padding: 8px 0;
}
.search-group__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 4px 0;
}
.search-group__head h2 {
  margin: 0;
}
.search-group__more {
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.search-group__more:hover {
  color: var(--ink);
}
.search-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.search-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 8px;
  border-bottom: 1px solid var(--line);
  color: var(--text);
  text-decoration: none;
}
.search-row:hover {
  background: var(--fill);
}
.search-row__icon {
  flex: none;
  margin-top: 1px;
  color: var(--muted);
}
.search-row__text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.search-row__title {
  overflow-wrap: anywhere;
}
.search-row__title mark {
  background: none;
  color: var(--accent-ink);
  font-weight: 600;
}
.search-row__subtitle {
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
}
.search-row__badge {
  flex: none;
  padding: 0 8px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.search-page__sentinel {
  height: 1px;
}
</style>
