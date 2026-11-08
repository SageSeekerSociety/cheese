<script setup lang="ts">
// 亲验页：真的技能面板 + 真的 useAgentSkills，只把网络层换成记录器。
// 接线照项目设置里「队友 → 技能」那一处（AgentTeamSettings）：打开就同时读池子、占「编辑中」。
import { onMounted, ref, watch } from 'vue'

import BeforeDialog from './before/src/components/agents/AgentSkillsDialog.vue'
import { useAgentSkills as useBefore } from './before/src/composables/useAgentSkills'
import { calls, gates, holdMode, side } from './recorder'

import AfterDialog from '@/components/agents/AgentSkillsDialog.vue'
import { useAgentSkills as useAfter } from '@/composables/useAgentSkills'

const Dialog = side === 'before' ? BeforeDialog : AfterDialog
const useAgentSkills = side === 'before' ? useBefore : useAfter

const target = ref({
  id: 'a1',
  handle: 'milo',
  seat_handle: 'milo',
  display_name: '米洛',
  name_source: 'human',
  project_id: 'p1',
  type_name: null,
  is_default: false,
  is_active: true,
  version: 1,
  editing: 'alice',
  configuration: { body: '', skills: ['wolfram'], model: null, effort: null, compact_percent: null },
} as never as { id: string; version: number; configuration: { skills: string[] } })
const toasts = ref<string[]>([])
const skills = useAgentSkills(
  () => 'p1',
  () => target.value as never,
  (m) => toasts.value.push(m),
  (_id, list, version) => {
    target.value = { ...target.value, version, configuration: { ...target.value.configuration, skills: list } }
  }
)
const open = ref(false)
function openPanel() {
  open.value = true
  void skills.openSkills()
  void skills.holdAgent('a1')
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'l' || e.key === 'L') gates.lease?.()
  if (e.key === 'i' || e.key === 'I') gates.install?.()
}
onMounted(() => window.addEventListener('keydown', onKey))
watch(open, (v) => {
  if (!v) void skills.releaseAgent('a1')
})
const link = (s: string, h: string) => `?side=${s}&hold=${h}`
const HOLDS = [
  ['blocked', '别人正占着（打开即只读）'],
  ['late', '占用的回答晚到（先点了装，手动放行）'],
  ['free', '没人占着（可改，对照用）'],
] as const
</script>

<template>
  <v-app>
    <v-main>
      <div class="h">
        <h1 class="t-title">技能面板只读亲验页</h1>
        <p class="t-meta-read">
          面板和取数逻辑是真的代码，只有网络层换成下面的记录器：不连任何机器、不写任何数据库。
        </p>
        <div class="h__row">
          <span class="t-eyebrow">代码版本</span>
          <a :class="{ on: side === 'before' }" :href="link('before', holdMode)">改前（D 分支头 7a9365013）</a>
          <a :class="{ on: side === 'after' }" :href="link('after', holdMode)">改后（本 PR）</a>
        </div>
        <div class="h__row">
          <span class="t-eyebrow">场景</span>
          <a v-for="[h, label] in HOLDS" :key="h" :class="{ on: holdMode === h }" :href="link(side, h)">{{ label }}</a>
        </div>
        <div class="h__row">
          <v-btn color="primary" variant="flat" @click="openPanel">打开「米洛」的技能面板</v-btn>
          <span class="t-meta-read">米洛现在带的：[{{ target.configuration.skills.join(', ') }}]</span>
        </div>
        <div v-if="holdMode === 'late'" class="h__row">
          <span class="t-meta-read">手动放行：</span>
          <v-btn size="small" variant="outlined" :disabled="!gates.install" @click="gates.install?.()">让安装完成</v-btn>
          <v-btn size="small" variant="outlined" :disabled="!gates.lease" @click="gates.lease?.()">让占用回答到达（alice 正占着）</v-btn>
        </div>
        <p v-if="holdMode === 'late'" class="t-meta-read">
          顺序：打开面板 → 点市场里 pdf 的 + → 面板开着时按键盘 L（占用回答到达），再按 I（安装完成）→ 看屏幕底部的记录。
        </p>
        <section class="log">
          <div class="t-eyebrow">记录器：面板发出的写操作</div>
          <p v-if="!calls.length" class="t-meta-read">还没有调用。</p>
          <div v-for="(c, i) in calls" :key="i" class="log__row" :class="`log__row--${c.kind}`">
            <span class="t-meta">{{ c.at }}</span> {{ c.text }}
          </div>
          <div v-for="(m, i) in toasts" :key="`t${i}`" class="log__row log__row--toast">提示：{{ m }}</div>
        </section>
      </div>
      <div v-if="open" class="bar">
        <div v-if="holdMode === 'late'">
          按 L：占用回答到达{{ gates.lease ? '（等待中）' : '' }}　按 I：安装完成{{ gates.install ? '（等待中）' : '' }}
        </div>
        <div v-for="(c, i) in calls" :key="i" :class="`log__row--${c.kind}`">{{ c.at }} {{ c.text }}</div>
        <div v-if="!calls.length">记录器：还没有调用</div>
      </div>
      <component
        :is="Dialog"
        v-model="open"
        agent-name="米洛"
        :carried="skills.carriedItems.value"
        :available="skills.available.value"
        :health="skills.skillHealth.value"
        :device-id="skills.deviceId.value"
        :market="skills.market.value"
        :market-loading="skills.marketLoading.value"
        :market-failed="skills.marketFailed.value"
        :market-failure-reason="skills.marketFailureReason.value"
        :market-forbidden="skills.marketForbidden.value"
        :editing-blocked="skills.editingBlocked.value"
        :editing-by="skills.editingBy.value"
        :loading="skills.loading.value"
        :scanning="skills.scanning.value"
        :saving="skills.saving.value"
        :can-manage="skills.canManage.value"
        @install="(p: { url: string; kind: 'skill' | 'plugin' }) => void skills.fetchWebSkill(p.url, p.kind)"
        @retry-market="() => void skills.openMarket()"
        @remove="(n: string) => void skills.removeSkill(n)"
      />
    </v-main>
  </v-app>
</template>

<style scoped>
.h { max-width: 720px; margin: 24px auto; padding: 0 16px; display: grid; gap: 12px; }
.h__row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.h__row a { padding: 4px 10px; border: 1px solid var(--line, #ccc); border-radius: 999px; text-decoration: none; color: inherit; }
.h__row a.on { background: var(--accent-wash, #fde9b8); border-color: var(--accent, #f57f17); font-weight: 600; }
.log { border: 1px solid var(--line, #ddd); border-radius: 8px; padding: 10px 12px; display: grid; gap: 4px; min-height: 90px; }
.log__row { font-family: ui-monospace, monospace; font-size: 13px; }
.log__row--machine, .log__row--list { color: #b3261e; font-weight: 600; }
.log__row--toast { color: #555; }
.bar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 99999; pointer-events: none; background: rgba(255,255,255,.96); border-top: 2px solid #f57f17; padding: 8px 16px; font: 13px/1.6 ui-monospace, monospace; }
</style>
