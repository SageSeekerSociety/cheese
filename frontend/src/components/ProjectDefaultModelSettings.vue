<script setup lang="ts">
import type { ProjectDefaultModel } from '../api'

import { computed, onMounted, ref, watch } from 'vue'

import { holdRevealGate } from '@/composables/useRevealGate'

import { getProjectDefaultModel, setProjectDefaultModel } from '../api'
import { t } from '../i18n'

// 项目默认模型：#1365 之后主线（房间聊天）读 binding.resolve(None, …)，它拿
// catalog 里 default=True 的那条；catalog 由 model_choices 算，项目 settings 里
// 显式写过 default_model 就把那条标 True。这一节就是那个写入口——之前只能手改
// 数据库，对应事故就是「agent 配了 deepseek，主线静默换到订阅 sonnet」。
const props = defineProps<{ projectId: string }>()

const state = ref<ProjectDefaultModel | null>(null)
const error = ref('')
const busy = ref(false)

// 本地编辑态：用户在下拉里选了一个值但还没保存。null = 清掉显式设置（回落部署默认）。
const draft = ref<string | null | undefined>(undefined)
const subagentDraft = ref<string | null>(null)

const effective = computed({
  get() {
    if (!state.value) return null
    const model = draft.value !== undefined ? draft.value : state.value.model
    return model ?? state.value.deployment_default
  },
  set(model: string | null) {
    draft.value = model
  },
})

const dirty = computed(() => {
  if (!state.value) return false
  const server = state.value.model ?? null
  const now = draft.value !== undefined ? draft.value : server
  return now !== server || subagentDraft.value !== (state.value.subagent_model ?? null)
})

async function load() {
  error.value = ''
  try {
    state.value = await getProjectDefaultModel(props.projectId)
    subagentDraft.value = state.value.subagent_model ?? null
    draft.value = undefined
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载默认模型失败'
  }
}

async function save() {
  if (!state.value?.can_manage) return
  busy.value = true
  error.value = ''
  try {
    const target = draft.value !== undefined ? draft.value : state.value.model
    state.value = await setProjectDefaultModel(props.projectId, target, subagentDraft.value)
    draft.value = undefined
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存默认模型失败'
  } finally {
    busy.value = false
  }
}

async function resetToDeploymentDefault() {
  if (!state.value?.can_manage) return
  draft.value = null
}

// 首次取数期间占住设置页的显示闸，见 useRevealGate。
const releaseGate = holdRevealGate()
onMounted(() => load().finally(releaseGate))
watch(() => props.projectId, load)
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">{{ t('work.models.description') }}</p>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <div class="d-flex flex-column" style="gap: 24px; max-width: 480px">
        <v-select
          v-model="effective"
          :items="state.choices"
          item-title="label"
          item-value="id"
          :disabled="busy || !state.can_manage"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :label="t('work.models.main')"
        />
        <v-select
          v-model="subagentDraft"
          :items="[{ id: null, label: t('work.models.inheritMain') }, ...state.choices]"
          item-title="label"
          item-value="id"
          :disabled="busy || !state.can_manage"
          autocomplete="off"
          density="compact"
          variant="outlined"
          :label="t('work.models.subagent')"
          :hint="t('work.models.subagentHint')"
          persistent-hint
        />
        <div class="d-flex align-center" style="gap: 12px">
          <v-btn
            v-if="state.can_manage"
            color="primary"
            variant="flat"
            density="comfortable"
            :disabled="busy || !dirty"
            @click="save"
          >
            保存
          </v-btn>
          <v-btn
            v-if="state.can_manage && state.model !== null"
            variant="text"
            density="comfortable"
            :disabled="busy"
            @click="resetToDeploymentDefault"
          >
            恢复部署默认
          </v-btn>
        </div>
      </div>
      <p v-if="!state.can_manage" class="text-body-2 text-medium-emphasis mt-3">
        {{ t('work.models.readOnly') }}
      </p>
    </template>
  </div>
</template>
