<script setup lang="ts">
// 「转让团队」：所有者退不掉团队（后端要他先转让或解散），这里就是那条出路。从团队
// 成员里挑一个人 → `PUT /teams/{id}/owner`：他成为所有者，我降为管理员，之后才退得掉。
//
// 名单是外面读来的（`composables/useTeamNav.ts` 打开时读一次，并且已经去掉我自己），
// 转让本身也在外面做：这一件只挑人、报上去（A 档）。被拒时弹窗不关，那句理由由外面
// 给回来；重开时清掉。
import type { Team, TeamMember } from '@/types'

import { ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 要交出去的团队。 */
  team: Team
  /** 能接手的人（已经去掉我自己）。 */
  candidates: TeamMember[]
  /** 名单还在路上：别把「还没有人」和「还没读到」画成同一句。 */
  loading?: boolean
  /** 正在转让：按钮转圈。 */
  busy?: boolean
  /** 上一次没成的理由（名单读不到、后端拒了），原样画在下面。 */
  error?: string | null
}>()
const emit = defineEmits<{ confirm: [userId: number] }>()

const open = defineModel<boolean>({ required: true })

const picked = ref<number | null>(null)

// 每次打开都从「还没挑」起手：上一次挑中的那个人不能替这一次作数。
watch(
  open,
  (now) => {
    if (now) picked.value = null
  },
  { immediate: true }
)

function confirm() {
  if (picked.value !== null && !props.busy) emit('confirm', picked.value)
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('home.nav.transferTeamTitle', { name: team.name })"
    :primary-label="t('home.nav.transferTeam')"
    :primary-loading="busy"
    :primary-disabled="picked === null"
    :max-width="480"
    @primary="confirm"
  >
    <div class="t-body c-muted">
      {{ t('home.nav.transferTeamBody') }}
      <div v-if="!loading && candidates.length === 0 && !error" class="t-meta mt-3">
        {{ t('home.nav.transferTeamNoOne') }}
      </div>
      <v-list v-else-if="candidates.length > 0" density="compact" nav class="mt-2 transfer-list">
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
