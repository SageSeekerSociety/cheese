<script setup lang="ts">
// 小队所有者 / 管理员管理「别人怎么找到、怎么进来」的地方：团队地址（handle，
// `/teams/<handle>`）、小队链接（长期有效，可重置）、加入要不要审批、搜不搜得到。
// 取数在 useTeamJoinLinkCard.ts 里由页面持有；这里只认 props/emits。
import type { Team, TeamVisibility } from '@/types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  team: Team
  link: { token: string; approval: boolean } | null
  busy: boolean
  error: string
  copied: boolean
  url: string
  handle: string
  handleError: string
  addressPrefix: string
}>()

defineEmits<{
  'update:handle': [value: string]
  save: []
  reset: []
  setApproval: [approval: boolean | null]
  setVisibility: [visibility: TeamVisibility]
  copy: []
}>()

const handleChanged = computed(() => props.handle.trim() !== props.team.handle)
</script>

<template>
  <v-card flat border rounded="lg" class="pa-4 mb-4">
    <h3 class="t-title mb-4">{{ t('work.teamLink.cardTitle') }}</h3>

    <p class="t-body mb-1">{{ t('work.teamLink.address') }}</p>
    <div class="d-flex flex-wrap align-center ga-2">
      <v-text-field
        :model-value="handle"
        autocomplete="off"
        :prefix="addressPrefix"
        :label="t('work.teamLink.address')"
        :hint="t('work.teamLink.addressHint')"
        :error-messages="handleError"
        persistent-hint
        density="compact"
        variant="outlined"
        class="link-field"
        @update:model-value="$emit('update:handle', $event)"
      />
      <BaseButton kind="primary" :disabled="busy || !handleChanged || !handle.trim()" @click="$emit('save')">
        {{ t('work.teamLink.save') }}
      </BaseButton>
    </div>

    <p class="t-body mt-4 mb-1">{{ t('work.teamLink.title') }}</p>
    <p class="t-meta c-muted mb-2">{{ t('work.teamLink.description') }}</p>
    <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
    <v-progress-linear v-if="busy && !link" indeterminate :aria-label="t('work.teamLink.loading')" />
    <template v-if="link">
      <div class="d-flex flex-wrap align-center ga-2">
        <v-text-field
          autocomplete="off"
          :model-value="url"
          :label="t('work.teamLink.title')"
          readonly
          hide-details
          density="compact"
          variant="outlined"
          class="link-field"
        />
        <BaseButton kind="primary" :disabled="busy" @click="$emit('copy')">
          {{ copied ? t('work.teamLink.copied') : t('work.teamLink.copy') }}
        </BaseButton>
        <BaseButton :disabled="busy" @click="$emit('reset')">{{ t('work.teamLink.reset') }}</BaseButton>
      </div>
      <p class="t-meta c-muted mt-2">{{ t('work.teamLink.resetHint') }}</p>

      <v-switch
        :model-value="link.approval"
        :label="t('work.teamLink.approval')"
        :disabled="busy"
        color="primary"
        hide-details
        inset
        class="mt-2"
        @update:model-value="$emit('setApproval', $event)"
      />
      <p class="t-meta c-muted">{{ t('work.teamLink.approvalHint') }}</p>
    </template>

    <p class="t-body mt-4 mb-1">{{ t('work.teamLink.visibility') }}</p>
    <v-radio-group
      :model-value="team.visibility"
      :disabled="busy"
      hide-details
      @update:model-value="(value) => $emit('setVisibility', value as TeamVisibility)"
    >
      <v-radio value="public" :label="`${t('work.teamLink.public')} · ${t('work.teamLink.publicHint')}`" />
      <v-radio value="stealth" :label="`${t('work.teamLink.stealth')} · ${t('work.teamLink.stealthHint')}`" />
    </v-radio-group>
  </v-card>
</template>

<style scoped>
.link-field {
  flex: 1 1 280px;
  min-width: 0;
}
</style>
