<!-- Thin compatibility wrapper: the agent-aware avatar is now the site-wide
     `common/UserAvatar`. Existing workspace callers pass `:user` / `:member`; clicking an
     agent opens the app-global 现场 (no per-page emit wiring needed). -->
<template>
  <UserAvatar :user="user" :member="member" :size="size" :show-meta="showMeta" />
</template>

<script setup lang="ts">
import UserAvatar from '@/components/common/UserAvatar.vue'
import type { UserSummary } from '@/network/api/workspace'
import type { Member } from '@/network/api/threads'

withDefaults(defineProps<{ user?: UserSummary; member?: Member; size?: number; showMeta?: boolean }>(), {
  size: 36,
  showMeta: true,
})
</script>
