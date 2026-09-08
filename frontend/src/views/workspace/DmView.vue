<script setup lang="ts">
import type { Topic } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getPrivateChat } from '@/api'
import ChatPanel from '@/components/ChatPanel.vue'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

// 私聊 (飞书私聊): a normal 1:1 chat in the content area — no document, no PR
// header, no accept box. `peer` is the URL's own word for who you are talking
// to: the literal `cheese` for the 芝士 DM, otherwise a teammate's handle. It
// lives in the address bar so a DM can be refreshed, shared and navigated back
// to like anything else; it used to exist only as a component ref, which is why
// reloading the page dropped you back into a topic.
defineOptions({ name: 'DmView' })

const props = defineProps<{ projectId: string; peer: string }>()
const router = useRouter()
const store = useWorkspaceStore()

const CHEESE = 'cheese'
const peerHandle = computed<string | null>(() => (props.peer === CHEESE ? null : props.peer))

const topic = ref<Topic | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

// Header label: the peer's name for a person DM, else 芝士.
const title = computed<string>(() => {
  if (peerHandle.value === null) return '芝士'
  const m = store.members.find((x) => x.user_handle === peerHandle.value)
  return m?.name || peerHandle.value
})

async function load() {
  const pid = props.projectId
  const peer = peerHandle.value
  const me = myHandle()
  loading.value = true
  error.value = null
  topic.value = null
  try {
    const fetched = await getPrivateChat(pid, me, peer ?? undefined)
    if (props.projectId !== pid || peerHandle.value !== peer) return
    topic.value = fetched
    // Opening a DM = reading it — without this its badge lights up and never clears.
    store.markDmRead(fetched.id, props.peer)
  } catch (e) {
    if (props.projectId === pid && peerHandle.value === peer) {
      error.value = e instanceof Error ? e.message : '打开私聊失败'
    }
  } finally {
    if (props.projectId === pid && peerHandle.value === peer) loading.value = false
  }
}

watch(() => [props.projectId, props.peer], load, { immediate: true })

function handleTurnDone() {
  void store.refreshTopics()
  if (topic.value) store.markDmRead(topic.value.id, props.peer)
  void store.refreshUnread()
}

function handleStateChanged(resource: string) {
  if (resource === 'topics') void store.refreshTopics()
}

// 私聊是从名册点进来的，所以 ← 回名册。手机上顶栏那颗 ← 走的是路由 meta 的
// backTo，两边指的是同一个地方。
function backToMembers() {
  void router.push({ name: 'project-members', params: { projectId: props.projectId } })
}

function openTopic(topicId: string) {
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

function handleMentionClick(handle: string) {
  void router.push({ name: 'member', params: { projectId: props.projectId, handle } })
}

function handleOpenResource(resource: string) {
  if (resource === 'decision') {
    void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind: 'decisions' } })
  } else if (resource === 'milestone') {
    void router.push({ name: 'calendar', params: { projectId: props.projectId } })
  }
}

async function handleUpgradeMessage(messageId: string) {
  const upgraded = await store.upgradeMessage(messageId)
  if (upgraded) openTopic(upgraded.id)
}
</script>

<template>
  <div class="dm-view d-flex flex-column fill-height" style="min-width: 0">
    <!-- 取会话这一步不画加载态。私聊要等两次：先拿到这个会话，再拿它的历史；
         头和输入框是 ChatPanel 带进来的，所以画在这里的骨架站的是头将要占的位置，
         等 ChatPanel 一进来就被顶下去——正是这条骨架要消灭的那种跳动。历史那一段
         由 ChatPanel 自己的骨架接手，它在消息真正会出现的地方。 -->
    <v-alert v-if="error" type="error" density="comfortable" class="ma-3">
      {{ error }}
    </v-alert>
    <ChatPanel
      v-else-if="topic"
      class="flex-grow-1"
      style="min-height: 0"
      :topic="topic"
      :pr-header="false"
      :always-summon="peerHandle === null"
      :title-override="title"
      back-label="成员"
      :members="store.members"
      :topic-list="store.topics"
      :show-composer="true"
      @back="backToMembers"
      @turn-done="handleTurnDone"
      @state-changed="handleStateChanged"
      @mention-click="handleMentionClick"
      @open-resource="handleOpenResource"
      @upgrade-message="handleUpgradeMessage"
      @open-topic="openTopic"
    />
  </div>
</template>

<style scoped>
.dm-view {
  flex: 1 1 auto;
}
</style>
