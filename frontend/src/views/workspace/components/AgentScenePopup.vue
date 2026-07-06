<!-- 现场: a floating popup onto a working agent's live Claude Code session. It
     does not occupy a layout zone — it opens over the workspace when you click a
     working agent's avatar. The live terminal (xterm.js/webtty over the
     connector) and the read-only→takeover switch are wired in Phase D; for now
     this is the framed placeholder that fixes the interaction and the space. -->
<template>
  <div class="wa-scene" @click.self="ws.closeScene">
    <v-card class="wa-scene__card" elevation="12">
      <div class="wa-scene__bar">
        <AgentAvatar :user="agent" :size="28" />
        <div class="wa-scene__title">
          <div class="wa-scene__name">{{ agent.nickname }} · 现场</div>
          <div class="wa-scene__sub">{{ agent.agentStatus === 'working' ? '工作中' : '空闲' }}</div>
        </div>
        <v-spacer />
        <v-switch
          v-model="takeover"
          density="compact"
          hide-details
          color="warning"
          :label="takeover ? '已接管' : '只读'"
          class="mr-2"
        />
        <v-btn size="small" variant="text" icon="mdi-close" @click="ws.closeScene" />
      </div>
      <div class="wa-scene__term">
        <div class="wa-scene__term-inner">
          <div class="wa-scene__line"><span class="wa-scene__prompt">cheese@{{ agent.username }}</span>:~$ claude</div>
          <div class="wa-scene__line wa-scene__dim">● 正在处理群消息…（Phase D 接入实时终端）</div>
          <div class="wa-scene__line wa-scene__dim">  逻辑只读：滚动查看真实终端，接管后编排命令暂停。</div>
          <div class="wa-scene__cursor" />
        </div>
      </div>
      <div class="wa-scene__foot text-caption text-medium-emphasis">
        实时终端将通过 connector (cheesed + ttyd/xterm.js) 接入，此处为占位。
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import type { UserSummary } from '@/network/api/workspace'

import { useWorkspace } from '../useWorkspace'

defineProps<{ agent: UserSummary }>()
const ws = useWorkspace()
const takeover = ref(false)
</script>

<style scoped>
.wa-scene {
  position: fixed;
  inset: 0;
  z-index: 2400;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
}
.wa-scene__card {
  width: min(760px, 92vw);
  border-radius: 14px;
  overflow: hidden;
}
.wa-scene__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-scene__name {
  font-weight: 700;
}
.wa-scene__sub {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.wa-scene__term {
  background: #0d1117;
  padding: 16px;
  min-height: 260px;
}
.wa-scene__term-inner {
  font-family: monospace;
  font-size: 13px;
  color: #c9d1d9;
  line-height: 1.7;
}
.wa-scene__prompt {
  color: #7ee787;
}
.wa-scene__dim {
  color: #8b949e;
}
.wa-scene__cursor {
  width: 8px;
  height: 15px;
  background: #c9d1d9;
  animation: wa-blink 1s step-end infinite;
}
@keyframes wa-blink {
  50% {
    opacity: 0;
  }
}
.wa-scene__foot {
  padding: 8px 14px;
}
</style>
