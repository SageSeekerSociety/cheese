<template>
  <div class="workspace-view d-flex flex-column h-100">
    <div class="docpane-header d-flex align-center px-4 py-3">
      <span class="text-h6 font-weight-bold">⌨ 终端</span>
      <span class="text-caption text-medium-emphasis ml-3">实验性功能 · 直连 cheesed 会话</span>
    </div>

    <div class="workspace-body flex-1 pa-4">
      <v-card class="terminal-card h-100" variant="outlined" rounded="lg">
        <GottyTerminal :session-id="sessionId" :token="token" />
      </v-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import GottyTerminal from '@/components/connector/GottyTerminal.vue'

const route = useRoute()

// The active cheese connector session id. Defaults to a fixed demo session
// until project→session selection lands; accepts ?session=<id> for now.
const sessionId = computed(() => (route.query.session as string | undefined) ?? 'demo-session')

// Demo session token. Until project→session selection wires the real per-actor
// token, accept ?token=<t> and fall back to the seeded demo token so the
// standalone connector demo works without a logged-in user.
const token = computed(() => (route.query.token as string | undefined) ?? 'demo-token')
</script>

<style scoped lang="scss">
.workspace-view {
  min-height: 0;
  background: #faf7f0;
}

.docpane-header {
  border-bottom: 1px solid #e5ddcb;
  flex: none;
}

.workspace-body {
  min-height: 0;
}

.terminal-card {
  display: flex;
  overflow: hidden;
  flex-direction: column;
}
</style>
