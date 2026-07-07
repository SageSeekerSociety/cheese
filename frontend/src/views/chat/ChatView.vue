<!-- 知是 2.0 聊天页（全局，与项目无关）。左栏是真实会话列表（GET /connector/threads）：
     群显示群名+图标、私聊显示对方昵称、都带 last_message 预览。选中驱动中间会话与右侧
     群设置抽屉。右上「新建群聊」= POST /connector/threads。UI 里没有 project_id。
     顶部一个「邀请」入口聚合 GET /connector/thread-applications 的待审批项。 -->
<template>
  <div class="chat-shell">
    <aside class="chat-list">
      <div class="chat-list__hd">
        <span>聊天</span>
        <v-spacer />
        <v-btn
          v-if="applications.length"
          size="small"
          variant="text"
          color="primary"
          class="chat-list__inv"
          @click="invitesOpen = true"
        >
          <v-badge :content="applications.length" color="error" inline>邀请</v-badge>
        </v-btn>
        <v-btn icon="mdi-plus" size="small" variant="text" title="新建群聊" @click="newOpen = true" />
      </div>

      <div class="chat-list__scroll">
        <div
          v-for="t in threads"
          :key="t.id"
          class="chat-list__item"
          :class="{ 'chat-list__item--active': t.id === activeThreadId }"
          @click="select(t.id)"
        >
          <div class="chat-list__ic" :class="{ 'chat-list__ic--dm': t.kind === 1 }">
            <v-icon :icon="t.kind === 1 ? 'mdi-account' : 'mdi-account-group'" size="20" />
          </div>
          <div class="chat-list__meta">
            <div class="chat-list__title">{{ titleOf(t) }}</div>
            <div class="chat-list__sub">{{ t.last_message?.text || '暂无消息' }}</div>
          </div>
        </div>
        <div v-if="!threads.length" class="chat-list__empty">还没有会话，点右上「＋」新建群聊。</div>
      </div>
    </aside>

    <ProjectChatView v-if="activeThreadId" :thread-id="activeThreadId" :thread="activeThread" />
    <div v-else class="chat-blank text-medium-emphasis">选择或新建一个会话开始聊天。</div>

    <GroupSidePanel
      :thread-id="activeThreadId"
      :thread="activeThread"
      @renamed="onRenamed"
      @dissolved="onDissolved"
    />

    <!-- 新建群聊 -->
    <v-dialog v-model="newOpen" width="420">
      <v-card>
        <v-card-title>新建群聊</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="newTitle"
            label="群名称"
            variant="outlined"
            density="comfortable"
            autofocus
            hide-details
            @keydown.enter.prevent="createGroup"
          />
          <div v-if="newError" class="chat-err">{{ newError }}</div>
          <div class="chat-hint">创建后你是群主，进群后可在「群设置」里拉人 / 拉 agent。</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="newOpen = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="creating" :disabled="!newTitle.trim()" @click="createGroup">
            创建
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 待审批邀请 -->
    <v-dialog v-model="invitesOpen" width="460">
      <v-card>
        <v-card-title>待处理邀请</v-card-title>
        <v-card-text>
          <div v-for="a in applications" :key="a.id" class="chat-inv">
            <div class="chat-inv__meta">
              <div class="chat-inv__title">
                {{ a.type === 'INVITATION' ? '邀请' : '申请' }}「{{ a.user.nickname }}」加入
                {{ a.thread_title || '群聊' }}
              </div>
              <div v-if="a.message" class="chat-inv__msg">{{ a.message }}</div>
            </div>
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" :loading="acting === a.id" @click="approve(a.id)">
              同意
            </v-btn>
            <v-btn size="small" variant="text" :loading="acting === a.id" @click="reject(a.id)">拒绝</v-btn>
          </div>
          <div v-if="!applications.length" class="chat-list__empty">没有待处理的邀请。</div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import type { Thread, ThreadApplication } from '@/network/api/threads'
import { ThreadsApi, threadDisplayTitle } from '@/network/api/threads'
import { currentUserId } from '@/services/account'

import GroupSidePanel from '../workspace/components/GroupSidePanel.vue'
import ProjectChatView from '../workspace/components/ProjectChatView.vue'

const threads = ref<Thread[]>([])
const activeThreadId = ref<number | null>(null)
const applications = ref<ThreadApplication[]>([])

