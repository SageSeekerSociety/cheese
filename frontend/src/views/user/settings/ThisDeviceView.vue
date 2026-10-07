<!--
  「这台设备」的画法（样稿 ④）：桌面 app 所在的这台电脑接入了没有、叫什么、提供给哪些团队、
  机主自己的 Claude Code，以及断开。取数和保存在同目录的 ThisDevice.vue。
-->
<template>
  <div class="settings-page">
    <header>
      <h1 class="t-page-title">{{ t('account.thisDevice.title') }}</h1>
    </header>

    <v-alert v-if="error" type="error" density="comfortable" class="mt-4">{{ error }}</v-alert>

    <div v-if="loading" class="d-flex justify-center py-6">
      <v-progress-circular indeterminate size="24" />
    </div>

    <section v-else-if="!connected" class="settings-card this-device__empty">
      <div class="this-device__status">
        <span class="this-device__dot" aria-hidden="true" />
        <span>{{ t('account.thisDevice.notConnected') }}</span>
      </div>
      <p class="this-device__body">{{ t('account.thisDevice.notConnectedBody') }}</p>
      <div>
        <BaseButton kind="primary" :loading="connecting" @click="emit('connect')">
          {{ t('account.connectFlow.connect') }}
        </BaseButton>
      </div>
    </section>

    <section v-else class="settings-card">
      <div class="srow">
        <span class="srow__k">{{ t('account.thisDevice.status') }}</span>
        <span class="this-device__status" :class="{ 'this-device__status--on': online }">
          <span class="this-device__dot" :class="{ 'this-device__dot--on': online }" aria-hidden="true" />
          {{ online ? t('account.thisDevice.online') : t('account.thisDevice.offline') }}
        </span>
      </div>

      <div class="srow">
        <label class="srow__k" for="this-device-name">{{ t('account.thisDevice.name') }}</label>
        <v-text-field
          id="this-device-name"
          v-model="draftName"
          autocomplete="off"
          variant="outlined"
          density="compact"
          hide-details
          class="this-device__field"
          @blur="emit('rename', draftName)"
          @keyup.enter="emit('rename', draftName)"
        />
      </div>

      <div class="srow srow--top">
        <span class="srow__k">{{ t('account.thisDevice.teams') }}</span>
        <div class="this-device__col">
          <DeviceTeamsPicker
            v-if="editingTeams"
            :teams="teams"
            :model-value="teamIds"
            @update:model-value="(ids) => emit('teams', ids)"
          />
          <div v-else class="this-device__chips">
            <span v-for="team in teamNames" :key="team" class="this-device__chip">{{ team }}</span>
            <span v-if="!teamNames.length" class="this-device__muted">{{ t('account.thisDevice.noTeams') }}</span>
          </div>
          <div class="this-device__row">
            <BaseButton kind="ghost" size="sm" @click="editingTeams = !editingTeams">
              {{ editingTeams ? t('account.thisDevice.teamsDone') : t('account.thisDevice.teamsEdit') }}
            </BaseButton>
          </div>
          <span class="this-device__muted">{{ t('account.thisDevice.teamsHint') }}</span>
        </div>
      </div>

      <div class="srow srow--top">
        <span class="srow__k">Claude Code</span>
        <ClaudeCodeLogin
          class="this-device__col"
          :logged-in="claudeLoggedIn"
          :plan="claudePlan"
          :service="claudeService"
          :state="claudeState"
          can-log-out
          :can-use-model-service="canUseModelService"
          @login="(console) => emit('claudeLogin', console)"
          @service="(service) => emit('claudeService', service)"
          @cancel="emit('claudeCancel')"
          @logout="emit('claudeLogout')"
        />
      </div>

      <div class="srow">
        <div class="this-device__col">
          <span class="srow__k">{{ t('account.thisDevice.disconnectTitle') }}</span>
          <span class="this-device__muted">{{ t('account.thisDevice.disconnectBody') }}</span>
        </div>
        <BaseButton kind="danger" @click="confirming = true">{{ t('account.thisDevice.disconnect') }}</BaseButton>
      </div>
    </section>

    <ConfirmDialog
      v-model="confirming"
      :title="t('account.thisDevice.disconnectConfirm')"
      :confirm-label="t('account.thisDevice.disconnect')"
      danger
      @confirm="disconnect"
    >
      {{ t('account.thisDevice.disconnectBody') }}
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import type { MyTeam } from '@/cx_types'
import type { ModelServiceInput } from '@/types/ownAgents'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import ClaudeCodeLogin from '@/components/settings/ClaudeCodeLogin.vue'
import DeviceTeamsPicker from '@/components/settings/DeviceTeamsPicker.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ThisDeviceView' })

const props = defineProps<{
  loading: boolean
  connected: boolean
  online: boolean
  connecting: boolean
  name: string
  teams: MyTeam[]
  teamIds: number[]
  claudeLoggedIn: boolean
  claudePlan: string | null
  /** 用其他模型服务时它调用的模型名。 */
  claudeService: string | null
  claudeState: 'idle' | 'preparing' | 'browser'
  /** 这个桌面端能不能接其他模型服务。 */
  canUseModelService: boolean
  error: string | null
}>()

const emit = defineEmits<{
  connect: []
  rename: [name: string]
  teams: [ids: number[]]
  claudeLogin: [console: boolean]
  claudeService: [service: ModelServiceInput]
  claudeCancel: []
  claudeLogout: []
  disconnect: []
}>()

const draftName = ref(props.name)
watch(
  () => props.name,
  (v) => (draftName.value = v)
)

const editingTeams = ref(false)
const teamNames = computed(() =>
  props.teams
    .filter((team) => props.teamIds.includes(team.id))
    .map((team) => (team.personal ? t('account.thisDevice.ownProjects') : team.name))
)

const confirming = ref(false)
function disconnect() {
  confirming.value = false
  emit('disconnect')
}
</script>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.this-device__empty {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px 24px;
}

.this-device__body {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.this-device__status {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.this-device__status--on {
  color: var(--ok-ink);
}

.this-device__dot {
  width: 8px;
  height: 8px;
  background: var(--faint);
  border-radius: var(--radius-pill);
}

.this-device__dot--on {
  background: var(--ok);
}

.this-device__field {
  max-width: 360px;
}

.this-device__col {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.this-device__row {
  display: flex;
}

.this-device__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.this-device__chip {
  padding: 2px 10px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background: var(--canvas);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.this-device__muted {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.srow--top {
  align-items: flex-start;
}
</style>
