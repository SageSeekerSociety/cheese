<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { loadCachedProjects } from '@/lib/projectCache'
import { DEFAULT_SHELL, shellFor } from '@/lib/shell'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectSidebar from '@/views/workspace/ProjectSidebar.vue'

// `/projects/:projectId` with nothing after it. It resolves to a real address
// rather than rendering anything of its own, so every workspace URL in the wild
// says what it is showing.
//
// It also carries the old `?topic=` links: every workspace link ever pasted
// into a chat is of that shape, and they must keep landing on their topic.
defineOptions({ name: 'WorkspaceEntry' })

const props = defineProps<{ projectId: string }>()
const { mdAndUp } = useDisplay()
const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()

function openTopic(topicId: string) {
  void router.replace({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// 旧链接: ?topic=<id> is answered before anything else — the target is already
// named in the address, so there is nothing to wait for and nothing to decide.
const legacyTopic = computed(() => (route.query.topic ? String(route.query.topic) : null))

// 第一屏落哪由**这个项目的壳**说了算（default 壳说：看板）。所以这里等的不再是
// 话题列表，而是**壳**——两件事，前一版把它们混成了一件，因为那时第一屏是个常量。
//
// 壳跟着项目行来，而项目行有快慢两种到货方式：这个浏览器上次已经见过这个项目时
// 是**同步**的（projectCache，App.vue 读的是同一份），从没见过时得等清单回来。
// 所以下面分成「知道了」和「等到了」两问：前者立刻跳，后者等清单落定再跳，落定时
// 还是没有这个项目（不是我的项目、或者清单压根加载失败）就按 default 跳——今天
// 的行为，不能因为壳层让谁卡在这一屏。
const shell = computed(
  () => shellFor(loadCachedProjects(myHandle()), props.projectId) ?? shellFor(store.projects, props.projectId)
)
const shellSettled = computed(() => shell.value !== null || store.projects.length > 0 || store.projectsSettled)

watch(
  [legacyTopic, mdAndUp, shellSettled],
  ([wanted, desktop, settled]) => {
    if (route.name !== 'workspace-project') return
    if (wanted) {
      openTopic(wanted)
      return
    }
    // 手机上这一层**就是**话题列表（页面栈：工作区 → 话题列表 → 话题页），所以
    // 不跳——跳了就永远看不到列表，也就没有"回上一层"可回。
    if (!desktop) return
    // 桌面: 进项目的第一屏由壳指定，default 是**看板**。第一眼该答的是「整个项目
    // 现在什么在跑、什么在等我」，而落进大本营答的是「这一个房间里最近说了什么」
    // ——那是一个房间的事，得靠人自己一个个点过去才拼得出全貌。大本营没有变远：
    // 它是常驻侧栏置顶的那一行，一次点击就到。
    //
    // 不等话题列表到货：目的地和列表无关，等只会换来一屏转圈。
    if (!settled) return
    const home = (shell.value ?? DEFAULT_SHELL).home
    if (!home) return
    void router.replace({ name: home, params: { projectId: props.projectId } })
  },
  { immediate: true }
)
</script>

<template>
  <!-- 手机: 这一层就是话题列表。桌面上这个地址什么都不画——它只是去第一屏路上
       的一瞬，画点什么就是闪一下。 -->
  <ProjectSidebar v-if="!mdAndUp" :project-id="projectId" page />
</template>
