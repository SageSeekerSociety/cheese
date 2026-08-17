<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useWorkspaceStore } from '@/stores/workspace'

// `/projects/:projectId` with nothing after it. It resolves to a real address
// rather than rendering anything of its own, so every workspace URL in the wild
// says what it is showing.
//
// It also carries the old `?topic=` links: every workspace link ever pasted
// into a chat is of that shape, and they must keep landing on their topic.
defineOptions({ name: 'WorkspaceEntry' })

const props = defineProps<{ projectId: string }>()
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
    // 大本营 (the root topic) is the project's coordination hub, and the
    // default view of a project. Until the list arrives there is nothing to
    // pick — the template holds a spinner, and an empty state if it stays empty.
    if (root) openTopic(root.id)
  },
  { immediate: true }
)
</script>

<template>
  <div class="d-flex align-center justify-center fill-height">
    <v-progress-circular v-if="store.loadingTopics || store.rootTopic" indeterminate color="primary" />
    <div v-else class="text-center">
      <div class="t-body c-muted">这个项目还没有话题</div>
      <div class="t-meta mt-1">在左边新建一个话题开始</div>
    </div>
  </div>
</template>
