<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { showsTopicList, useWorkspaceLayout } from '@/composables/useWorkspaceLayout'

import { useCommands } from '@/commands'
import TopicSidebar from '@/components/TopicSidebar.vue'
import { t } from '@/i18n'
import { cancelPrefetch, prefetchNow, prefetchOnHover } from '@/lib/routePrefetch'
import { useWorkspaceStore } from '@/stores/workspace'
import BoardSummary from '@/views/workspace/BoardSummary.vue'
import SplitListColumn from '@/views/workspace/SplitListColumn.vue'

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
const layout = useWorkspaceLayout()

// 两栏（平板）时，常驻的这一份在列表和房间那两层画成左边一栏：它挂在项目框的
// sidebar 视图上，换话题时不卸载，只有右边的房间在换。别的层（看板、文档、设置）
// 仍是一整页，这一栏不画。
const column = computed(
  () => !props.page && layout.value === 'split' && showsTopicList(route.name) && !store.accessDenied
)

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

// 按下去到松开、路由真的跳过去之间还有几十到一百多毫秒；触屏上没有「停住」这回事，
// 这是唯一的提前量。
function onPressTopic(topicId: string) {
  prefetchNow({
    router,
    to: { name: 'workspace-topic', params: { projectId: props.projectId, topicId } },
    topicId,
  })
}

const creatingTopic = ref(false)
async function onCreateTopic(title: string) {
  if (creatingTopic.value) return
  creatingTopic.value = true
  // Fetch the room view while the server creates the room.
  void import('./TopicView.vue').catch(() => {})
  try {
    const topic = await store.create(title)
    if (topic)
      await router.push({ name: 'workspace-topic', params: { projectId: topic.project_id, topicId: topic.id } })
  } finally {
    creatingTopic.value = false
  }
}

// 新建话题和侧栏上那颗 ＋ 是同一件事。
useCommands(() => [
  {
    id: 'topic.new',
    title: t('navigation.palette.newTopic'),
    icon: 'mdi-plus',
    disabled: creatingTopic.value,
    run: () => void onCreateTopic(''),
  },
])
</script>

<template>
  <!-- 私聊不在这里了：名册和它的未读都归成员页，侧栏只在「成员」那一行上挂一个
       未读总数（privateUnreadMap 传的就是给它算总数用的）。 -->
  <!-- 进不来的时候侧栏整个不渲染。留着它，非成员看到的是一份点得动的目录——包括
       一颗「＋新建话题」，按下去只会撞一个 403。说明那一屏已经说了他该干什么，
       旁边不该再摆一排他做不到的事。左边那条项目 rail 不在这个组件里，所以「离开
       这里」的路还在。 -->
  <SplitListColumn :active="column">
    <TopicSidebar
      v-if="!store.accessDenied && (page || column || mdAndUp)"
      :page="page || column"
      :column="column"
      :projects="store.projects"
      :selected-project-id="store.projectId"
      :topics="store.topics"
      :selected-topic-id="activeTopicId"
      :loading-topics="store.loadingTopics"
      :creating-topic="creatingTopic"
      :active-docs="activeDocs"
      :unread-map="store.unreadMap"
      :private-unread-map="store.privateUnreadMap"
      @select-topic="openTopic"
      @hover-topic="onHoverTopic"
      @press-topic="onPressTopic"
      @leave-topic="cancelPrefetch"
      @select-docs="openDocs"
      @unarchive-topic="store.unarchive"
      @rename-topic="(p) => store.renameTopic(p.id, p.title)"
      @create-topic="onCreateTopic"
    >
      <!-- 手机上进项目落在话题列表上而不是看板上，所以看板的一句话摘要放在列表最顶上，
           点下去是看板。桌面上项目名那一行就是看板的入口。 -->
      <template v-if="page || column" #top>
        <BoardSummary :project-id="projectId" />
      </template>
    </TopicSidebar>
  </SplitListColumn>
</template>
