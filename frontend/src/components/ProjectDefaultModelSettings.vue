<script setup lang="ts">
import { onMounted, watch } from 'vue'

import { useProjectDefaultModel } from '@/composables/useProjectDefaultModel'
import { holdRevealGate } from '@/composables/useRevealGate'

import { t } from '../i18n'
import { modelChoiceProps } from '../lib/modelChoices'

import BaseButton from '@/components/base/BaseButton.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'

// 项目默认模型：#1365 之后主线（房间聊天）读 binding.resolve(None, …)，它拿
// catalog 里 default=True 的那条；catalog 由 model_choices 算，项目 settings 里
// 显式写过 default_model 就把那条标 True。这一节就是那个写入口——之前只能手改
// 数据库，对应事故就是「agent 配了 deepseek，主线静默换到订阅 sonnet」。
const props = defineProps<{ projectId: string }>()

const {
  state,
  loadError,
  effective,
  subagentDraft,
  mainItems,
  subagentItems,
  dirty,
  saving: busy,
  saved,
  saveError,
  load,
  save,
  resetToDeploymentDefault,
} = useProjectDefaultModel(() => props.projectId)

// 首次取数期间占住设置页的显示闸，见 useRevealGate。
const releaseGate = holdRevealGate()
onMounted(() => load().finally(releaseGate))
watch(() => props.projectId, load)
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">{{ t('work.models.description') }}</p>
    <v-alert v-if="loadError" type="error" variant="tonal" class="mb-3">{{ loadError }}</v-alert>
    <template v-if="state">
      <div class="d-flex flex-column" style="gap: 24px; max-width: 480px">
        <v-select
          v-model="effective"
          :items="mainItems"
          item-title="label"
          item-value="id"
          :item-props="modelChoiceProps"
          :disabled="busy || !state.can_manage"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :label="t('work.models.main')"
        />
        <v-select
          v-model="subagentDraft"
          :items="subagentItems"
          item-title="label"
          item-value="id"
          :item-props="modelChoiceProps"
          :disabled="busy || !state.can_manage"
          autocomplete="off"
          density="compact"
          variant="outlined"
          :label="t('work.models.subagent')"
          :hint="t('work.models.subagentHint')"
          persistent-hint
        />
        <div class="d-flex align-center" style="gap: 12px">
          <BaseButton v-if="state.can_manage" kind="primary" size="sm" :disabled="busy || !dirty" @click="save">
            {{ t('work.projectSettings.defaultModelBlock.save') }}
          </BaseButton>
          <BaseButton
            v-if="state.can_manage && state.model !== null"
            kind="secondary"
            size="sm"
            :disabled="busy"
            @click="resetToDeploymentDefault"
          >
            {{ t('work.projectSettings.defaultModelBlock.reset') }}
          </BaseButton>
          <SaveStatus :saving="busy" :saved="saved" :error="saveError" />
        </div>
      </div>
      <p v-if="!state.can_manage" class="text-body-2 text-medium-emphasis mt-3">
        {{ t('work.models.readOnly') }}
      </p>
    </template>
  </div>
</template>
