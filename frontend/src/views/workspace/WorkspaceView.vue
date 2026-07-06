<!-- 知是 2.0 workspace shell. Desktop: far-left rail | list | conversation |
     context pane, with the 现场 popup as an overlay. Mobile: a navigation stack
     (list ⇄ chat) with a bottom tab bar mirroring the rail, and the context pane
     / 现场 as full-screen overlays. Integrated into the existing app; reached at
     /workspace/:projectId?experimental=true. -->
<template>
  <div class="wa-shell" :class="{ 'wa-shell--mobile': mobile }">
    <!-- far-left rail -->
    <nav v-if="!mobile" class="wa-rail">
      <v-btn
        v-for="s in sections"
        :key="s.value"
        :icon="s.icon"
        :color="ws.state.section === s.value ? 'primary' : undefined"
        :variant="ws.state.section === s.value ? 'tonal' : 'text'"
        size="small"
        class="wa-rail__btn"
        :title="s.label"
        @click="ws.setSection(s.value)"
      />
      <div class="wa-rail__spacer" />
      <v-btn v-for="d in disabledRail" :key="d.icon" :icon="d.icon" variant="text" size="small" disabled :title="`${d.label}（待实现）`" />
    </nav>

    <!-- list column -->
    <section v-show="!mobile || mobileView === 'list'" class="wa-col wa-col--list">
      <NavList />
    </section>

    <!-- conversation -->
    <section v-show="!mobile || mobileView === 'chat'" class="wa-col wa-col--conv">
      <div v-if="mobile" class="wa-mobile-back">
        <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" @click="mobileView = 'list'">返回</v-btn>
      </div>
      <Conversation />
    </section>

    <!-- context pane (desktop side pane) -->
    <section v-if="!mobile && ws.state.context" class="wa-col wa-col--ctx">
      <ContextPane />
    </section>

    <!-- mobile bottom tabs -->
    <nav v-if="mobile" class="wa-tabs">
      <v-btn
        v-for="s in sections"
        :key="s.value"
        :icon="s.icon"
        :color="ws.state.section === s.value ? 'primary' : undefined"
        variant="text"
        size="small"
        @click="onMobileSection(s.value)"
      />
    </nav>

    <!-- mobile context pane as full-screen overlay -->
    <v-dialog v-if="mobile" :model-value="!!ws.state.context" fullscreen @update:model-value="ws.closeContext">
      <v-card><ContextPane /></v-card>
    </v-dialog>

    <!-- 现场 popup -->
    <AgentScenePopup v-if="ws.state.sceneAgent" :agent="ws.state.sceneAgent" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import type { WorkspaceSection } from './useWorkspace'
import { useWorkspace } from './useWorkspace'

import AgentScenePopup from './components/AgentScenePopup.vue'
import Conversation from './components/Conversation.vue'
import ContextPane from './components/ContextPane.vue'
import NavList from './components/NavList.vue'

const props = defineProps<{ projectId?: string }>()
const ws = useWorkspace()
const { mobile } = useDisplay()

const mobileView = ref<'list' | 'chat'>('list')

const sections: { value: WorkspaceSection; icon: string; label: string }[] = [
  { value: 'threads', icon: 'mdi-forum-outline', label: '群聊' },
  { value: 'documents', icon: 'mdi-file-document-multiple-outline', label: '文档' },
  { value: 'workitems', icon: 'mdi-checkbox-marked-circle-outline', label: '事项' },
]
const disabledRail = [
  { icon: 'mdi-calendar-outline', label: '日历' },
  { icon: 'mdi-account-group-outline', label: '成员' },
  { icon: 'mdi-view-dashboard-outline', label: '看板' },
]

function onMobileSection(s: WorkspaceSection) {
  ws.setSection(s)
  mobileView.value = 'list'
}

// on mobile, selecting a thread advances the nav stack to the conversation
watch(
  () => ws.state.activeThreadId,
  () => {
    if (mobile.value && ws.state.section === 'threads') mobileView.value = 'chat'
  }
)

ws.init(Number(props.projectId) || 1)
</script>

<style scoped>
.wa-shell {
  display: flex;
  height: 100%;
  overflow: hidden;
}
.wa-shell--mobile {
  flex-direction: column;
}
.wa-rail {
  width: 64px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 10px 0;
  border-right: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgba(var(--v-theme-on-surface), 0.02);
}
.wa-rail__spacer {
  flex: 1;
}
.wa-col {
  height: 100%;
  min-width: 0;
}
.wa-col--list {
  width: 288px;
  flex-shrink: 0;
}
.wa-col--conv {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.wa-col--ctx {
  width: 400px;
  flex-shrink: 0;
}
.wa-mobile-back {
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-shell--mobile .wa-col--list,
.wa-shell--mobile .wa-col--conv {
  width: 100%;
  flex: 1;
}
.wa-tabs {
  display: flex;
  justify-content: space-around;
  padding: 4px 0;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgb(var(--v-theme-surface));
}
</style>
