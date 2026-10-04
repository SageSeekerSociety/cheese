<script setup lang="ts">
// 搜索结果页：命令面板里每类内容只列前几条，这一页看全。
//
// 词和看哪一栏都在地址上（`?q=…&kind=…`），刷新、后退、把链接发给别人都回到同一处。
// 「全部」一栏每类列前几条，和面板一样；点进某一栏就是那一类的全部，滚到底接着加载。
// 每一行长什么样、点开去哪，和面板用的是同一份（views/workspace/search/*.palette.ts）。
import type { PaletteItem } from '@/commands/palette/sources'
import type { ContentKind } from '@/views/workspace/search/projectSearch'

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { searchProject, searchProjectCounted } from '@/api'
import { firstWord } from '@/commands/palette/results'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'
import { docs } from '@/views/workspace/search/docs.palette'
import { library } from '@/views/workspace/search/library.palette'
import { messages } from '@/views/workspace/search/messages.palette'
import { projectDocs } from '@/views/workspace/search/projectDocs.palette'
import { tasks } from '@/views/workspace/search/tasks.palette'

defineOptions({ name: 'ProjectSearchView' })

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const router = useRouter()
// 手机上带着词进来时不自动聚焦：键盘一弹就把结果挡住了。
const { mdAndUp } = useDisplay()

const KINDS: ContentKind[] = [messages, tasks, docs, projectDocs, library]
const PREVIEW = 5
const PAGE = 20

const query = computed(() => (typeof route.query.q === 'string' ? route.query.q.trim() : ''))
const kind = computed(() => KINDS.find((k) => k.id === route.query.kind) ?? null)

// ---- 输入：停一下再改地址，地址一变下面重新搜 ---------------------------------

const draft = ref(query.value)
watch(query, (next) => {
  if (next !== draft.value.trim()) draft.value = next
})
let typing: ReturnType<typeof setTimeout> | undefined
watch(draft, (next) => {
  clearTimeout(typing)
  typing = setTimeout(() => {
    const q = next.trim()
    if (q === query.value) return
    void router.replace({ query: { ...route.query, q: q || undefined } })
  }, 300)
})
onBeforeUnmount(() => clearTimeout(typing))

function openKind(next: unknown) {
  const id = String(next)
  void router.replace({ query: { ...route.query, kind: id === 'all' ? undefined : id } })
}

// ---- 每一栏有多少 ------------------------------------------------------------

const counts = ref<Record<string, number> | null>(null)
const countOf = (k: ContentKind) =>
  counts.value ? k.only.reduce((sum, key) => sum + (counts.value![key] ?? 0), 0) : null
const total = computed(() => (counts.value ? KINDS.reduce((sum, k) => sum + (countOf(k) ?? 0), 0) : null))

// ---- 结果 --------------------------------------------------------------------

const preview = ref<{ kind: ContentKind; items: PaletteItem[] }[]>([])
const items = ref<PaletteItem[]>([])
const loading = ref(false)
const failed = ref(false)
// 滚到底加载下一页失败：已经到手的那一段不能扔，只在它下面就地报错 + 重试
// ——整页换成一个错误会把已经看到的结果也弄没。
const moreFailed = ref(false)
// 服务端那句真实原因（初始和翻页共用，任一时刻只有一个块会显示它）。
const errorReason = ref('')
const exhausted = ref(false)
// 词或栏换了，还在路上的那一次回来时不认。
let asked = 0

async function load() {
  const ask = ++asked
  preview.value = []
  items.value = []
  exhausted.value = false
  failed.value = false
  moreFailed.value = false
  errorReason.value = ''
  counts.value = null
  if (!query.value) return
  loading.value = true
  try {
    // 第一页和每一类的条数一次问完。
    const { hits, counts: found } = kind.value
      ? await searchProjectCounted(props.projectId, query.value, PAGE, { only: kind.value.only, offset: 0 })
      : await searchProjectCounted(props.projectId, query.value, PREVIEW)
    if (ask !== asked) return
    counts.value = found
    if (kind.value) {
      items.value = kind.value.itemsOf(hits, props.projectId, router)
      exhausted.value = items.value.length < PAGE
    } else {
      preview.value = KINDS.map((k) => ({ kind: k, items: k.itemsOf(hits, props.projectId, router) })).filter(
        (group) => group.items.length
      )
    }
  } catch (e) {
    if (ask === asked) {
      failed.value = true
      errorReason.value = e instanceof Error ? e.message : ''
    }
  } finally {
    if (ask === asked) loading.value = false
  }
}

async function loadMore() {
  const current = kind.value
  if (!current || loading.value || exhausted.value || failed.value) return
  const ask = asked
  loading.value = true
  moreFailed.value = false
  try {
    const hits = await searchProject(props.projectId, query.value, PAGE, {
      only: current.only,
      offset: items.value.length,
    })
    if (ask !== asked) return
    const more = current.itemsOf(hits, props.projectId, router)
    items.value = [...items.value, ...more]
    exhausted.value = more.length < PAGE
  } catch (e) {
    if (ask === asked) {
      moreFailed.value = true
      errorReason.value = e instanceof Error ? e.message : ''
    }
  } finally {
    if (ask === asked) loading.value = false
  }
}

// 「暂无结果」下面的一键清除：清掉搜索词，回到还没输的状态（§8.1）。
function clearQuery() {
  draft.value = ''
  void router.replace({ query: { ...route.query, q: undefined } })
}

watch([() => props.projectId, query, kind], () => void load(), { immediate: true })

// 滚到列表底下那一截露出来，接着加载下一页。
const sentinel = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | undefined
watch(sentinel, (el) => {
  observer?.disconnect()
  if (!el || typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) void loadMore()
  })
  observer.observe(el)
})
onBeforeUnmount(() => observer?.disconnect())

const sections = computed(() =>
  kind.value ? (items.value.length ? [{ kind: kind.value, items: items.value }] : []) : preview.value
)

function segments(item: PaletteItem): { text: string; hit: boolean }[] {
  const range = firstWord(item.title, query.value)
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
        @update:model-value="openKind"
      >
        <v-tab value="all" class="text-none">
          {{ t('navigation.search.all') }}<span v-if="total !== null" class="search-tabs__count">{{ total }}</span>
        </v-tab>
        <v-tab v-for="k in KINDS" :key="k.id" :value="k.id" class="text-none">
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
        @retry="load"
      />
      <BaseEmptyState
        v-else-if="query && !loading && !items.length && !preview.length"
        size="inline"
        align="center"
        class="search-page__empty"
        :title="t('navigation.palette.empty')"
        :action="t('navigation.search.clear')"
        @action="clearQuery"
      />

      <!-- 「全部」每类一段，段头带「查看全部」；某一栏就是一段，没有段头。 -->
      <section v-for="group in sections" :key="group.kind.id" class="search-group">
        <div v-if="!kind" class="search-group__head">
          <h2 class="t-eyebrow-read">{{ t(group.kind.label) }}</h2>
          <button
            v-if="(countOf(group.kind) ?? 0) > group.items.length"
            type="button"
            class="search-group__more t-meta"
            @click="openKind(group.kind.id)"
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
        @retry="loadMore"
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
