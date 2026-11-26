<script setup lang="ts">
// 「AI 队友」—— 这个项目里所有 AI 队友，项目设置里的一节。
//
// 它曾经是一整页，而项目设置里只有仓库、分支保护和运行环境 —— 于是设置页对一个
// 没绑仓库的项目是空的，还写着「角色设定请到 AI 队友 中修改」，把人往外指。
// 队友的角色设定本来就是这个项目的设置，所以它回到这里。
//
// 每一行除了名字和类型，还写着它跑在哪个模型上、思考强度是哪一档：两样合起来才
// 说得出这个队友多快、多贵。记忆不在这里：它由平台统一管理，不是队友的一项设置。
//
// 取数在 `useProjectAgents`（名册、类型目录、模型目录，和「设为默认 / 停用」两条
// 动作），这一节只画和接线。
import type { ProjectAgent } from '@/cx_types'

import { computed, nextTick, ref, watch } from 'vue'

import { useAgentSkills } from '@/composables/useAgentSkills'
import { useProjectAgents } from '@/composables/useProjectAgents'

import OwnAgentsSettings from './OwnAgentsSettings.vue'

import AgentEditorDialog from '@/components/agents/AgentEditorDialog.vue'
import AgentSkillsDialog from '@/components/agents/AgentSkillsDialog.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'
import { teammateName } from '@/lib/agentNames'
import { effortLabel, typeLabel } from '@/lib/projectAgents'
import { toast } from '@/lib/toast'

defineOptions({ name: 'AgentTeamSettings' })

// `focus` / `why` 来自地址上的 `?focus=<队友>&why=login_expired`：频道顶部红行的
// 「重新登录」带过来的。地址归视图读，组件只收结果（`src/components` 里不读路由）。
const props = defineProps<{ projectId: string; focus?: string; why?: string }>()

const {
  agents,
  types,
  models,
  backendMissing,
  loading,
  refreshing,
  refresh,
  error,
  dismissError,
  setDefault,
  deactivating,
  deactivate,
  projectAutomatic,
} = useProjectAgents(() => props.projectId)

// 从频道顶部的红行点「重新登录」过来：`?focus=<队友>&why=login_expired`。对应那位队友的卡片
// 滚到眼前、闪一下，展开登录过期的红条；用完不用离开这一页。
const focusHandle = computed(() => props.focus ?? '')
const focusWhy = computed(() => props.why ?? '')
/** 展开了「账号与登录」那块的队友（agent.id）。 */
const loginOpen = ref<Set<string>>(new Set())
/** 点了「我已重新登录」的队友：红条换成已连接。 */
const loginDone = ref<Set<string>>(new Set())
const pulsing = ref<string | null>(null)
function isFocused(agent: ProjectAgent): boolean {
  return !!focusHandle.value && (agent.seat_handle === focusHandle.value || agent.handle === focusHandle.value)
}
function loginExpired(agent: ProjectAgent): boolean {
  return isFocused(agent) && focusWhy.value === 'login_expired' && !loginDone.value.has(agent.id)
}
function toggleLogin(agent: ProjectAgent) {
  const next = new Set(loginOpen.value)
  if (next.has(agent.id)) next.delete(agent.id)
  else next.add(agent.id)
  loginOpen.value = next
}
function loginPanelOpen(agent: ProjectAgent): boolean {
  return loginExpired(agent) || loginOpen.value.has(agent.id) || loginDone.value.has(agent.id)
}
function reloggedIn(agent: ProjectAgent) {
  loginDone.value = new Set(loginDone.value).add(agent.id)
  void refresh()
}
watch(
  () => [agents.value.length, focusHandle.value] as const,
  async () => {
    const target = agents.value.find(isFocused)
    if (!target || pulsing.value === target.id) return
    pulsing.value = target.id
    await nextTick()
    const el = document.querySelector(`[data-agent-id="${target.id}"]`)
    el?.scrollIntoView?.({ block: 'center' })
  },
  { immediate: true }
)

// 「跟随项目」时也把项目那个模型的名字写出来：否则这一行说不清它到底跑在哪。
function modelOf(agent: ProjectAgent): string {
  const chosen = agent.configuration.model
  if (chosen) {
    const label = models.value.find((m) => m.id === chosen)?.label ?? chosen
    return t('work.projectSettings.agents.rowModel', { model: label })
  }
  const main = models.value.find((m) => m.default)
  return t('work.projectSettings.agents.rowModel', {
    model: projectAutomatic.value
      ? t('work.projectSettings.agents.rowInherit', { model: t('work.models.automaticPlain') })
      : main
        ? t('work.projectSettings.agents.rowInherit', { model: main.label })
        : t('work.projectSettings.agents.rowInheritPlain'),
  })
}

