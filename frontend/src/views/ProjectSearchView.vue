<script setup lang="ts">
// 搜索结果页的容器：命令面板里每类内容只列前几条，这一页看全。
//
// 词和看哪一栏都在地址上（`?q=…&kind=…`），刷新、后退、把链接发给别人都回到同一处。
// 「全部」一栏每类列前几条，和面板一样；点进某一栏就是那一类的全部，滚到底接着加载。
// 每一行长什么样、点开去哪，和面板用的是同一份（views/workspace/search/*.palette.ts）。
// 画面在 ProjectSearchViewView.vue：只收 props、只发事件。
import type { PaletteItem } from '@/commands/palette/sources'
import type { ContentKind } from '@/views/workspace/search/projectSearch'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ProjectSearchViewView from './ProjectSearchViewView.vue'

import { searchProject, searchProjectCounted } from '@/api'
import { docs } from '@/views/workspace/search/docs.palette'
import { library } from '@/views/workspace/search/library.palette'
import { messages } from '@/views/workspace/search/messages.palette'
import { projectDocs } from '@/views/workspace/search/projectDocs.palette'
import { tasks } from '@/views/workspace/search/tasks.palette'

defineOptions({ name: 'ProjectSearchView' })

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const router = useRouter()

const KINDS: ContentKind[] = [messages, tasks, docs, projectDocs, library]
const PREVIEW = 5
const PAGE = 20

const query = computed(() => (typeof route.query.q === 'string' ? route.query.q.trim() : ''))
const kind = computed(() => KINDS.find((k) => k.id === route.query.kind) ?? null)

// ---- 地址：输入框停一下（在画面里）后抛上来的词、点了哪一栏，都改地址 -------------

function onQueryChange(next: string) {
  void router.replace({ query: { ...route.query, q: next || undefined } })
}

function openKind(id: string) {
  void router.replace({ query: { ...route.query, kind: id === 'all' ? undefined : id } })
}

// ---- 每一栏有多少 ------------------------------------------------------------

const counts = ref<Record<string, number> | null>(null)

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

// 「暂无结果」下面的一键清除：清掉搜索词，回到还没输的状态（§8.1）。输入框由画面清。
function clearQuery() {
  void router.replace({ query: { ...route.query, q: undefined } })
}

watch([() => props.projectId, query, kind], () => void load(), { immediate: true })

const sections = computed(() =>
  kind.value ? (items.value.length ? [{ kind: kind.value, items: items.value }] : []) : preview.value
)
</script>

<template>
  <ProjectSearchViewView
    :query="query"
    :kinds="KINDS"
    :kind-id="kind?.id ?? null"
    :counts="counts"
    :sections="sections"
    :loading="loading"
    :failed="failed"
    :more-failed="moreFailed"
    :error-reason="errorReason"
    :exhausted="exhausted"
    @query-change="onQueryChange"
    @select-kind="openKind"
    @clear-query="clearQuery"
    @load="load"
    @load-more="loadMore"
  />
</template>
