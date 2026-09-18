<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useWorkspaceStore } from '@/stores/workspace'
import ProjectAccessNotice from '@/views/workspace/ProjectAccessNotice.vue'

// 项目工作台的框架层 (P0). Everything inside a project is a CHILD of this
// route, which is the whole point: the project sidebar (rendered through the
// app-wide `sidebar` named view, same mechanism as 首页/空间/设置) never
// unmounts, so 总览/日历/设置/成员/文档 stop being pages you get thrown out to
// and become ordinary destinations inside the workspace. Before this existed
// each of those pages had to hand-roll its own 返回 button and its own content
// width, because there was no frame to come back to.
defineOptions({ name: 'ProjectShell' })

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const store = useWorkspaceStore()

// The route is the single source of truth for "what am I looking at" — the
// store only mirrors it so background refreshes know which badge not to light.
watch(
  () => [route.params.topicId, route.params.peer],
  ([topicId, peer]) => {
    store.activeTopicId = typeof topicId === 'string' ? topicId : null
    store.activeDmPeer = typeof peer === 'string' ? peer : null
  },
  { immediate: true }
)

watch(
  () => props.projectId,
  (id) => void store.openProject(id),
  { immediate: true }
)

// 实时性: poll unread badges so messages landing in OTHER topics light up
// without a manual refresh. The same tick refreshes the topic list, so the
// sidebar's 芝士还在跑 呼吸点 fades for topics you are not watching too.
let pollTimer: number | undefined
onMounted(() => {
  pollTimer = window.setInterval(() => {
    void store.refreshUnread()
    void store.refreshTopics()
  }, 30_000)
})
onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
})

// Snackbar v-model bridge: visible while there's an error; writing false clears.
const hasError = computed<boolean>({
  get: () => store.error !== null,
  set: (v) => {
    if (!v) store.error = null
  },
})
</script>

<template>
  <div class="project-shell fill-height">
    <!-- 进不来的时候，整块内容区换成说明，而不是让人对着一个空壳猜。侧栏和顶栏
         留着，因为「离开这里」的路都在那上面。 -->
    <ProjectAccessNotice v-if="store.accessDenied" :reason="store.accessDenied" />
    <router-view v-else />

    <v-snackbar v-model="hasError" color="error" timeout="4000" location="bottom">
      {{ store.error }}
    </v-snackbar>
  </div>
</template>

<style scoped>
.project-shell {
  width: 100%;
  overflow: hidden;
}
</style>
