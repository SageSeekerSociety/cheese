<!-- 现场: a floating popup onto a working agent's live Claude Code session. It
     does not occupy a layout zone — it opens over the workspace when you click a
     working agent's avatar. The live terminal is a real xterm.js bound to the
     agent's connector session over the /connector/session/{id}/screen websocket
     (GottyTerminal); it is read-only until you flip takeover in the terminal's
     own toolbar (逻辑只读, arbitrated by the backend). Session discovery (agent
     → connector session id/token) is stubbed until the agent/session login
     lands; for now it accepts ?session=&?token= and falls back to the demo
     session so the experimental view works standalone. -->
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
        <v-btn size="small" variant="text" icon="mdi-close" @click="ws.closeScene" />
      </div>
      <div class="wa-scene__term">
        <GottyTerminal :session-id="sessionId" :token="token" />
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import type { UserSummary } from '@/network/api/workspace'

import GottyTerminal from '@/components/connector/GottyTerminal.vue'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'

defineProps<{ agent: UserSummary }>()
const ws = useWorkspace()
const route = useRoute()

// The connector session this agent is bound to. Until agent/session login maps
// an agent user -> its live connector session (and a viewer auth model lands),
// this is a demo stopgap: default to the seeded demo session/token so clicking
// a working agent's avatar connects with no manual URL params, overridable via
// ?session=&?token=. Replaced when agent/session login + viewer authz arrive.
const sessionId = computed(() => (route.query.session as string | undefined) ?? 'demo-session')
const token = computed(() => (route.query.token as string | undefined) ?? 'demo-token')
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
  width: min(860px, 94vw);
  height: min(560px, 88vh);
  border-radius: 14px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.wa-scene__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  flex: none;
}
.wa-scene__name {
  font-weight: 700;
}
.wa-scene__sub {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.wa-scene__term {
  flex: 1;
  min-height: 0;
}
</style>
