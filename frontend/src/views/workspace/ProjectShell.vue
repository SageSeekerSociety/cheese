<script setup lang="ts">
import type { RememberedPlace } from '@/stores/workspace'

import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { useQuery } from '@tanstack/vue-query'

import { provideTopicMemory } from '@/composables/useTopicMemory'

import { t } from '@/i18n'
import { routeIds } from '@/lib/addresses'
import { firstPaintDone } from '@/lib/afterFirstPaint'
import { trackNavigations } from '@/lib/navigationProgress'
import { lookupRef, refsVersion } from '@/lib/refDirectory'
import { warmPagesWhenIdle } from '@/lib/routePrefetch'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { notifyLevelsQuery, privateUnreadQuery, topicsQuery, unreadQuery } from '@/query/project'
import { useProjectFeed } from '@/query/projectFeed'
import { useAgentContext } from '@/stores/agent'
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
const ids = computed(() => routeIds(route.params))

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
// 芝士在项目里读的是眼前这一页：打开的文档，所在的频道、任务或支线，别的页就是这个项目。
// 编号还没换成 UUID（短编号刚进来）时先当项目。
useAgentContext(() => {
  const { docId, threadId, taskId, topicId, projectId } = ids.value
  const place = [threadId, taskId, topicId].find((id) => id && UUID.test(id))
  const page =
    docId && UUID.test(docId)
      ? ({ kind: 'doc', id: docId } as const)
      : place
        ? ({ kind: 'topic', id: place } as const)
        : projectId && UUID.test(projectId)
          ? ({ kind: 'project', id: projectId } as const)
          : null
  return {
    page,
    label: pageLabel(page),
    project: projectId && UUID.test(projectId) && store.projectName ? { id: projectId, name: store.projectName } : null,
    greeting: t('tasks.assistant.project.greeting'),
    starters: [
      t('tasks.assistant.project.starters.mine'),
      place ? t('tasks.assistant.project.starters.recap') : t('tasks.assistant.project.starters.progress'),
      t('tasks.assistant.project.starters.handOff'),
    ],
  }
})
/** 「当前页面」那枚标签上写的名字：频道、任务、支线、文档按引用的名字，别的页是项目。
 *  名字还没问回来时先不写（`refsVersion` 让它回来后重算）。 */
function pageLabel(page: { kind: 'doc' | 'topic' | 'project'; id: string } | null): string | undefined {
  if (!page) return undefined
  if (page.kind === 'project')
    return store.projectName ? t('tasks.assistant.page.project', { name: store.projectName }) : undefined
  void refsVersion.value
  const found = lookupRef(page.id)
  if (!found || found === 'missing' || found.kind === 'unreadable') return undefined
  const key = { room: 'channel', task: 'task', thread: 'thread', document: 'doc' }[found.kind]
  return t(`tasks.assistant.page.${key}`, { name: found.title || t('work.room.doc.untitled') })
}

/**
 * 这一页在项目里那一段地址：`tasks/318`、`overview`、`members/lisi`；项目首页是空串。
 * 地址里项目那一段用的是短名或编号（守卫 `canonicalAddress` 换过），所以按当前地址
 * 上写的那个 ref 截，不是按项目 id。
 */
function placePathOf(path: string, projectRef: string): string {
  const prefix = `/projects/${projectRef}/`
  return path.startsWith(prefix) ? path.slice(prefix.length) : ''
}

/**
 * 正待着的这一页，记进「上次待的地方」。
 *
 * 记的是地址，不分页面种类——项目里的每一页都算一个地方。只认任务和频道的话，从
 * 成员页、资料库离开再回来就掉回频道列表，那正是「只能回到任务」这一句说的毛病。
 *
 * 两类不算：盖在页面上的一层（`settingsOverlay`，回到它等于回来时先站在设置里），
 * 和只有查询才说得清是哪一页的搜索页（查询串不记，回来就是一张空搜索页）。
 */
function placeOf(): RememberedPlace | null {
  if (route.meta.settingsOverlay === true || route.name === 'project-search') return null
  const ref = typeof route.params.projectId === 'string' ? route.params.projectId : ''
  const topicId = ids.value.topicId
  return {
    path: placePathOf(route.path, ref),
    room: typeof topicId === 'string' ? topicId : null,
  }
}

watch(
  () => [route.path, ids.value.topicId, route.params.peer, props.projectId] as const,
  ([, topicId, peer, projectId]) => {
    store.activeTopicId = typeof topicId === 'string' ? topicId : null
    store.activeDmPeer = typeof peer === 'string' ? peer : null
    // 正待着的这一页记在这个项目名下：下次从 rail 点回来、或者重开应用，直接落回它。
    // 带上 projectId 一起看，是因为深链接进来时 topicId 一开始就有、项目这时候才到位。
    if (!projectId) return
    const place = placeOf()
    if (place) store.rememberPlace(projectId, place)
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

// 频道清单、未读、提醒档位：框架这一层一直看着它们。别的房间里有人说话、改了名、
// 芝士开始或停下，项目推一帧过来，变了的那一份重读（query/projectFeed）。
// 地址上的项目换不出来（没登录、没有这个项目或看不到）时，这一层也不拿短名去请求。
const me = myHandle()
const reachable = computed(() => !store.projectRefused)
// 未读、私聊未读、通知档位三样只画角标，不挡内容：第一遍和推送订阅一起排到首帧之后
//（`lib/afterFirstPaint.ts`），开页那一瞬间先把页面自己要的数据发出去。清单
//（topicsQuery）不推迟——侧栏和内容区都靠它画。
const badges = computed(() => reachable.value && firstPaintDone.value)
useQuery(
  computed(() => ({ ...topicsQuery(props.projectId), enabled: reachable.value })),
  queryClient
)
useQuery(
  computed(() => ({ ...unreadQuery(props.projectId, me), enabled: !!me && badges.value })),
  queryClient
)
useQuery(
  computed(() => ({ ...privateUnreadQuery(props.projectId, me), enabled: !!me && badges.value })),
  queryClient
)
useQuery(
  computed(() => ({ ...notifyLevelsQuery(props.projectId), enabled: badges.value })),
  queryClient
)
useProjectFeed(
  computed(() => (badges.value ? props.projectId : '')),
  me || null
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
