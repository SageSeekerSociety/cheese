<script setup lang="ts">
import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { getTopicComputeProfile, setTopicComputeChoice } from '../api'
import { choiceDetail, choiceKey, compactChoices } from '../lib/computeConfig'

import ComputeChoiceForm from './ComputeChoiceForm.vue'

const props = defineProps<{ topicId: string }>()
const state = ref<TopicComputeProfile | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const menuOpen = ref(false)
const more = ref(false)
const choices = computed(() =>
  state.value ? compactChoices(state.value.project_default, state.value.favorites, state.value.choice) : []
)
const cloudAvailable = computed(() => state.value?.profiles.some((p) => p.id === 'cloud' && p.available) ?? false)
function online(choice: ComputeChoice): string {
  if (!choice.device_id) return ''
  const device = state.value?.devices.find((d) => d.device_id === choice.device_id)
  return device ? (device.online ? '在线' : '离线') : '已移出团队'
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    state.value = await getTopicComputeProfile(props.topicId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载算力失败'
  } finally {
    loading.value = false
  }
}
async function pick(choice: ComputeChoice) {
  if (!state.value || state.value.locked) return
  saving.value = true
  error.value = ''
  try {
    await setTopicComputeChoice(props.topicId, choice)
    await load()
    menuOpen.value = false
    more.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : '选择算力失败'
  } finally {
    saving.value = false
  }
}
onMounted(load)
onMounted(() => window.addEventListener('project-compute-updated', load))
onBeforeUnmount(() => window.removeEventListener('project-compute-updated', load))
watch(menuOpen, (open) => {
  if (open) void load()
})
watch(
  () => props.topicId,
  () => {
    menuOpen.value = false
    more.value = false
    void load()
  }
)
</script>

<template>
  <div class="compute-picker">
    <template v-if="state">
      <span v-if="state.visibility?.machine_access" class="cp-machine" :title="state.visibility.notice">
        <span class="status-dot status-dot--warn" /> 整台机器
      </span>
      <span v-if="state.locked" class="cp-chip" title="运行环境已固定，新建房间可另选配置">
        <v-icon size="13">mdi-lock-outline</v-icon> {{ state.choice.name }}
      </span>
      <v-menu v-else v-model="menuOpen" location="top start" :close-on-content-click="false">
        <template #activator="{ props: menuProps }">
          <button type="button" class="cp-chip" v-bind="menuProps" :disabled="loading">
            <v-icon size="16">{{ state.choice.profile === 'cloud' ? 'mdi-cloud-outline' : 'mdi-laptop' }}</v-icon>
            {{ state.choice.name }}
            <span v-if="choiceKey(state.choice) === choiceKey(state.project_default)" class="cp-badge">项目默认</span>
            <v-icon size="16">mdi-menu-up</v-icon>
          </button>
        </template>
        <v-card class="cp-menu">
          <div class="cp-heading">选择运行环境</div>
          <p class="cp-hint">仅影响当前房间，首次运行后固定</p>
          <button
            v-for="choice in choices"
            :key="choiceKey(choice)"
            type="button"
            class="cp-row"
            :disabled="saving"
            @click="pick(choice)"
          >
            <v-icon size="18">{{
              choiceKey(choice) === choiceKey(state.choice) ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'
            }}</v-icon>
            <div class="cp-body">
              <div class="cp-label">
                {{ choice.name }}
                <span v-if="choiceKey(choice) === choiceKey(state.project_default)" class="cp-badge">项目默认</span>
              </div>
              <div class="cp-hint">
                {{ choiceDetail(choice) }}<span v-if="online(choice)"> · {{ online(choice) }}</span>
              </div>
            </div>
          </button>
          <button type="button" class="cp-row" :disabled="saving" @click="more = !more">
            <v-icon size="18">{{ more ? 'mdi-chevron-up' : 'mdi-chevron-right' }}</v-icon> 其他配置与设备
          </button>
          <ComputeChoiceForm
            v-if="more"
            :devices="state.devices"
            :cloud-available="cloudAvailable"
            :busy="saving"
            @select="pick"
          />
          <p v-if="error" role="alert" class="cp-error">{{ error }}</p>
        </v-card>
      </v-menu>
    </template>
    <button v-else-if="error" type="button" class="cp-chip" @click="load">算力加载失败，重试</button>
  </div>
</template>

<style scoped>
.compute-picker {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cp-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
}
.cp-chip:hover {
  background: var(--fill);
}
.cp-menu {
  width: 420px;
  max-width: calc(100vw - 32px);
  max-height: 70vh;
  overflow-y: auto;
  padding: 8px;
}
.cp-heading {
  padding: 8px;
  font-size: 14px;
  font-weight: 600;
}
.cp-hint {
  margin: 4px 8px 8px;
  color: var(--muted);
  font-size: 13px;
}
.cp-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 12px 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.cp-row:hover {
  background: var(--fill);
}
.cp-row:disabled {
  cursor: wait;
}
.cp-body {
  flex: 1;
  min-width: 0;
}
.cp-body .cp-hint {
  margin: 4px 0 0;
}
.cp-label {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.cp-badge {
  display: inline-block;
  padding: 0 4px;
  background: var(--fill);
  color: var(--muted);
  border-radius: var(--radius-sm);
  font-size: 13px;
  white-space: nowrap;
}
.cp-machine {
  color: var(--muted);
  font-size: 13px;
}
.cp-error {
  padding: 8px;
  color: var(--danger-ink);
  font-size: 13px;
}
</style>
