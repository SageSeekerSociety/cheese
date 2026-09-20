<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

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

function openTopic(topicId: string) {
  void router.replace({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// 旧链接: ?topic=<id> is answered before anything else — the target is already
// named in the address, so there is nothing to wait for and nothing to decide.
const legacyTopic = computed(() => (route.query.topic ? String(route.query.topic) : null))

watch(
  [legacyTopic, mdAndUp],
  ([wanted, desktop]) => {
    if (route.name !== 'workspace-project') return
    if (wanted) {
      openTopic(wanted)
      return
    }
    // 手机上这一层**就是**话题列表（页面栈：工作区 → 话题列表 → 话题页），所以
    // 不跳——跳了就永远看不到列表，也就没有"回上一层"可回。
    if (!desktop) return
    // 桌面: 进项目的第一屏是**看板**。第一眼该答的是「整个项目现在什么在跑、什么
    // 在等我」，而落进大本营答的是「这一个房间里最近说了什么」——那是一个房间的
    // 事，得靠人自己一个个点过去才拼得出全貌。大本营没有变远：它是常驻侧栏置顶
    // 的那一行，一次点击就到。
    //
    // 不等话题列表到货：目的地和列表无关，等只会换来一屏转圈。
    void router.replace({ name: 'workspace-running', params: { projectId: props.projectId } })
  },
  { immediate: true }
)
</script>

<template>
  <!-- 手机: 这一层就是话题列表。桌面上这个地址什么都不画——它只是去看板路上的一
       瞬，画点什么就是闪一下。 -->
  <ProjectSidebar v-if="!mdAndUp" :project-id="projectId" page />
</template>
