<script setup lang="ts">
/**
 * 后台「运行记录」（`/admin/run-records`）：平台的报错，和它在各个项目里自己处理掉的事。
 *
 * 这一层是容器：取数、跟着筛选重取、点开一种时取它的详情。画面在
 * `AdminRunRecordsPageView.vue`。
 */
import type {
  RunRecordDetail,
  RunRecordGroup,
  RunRecordGroupFilter,
  RunRecordOverview,
  RunRecordWindow,
} from '@/views/admin/runRecordsApi'

import { onMounted, ref, watch } from 'vue'

import AdminRunRecordsPageView from '@/views/admin/AdminRunRecordsPageView.vue'
import { getRunRecordDetail, getRunRecords } from '@/views/admin/runRecordsApi'

defineOptions({ name: 'AdminRunRecordsPage' })

const overview = ref<RunRecordOverview | null>(null)
const loading = ref(true)
const failed = ref(false)
const window = ref<RunRecordWindow>('24h')
const group = ref<RunRecordGroupFilter>('all')
const query = ref('')
const selected = ref<RunRecordGroup | null>(null)
const detail = ref<RunRecordDetail | null>(null)
const detailFailed = ref(false)

let generation = 0
async function load() {
  const mine = ++generation
  loading.value = true
  failed.value = false
  try {
    const next = await getRunRecords(window.value, group.value, query.value.trim())
    if (mine === generation) overview.value = next
  } catch {
    if (mine === generation) failed.value = true
  } finally {
    if (mine === generation) loading.value = false
  }
}

async function open(g: RunRecordGroup | null) {
  selected.value = g
  detail.value = null
  detailFailed.value = false
  if (!g) return
  try {
    const found = await getRunRecordDetail(g.key, g.kind, window.value)
    if (selected.value === g) detail.value = found
  } catch {
    if (selected.value === g) detailFailed.value = true
  }
}

let typing: ReturnType<typeof setTimeout> | undefined
watch([window, group], () => {
  selected.value = null
  void load()
})
watch(query, () => {
  clearTimeout(typing)
  typing = setTimeout(() => void load(), 300)
})
onMounted(load)
</script>

<template>
  <AdminRunRecordsPageView
    v-model:window="window"
    v-model:group="group"
    v-model:query="query"
    :overview="overview"
    :loading="loading"
    :failed="failed"
    :selected="selected ? `${selected.kind}:${selected.key}` : null"
    :detail="detail"
    :detail-failed="detailFailed"
    @select="open"
    @retry="selected ? open(selected) : load()"
  />
</template>
