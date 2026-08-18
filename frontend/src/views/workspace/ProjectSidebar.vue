<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import TopicSidebar from '@/components/TopicSidebar.vue'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

// 项目侧栏, rendered through the app-wide `sidebar` named view so it survives
// every navigation inside the project (ProjectShell's doc comment says why).
//
// Its one rule: EVERY click here is a route change in the content area. Nothing
// in this file opens something "in place" — that split (some rows swapped the
// panel, others pushed a full page and took the sidebar with them) was the
// reason a click's outcome was unpredictable.
defineOptions({ name: 'ProjectSidebar' })

// `page`: 手机上话题列表是页面栈的一层，占满内容区（由 WorkspaceEntry 挂起来）。
// 不带这个 prop 的那份是常驻侧栏——桌面才有，所以手机上它整个不渲染。
const props = defineProps<{ projectId: string; page?: boolean }>()
const { mdAndUp } = useDisplay()
const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()

// Active state is read off the URL, never off a local flag.
const activeTopicId = computed(() => (route.name === 'workspace-topic' ? String(route.params.topicId) : null))
const activeDmPeer = computed(() => (route.name === 'workspace-dm' ? String(route.params.peer) : null))
const activeDocs = computed(() => (route.name === 'project-docs' ? String(route.params.kind) : null))

function openTopic(topicId: string) {
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}
function openDm(peer: string) {
  void router.push({ name: 'workspace-dm', params: { projectId: props.projectId, peer } })
}
function openDocs(kind: string) {
  void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind } })
}

async function onCreateTopic(title: string) {
  const topic = await store.create(title)
  if (topic) openTopic(topic.id)
}

async function onSplitTopic(payload: { topicId: string; title: string }) {
  const sub = await store.split(payload.topicId, payload.title)
  if (sub) openTopic(sub.id)
}

// Archiving the topic you are looking at closes it — go back to the project
// root rather than leaving a frozen archive open in the content area.
async function onArchiveTopic(topicId: string) {
  await store.archive(topicId)
  if (activeTopicId.value === topicId) {
    void router.replace({ name: 'workspace-project', params: { projectId: props.projectId } })
  }
}
</script>

<template>
  <TopicSidebar
    v-if="page || mdAndUp"
    :page="page"
    :width="store.railWidth"
    :projects="store.projects"
    :selected-project-id="store.projectId"
    :topics="store.topics"
    :selected-topic-id="activeTopicId"
    :loading-topics="store.loadingTopics"
    :private-active="activeDmPeer === 'cheese'"
    :members="store.members"
    :me-handle="myHandle()"
    :active-peer="activeDmPeer === 'cheese' ? null : activeDmPeer"
    :active-docs="activeDocs"
    :unread-map="store.unreadMap"
    :private-unread-map="store.privateUnreadMap"
    @update:width="store.setRailWidth"
    @select-topic="openTopic"
    @select-private="openDm('cheese')"
    @select-peer-dm="openDm"
    @select-docs="openDocs"
    @archive-topic="onArchiveTopic"
    @unarchive-topic="store.unarchive"
    @rename-topic="(p) => store.renameTopic(p.id, p.title)"
    @create-topic="onCreateTopic"
    @split-topic="onSplitTopic"
  />
</template>
