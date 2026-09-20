<script setup lang="ts">
import type { ComputeChoice, ProjectComputeConfigs } from '../cx_types'

import { onMounted, ref, watch } from 'vue'

import { getProjectComputeConfigs, saveProjectComputeConfigs } from '../api'
import { choiceDetail, choiceKey, compactChoices } from '../lib/computeConfig'

import ComputeChoiceForm from './ComputeChoiceForm.vue'

const props = defineProps<{ projectId: string }>()
const state = ref<ProjectComputeConfigs | null>(null)
const error = ref('')
const busy = ref(false)
const adding = ref(false)
async function load() {
  error.value = ''
  try {
    state.value = await getProjectComputeConfigs(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载算力配置失败'
  }
}
async function save(defaultChoice: ComputeChoice, favorites: ComputeChoice[]) {
  if (!state.value?.can_manage) return
  busy.value = true
  error.value = ''
  try {
    const result = await saveProjectComputeConfigs(props.projectId, { default: defaultChoice, favorites })
    state.value = { ...state.value, ...result }
    window.dispatchEvent(new Event('project-compute-updated'))
    adding.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存算力配置失败'
  } finally {
    busy.value = false
  }
}
function add(choice: ComputeChoice) {
  if (!state.value) return
  return save(
    state.value.default,
    compactChoices(state.value.default, [...state.value.favorites, choice]).filter(
      (c) => choiceKey(c) !== choiceKey(state.value!.default)
    )
  )
}
function makeDefault(choice: ComputeChoice) {
  if (!state.value) return
  return save(
    choice,
    compactChoices(state.value.default, state.value.favorites).filter((c) => choiceKey(c) !== choiceKey(choice))
  )
}
onMounted(load)
watch(() => props.projectId, load)
</script>

<template>
  <div>
    <h3 class="text-subtitle-1 mb-2">默认与常用算力</h3>
    <p class="text-body-2 text-medium-emphasis mb-4">
      房间直接使用项目默认；临时选择只影响当前房间。已运行的房间保留原环境
    </p>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <div v-for="choice in compactChoices(state.default, state.favorites)" :key="choiceKey(choice)" class="config-row">
        <div>
          <span>{{ choice.name }}</span>
          <span v-if="choiceKey(choice) === choiceKey(state.default)" class="default-label">项目默认</span>
          <div class="text-body-2 text-medium-emphasis mt-1">{{ choiceDetail(choice) }}</div>
        </div>
        <div v-if="state.can_manage && choiceKey(choice) !== choiceKey(state.default)" class="d-flex">
          <v-btn size="small" variant="text" :disabled="busy" @click="makeDefault(choice)">设为默认</v-btn>
          <v-btn
            size="small"
            variant="text"
            :disabled="busy"
            @click="
              save(
                state.default,
                state.favorites.filter((c) => choiceKey(c) !== choiceKey(choice))
              )
            "
            >移出常用</v-btn
          >
        </div>
      </div>
      <v-btn v-if="state.can_manage" variant="text" class="mt-3" :disabled="busy" @click="adding = !adding">{{
        adding ? '收起' : '添加常用配置'
      }}</v-btn>
      <ComputeChoiceForm
        v-if="adding && state.can_manage"
        :devices="state.devices"
        :cloud-available="state.cloud_available"
        :busy="busy"
        named
        @select="add"
      />
      <p v-if="!state.can_manage" class="text-body-2 text-medium-emphasis mt-3">
        项目负责人管理默认和常用配置；你仍可在房间中临时选择算力
      </p>
    </template>
  </div>
</template>

<style scoped>
.config-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 0;
  border-bottom: 1px solid var(--line);
}
.default-label {
  margin-left: 8px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 13px;
}
</style>
