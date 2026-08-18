<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

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

// 旧链接: ?topic=<id> is answered before the topic list arrives — the target is
// already named, so there is nothing to wait for.
const legacyTopic = computed(() => (route.query.topic ? String(route.query.topic) : null))

watch(
  [legacyTopic, () => store.rootTopic],
  ([wanted, root]) => {
    if (route.name !== 'workspace-project') return
    if (wanted) {
      openTopic(wanted)
      return
    }
    // 手机上这一层**就是**话题列表（页面栈：工作区 → 话题列表 → 话题页），所以
    // 不再直接跳进大本营——跳了就永远看不到列表，也就没有"回上一层"可回。
    // 桌面上列表是常驻侧栏，这个地址本身不显示任何东西，落到大本营才对。
    if (!mdAndUp.value) return
    // 大本营 (the root topic) is the project's coordination hub, and the
    // default view of a project. Until the list arrives there is nothing to
    // pick — the template holds a spinner, and an empty state if it stays empty.
    if (root) openTopic(root.id)
  },
  { immediate: true }
)
</script>

<template>
  <!-- 手机: 这一层就是话题列表 -->
  <ProjectSidebar v-if="!mdAndUp" :project-id="projectId" page />
  <!-- 桌面: 列表在常驻侧栏里，这个地址只是去大本营路上的一瞬 -->
  <div v-else class="d-flex align-center justify-center fill-height">
    <v-progress-circular v-if="store.loadingTopics || store.rootTopic" indeterminate color="primary" />
    <div v-else class="text-center">
      <div class="t-body c-muted">这个项目还没有话题</div>
      <div class="t-meta mt-1">在左边新建一个话题开始</div>
    </div>
  </div>
</template>
