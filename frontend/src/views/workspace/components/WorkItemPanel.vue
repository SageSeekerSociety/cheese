<!-- Context pane: a work item. Shows the lock model directly — once owned, the
     title/description are frozen and the only action is appending an annotation
     (which notifies the owner). Claim takes the exclusive lock. -->
<template>
  <div v-if="detail" class="wa-wi">
    <div class="wa-wi__head">
      <v-icon :icon="statusIcon" :color="statusColor" class="mr-2" />
      <span class="wa-wi__title">{{ item.title }}</span>
      <v-chip v-if="item.locked" size="x-small" color="grey" variant="tonal" prepend-icon="mdi-lock" class="ml-2">已锁定</v-chip>
    </div>

    <p class="wa-wi__desc">{{ item.description || '（无描述）' }}</p>

    <div class="wa-wi__meta">
      <template v-if="item.owner">
        <AgentAvatar :user="item.owner" :size="24" @open-scene="ws.openScene" />
        <span class="ml-2">{{ item.owner.nickname }} 负责</span>
      </template>
      <span v-else class="text-medium-emphasis">未认领</span>
      <v-spacer />
      <v-select
        v-if="isOwner"
        :model-value="item.status"
        :items="statusItems"
        density="compact"
        variant="outlined"
        hide-details
        style="max-width: 150px"
        @update:model-value="changeStatus"
      />
      <v-btn v-else-if="!item.owner" color="primary" size="small" prepend-icon="mdi-hand-back-right" @click="claim">认领</v-btn>
    </div>

    <v-divider class="my-3" />

    <div class="wa-wi__ann-title text-subtitle-2">附加注记 ({{ detail.annotations.length }})</div>
    <div class="wa-wi__anns">
      <div v-for="a in detail.annotations" :key="a.id" class="wa-wi__ann">
        <AgentAvatar :user="a.author" :size="24" @open-scene="ws.openScene" />
        <div class="wa-wi__ann-body">
          <div class="wa-wi__ann-meta">
            <span class="wa-wi__ann-name">{{ a.author.nickname }}</span>
            <span class="wa-wi__ann-time">{{ fmt(a.createdAt) }}</span>
          </div>
          <div class="wa-wi__ann-text">{{ a.content }}</div>
        </div>
      </div>
      <div v-if="!detail.annotations.length" class="text-caption text-medium-emphasis">还没有注记</div>
    </div>

    <div class="wa-wi__composer">
      <v-text-field
        v-model="note"
        placeholder="添加注记（会通知负责人）…"
        density="comfortable"
        variant="solo-filled"
        flat
        hide-details
        append-inner-icon="mdi-send"
        @keydown.enter.prevent="addNote"
        @click:append-inner="addNote"
      />
    </div>
  </div>
  <div v-else class="wa-wi__loading"><v-progress-circular indeterminate color="primary" /></div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { WorkItemDetail, WorkItemStatus } from '@/network/api/workspace'
import { WorkspaceApi } from '@/network/api/workspace'
import { currentUserId } from '@/services/account'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'

const props = defineProps<{ workItemId: number }>()
const ws = useWorkspace()

const detail = ref<WorkItemDetail | null>(null)
const note = ref('')

const item = computed(() => detail.value!.workItem)
const isOwner = computed(() => detail.value?.workItem.owner?.id === currentUserId.value)

const statusItems = [
  { title: '待处理', value: 'open' },
  { title: '进行中', value: 'in_progress' },
  { title: '已完成', value: 'done' },
  { title: '受阻', value: 'blocked' },
]
const statusIcon = computed(() => {
  const s = item.value.status
  return s === 'done' ? 'mdi-check-circle' : s === 'in_progress' ? 'mdi-progress-clock' : s === 'blocked' ? 'mdi-alert-circle-outline' : 'mdi-circle-outline'
})
const statusColor = computed(() => {
  const s = item.value.status
  return s === 'done' ? 'success' : s === 'in_progress' ? 'primary' : s === 'blocked' ? 'error' : 'grey'
})

async function load() {
  detail.value = null
  const res = await WorkspaceApi.getWorkItem(props.workItemId)
  detail.value = res.data
}

async function claim() {
  await WorkspaceApi.claimWorkItem(props.workItemId)
  await load()
  ws.notifyContextChanged()
}

async function changeStatus(status: WorkItemStatus) {
  await WorkspaceApi.updateStatus(props.workItemId, { status })
  await load()
  ws.notifyContextChanged()
}

async function addNote() {
  if (!note.value.trim()) return
  await WorkspaceApi.addAnnotation(props.workItemId, { content: note.value.trim() })
  note.value = ''
  await load()
  ws.notifyContextChanged()
}

function fmt(ts: number) {
  return new Date(ts).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

watch(() => props.workItemId, load, { immediate: true })
</script>

<style scoped>
.wa-wi {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
}
.wa-wi__head {
  display: flex;
  align-items: center;
}
.wa-wi__title {
  font-size: 16px;
  font-weight: 700;
}
.wa-wi__desc {
  margin: 10px 0;
  font-size: 14px;
  color: rgba(var(--v-theme-on-surface), 0.75);
  white-space: pre-wrap;
}
.wa-wi__meta {
  display: flex;
  align-items: center;
  font-size: 13px;
}
.wa-wi__ann-title {
  margin-bottom: 8px;
}
.wa-wi__anns {
  flex: 1;
  overflow-y: auto;
}
.wa-wi__ann {
  display: flex;
  gap: 8px;
  padding: 8px 0;
}
.wa-wi__ann-body {
  flex: 1;
}
.wa-wi__ann-meta {
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.wa-wi__ann-name {
  font-size: 13px;
  font-weight: 600;
}
.wa-wi__ann-time {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
.wa-wi__ann-text {
  font-size: 14px;
  white-space: pre-wrap;
}
.wa-wi__composer {
  padding-top: 8px;
}
.wa-wi__loading {
  display: flex;
  justify-content: center;
  padding: 40px;
}
</style>
