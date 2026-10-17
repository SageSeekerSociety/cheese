<script setup lang="ts">
// 「移到团队」的对话框（#3830）：挑一个自己管理的团队，个人项目就归到那里。项目里的人和
// 角色都不变。团队清单、移动和失败理由都由页面递进来；被拒不关窗，理由留在弹窗里。
import type { MovableTeam } from '@/composables/useMoveProject'

import { ref, watch } from 'vue'

import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

defineProps<{
  teams: MovableTeam[]
  loading: boolean
  moving: boolean
  error: string | null
}>()
const emit = defineEmits<{ move: [teamId: number] }>()
const open = defineModel<boolean>({ required: true })

const picked = ref<number | null>(null)
watch(open, (v) => {
  if (v) picked.value = null
})
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.members.moveTitle')"
    :primary-label="t('work.members.moveSubmit')"
    :primary-loading="moving"
    :primary-disabled="picked === null"
    :cancel-label="t('work.members.cancel')"
    :max-width="440"
    @primary="picked !== null && emit('move', picked)"
  >
    <p class="t-body c-muted mb-5">{{ t('work.members.moveHint') }}</p>
    <div v-if="!loading && teams.length === 0" class="t-meta c-muted">{{ t('work.members.moveEmpty') }}</div>
    <BaseField v-else :label="t('work.members.moveTeam')">
      <v-select
        v-model="picked"
        autocomplete="off"
        :items="teams"
        item-title="name"
        item-value="id"
        :loading="loading"
        :aria-label="t('work.members.moveTeam')"
        variant="outlined"
        density="comfortable"
        hide-details
      />
    </BaseField>
    <v-alert v-if="error" type="error" density="comfortable" class="mt-4">{{ error }}</v-alert>
  </AdaptiveDialog>
</template>
