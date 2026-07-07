<!-- The one app-global 现场 (live agent screen) viewer. Mounted once in App.vue; any
     agent-aware avatar anywhere opens it via the agentScene store. As large as possible:
     full width AND full height. -->
<template>
  <v-dialog
    :model-value="!!scene.current"
    width="96vw"
    max-width="96vw"
    height="94vh"
    max-height="94vh"
    scrim="rgba(0,0,0,0.6)"
    @update:model-value="(v: boolean) => { if (!v) scene.close() }"
  >
    <v-card v-if="scene.current" class="scene-card">
      <div class="scene-hd">
        <v-icon icon="mdi-monitor-eye" class="me-2" size="20" />
        <span class="scene-title">{{ title }} · 现场</span>
        <v-spacer />
        <v-btn icon="mdi-open-in-new" variant="text" size="small" title="全屏页面（新标签）" @click="maximize" />
        <v-btn icon="mdi-close" variant="text" size="small" title="关闭" @click="scene.close()" />
      </div>
      <div class="scene-body">
        <SceneTerminal
          :key="scene.current.sid"
          :session-id="scene.current.sid"
          :device-id="scene.current.device_id"
        />
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useAgentSceneStore } from '@/stores/agentScene'

import SceneTerminal from '@/views/workspace/components/SceneTerminal.vue'

const scene = useAgentSceneStore()
const title = computed(() =>
  scene.current ? scene.current.nickname || `agent#${scene.current.agent_user_id}` : '',
)

function maximize(): void {
  const s = scene.current
  if (!s) return
  window.open(`/workspace/scene/${s.device_id}/${s.sid}?exp=true`, '_blank')
  scene.close()
}
</script>

<style scoped>
.scene-card {
  display: flex;
  flex-direction: column;
  height: 100%; /* fill the 94vh dialog — as tall as possible */
}
.scene-hd {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  flex: none;
}
.scene-title {
  font-weight: 600;
}
/* A plain block flex-item (NOT display:flex): SceneTerminal's own flex column fills
   100% width+height. Making this display:flex would leave the terminal with no defined
   width and the xterm fit loop shrinks it to a sliver on the left. */
.scene-body {
  flex: 1;
  min-height: 0;
}
</style>
