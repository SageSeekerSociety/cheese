<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import TopicSidebar from '@/components/TopicSidebar.vue'
import { cancelPrefetch, prefetchOnHover } from '@/lib/routePrefetch'
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
const activeDocs = computed(() => (route.name === 'project-docs' ? String(route.params.kind) : null))

function openTopic(topicId: string) {
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}
function openDocs(kind: string) {
  void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind } })
}

// 指针停在一行上时，把点下去之后要等的两段先走掉：这个页面的代码，和这个话题最新
// 那一页消息。走的是同一个 openTopic 的落点，所以预热的和点开的永远是同一个东西。
function onHoverTopic(topicId: string) {
  prefetchOnHover({
    router,
    to: { name: 'workspace-topic', params: { projectId: props.projectId, topicId } },
    topicId,
  })
}

async function onCreateTopic(title: string) {
  const topic = await store.create(title)
  if (topic) openTopic(topic.id)
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
  <!-- 私聊不在这里了：名册和它的未读都归成员页，侧栏只在「成员」那一行上挂一个
       未读总数（privateUnreadMap 传的就是给它算总数用的）。 -->
  <!-- 进不来的时候侧栏整个不渲染。留着它，非成员看到的是一份点得动的目录——包括
       一颗「＋新建话题」，按下去只会撞一个 403。说明那一屏已经说了他该干什么，
       旁边不该再摆一排他做不到的事。左边那条项目 rail 不在这个组件里，所以「离开
       这里」的路还在。 -->
  <TopicSidebar
    v-if="!store.accessDenied && (page || mdAndUp)"
    :page="page"
    :width="store.railWidth"
    :projects="store.projects"
    :selected-project-id="store.projectId"
    :topics="store.topics"
    :selected-topic-id="activeTopicId"
    :loading-topics="store.loadingTopics"
    :active-docs="activeDocs"
    :unread-map="store.unreadMap"
    :private-unread-map="store.privateUnreadMap"
    @update:width="store.setRailWidth"
    @select-topic="openTopic"
    @hover-topic="onHoverTopic"
    @leave-topic="cancelPrefetch"
    @select-docs="openDocs"
    @archive-topic="onArchiveTopic"
    @unarchive-topic="store.unarchive"
    @rename-topic="(p) => store.renameTopic(p.id, p.title)"
    @create-topic="onCreateTopic"
  />
</template>
