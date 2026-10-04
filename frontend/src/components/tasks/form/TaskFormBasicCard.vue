<script setup lang="ts">
// 基本信息那张卡：题名、参与身份、题目等级、人数 / 队伍上限，以及选了「队伍」之后才
// 出现的那两项（锁定策略、最小 / 最大队伍人数）和底下那段说明。
//
// 它只画。值是 props 进来的，改一个字就往上报一次（`v-model:<字段>`），要标红的那几
// 样是 `defineField` 的另一半（`:name-control` 之类 —— 一个对象，`v-bind` 到控件上）。
// 这一件不知道表单从哪来、提交去哪，所以能单独摆在预览站里。
//
// `isEditing` 只做一件事：题名和参与身份在改题时不让动（参与身份改了就是另一道题）。
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`，`v-bind` 到控件上。 */
type FieldControl = Record<string, any>

defineProps<{
  isEditing?: boolean
  /** 走「只发参数」那条路时不画题名（名字由外面定）。 */
  parametersOnly?: boolean
  nameControl: FieldControl
  submitterTypeControl: FieldControl
  rankControl: FieldControl
  participantLimitControl: FieldControl
  teamLockingPolicyControl: FieldControl
  minTeamSizeControl: FieldControl
  maxTeamSizeControl: FieldControl
}>()

const name = defineModel<string | undefined>('name', { required: true })
const submitterType = defineModel<'USER' | 'TEAM' | undefined>('submitterType', { required: true })
const rank = defineModel<number | undefined>('rank', { required: true })
const participantLimit = defineModel<number | null | undefined>('participantLimit', { required: true })
/** 「不限」那一勾：不是表单字段，只说明旁边那个数现在算不算数。 */
const participantLimitUnlimited = defineModel<boolean | undefined>('participantLimitUnlimited', { required: true })
const teamLockingPolicy = defineModel<'NO_LOCK' | 'LOCK_ON_APPROVAL' | undefined>('teamLockingPolicy', {
  required: true,
})
const minTeamSize = defineModel<number | undefined>('minTeamSize', { required: true })
const maxTeamSize = defineModel<number | undefined>('maxTeamSize', { required: true })

const { t } = useI18n()

const teamLockingPolicyItems = computed(() => [
  {
    title: t('tasks.form.teamLockingPolicyNoLock'),
    value: 'NO_LOCK',
    description: t('tasks.form.teamLockingPolicyNoLockDesc'),
  },
  {
    title: t('tasks.form.teamLockingPolicyLockOnApproval'),
    value: 'LOCK_ON_APPROVAL',
    description: t('tasks.form.teamLockingPolicyLockOnApprovalDesc'),
  },
])
</script>

<template>
  <TaskFormSection icon="mdi-information-outline" :title="t('tasks.form.basicInfo')">
    <v-row dense>
      <v-col cols="12">
        <v-text-field
          v-if="!parametersOnly"
          v-model="name"
          autocomplete="off"
          :label="t('tasks.form.taskName')"
          required
          aria-required="true"
          v-bind="nameControl"
        >
          <template #label="{ label }"> {{ label }}<span class="tf-req" aria-hidden="true"></span> </template>
        </v-text-field>
      </v-col>

      <v-col cols="12" md="6">
        <v-radio-group
          v-model="submitterType"
          :label="t('tasks.form.participantType')"
          required
          aria-required="true"
          inline
          :disabled="isEditing"
          v-bind="submitterTypeControl"
          class="mt-0"
        >
          <template #label="{ label }"> {{ label }}<span class="tf-req" aria-hidden="true"></span> </template>
          <v-radio :label="t('tasks.form.individual')" value="USER"></v-radio>
          <v-radio :label="t('tasks.form.team')" value="TEAM"></v-radio>
        </v-radio-group>
      </v-col>

      <v-col cols="12" md="6">
        <v-radio-group
          v-model="rank"
          :label="t('tasks.form.taskLevel')"
          required
          aria-required="true"
          inline
          v-bind="rankControl"
          class="mt-0"
        >
          <template #label="{ label }"> {{ label }}<span class="tf-req" aria-hidden="true"></span> </template>
          <v-radio :label="t('tasks.form.beginner')" :value="1"></v-radio>
          <v-radio :label="t('tasks.form.intermediate')" :value="2"></v-radio>
          <v-radio :label="t('tasks.form.advanced')" :value="3"></v-radio>
        </v-radio-group>
      </v-col>

      <v-col cols="12" md="6">
        <div class="d-flex align-center">
          <v-text-field
            v-model.number="participantLimit"
            :label="submitterType === 'TEAM' ? t('tasks.form.teamLimit') : t('tasks.form.participantLimit')"
            type="number"
            min="1"
            :disabled="participantLimitUnlimited"
            v-bind="participantLimitControl"
            :hint="t('tasks.form.participantLimitHint')"
            class="flex-grow-1"
          >
            <template #append-inner>
              <v-icon size="small" color="primary">mdi-account-group</v-icon>
            </template>
          </v-text-field>
          <!-- 「不限」是把输入框里的数**去掉**（不填 = 不限），可手删拿到的空串会被
               校验当成填错，所以那条路走不通；这个勾给出一条走得通的，并且把框锁上
               —— 锁上之后就看得见「这个数现在不算数」。 -->
          <v-checkbox
            v-model="participantLimitUnlimited"
            :label="t('tasks.form.unlimited')"
            density="compact"
            hide-details
            class="ms-4 flex-grow-0"
          />
        </div>
      </v-col>

      <!-- 小队人数限制 -->
      <template v-if="submitterType === 'TEAM'">
        <v-col cols="12" md="6">
          <v-select
            v-model="teamLockingPolicy"
            autocomplete="off"
            :label="t('tasks.form.teamLockingPolicy')"
            required
            aria-required="true"
            v-bind="teamLockingPolicyControl"
            density="comfortable"
            :items="teamLockingPolicyItems"
            item-title="title"
            item-value="value"
          >
            <template #label="{ label }"> {{ label }}<span class="tf-req" aria-hidden="true"></span> </template>
            <template #prepend-inner>
              <v-icon size="small" color="primary">mdi-lock-outline</v-icon>
            </template>
            <template #item="{ item, props: slotProps }">
              <v-list-item v-bind="slotProps">
                <template #prepend>
                  <v-icon
                    :icon="item.raw && item.raw.value === 'NO_LOCK' ? 'mdi-lock-open-outline' : 'mdi-lock-outline'"
                    color="primary"
                    class="mr-2"
                  ></v-icon>
                </template>
                <v-list-item-subtitle v-if="item.raw" class="text-wrap">{{
                  item.raw.description
                }}</v-list-item-subtitle>
              </v-list-item>
            </template>
          </v-select>
        </v-col>

        <v-col cols="12" md="6">
          <v-text-field
            v-model.number="minTeamSize"
            :label="t('tasks.form.minTeamSize')"
            type="number"
            required
            aria-required="true"
            min="1"
            v-bind="minTeamSizeControl"
          >
            <template #label="{ label }"> {{ label }}<span class="tf-req" aria-hidden="true"></span> </template>
            <template #append-inner>
              <v-icon size="small" color="primary">mdi-account-multiple-outline</v-icon>
            </template>
          </v-text-field>
        </v-col>
        <v-col cols="12" md="6">
          <v-text-field
            v-model.number="maxTeamSize"
            :label="t('tasks.form.maxTeamSize')"
            type="number"
            required
            aria-required="true"
            min="1"
            v-bind="maxTeamSizeControl"
          >
            <template #label="{ label }"> {{ label }}<span class="tf-req" aria-hidden="true"></span> </template>
            <template #append-inner>
              <v-icon size="small" color="primary">mdi-account-group</v-icon>
            </template>
          </v-text-field>
        </v-col>
        <v-col cols="12">
          <v-alert
            color="info"
            variant="tonal"
            density="comfortable"
            border="start"
            class="mt-2"
            icon="mdi-information-outline"
          >
            <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.teamMemberManagement') }}</div>
            <p class="text-body-2 mb-0">
              •
              <i18n-t scope="global" keypath="tasks.form.teamNote.recorded" tag="span">
                <template #roster>
                  <strong>{{ t('tasks.form.teamNote.roster') }}</strong>
                </template>
              </i18n-t>
              <br />
              • {{ t('tasks.form.teamNote.basis') }}<br />
              • <strong>{{ t('tasks.form.teamLockingPolicyNoLock') }}</strong
              >{{ t('tasks.form.teamNote.noLock') }}<br />
              • <strong>{{ t('tasks.form.teamLockingPolicyLockOnApproval') }}</strong
              >{{ t('tasks.form.teamNote.lockOnApproval') }}
            </p>
          </v-alert>
        </v-col>
      </template>
    </v-row>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
