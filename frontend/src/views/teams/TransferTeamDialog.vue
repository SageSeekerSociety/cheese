<script setup lang="ts">
// 「转让团队」：所有者退不掉团队（后端要他先转让或解散），这里就是那条出路。从团队
// 成员里挑一个人 → `PUT /teams/{id}/owner`：他成为所有者，我降为管理员，之后才退得掉。
// 被拒时弹窗不关，那句理由原样留在弹窗里；重开时清掉。
//
// 候选人名单是 props 进来的：读成员、把我自己摘掉都是外面的事。这一只只管「挑谁」，
// 挑好了喊一声（`submit`）。
import type { Team, TeamMember } from '@/types'

import { ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

const props = defineProps<{
  modelValue: boolean
  team: Team
  /** 能接手的人：团队里除我以外的成员。外面读好再给。 */
  candidates: TeamMember[]
  /** 正在转让：按钮转起来，也挡住第二次提交。 */
  transferring?: boolean
  /** 读名单失败、或上一次转让为什么没成。 */
  error?: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [userId: number]
}>()

const picked = ref<number | null>(null)

// 每次打开都从「还没挑」起手。
watch(
  () => props.modelValue,
  (open) => {
    if (open) picked.value = null
  },
  { immediate: true }
)

function transfer() {
  if (picked.value === null || props.transferring) return
  emit('submit', picked.value)
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="t('home.nav.transferTeamTitle', { name: team.name })"
    :primary-label="t('home.nav.transferTeam')"
    :primary-loading="transferring"
    :primary-disabled="picked === null"
    :max-width="480"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="transfer"
  >
    <div class="t-body c-muted">
      {{ t('home.nav.transferTeamBody') }}
      <div v-if="candidates.length === 0 && !error" class="t-meta mt-3">{{ t('home.nav.transferTeamNoOne') }}</div>
      <v-list v-else density="compact" nav class="mt-2 transfer-list">
        <v-list-item
          v-for="m in candidates"
          :key="m.user.id"
          :active="picked === m.user.id"
          rounded="lg"
          @click="picked = m.user.id"
        >
          <template #prepend>
            <UserAvatar
              :name="m.user.nickname || m.user.username"
              :avatar="m.user.avatarId ? getAvatarUrl(m.user.avatarId) : ''"
              :size="28"
              class="me-3"
            />
          </template>
          <v-list-item-title class="t-body">{{ m.user.nickname || m.user.username }}</v-list-item-title>
          <v-list-item-subtitle class="t-meta">{{ m.user.username }}</v-list-item-subtitle>
        </v-list-item>
      </v-list>
      <v-alert v-if="error" type="error" density="comfortable" class="mt-4">{{ error }}</v-alert>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.transfer-list {
  max-height: 240px;
  overflow-y: auto;
}
</style>
