<template>
  <InvitationListView :users="users" :is-invited="isInvited" @invite="invite" />
</template>

<script setup lang="ts">
// 「邀请回答」名单**取数的那一半**：拉名单与请人都走
// `composables/useInvitationList`，这里只把它和 `InvitationListView.vue` 接起来。
//
// 外部接口没变：还是吃一个 `questionId` prop。
import { useInvitationList } from '@/composables/useInvitationList'

import InvitationListView from './InvitationListView.vue'

const props = defineProps<{
  questionId: number
}>()

const { users, isInvited, invite } = useInvitationList(() => props.questionId)
</script>