const editing = ref<ProjectAgent | null>(null)
const editorOpen = ref(false)
const deactivateTarget = ref<ProjectAgent | null>(null)
// 确认框的开关跟着「选中的那个队友」走：有目标就是开着，关掉就把目标清掉。
const deactivateOpen = computed({
  get: () => deactivateTarget.value !== null,
  set: (value) => {
    if (!value) deactivateTarget.value = null
  },
})
const deactivateTitle = computed(() =>
  deactivateTarget.value
    ? t('work.projectSettings.agents.deactivateTitle', {
        name:
          teammateName(deactivateTarget.value.display_name, deactivateTarget.value.name_source) ||
          deactivateTarget.value.handle,
      })
    : ''
)
function openEdit(agent: ProjectAgent) {
  editing.value = agent
  editorOpen.value = true
}

// 「技能」：每个队友各开一份面板，编辑它带哪些技能。技能跟着人走（见 useAgentSkills），
// 所以入口在这一行队友上。保存后把那行就地改掉，不整份重拉。
const skillTarget = ref<ProjectAgent | null>(null)
const skillsOpen = ref(false)
// 名册是 query 缓存里那一份、只读，保存后不重读：每一位最近一次存成功的名单和版本记在
// 这里，下次打开这一位时盖在名册那一行上，版本号才接得上。
const savedSkills = new Map<string, { skills: string[]; version: number }>()
function withSaved(row: ProjectAgent): ProjectAgent {
  const saved = savedSkills.get(row.id)
  if (!saved || saved.version <= row.version) return row
  return { ...row, configuration: { ...row.configuration, skills: saved.skills }, version: saved.version }
}
const skills = useAgentSkills(
  () => props.projectId,
  () => skillTarget.value,
  (message) => toast.error(message),
  (agentId, list, version, current) => {
    savedSkills.set(agentId, { skills: list, version })
    // 面板已经换场（关掉、换了人或重新打开）：只记下，下次打开时接上。
    if (!current || skillTarget.value?.id !== agentId) return
    const row = agents.value.find((a) => a.id === agentId)
    // 面板正用着的就是这一笔存下的名单：版本跟着这一笔走，不拿名册里可能更新的那一版——
    // 那一版里别人的改动面板上没有，配上它的版本号会把别人的改动悄悄盖掉。
    if (row) skillTarget.value = { ...row, configuration: { ...row.configuration, skills: list }, version }
  },
  // 409：别人先写了。重读一遍，并把面板目标重指到新读回来的那一行 —— 名单
  // 刷新是整份换成新对象，skillTarget 不跟过去，下一次保存还带旧 version，只会再撞 409。
  async () => {
    await refresh()
    const fresh = agents.value.find((a) => a.id === skillTarget.value?.id)
    if (fresh) skillTarget.value = withSaved(fresh)
  }
)
function openSkills(agent: ProjectAgent) {
  skillTarget.value = withSaved(agent)
  skillsOpen.value = true
  void skills.openSkills()
  // 打开就占住「编辑中」：别人进来会看到「XX 正在编辑」，而不是白改一场。
  void skills.holdAgent(agent.id)
}
// 关掉就放掉。租约自己会过期，这只是不让人等那 120 秒。
watch(skillsOpen, (isOpen) => {
  if (!isOpen && skillTarget.value) void skills.releaseAgent(skillTarget.value.id)
})

// 确认框在这次请求期间不关：按钮上的 loading 就在它里面。成败都收掉它 —— 停下了
// 的那一个不再是一行队友，失败的由上面那条 alert 说理由。
async function confirmDeactivate() {
  const agent = deactivateTarget.value
  if (!agent) return
  try {
    await deactivate(agent)
  } finally {
    deactivateTarget.value = null
  }
}
</script>

