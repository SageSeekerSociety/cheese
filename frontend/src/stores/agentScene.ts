import { defineStore } from 'pinia'
import { ref } from 'vue'

// The single, app-global 现场 (live agent screen) target. Any avatar anywhere opens the
// one floating viewer through this store; <AgentSceneDialog> (mounted once in App.vue)
// renders it. Keeps 现场 opening decoupled from any particular page/composable.
export interface SceneTarget {
  sid: string
  device_id: string
  agent_user_id: number
  nickname?: string
}

export const useAgentSceneStore = defineStore('agentScene', () => {
  const current = ref<SceneTarget | null>(null)
  function open(target: SceneTarget): void {
    current.value = target
  }
  function close(): void {
    current.value = null
  }
  return { current, open, close }
})
