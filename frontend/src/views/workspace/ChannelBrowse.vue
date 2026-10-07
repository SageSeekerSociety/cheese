<script setup lang="ts">
// 「浏览频道」这一页（`/projects/:projectId/channels`）：取项目的频道列表，加入、退出、
// 新建交给工作区 store，画法在 `ChannelBrowseView`。
import type { ChannelEntry } from '@/types/channelDirectory'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import ChannelBrowseView from './ChannelBrowseView.vue'

import { listChannels } from '@/api/channels'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const store = useWorkspaceStore()
const router = useRouter()

const channels = ref<ChannelEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const busyId = ref<string | null>(null)
const creating = ref(false)
const creatingOpen = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    channels.value = (await listChannels(props.projectId)).items
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.projectId,
  () => void load(),
  { immediate: true }
)

const canCreate = computed(() => !store.isExternal(myHandle()))

function open(id: string) {
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId: id } })
}

async function setJoined(id: string, joined: boolean) {
  busyId.value = id
  try {
    if (await store.setJoined(id, joined)) {
      channels.value = channels.value.map((c) => (c.id === id ? { ...c, joined } : c))
      void load()
    }
  } finally {
    busyId.value = null
  }
}

async function create(channel: { title: string; description: string; membersOnly: boolean }) {
  creating.value = true
  try {
    const topic = await store.create(channel.title, channel.description, channel.membersOnly)
    if (!topic) return
    creatingOpen.value = false
    open(topic.id)
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <ChannelBrowseView
    v-model:creating-open="creatingOpen"
    :channels="channels"
    :loading="loading"
    :error="error"
    :busy-id="busyId"
    :creating="creating"
    :can-create="canCreate"
    @open="open"
    @join="(id) => setJoined(id, true)"
    @leave="(id) => setJoined(id, false)"
    @create="create"
    @retry="load"
  />
</template>
