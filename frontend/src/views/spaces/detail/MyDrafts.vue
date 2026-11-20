<template>
  <MyDraftsView
    :drafts="drafts"
    :loading="loading"
    :failed="failed"
    :removing="removing"
    :publish-to="{ name: 'SpacesDetailPublishTask', params: { spaceId: String(spaceId) } }"
    @open="open"
    @remove="remove"
    @retry="load"
  />
</template>

<script setup lang="ts">
// 「我的草稿」页的那一层：读我在这个空间的草稿，删一份，打开一份（新题目在发布页接着写，
// 已发布题目的修改在改题页）。画在 MyDraftsView.vue。
import type { TaskDraftSummary } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import MyDraftsView from './MyDraftsView.vue'

import { TaskDraftsApi } from '@/api/taskDrafts'
import { toast } from '@/lib/toast'
import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const spaceStore = useSpaceStore()

const spaceId = computed(() => Number(route.params.spaceId))
const drafts = ref<TaskDraftSummary[]>([])
const loading = ref(true)
const failed = ref(false)
const removing = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    drafts.value = await TaskDraftsApi.list(spaceId.value)
    spaceStore.setMyDraftCount(drafts.value.length)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

function open(draft: TaskDraftSummary) {
  if (draft.taskId)
    void router.push({ name: 'TasksEdit', params: { spaceId: String(spaceId.value), taskId: String(draft.taskId) } })
  else
    void router.push({
      name: 'SpacesDetailPublishTask',
      params: { spaceId: String(spaceId.value) },
      query: { draft: String(draft.id) },
    })
}

async function remove(id: number) {
  removing.value = true
  try {
    await TaskDraftsApi.remove(id)
    drafts.value = drafts.value.filter((d) => d.id !== id)
    spaceStore.setMyDraftCount(drafts.value.length)
  } catch {
    toast.error(t('spaces.detail.publishTask.strip.removeFailed'))
  } finally {
    removing.value = false
  }
}

onMounted(load)
</script>