<template>
  <section class="page-section agent-team">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-robot-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.agents.title') }}</span>
      <v-spacer />
      <BaseButton
        icon="mdi-refresh"
        size="sm"
        class="mr-1"
        :aria-label="t('work.projectSettings.agents.refresh')"
        :loading="loading || refreshing"
        @click="refresh"
      />
    </div>
    <div class="page-section-body">
      <p class="t-body c-muted mb-6" style="max-width: 640px">
        {{ t('work.projectSettings.agents.intro') }}
      </p>

      <v-alert v-if="backendMissing" type="info" density="comfortable" class="mb-4">
        {{ t('work.projectSettings.agents.unsupported') }}
      </v-alert>

      <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="dismissError">
        {{ error }}
      </v-alert>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>

      <OwnAgentsSettings :project-id="projectId" @added="refresh" />

      <v-card
        v-for="a in agents"
        :key="a.id"
        class="mb-3 pa-4"
        :class="{ 'agent-card--pulse': pulsing === a.id }"
        variant="outlined"
        :data-agent-id="a.id"
      >
        <div class="agent-head">
          <div class="agent-head__id">
            <CheeseAvatar
              :name="teammateName(a.display_name, a.name_source) || a.handle"
              :handle="a.seat_handle"
              :size="36"
              class="mr-3 flex-shrink-0"
            />
            <div class="min-w-0">
              <div class="d-flex align-center flex-wrap ga-2">
                <span class="t-title agent-head__name">{{
                  teammateName(a.display_name, a.name_source) || a.handle
                }}</span>
                <v-chip v-if="a.is_default" size="x-small" color="primary" variant="tonal">{{
                  t('work.projectSettings.agents.default')
                }}</v-chip>
                <v-chip v-if="a.is_active === false" size="x-small" variant="tonal">{{
                  t('work.projectSettings.agents.inactive')
                }}</v-chip>
              </div>
              <div class="t-meta c-muted agent-head__name">{{ a.handle }} · {{ typeLabel(types, a.type_name) }}</div>
            </div>
          </div>
          <div class="agent-head__actions">
            <BaseButton size="sm" @click="openEdit(a)">{{ t('work.projectSettings.agents.edit') }}</BaseButton>
            <v-menu location="bottom end">
              <template #activator="{ props: menuProps }">
                <BaseButton size="sm" v-bind="menuProps" data-testid="agent-more">{{
                  t('work.projectSettings.agents.more')
                }}</BaseButton>
              </template>
              <v-list density="compact">
                <v-list-item v-if="a.is_active !== false" @click="openSkills(a)">
                  <v-list-item-title>{{ t('work.room.skills.button') }}</v-list-item-title>
                </v-list-item>
                <v-list-item v-if="!a.is_default && a.is_active !== false" @click="setDefault(a)">
                  <v-list-item-title>{{ t('work.projectSettings.agents.setDefault') }}</v-list-item-title>
                </v-list-item>
                <v-list-item @click="toggleLogin(a)">
                  <v-list-item-title>{{ t('work.projectSettings.agents.accountLogin') }}</v-list-item-title>
                </v-list-item>
                <v-list-item v-if="a.is_active !== false" @click="deactivateTarget = a">
                  <v-list-item-title class="text-error">{{
                    t('work.projectSettings.agents.deactivate')
                  }}</v-list-item-title>
                </v-list-item>
              </v-list>
            </v-menu>
          </div>
        </div>

        <div class="agent-facts t-meta c-muted mt-3">
          <span>{{ modelOf(a) }}</span>
          <span>{{ t('work.projectSettings.agents.rowEffort', { effort: effortLabel(a.configuration.effort) }) }}</span>
        </div>

        <div v-if="loginPanelOpen(a)" class="agent-login mt-3" data-testid="agent-login">
          <div v-if="loginDone.has(a.id)" class="agent-login__ok t-body">
            <v-icon size="16" color="success">mdi-check-circle-outline</v-icon>
            {{ t('work.projectSettings.agents.loginDone') }}
          </div>
          <template v-else>
            <div v-if="loginExpired(a)" class="agent-login__bad t-body">
              <v-icon size="16" color="error">mdi-cloud-off-outline</v-icon>
              <strong>{{ t('work.projectSettings.agents.loginExpired') }}</strong>
            </div>
            <p class="t-body c-muted mt-2 mb-1">{{ t('work.projectSettings.agents.loginWhy') }}</p>
            <p class="t-body mb-1">{{ t('work.projectSettings.agents.loginHow') }}</p>
            <pre class="agent-login__cmd">claude /login</pre>
            <BaseButton size="sm" kind="primary" @click="reloggedIn(a)">{{
              t('work.projectSettings.agents.loginDoneButton')
            }}</BaseButton>
          </template>
        </div>
      </v-card>
    </div>

    <AgentEditorDialog v-model="editorOpen" :project-id="projectId" :agent="editing" :types="types" @saved="refresh" />

    <AgentSkillsDialog
      v-if="skillTarget"
      v-model="skillsOpen"
      :agent-name="teammateName(skillTarget.display_name, skillTarget.name_source) || skillTarget.handle"
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
      @install="(payload) => void skills.fetchWebSkill(payload.url, payload.kind)"
      @retry-market="() => void skills.openMarket()"
    />

    <ConfirmDialog
      v-model="deactivateOpen"
      :title="deactivateTitle"
      :confirm-label="t('work.projectSettings.agents.deactivate')"
      danger
      :loading="deactivating"
      @confirm="confirmDeactivate"
    >
      {{ t('work.projectSettings.agents.deactivateHint') }}
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.agent-card--pulse {
  animation: agent-card-pulse 1.6s ease-out 1;
}
@keyframes agent-card-pulse {
  0% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 55%, transparent);
  }
  100% {
    box-shadow: 0 0 0 14px transparent;
  }
}
.agent-login {
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.agent-login__bad {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: var(--radius-md, 8px);
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.agent-login__ok {
  display: flex;
  align-items: center;
  gap: 8px;
}
.agent-login__cmd {
  margin: 6px 0 10px;
  padding: 8px 12px;
  border-radius: var(--radius-md, 8px);
  background: var(--ink);
  color: var(--surface);
  font:
    12px/1.5 ui-monospace,
    monospace;
}
.min-w-0 {
  min-width: 0;
}

/* 名字一组、按钮一组：放不下时整组按钮换到下一行靠右，而不是把名字挤成一列，
   也不是三颗按钮一颗一颗地掉下去。 */
.agent-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.agent-head__id {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
}
.agent-head__name {
  overflow-wrap: anywhere;
}
.agent-head__actions {
  display: flex;
  flex: none;
  align-items: center;
  margin-inline-start: auto;
}

.agent-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
}
</style>
