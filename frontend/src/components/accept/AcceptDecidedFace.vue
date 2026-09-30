<script setup lang="ts">
// Accepted topic: 采纳可撤销 (spec §6.3). 归档话题上那张已采纳的卡 —— 它存在的
// 理由就是给一次反悔留个入口。
import type { AcceptCard } from '@/cx_types'

import UserRef from '@/components/common/UserRefLink.vue'

defineProps<{
  card: AcceptCard
  busy: boolean
}>()

defineEmits<{ (e: 'revoke'): void }>()
</script>

<template>
  <v-card variant="outlined" class="merge-box">
    <div class="pa-3">
      <div class="text-body-2 c-muted mb-3"><UserRef :handle="card.decided_by" /> 已采纳</div>
      <v-btn
        variant="outlined"
        class="btn-secondary"
        :loading="busy"
        :disabled="busy"
        prepend-icon="mdi-undo"
        @click="$emit('revoke')"
      >
        撤回采纳
      </v-btn>
    </div>
  </v-card>
</template>

<style scoped src="./accept-card.css"></style>
