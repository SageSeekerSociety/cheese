<script setup lang="ts">
// 「转让团队」：所有者退不掉团队（后端要他先转让或解散），这里就是那条出路。从团队
// 成员里挑一个人 → `PUT /teams/{id}/owner`：他成为所有者，我降为管理员，之后才退得掉。
// 被拒时弹窗不关，那句理由原样留在弹窗里；重开时清掉。
import type { Team, TeamMember } from '@/types'

import { computed, ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'
import AccountService from '@/services/account'

const props = defineProps<{ team: Team }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ transferred: [team: Team] }>()

const members = ref<TeamMember[]>([])
const picked = ref<number | null>(null)
const transferring = ref(false)
const error = ref<string | null>(null)

const candidates = computed(() => members.value.filter((m) => m.user.id !== AccountService.user?.id))

watch(
  open,
  async (v) => {
    if (!v) return
    error.value = null
    picked.value = null
    try {
      members.value = (await TeamsApi.getMembers(props.team.id)).data.members
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('home.nav.transferTeamLoadFailed')
    }
  },
  { immediate: true }
)

async function transfer() {
  if (picked.value === null) return
  transferring.value = true
  error.value = null
  try {
    const { data } = await TeamsApi.transferOwner(props.team.id, picked.value)
    open.value = false
    emit('transferred', data.team)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('home.nav.transferTeamFailed')
  } finally {
    transferring.value = false
  }
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('home.nav.transferTeamTitle', { name: team.name })"
    :primary-label="t('home.nav.transferTeam')"
    :primary-loading="transferring"
    :primary-disabled="picked === null"
    :max-width="480"
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
          <v-list-item-subtitle class="t-meta">@{{ m.user.username }}</v-list-item-subtitle>
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
