<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { useQuery } from '@tanstack/vue-query'

import { provideTopicMemory } from '@/composables/useTopicMemory'

import { t } from '@/i18n'
import { routeIds } from '@/lib/addresses'
import { trackNavigations } from '@/lib/navigationProgress'
import { queryClient } from '@/lib/queryClient'
import { warmPagesWhenIdle } from '@/lib/routePrefetch'
import { myHandle } from '@/me'
import { notifyLevelsQuery, privateUnreadQuery, topicsQuery, unreadQuery } from '@/queries/project'
import { usePageTitleStore } from '@/stores/title'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectAccessNotice from '@/views/workspace/ProjectAccessNotice.vue'

// 项目工作台的框架层 (P0). Everything inside a project is a CHILD of this
// route, which is the whole point: the project sidebar (rendered through the
// app-wide `sidebar` named view, same mechanism as 首页/空间/设置) never
// unmounts, so 总览/设置/成员/文档 stop being pages you get thrown out to
// and become ordinary destinations inside the workspace. Before this existed
// each of those pages had to hand-roll its own 返回 button and its own content
// width, because there was no frame to come back to.
defineOptions({ name: 'ProjectShell' })

const PROJECT_FRAME_TITLE = 'project-frame'

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()
// The topic page below is rebuilt per topic; what it keeps across topics lives here.
provideTopicMemory()

// 点下去到新内容出现之间，路由在等目标那一页的懒加载 chunk（见 lib/routePrefetch.ts
// 开头）。那一段里屏幕上一动不动，点一下像是没点到；这个计数让内容区当场给出反馈。
const { navigating } = trackNavigations(router)

// The route is the single source of truth for "what am I looking at" — the
// store only mirrors it so background refreshes know which badge not to light.
watch(
  () => [routeIds(route.params).topicId, route.params.peer, props.projectId] as const,
  ([topicId, peer, projectId]) => {
    store.activeTopicId = typeof topicId === 'string' ? topicId : null
    store.activeDmPeer = typeof peer === 'string' ? peer : null
    // 正待着的这个房间记在这个项目名下：下次从 rail 点回来，直接落回它。带上
    // projectId 一起看，是因为深链接进来时 topicId 一开始就有、项目这时候才到位。
    if (typeof topicId === 'string' && projectId) store.rememberTopic(projectId, topicId)
  },
  { immediate: true }
)

watch(
  () => props.projectId,
  (id) => store.openProject(id),
  { immediate: true }
)

// 顶栏标题 = 项目名（路由 meta 的 dynamicTitleKey 指到这里）。
const titles = usePageTitleStore()
watch(
  () => store.projectName,
  (name) => {
    if (name) titles.setDynamicTitle(name, PROJECT_FRAME_TITLE)
    else titles.clearDynamicTitle(PROJECT_FRAME_TITLE)
  },
  { immediate: true }
)
onUnmounted(() => titles.clearDynamicTitle(PROJECT_FRAME_TITLE))

// 实时性: poll unread badges so messages landing in OTHER topics light up
// without a manual refresh. The same tick refreshes the topic list, so the
// sidebar's 芝士还在跑 呼吸点 fades for topics you are not watching too. 标签页在
// 后台时不问，切回来时过期了的再问一次（lib/queryClient）。
const POLL_MS = 30_000
const me = myHandle()
useQuery(
  computed(() => ({ ...topicsQuery(props.projectId), refetchInterval: POLL_MS })),
  queryClient
)
useQuery(
  computed(() => ({ ...unreadQuery(props.projectId, me), enabled: !!me, refetchInterval: POLL_MS })),
  queryClient
)
useQuery(
  computed(() => ({ ...privateUnreadQuery(props.projectId, me), enabled: !!me, refetchInterval: POLL_MS })),
  queryClient
)
useQuery(
  computed(() => ({ ...notifyLevelsQuery(props.projectId), refetchInterval: POLL_MS })),
  queryClient
)

// 这个框架底下这几页的代码，趁空闲先下下来：侧栏那一行「总览/资料库」、侧栏和总览
// 里的任务行，点下去就是它们。按页名热，因为框架这一层拿不到每一页的地址参数
//（另一个话题、另一个任务），也不该为了预热编一份出来（见 routePrefetch 的 warmPage）。
const IDLE_WARM_PAGES = ['workspace-overview', 'project-library', 'workspace-task', 'project-tasks']
// 刚进页面这几秒先不热：人到了就是要动手的，四份代码包一起下会跟他第一下抢那条路
//（实测把「总览切资料库」的列表推后了 ~600 ms，比不热还慢）。等页面静下来再热，热的
// 还是这四页，受益的是后面的切换。按下和悬浮那两处预取不在这里，它们跟着指针走。
const IDLE_WARM_DELAY_MS = 2500
let startIdleWarm: number | undefined
let stopIdleWarm: (() => void) | undefined

onMounted(() => {
  startIdleWarm = window.setTimeout(() => {
    stopIdleWarm = warmPagesWhenIdle(router, IDLE_WARM_PAGES)
  }, IDLE_WARM_DELAY_MS)
})
onUnmounted(() => {
  if (startIdleWarm !== undefined) window.clearTimeout(startIdleWarm)
  stopIdleWarm?.()
})

// 工作台报错统一走全局 toast：store.error 一旦有值就弹一条并清空，避免同一条
// 错误在后续渲染里重复弹出。
watch(
  () => store.error,
  (message) => {
    if (!message) return
    toast.error(message)
    store.error = null
  }
)
</script>

<template>
  <div class="project-shell fill-height">
    <!-- 跳转还在路上（见 script 里的 trackNavigations）：内容区顶上一条进度条，点下去
         当帧就出现。不换掉现在这一页——换掉等于把屏幕上已有的东西闪一下；也不给每页
         各做一套骨架，那一帧里还不知道要去的是哪一页长什么样。 -->
    <v-progress-linear
      v-if="navigating"
      class="project-shell__progress"
      indeterminate
      color="primary"
      :aria-label="t('shell.loading')"
    />
    <!-- Screen-reader heading for the frame. Text from the same store the top
         bar reads (the `project-frame` dynamic title), so the two can never
         drift. Hidden: on desktop the project name lives in the sidebar's top
         row, on mobile in the top bar; neither is a heading. -->
    <h1 v-if="store.projectName" class="visually-hidden">{{ store.projectName }}</h1>
    <!-- 进不来的时候，整块内容区换成说明，而不是让人对着一个空壳猜。侧栏和顶栏
         留着，因为「离开这里」的路都在那上面。 -->
    <ProjectAccessNotice v-if="store.accessDenied" :reason="store.accessDenied" />
    <!-- 一个话题一个实例：换话题就换一整棵组件树。复用同一个实例的话，每个挂在话题
         下面的组件都得自己记得在换话题时清空、并丢掉上一个话题迟到的响应——漏一处，
         上一个话题的东西就会在下一个话题里露出来。只有话题页带 topicId，别的页
         不受影响。 -->
    <router-view v-else v-slot="{ Component, route: current }">
      <component :is="Component" :key="current.params.topicId" />
    </router-view>
  </div>
</template>

<style scoped>
.project-shell {
  position: relative;
  width: 100%;
  overflow: hidden;
}
/* 贴着内容区上沿，不占位置：出现和消失都不该让下面那一页动一下。 */
.project-shell__progress {
  position: absolute;
  inset-block-start: 0;
  inset-inline: 0;
  z-index: 2;
  margin: 0;
}
</style>
