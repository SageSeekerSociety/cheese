<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useWorkspaceLayout } from '@/composables/useWorkspaceLayout'

import { t } from '@/i18n'
import { DEFAULT_SHELL, shellFor } from '@/lib/shell'
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
const layout = useWorkspaceLayout()
const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()

function openTopic(topicId: string) {
  void router.replace({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// 旧链接: ?topic=<id> is answered before anything else — the target is already
// named in the address, so there is nothing to wait for and nothing to decide.
const legacyTopic = computed(() => (route.query.topic ? String(route.query.topic) : null))

// 第一屏落哪由**这个项目的壳**说了算（default 壳说：项目总览）。所以这里等的不再是
// 话题列表，而是**壳**——两件事，前一版把它们混成了一件，因为那时第一屏是个常量。
//
// 壳跟着项目行来，而项目行有快慢两种到货方式：这个标签页上次已经见过这个项目时
// 清单当场就在（lib/queryPersist 存的那份），从没见过时得等清单回来。
// 所以下面分成「知道了」和「等到了」两问：前者立刻跳，后者等清单落定再跳，落定时
// 还是没有这个项目（不是我的项目、或者清单压根加载失败）就按 default 跳——今天
// 的行为，不能因为壳层让谁卡在这一屏。
const shell = computed(() => shellFor(store.projects, props.projectId))
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
    // 桌面: 进项目的第一屏由壳指定，default 是**项目总览**。第一眼该答的是「这个
    // 项目怎么样了」，而落进「综合」答的是「这一个频道里最近说了什么」——那是一个
    // 频道的事。「综合」没有变远：它是常驻侧栏频道里的第一行，一次点击就到。
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
  <ProjectSidebar v-if="layout === 'phone'" :project-id="projectId" page />
  <!-- 两栏（平板）: 话题列表是左边那一栏（ProjectSidebar 常驻的那一份），这里是还
       没打开房间时的右边。 -->
  <div v-else-if="layout === 'split'" class="workspace-unselected">
    <span class="t-body c-muted">{{ t('work.room.unselected') }}</span>
  </div>
</template>

<style scoped>
.workspace-unselected {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: var(--surface);
}
</style>
