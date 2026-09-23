<script setup lang="ts">
// 小队的对外一面：没加入的人看到的就是这一页。按地址打开（/teams/:handle）和按小队链接
// 打开（/team-invites/:token）是同一个组件，只有「加入」走哪条接口不同，由调用方传进来。
// 小队开着审批时，「加入」提交的是申请（带理由），结果是 pending；关着就直接成为成员。
import type { Team } from '@/types'

import { computed, ref } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

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
      <v-avatar size="56" color="surface-variant">
        <v-img :src="getAvatarUrl(team.avatarId)" alt="" />
      </v-avatar>
      <div class="ml-4">
        <h1 class="t-page-title">{{ team.name }}</h1>
        <p class="t-meta c-muted">@{{ team.handle }}</p>
        <div class="t-meta c-muted mt-1">
          <span v-if="team.owner">{{ t('work.teamProfile.owner', { name: team.owner.nickname }) }} · </span>
          {{ t('work.teamProfile.memberCount', { count: memberCount }) }}
        </div>
      </div>
    </div>
    <p v-if="team.intro" class="t-body mb-6">{{ team.intro }}</p>

    <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>

    <template v-if="team.joinStatus === 'member'">
      <p class="t-body c-muted mb-4">{{ t('work.teamProfile.member') }}</p>
      <v-btn color="primary" variant="flat" :to="{ name: 'TeamsDetailDefault', params: { handle: team.handle } }">
        {{ t('work.teamProfile.enter') }}
      </v-btn>
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
      <v-btn color="primary" variant="flat" :loading="busy" @click="submit">
        {{ team.joinApproval ? t('work.teamProfile.apply') : t('work.teamProfile.join') }}
      </v-btn>
    </div>
  </v-card>
</template>
