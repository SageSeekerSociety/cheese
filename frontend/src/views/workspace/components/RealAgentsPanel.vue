<!-- 真 agent 面板（内测）：在项目里创建真实 agent、列出在跑的 agent、点开它的现场
     与之对话。直接打后端连接器接口（/connector/projects|devices/...），用当前登录
     用户的 JWT 鉴权（GottyTerminal 默认取 AccountService.accessToken）。自成一体、
     不改动既有 store 驱动的会话流，便于逐步替换到聊天内的头像交互。 -->
<template>
  <div class="ra-panel">
    <div class="ra-head">
      <span class="ra-title">现场 · agents</span>
      <v-btn size="small" color="primary" variant="tonal" :loading="creating" @click="create">＋ 创建 agent</v-btn>
    </div>
    <div v-if="error" class="ra-err">{{ error }}</div>
    <div class="ra-list">
      <div v-for="a in agents" :key="a.sid" class="ra-item" @click="open(a)">
        <span class="ra-dot" />🤖 agent#{{ a.agent_user_id }}
        <span class="ra-sid">{{ a.sid }}</span>
      </div>
      <div v-if="!agents.length" class="ra-empty">还没有 agent，点上面「创建 agent」</div>
    </div>

    <v-dialog v-model="sceneOpen" width="880">
      <v-card v-if="current" class="ra-scene">
        <div class="ra-scene-bar">
          <span>agent#{{ current.agent_user_id }} · 现场</span>
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" size="small" @click="sceneOpen = false" />
        </div>
        <div class="ra-scene-term">
          <SceneTerminal :key="current.sid" :session-id="current.sid" :device-id="current.device_id" />
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

import SceneTerminal from './SceneTerminal.vue'

import { authFetch } from '@/network/api/connectorFetch'

const props = defineProps<{ projectId: number }>()

interface Agent {
  agent_user_id: number
  sid: string
  device_id: string
}

const agents = ref<Agent[]>([])
const creating = ref(false)
const error = ref('')
const sceneOpen = ref(false)
const current = ref<Agent | null>(null)

async function refresh(): Promise<void> {
  try {
    const r = await authFetch(`/connector/projects/${props.projectId}/agents`)
    if (r.ok) agents.value = (await r.json()).agents ?? []
  } catch {
    /* transient; next poll retries */
  }
}

async function create(): Promise<void> {
  creating.value = true
  error.value = ''
  try {
    const r = await authFetch(`/connector/projects/${props.projectId}/agents`, { method: 'POST', body: '{}' }, true)
    const data = await r.json().catch(() => ({}))
    if (!r.ok) {
      error.value = data?.message ?? `创建失败（${r.status}）——是否有客户机连到本项目？`
      return
    }
    await refresh()
    const created = agents.value.find((a) => a.sid === data.sid)
    if (created) open(created)
  } finally {
    creating.value = false
  }
}

function open(a: Agent): void {
  current.value = a
  sceneOpen.value = true
}

let timer = 0
onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 4000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.ra-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 240px;
  z-index: 1500;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.12);
  padding: 8px;
}
.ra-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.ra-title {
  font-weight: 700;
  font-size: 13px;
}
.ra-err {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin: 4px 0;
}
.ra-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.ra-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
}
.ra-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
}
.ra-sid {
  opacity: 0.5;
  font-size: 11px;
}
.ra-empty {
  opacity: 0.55;
  font-size: 12px;
  padding: 6px;
}
.ra-scene {
  display: flex;
  flex-direction: column;
  height: min(560px, 84vh);
}
.ra-scene-bar {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  font-weight: 600;
}
.ra-scene-term {
  flex: 1;
  min-height: 0;
}
.ra-say {
  display: flex;
  gap: 6px;
  padding: 8px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.ra-say input {
  flex: 1;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 6px;
  padding: 6px 8px;
  background: transparent;
  color: inherit;
}
</style>