const activeThread = computed(() => threads.value.find((t) => t.id === activeThreadId.value) ?? null)

function titleOf(t: Thread): string {
  return threadDisplayTitle(t, currentUserId.value ?? null)
}

function select(id: number): void {
  activeThreadId.value = id
}

async function loadThreads(): Promise<void> {
  try {
    const { threads: fresh } = await ThreadsApi.listThreads()
    threads.value = fresh
    if (activeThreadId.value == null && fresh.length) activeThreadId.value = fresh[0].id
    else if (activeThreadId.value != null && !fresh.some((t) => t.id === activeThreadId.value)) {
      activeThreadId.value = fresh[0]?.id ?? null
    }
  } catch {
    /* transient */
  }
}

async function loadApplications(): Promise<void> {
  try {
    const { applications: fresh } = await ThreadsApi.listApplications()
    applications.value = fresh
  } catch {
    /* transient */
  }
}

// ── new group ──
const newOpen = ref(false)
const newTitle = ref('')
const creating = ref(false)
const newError = ref('')
async function createGroup(): Promise<void> {
  const title = newTitle.value.trim()
  if (!title) return
  creating.value = true
  newError.value = ''
  try {
    const { thread } = await ThreadsApi.createThread({ title })
    newOpen.value = false
    newTitle.value = ''
    await loadThreads()
    activeThreadId.value = thread.id
  } catch (e) {
    newError.value = (e as Error).message
  } finally {
    creating.value = false
  }
}

// ── invites ──
const invitesOpen = ref(false)
const acting = ref<number | null>(null)
async function approve(id: number): Promise<void> {
  acting.value = id
  try {
    await ThreadsApi.approveApplication(id)
    await Promise.all([loadApplications(), loadThreads()])
  } finally {
    acting.value = null
  }
}
async function reject(id: number): Promise<void> {
  acting.value = id
  try {
    await ThreadsApi.rejectApplication(id)
    await loadApplications()
  } finally {
    acting.value = null
  }
}

function onDissolved(tid: number): void {
  threads.value = threads.value.filter((t) => t.id !== tid)
  if (activeThreadId.value === tid) {
    activeThreadId.value = threads.value[0]?.id ?? null
  }
  void loadThreads()
}

function onRenamed(thread: Thread): void {
  const idx = threads.value.findIndex((t) => t.id === thread.id)
  if (idx >= 0) threads.value[idx] = { ...threads.value[idx], ...thread }
}

let timer = 0
onMounted(() => {
  void loadThreads()
  void loadApplications()
  timer = window.setInterval(() => {
    void loadThreads()
    void loadApplications()
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.chat-shell {
  display: flex;
  height: 100%;
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}
.chat-list {
  width: 280px;
  flex: none;
  border-right: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  display: flex;
  flex-direction: column;
}
.chat-list__hd {
  display: flex;
  align-items: center;
  padding: 10px 8px 8px 16px;
  font-weight: 700;
  font-size: 15px;
}
.chat-list__inv {
  min-width: 0;
}
.chat-list__scroll {
  flex: 1;
  overflow-y: auto;
}
.chat-list__item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
}
.chat-list__item:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.chat-list__item--active {
  background: rgba(var(--v-theme-primary), 0.1);
}
.chat-list__ic {
  width: 40px;
  height: 40px;
  flex: none;
  border-radius: 10px;
  background: rgba(var(--v-theme-primary), 0.14);
  color: rgb(var(--v-theme-primary));
  display: flex;
  align-items: center;
  justify-content: center;
}
.chat-list__ic--dm {
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.1);
  color: rgb(var(--v-theme-on-surface));
}
.chat-list__meta {
  min-width: 0;
  flex: 1;
}
.chat-list__title {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-list__sub {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-list__empty {
  padding: 16px;
  font-size: 13px;
  opacity: 0.55;
}
.chat-blank {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
}
.chat-err {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin-top: 8px;
}
.chat-hint {
  font-size: 12px;
  opacity: 0.6;
  margin-top: 10px;
}
.chat-inv {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.chat-inv__meta {
  min-width: 0;
}
.chat-inv__title {
  font-size: 14px;
}
.chat-inv__msg {
  font-size: 12px;
  opacity: 0.6;
}
</style>
