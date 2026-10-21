<script setup lang="ts">
import type { AdminInviteCode } from '@/types/invites'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { absDate } from '@/lib/dateText'
import { PERSONAL_INVITE_NOTE } from '@/types/invites'

// 平台发的注册邀请码：列出、生成、复制、停用。每个人自己的那张（备注 `personal`）
// 默认收起：它们由各人的「邀请好友」页管，数量会远多于平台发的码。
const props = defineProps<{
  /** `null` = 还没到货（或读失败，见 `error`）。 */
  codes: AdminInviteCode[] | null
  error: string | null
  busy: boolean
  /** 生成或停用失败的原话；`null` = 没失败。 */
  actionError: string | null
}>()

const emit = defineEmits<{
  create: [maxUses: number, note: string]
  deactivate: [id: number]
  copy: [code: string]
  retry: []
}>()

const { t } = useI18n()

const showPersonal = ref(false)
const isPersonal = (code: AdminInviteCode) => code.note === PERSONAL_INVITE_NOTE
const personalCount = computed(() => (props.codes ?? []).filter(isPersonal).length)
const shown = computed(() => (props.codes ?? []).filter((code) => showPersonal.value || !isPersonal(code)))

const maxUses = ref(1)
const note = ref('')
const canCreate = computed(() => Number.isInteger(maxUses.value) && maxUses.value >= 1 && !props.busy)
function create() {
  if (canCreate.value) emit('create', maxUses.value, note.value)
}

const confirming = ref<AdminInviteCode | null>(null)
const confirmOpen = computed({
  get: () => confirming.value !== null,
  set: (open: boolean) => {
    if (!open) confirming.value = null
  },
})
function deactivate() {
  if (!confirming.value) return
  emit('deactivate', confirming.value.id)
  confirming.value = null
}

const dateOf = (at: string | null) => (at ? absDate(Date.parse(at)) : '—')
</script>

<template>
  <section class="aic">
    <div class="aic__head">
      <h2 class="aic__h t-title">{{ t('credits.inviteCodes.title') }}</h2>
      <form class="aic__create" @submit.prevent="create">
        <v-text-field
          v-model.number="maxUses"
          type="number"
          min="1"
          autocomplete="off"
          variant="outlined"
          density="compact"
          :label="t('credits.inviteCodes.maxUses')"
          hide-details
          class="aic__uses"
        />
        <v-text-field
          v-model="note"
          autocomplete="off"
          variant="outlined"
          density="compact"
          :label="t('credits.inviteCodes.note')"
          hide-details
          class="aic__note"
        />
        <BaseButton type="submit" kind="secondary" prepend-icon="mdi-plus" :disabled="!canCreate" :loading="busy">
          {{ t('credits.inviteCodes.create') }}
        </BaseButton>
      </form>
    </div>
    <p v-if="actionError" class="aic__error" role="alert">{{ actionError }}</p>
    <v-switch
      v-if="personalCount"
      v-model="showPersonal"
      density="compact"
      hide-details
      :label="t('credits.inviteCodes.showPersonal', { n: personalCount })"
    />

    <BaseTable
      :label="t('credits.inviteCodes.label')"
      :cols="[null, '120px', null, '120px', '96px', '160px']"
      :loading="!props.codes && !props.error"
      :skeleton-rows="2"
      :state="props.error !== null ? 'error' : 'rows'"
      cards
      :empty="props.codes && !shown.length ? t('credits.inviteCodes.empty') : null"
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('credits.inviteCodes.column.code') }}</th>
          <th scope="col">{{ t('credits.inviteCodes.column.uses') }}</th>
          <th scope="col">{{ t('credits.inviteCodes.column.note') }}</th>
          <th scope="col">{{ t('credits.inviteCodes.column.created') }}</th>
          <th scope="col">{{ t('credits.inviteCodes.column.status') }}</th>
          <th scope="col" class="aic__num">{{ t('credits.inviteCodes.column.actions') }}</th>
        </tr>
      </template>

      <template #error>
        <BaseLoadError
          :title="t('credits.inviteCodes.loadFailed')"
          :error="props.error || undefined"
          :retry-label="t('credits.inviteCodes.retry')"
          @retry="emit('retry')"
        />
      </template>

      <tr v-for="code in shown" :key="code.id" :data-code="code.code">
        <td data-card="primary">
          <span class="aic__code">{{ code.code }}</span>
          <span v-if="isPersonal(code)" class="aic__tag">{{ t('credits.inviteCodes.personal') }}</span>
        </td>
        <td class="t-num" :data-label="t('credits.inviteCodes.column.uses')">
          {{ code.useCount }} / {{ code.maxUses }}
        </td>
        <td :data-label="t('credits.inviteCodes.column.note')">{{ isPersonal(code) ? '' : code.note }}</td>
        <td class="t-num" :data-label="t('credits.inviteCodes.column.created')">{{ dateOf(code.createdAt) }}</td>
        <td :data-label="t('credits.inviteCodes.column.status')">
          <span class="aic__status">
            <span class="status-dot" :class="code.isActive ? 'status-dot--ok' : 'status-dot--muted'" />
            {{ code.isActive ? t('credits.inviteCodes.active') : t('credits.inviteCodes.inactive') }}
          </span>
        </td>
        <td class="aic__num" :data-label="t('credits.inviteCodes.column.actions')">
          <BaseButton kind="ghost" size="sm" @click="emit('copy', code.code)">{{
            t('credits.inviteCodes.copy')
          }}</BaseButton>
          <BaseButton v-if="code.isActive" kind="ghost" size="sm" :disabled="busy" @click="confirming = code">{{
            t('credits.inviteCodes.deactivate')
          }}</BaseButton>
        </td>
      </tr>
    </BaseTable>

    <ConfirmDialog
      v-model="confirmOpen"
      :title="t('credits.inviteCodes.deactivateTitle', { code: confirming?.code ?? '' })"
      :confirm-label="t('credits.inviteCodes.deactivate')"
      danger
      @confirm="deactivate"
    >
      {{ t('credits.inviteCodes.deactivateBody') }}
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.aic {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.aic__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
}

.aic__h {
  margin: 0;
  color: var(--ink);
}

.aic__create {
  display: flex;
  flex: 1 1 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}

.aic__uses {
  flex: 0 0 120px;
}

.aic__note {
  flex: 0 1 220px;
}

.aic__error {
  margin: 0;
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}

.aic__code {
  color: var(--ink);
  font-family: var(--font-mono);
  font-weight: 600;
}

.aic__tag {
  margin-left: 8px;
  padding: 0 6px;
  background: var(--fill-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.aic__status {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.aic__num {
  text-align: right;
}
</style>
