<script setup lang="ts">
// Accepted topic: 采纳可撤销 (spec §6.3). 归档话题上那张已采纳的卡 —— 它存在的
// 理由就是给一次反悔留个入口。
import type { AcceptCard } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

defineProps<{
  card: AcceptCard
  busy: boolean
}>()

defineEmits<{ (e: 'revoke'): void }>()
</script>

<template>
  <v-card variant="outlined" class="merge-box">
    <div class="pa-3">
      <i18n-t scope="global" keypath="work.room.accept.acceptedBy" tag="div" class="text-body-2 c-muted mb-3">
        <template #who><UserRef :handle="card.decided_by" /></template>
      </i18n-t>
      <BaseButton kind="secondary" :loading="busy" :disabled="busy" prepend-icon="mdi-undo" @click="$emit('revoke')">
        {{ t('work.room.accept.revoke') }}
      </BaseButton>
    </div>
  </v-card>
</template>

<style scoped src="./accept-card.css"></style>
