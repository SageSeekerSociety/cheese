<script setup lang="ts">
import type { RoomTask } from '@/cx_types'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { showsTopicList, useWorkspaceLayout } from '@/composables/useWorkspaceLayout'

import { useCommands } from '@/commands'
import TopicSidebar from '@/components/TopicSidebar.vue'
import { t } from '@/i18n'
import { routeIds } from '@/lib/addresses'
import { readProjectTasks } from '@/lib/projectTasks'
import { railTasksByChannel } from '@/lib/railTasks'
import { cancelPrefetch, prefetchNow, prefetchOnHover } from '@/lib/routePrefetch'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
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
// sidebar 视图上，换话题时不卸载，只有右边的房间在换。别的层（总览、文档、设置）
// 仍是一整页，这一栏不画。
const column = computed(
  () => !props.page && layout.value === 'split' && showsTopicList(route.name) && !store.accessDenied
)

// Active state is read off the URL, never off a local flag.
const ids = computed(() => routeIds(route.params))
const activeTopicId = computed(() => (route.name === 'workspace-topic' ? ids.value.topicId ?? null : null))
const activeTaskId = computed(() => (route.name === 'workspace-task' ? ids.value.taskId ?? null : null))
// 「全部任务」带着频道筛选打开时，那个频道下面的「全部任务」一行是选中的。
const activeAllTasks = computed(() =>
  route.name === 'project-tasks' && typeof route.query.channel === 'string' ? route.query.channel : null
)

// 每个频道里和我有关的几条任务，挂在频道那一行下面（`lib/railTasks`）。和全部任务、项目
// 总览读同一份（`readProjectTasks`），它们刚读过就拿那一份。任务变了（新建、开始、关闭）
// 房间会收到通知（`store.tasksChanged`），那时立刻重读；换个页面不算变，30 秒内读过的
// 就用那一份 —— 这份清单是整个项目的任务，后端每读一次都要把它们全部算一遍。
const TASKS_REFRESH_MS = 30_000
const tasks = ref<RoomTask[]>([])
async function loadTasks(maxAgeMs?: number) {
  const pid = props.projectId
  try {
    const payload = await readProjectTasks(pid, { maxAgeMs, open: true })
    if (props.projectId === pid) tasks.value = payload.data
  } catch {
    // 留着上一次的那份。
  }
}
const railTasks = computed(() => railTasksByChannel(tasks.value, myHandle()))
const roomTasks = computed(() =>
  Object.fromEntries(Object.entries(railTasks.value).map(([channel, rail]) => [channel, rail.shown]))
)
const roomTaskTotals = computed(() =>
  Object.fromEntries(Object.entries(railTasks.value).map(([channel, rail]) => [channel, rail.total]))
)
let tasksTimer: number | undefined
// 看不见的标签页不读；回到前台时补一次。
function refreshTasksIfVisible() {
  if (document.visibilityState !== 'hidden') void loadTasks(TASKS_REFRESH_MS)
}
onMounted(() => {
  void loadTasks()
  tasksTimer = window.setInterval(refreshTasksIfVisible, TASKS_REFRESH_MS)
  document.addEventListener('visibilitychange', refreshTasksIfVisible)
})
onUnmounted(() => {
  window.clearInterval(tasksTimer)
  document.removeEventListener('visibilitychange', refreshTasksIfVisible)
})
watch(
  () => props.projectId,
  () => void loadTasks(2_000)
)
watch(
  () => route.fullPath,
  () => void loadTasks(TASKS_REFRESH_MS)
)
watch(
  () => store.tasksChanged,
  () => void loadTasks()
)
function openAllTasks(channelId: string) {
  void router.push({ name: 'project-tasks', params: { projectId: props.projectId }, query: { channel: channelId } })
}
function openOverview() {
  void router.push({ name: 'workspace-overview', params: { projectId: props.projectId } })
}
// 「浏览频道」：全部频道都在那一页，加入、退出、新建也在那里。
function browseChannels() {
  void router.push({ name: 'project-channels', params: { projectId: props.projectId } })
}
const browsingChannels = computed(() => route.name === 'project-channels')
function openTask(task: { roomId: string; taskId: string }) {
  void router.push({
    name: 'workspace-task',
    params: { projectId: props.projectId, topicId: task.roomId, taskId: task.taskId },
  })
}
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

// 鼠标按下去到松开、路由真的跳过去之间还有几十到一百多毫秒；手快的人停不满 150ms，
// hover 预取还没起头。
function onPressTopic(topicId: string) {
  prefetchNow({
    router,
    to: { name: 'workspace-topic', params: { projectId: props.projectId, topicId } },
    topicId,
  })
}

// 新建频道在「浏览频道」那一页（先起名再建），命令面板里这一条去那里。
useCommands(() => [
  {
    id: 'topic.new',
    title: t('navigation.palette.newTopic'),
    icon: 'mdi-plus',
    run: () => void router.push({ name: 'project-channels', params: { projectId: props.projectId } }),
  },
  // 全部标为已读（同 Slack 的 Shift+Esc）：只在真有未读时登记，没有时 Shift+Esc 照旧归
  // 别人（比如关掉一个浮层）。
  ...(Object.keys(store.unreadMap).length
    ? [
        {
          id: 'topics.markAllRead',
          title: t('work.room.menu.markAllRead'),
          icon: 'mdi-check-all',
          shortcut: 'shift+escape',
          run: () => void store.markAllRead(),
        },
      ]
    : []),
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
      :room-tasks="roomTasks"
      :room-task-totals="roomTaskTotals"
      :all-tasks-channel-id="activeAllTasks"
      :selected-task-id="activeTaskId"
      :browsing-channels="browsingChannels"
      :loading-topics="store.loadingTopics"
      :error="store.topicsError"
      :active-docs="activeDocs"
      :unread-map="store.unreadMap"
      :muted-of="store.isMuted"
      :private-unread-map="store.privateUnreadMap"
      @select-topic="openTopic"
      @select-task="openTask"
      @all-tasks="openAllTasks"
      @browse-channels="browseChannels"
      @hover-topic="onHoverTopic"
      @press-topic="onPressTopic"
      @leave-topic="cancelPrefetch"
      @select-docs="openDocs"
      @retry="store.reloadTopics()"
      @rename-topic="(p) => store.renameTopic(p.id, p.title)"
    >
      <!-- 手机上进项目落在频道列表上，项目名下那一行收进了菜单，所以去项目总览的那一行
           放在列表最顶上。桌面上它就是项目名下那一行的第一格。 -->
      <template v-if="page || column" #top>
        <button type="button" class="overview-entry t-body" data-testid="overview-entry" @click="openOverview">
          <v-icon size="18" class="overview-entry__icon">mdi-view-dashboard-outline</v-icon>
          <span class="overview-entry__label">{{ t('navigation.project.overview') }}</span>
          <v-icon size="18" class="overview-entry__go">mdi-chevron-right</v-icon>
        </button>
      </template>
    </TopicSidebar>
  </SplitListColumn>
</template>

<style scoped>
.overview-entry {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 44px;
  padding: 0 16px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--ink);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.overview-entry__icon,
.overview-entry__go {
  color: var(--faint);
}
.overview-entry__label {
  flex: 1 1 auto;
}
</style>
