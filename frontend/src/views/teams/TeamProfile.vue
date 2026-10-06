<script setup lang="ts">
// 小队的对外一面：没加入的人看到的就是这一页。按地址打开（/teams/:handle）和按小队链接
// 打开（/team-invites/:token）是同一个组件，只有「加入」走哪条接口不同，由调用方传进来。
// 小队开着审批时，「加入」提交的是申请（带理由），结果是 pending；关着就直接成为成员。
import type { Team } from '@/types'

import { computed, ref } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

const props = defineProps<{
  team: Team
  join: (message: string) => Promise<void>
}>()

const message = ref('')
const busy = ref(false)
const error = ref('')

// owner 单独一列，admins / members 各自计数，三者相加才是全部人数。
const memberCount = computed(
  () => (props.team.owner ? 1 : 0) + (props.team.admins?.total ?? 0) + (props.team.members?.total ?? 0)
)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await props.join(message.value.trim())
    message.value = ''
  } catch {
    error.value = t('work.teamProfile.failed')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <v-card class="pa-6" max-width="560" width="100%" rounded="lg" flat border>
    <div class="t-eyebrow c-muted mb-4">{{ t('work.teamProfile.eyebrow') }}</div>
    <div class="d-flex align-center mb-4">
      <UserAvatar kind="org" :avatar="getAvatarUrl(team.avatarId)" :name="team.name" size="56" />
      <div class="ml-4">
        <h1 class="t-page-title">{{ team.name }}</h1>
        <p class="t-meta c-muted">{{ team.handle }}</p>
        <div class="t-meta c-muted mt-1">
          <span v-if="team.owner">
            <i18n-t keypath="work.teamProfile.owner" tag="span">
              <template #name><UserRef :handle="team.owner.username" :name="team.owner.nickname" /></template>
            </i18n-t>
            ·
          </span>
          {{ t('work.teamProfile.memberCount', { count: memberCount }) }}
        </div>
      </div>
    </div>
    <p v-if="team.intro" class="t-body mb-6">{{ team.intro }}</p>

    <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>

    <template v-if="team.joinStatus === 'member'">
      <p class="t-body c-muted mb-4">{{ t('work.teamProfile.member') }}</p>
      <BaseButton kind="primary" :to="{ name: 'TeamsDetailDefault', params: { handle: team.handle } }">
        {{ t('work.teamProfile.enter') }}
      </BaseButton>
    </template>
    <p v-else-if="team.joinStatus === 'pending'" class="t-body c-muted">{{ t('work.teamProfile.pending') }}</p>
    <div v-else>
      <p class="t-body c-muted mb-4">
        {{ team.joinApproval ? t('work.teamProfile.applyHint') : t('work.teamProfile.joinHint') }}
      </p>
      <v-textarea
        v-if="team.joinApproval"
        v-model="message"
        autocomplete="off"
        :label="t('work.teamProfile.reason')"
        :placeholder="t('work.teamProfile.reasonPlaceholder')"
        variant="outlined"
        rows="3"
        auto-grow
        hide-details
        class="mb-4"
      />
      <BaseButton kind="primary" :loading="busy" @click="submit">
        {{ team.joinApproval ? t('work.teamProfile.apply') : t('work.teamProfile.join') }}
      </BaseButton>
    </div>
  </v-card>
</template>
