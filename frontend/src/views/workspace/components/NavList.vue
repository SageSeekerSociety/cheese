<!-- The middle list column. Its contents follow the far-left rail:
     群聊 → threads (select loads the conversation),
     文档 → document tree (open in the context pane),
     事项 → work-item tree (open in the context pane). -->
<template>
  <div class="wa-list">
    <div class="wa-list__head">
      <span class="wa-list__title">{{ title }}</span>
      <v-spacer />
      <v-btn size="x-small" variant="text" icon="mdi-plus" :title="`新建`" @click="onCreate" />
    </div>

    <div class="wa-list__scroll">
      <!-- 群聊 -->
      <template v-if="ws.state.section === 'threads'">
        <div
          v-for="t in ws.state.threads"
          :key="t.id"
          class="wa-item"
          :class="{ 'wa-item--active': t.id === ws.state.activeThreadId }"
          @click="ws.selectThread(t.id)"
        >
          <v-icon :icon="t.kind === 'management' ? 'mdi-sitemap-outline' : 'mdi-forum-outline'" size="20" class="mr-2" />
          <div class="wa-item__main">
            <div class="wa-item__row">
              <span class="wa-item__name">{{ t.title }}</span>
              <span v-if="t.lastMessage" class="wa-item__time">{{ hm(t.lastMessage.createdAt) }}</span>
            </div>
            <div class="wa-item__sub">{{ t.lastMessage ? `${t.lastMessage.author.nickname}: ${t.lastMessage.content}` : '暂无消息' }}</div>
          </div>
          <v-badge v-if="t.unread" :content="t.unread" color="primary" inline />
        </div>
      </template>

      <!-- 文档 -->
      <template v-else-if="ws.state.section === 'documents'">
        <div
          v-for="d in ws.state.documents"
          :key="d.id"
          class="wa-item"
          :class="{ 'wa-item--active': isCtx('document', d.id), 'wa-item--child': d.parentId != null }"
          @click="ws.openContext({ type: 'document', id: d.id })"
        >
          <v-icon icon="mdi-file-document-outline" size="20" class="mr-2" color="indigo" />
          <div class="wa-item__main">
            <div class="wa-item__name">{{ d.title }}</div>
            <div class="wa-item__sub">{{ d.updatedBy ? `${d.updatedBy.nickname} 更新` : '' }}</div>
          </div>
        </div>
      </template>

      <!-- 事项 -->
      <template v-else>
        <div
          v-for="w in ws.state.workItems"
          :key="w.id"
          class="wa-item"
          :class="{ 'wa-item--active': isCtx('workitem', w.id), 'wa-item--child': w.parentId != null }"
          @click="ws.openContext({ type: 'workitem', id: w.id })"
        >
          <v-icon :icon="statusIcon(w.status)" size="20" class="mr-2" :color="statusColor(w.status)" />
          <div class="wa-item__main">
            <div class="wa-item__row">
              <span class="wa-item__name">{{ w.title }}</span>
              <v-icon v-if="w.locked" icon="mdi-lock" size="13" class="wa-item__lock" />
            </div>
            <div class="wa-item__sub">
              <AgentAvatar v-if="w.owner" :user="w.owner" :size="16" @open-scene="ws.openScene" />
              <span class="ml-1">{{ w.owner ? w.owner.nickname : '未认领' }}</span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { WorkItemStatus } from '@/network/api/workspace'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'

const ws = useWorkspace()

const title = computed(() =>
  ws.state.section === 'threads' ? '群聊' : ws.state.section === 'documents' ? '文档' : '事项'
)

function isCtx(type: 'document' | 'workitem', id: number) {
  return ws.state.context?.type === type && ws.state.context.id === id
}

function hm(ts: number) {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function statusIcon(s: WorkItemStatus) {
  return s === 'done'
    ? 'mdi-check-circle'
    : s === 'in_progress'
      ? 'mdi-progress-clock'
      : s === 'blocked'
        ? 'mdi-alert-circle-outline'
        : 'mdi-circle-outline'
}
function statusColor(s: WorkItemStatus) {
  return s === 'done' ? 'success' : s === 'in_progress' ? 'primary' : s === 'blocked' ? 'error' : 'grey'
}

async function onCreate() {
  const title = window.prompt(ws.state.section === 'threads' ? '新群聊标题' : ws.state.section === 'documents' ? '新文档标题' : '新事项标题')
  if (!title) return
  const { WorkspaceApi } = await import('@/network/api/workspace')
  const pid = ws.state.projectId
  if (ws.state.section === 'threads') {
    await WorkspaceApi.createThread(pid, { title })
    await ws.loadThreads()
  } else if (ws.state.section === 'documents') {
    const res = await WorkspaceApi.createDocument(pid, { title })
    await ws.refreshDocuments()
    ws.openContext({ type: 'document', id: res.data.document.id })
  } else {
    const res = await WorkspaceApi.createWorkItem(pid, { title })
    await ws.refreshWorkItems()
    ws.openContext({ type: 'workitem', id: res.data.workItem.id })
  }
}
</script>

<style scoped>
.wa-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-right: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-list__head {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-list__title {
  font-weight: 700;
  font-size: 15px;
}
.wa-list__scroll {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}
.wa-item {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 9px 10px;
  border-radius: 10px;
  cursor: pointer;
}
.wa-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.wa-item--active {
  background: rgb(var(--v-theme-primary), 0.12);
}
.wa-item--child {
  margin-left: 18px;
}
.wa-item__main {
  min-width: 0;
  flex: 1;
}
.wa-item__row {
  display: flex;
  align-items: center;
  gap: 4px;
}
.wa-item__name {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}
.wa-item__time {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
.wa-item__lock {
  color: rgba(var(--v-theme-on-surface), 0.4);
}
.wa-item__sub {
  display: flex;
  align-items: center;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
