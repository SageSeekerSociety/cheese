<!--
  团队设置最后那一栏：所有者在这里转让或解散团队，其他成员在这里退出。只吃 props：
  三件事的请求都在页面（`Leave.vue`），这里画两三行和它们各自的确认框，报按了哪一颗。
-->
<template>
  <div class="settings-page">
    <header class="settings-page__head">
      <h1 class="t-page-title">{{ owner ? t('teams.settings.transferAndDisband') : t('teams.settings.leave') }}</h1>
    </header>

    <section v-if="owner" class="settings-card">
      <div class="srow">
        <div class="srow__v leave-row">
          <span class="leave-row__title">{{ t('home.nav.transferTeam') }}</span>
          <span>{{ t('teams.settings.transferHint') }}</span>
        </div>
        <BaseButton kind="secondary" @click="transferring = true">{{ t('teams.settings.transfer') }}</BaseButton>
      </div>
      <div class="srow">
        <div class="srow__v leave-row">
          <span class="leave-row__title leave-row__title--danger">{{ t('home.nav.disbandTeam') }}</span>
          <span>{{ t('teams.settings.disbandHint') }}</span>
        </div>
        <BaseButton kind="danger" @click="disbanding = true">{{ t('teams.settings.disband') }}</BaseButton>
      </div>
    </section>

    <section v-else class="settings-card">
      <div class="srow">
        <div class="srow__v leave-row">
          <span class="leave-row__title leave-row__title--danger">{{ t('home.nav.leaveTeam') }}</span>
          <span>{{ t('teams.settings.leaveHint') }}</span>
        </div>
        <BaseButton kind="danger" @click="leaving = true">{{ t('teams.settings.leaveAction') }}</BaseButton>
      </div>
    </section>

    <!-- Transfer and disband show their reason inside their own dialog; leaving has none. -->
    <p v-if="error && !transferring && !disbanding" role="alert" class="t-body c-danger">{{ error }}</p>

    <TransferTeamDialog
      v-model="transferring"
      :team="team"
      :candidates="candidates"
      :loading="candidatesLoading"
      :busy="busy"
      :error="error"
      @confirm="(userId: number) => emit('transfer', userId)"
    />
    <DisbandTeamDialog v-model="disbanding" :team="team" :busy="busy" :error="error" @confirm="emit('disband')" />
    <ConfirmDialog
      v-model="leaving"
      :title="t('home.nav.leaveTeamTitle', { name: team.name })"
      :confirm-label="t('home.nav.leaveTeam')"
      :loading="busy"
      danger
      @confirm="emit('leave')"
    >
      {{ t('home.nav.leaveTeamBody') }}
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import type { Team, TeamMember } from '@/types'

import { watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
import DisbandTeamDialog from '@/views/teams/DisbandTeamDialog.vue'
import TransferTeamDialog from '@/views/teams/TransferTeamDialog.vue'

defineProps<{
  team: Team
  /** 读者是所有者：转让与解散；别人：退出。 */
  owner: boolean
  /** 能接手的人（已经去掉读者自己）。 */
  candidates: TeamMember[]
  candidatesLoading: boolean
  /** 正在转让、解散或退出：确认框上的按钮转圈。 */
  busy: boolean
  /** 上一次没做成的理由，画在打开着的那个确认框里。 */
  error: string | null
}>()

const emit = defineEmits<{
  /** 打开了转让的框：页面去读能接手的人。 */
  openTransfer: []
  transfer: [userId: number]
  disband: []
  leave: []
}>()

const transferring = defineModel<boolean>('transferring', { required: true })
const disbanding = defineModel<boolean>('disbanding', { required: true })
const leaving = defineModel<boolean>('leaving', { required: true })

watch(transferring, (open) => {
  if (open) emit('openTransfer')
})
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.leave-row {
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
}

.leave-row__title {
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
}

.leave-row__title--danger {
  color: var(--danger-ink);
}
</style>
