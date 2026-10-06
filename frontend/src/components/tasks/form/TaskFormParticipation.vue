<script setup lang="ts">
// 「参与」那一节：个人还是团队、难度、人数上限；团队题再加每队几人、报名通过后还能
// 不能换人。
//
// 它只画：值都是 props 进来的，改一次往上报一次。
import type { TaskTeamMembershipLockPolicy } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`。 */
type FieldControl = Record<string, any>

defineProps<{
  /** 改题时参与方式不能再换（领取记录按它分了人和队）。 */
  isEditing: boolean
  submitterTypeControl: FieldControl
  rankControl: FieldControl
  participantLimitControl: FieldControl
  minTeamSizeControl: FieldControl
  maxTeamSizeControl: FieldControl
}>()

const submitterType = defineModel<'USER' | 'TEAM' | undefined>('submitterType', { required: true })
const rank = defineModel<number | undefined>('rank', { required: true })
const participantLimit = defineModel<number | null | undefined>('participantLimit', { required: true })
const participantLimitUnlimited = defineModel<boolean>('participantLimitUnlimited', { required: true })
const teamLockingPolicy = defineModel<TaskTeamMembershipLockPolicy | undefined>('teamLockingPolicy', {
  required: true,
})
const minTeamSize = defineModel<number | undefined>('minTeamSize', { required: true })
const maxTeamSize = defineModel<number | undefined>('maxTeamSize', { required: true })

const { t } = useI18n()

const errorOf = (control: FieldControl): string | undefined => control['error-messages']?.[0]

const submitterOptions = computed(() => [
  { value: 'USER', label: t('tasks.form.individual') },
  { value: 'TEAM', label: t('tasks.form.team') },
])

const rankOptions = computed(() => [
  { value: '1', label: t('tasks.form.beginner') },
  { value: '2', label: t('tasks.form.intermediate') },
  { value: '3', label: t('tasks.form.advanced') },
])

const submitterValue = computed({
  get: () => submitterType.value ?? '',
  set: (value: string) => {
    submitterType.value = value as 'USER' | 'TEAM'
  },
})

const rankValue = computed({
  get: () => (rank.value ? String(rank.value) : ''),
  set: (value: string) => {
    rank.value = Number(value)
  },
})

const lockOptions = computed(() => [
  { value: 'NO_LOCK', label: t('tasks.form.teamLocking.free'), hint: t('tasks.form.teamLocking.freeHint') },
  {
    value: 'LOCK_ON_APPROVAL',
    label: t('tasks.form.teamLocking.locked'),
    hint: t('tasks.form.teamLocking.lockedHint'),
  },
])

const isTeam = computed(() => submitterType.value === 'TEAM')
const limitLabel = computed(() => (isTeam.value ? t('tasks.form.teamLimit') : t('tasks.form.participantLimit')))

/** 数字框：清空就是没填（`undefined`），不是 0。 */
const toNumber = (value: string | number | null) => (value === '' || value === null ? undefined : Number(value))
</script>

<template>
  <TaskFormSection :title="t('tasks.form.participation')">
    <div class="tf-grid">
      <BaseField
        :label="t('tasks.form.submitterType')"
        required
        :hint="t('tasks.form.submitterTypeFixed')"
        :error="errorOf(submitterTypeControl)"
      >
        <SegmentedControl
          v-if="!isEditing"
          v-model="submitterValue"
          :options="submitterOptions"
          :label="t('tasks.form.submitterType')"
          size="md"
          class="tf-seg"
        />
        <span v-else class="tf-fixed" data-testid="submitter-type-fixed">{{
          submitterOptions.find((option) => option.value === submitterType)?.label
        }}</span>
      </BaseField>

      <BaseField :label="t('tasks.form.rank')" required :error="errorOf(rankControl)">
        <SegmentedControl
          v-model="rankValue"
          :options="rankOptions"
          :label="t('tasks.form.rank')"
          size="md"
          class="tf-seg"
        />
      </BaseField>
    </div>

    <div class="tf-grid">
      <BaseField
        v-if="isTeam"
        :label="t('tasks.form.teamSize')"
        required
        :error="errorOf(maxTeamSizeControl) ?? errorOf(minTeamSizeControl)"
      >
        <div class="tf-inline">
          <v-text-field
            :model-value="minTeamSize"
            type="number"
            min="1"
            class="tf-num"
            :aria-label="t('tasks.form.minTeamSize')"
            :error="Boolean(errorOf(minTeamSizeControl))"
            variant="outlined"
            density="comfortable"
            hide-details
            @update:model-value="minTeamSize = toNumber($event)"
          />
          <span aria-hidden="true">–</span>
          <v-text-field
            :model-value="maxTeamSize"
            type="number"
            min="1"
            class="tf-num"
            :aria-label="t('tasks.form.maxTeamSize')"
            :error="Boolean(errorOf(maxTeamSizeControl))"
            variant="outlined"
            density="comfortable"
            hide-details
            @update:model-value="maxTeamSize = toNumber($event)"
          />
          <span>{{ t('tasks.form.teamSizeUnit') }}</span>
        </div>
      </BaseField>

      <BaseField :label="limitLabel" :error="errorOf(participantLimitControl)">
        <template #default="{ id, describedby, invalid }">
          <div class="tf-inline">
            <v-text-field
              :id="id"
              :model-value="participantLimit"
              :aria-describedby="describedby"
              :aria-invalid="invalid"
              :error="invalid"
              :disabled="participantLimitUnlimited"
              :placeholder="participantLimitUnlimited ? t('tasks.form.unlimited') : undefined"
              type="number"
              min="1"
              class="tf-limit"
              variant="outlined"
              density="comfortable"
              hide-details
              @update:model-value="participantLimit = toNumber($event) ?? null"
            />
            <!-- 「不限」是把输入框里的数去掉（不填 = 不限），可手删拿到的空串会被校验当成
                 填错，所以给一条走得通的路，并把框锁上：锁上就看得见这个数现在不算数。 -->
            <v-checkbox
              v-model="participantLimitUnlimited"
              :label="t('tasks.form.unlimited')"
              density="compact"
              hide-details
              class="flex-grow-0"
            />
          </div>
        </template>
      </BaseField>
    </div>

    <BaseField v-if="isTeam" :label="t('tasks.form.teamLocking.label')">
      <v-radio-group v-model="teamLockingPolicy" hide-details density="compact" class="tf-choices">
        <v-radio v-for="option in lockOptions" :key="option.value" :value="option.value">
          <template #label>
            <span>
              {{ option.label }}
              <span class="tf-choice-hint">{{ option.hint }}</span>
            </span>
          </template>
        </v-radio>
      </v-radio-group>
    </BaseField>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
<style scoped>
.tf-seg {
  align-self: flex-start;
}

.tf-fixed {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--muted);
  font-size: 14px;
}

.tf-limit {
  flex: 1 1 160px;
  min-width: 0;
}
</style>
