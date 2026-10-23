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

import { computed, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'

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

defineOptions({ name: 'AgentTeamSettings' })

const props = defineProps<{ projectId: string }>()

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
  settingDefault,
  setDefault,
  deactivating,
  deactivate,
} = useProjectAgents(() => props.projectId)

// 「跟随项目」时也把项目那个模型的名字写出来：否则这一行说不清它到底跑在哪。
function modelOf(agent: ProjectAgent): string {
  const chosen = agent.configuration.model
  if (chosen) {
    const label = models.value.find((m) => m.id === chosen)?.label ?? chosen
    return t('work.projectSettings.agents.rowModel', { model: label })
  }
  const main = models.value.find((m) => m.default)
  return t('work.projectSettings.agents.rowModel', {
    model: main
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
const skills = useAgentSkills(
  () => props.projectId,
  () => skillTarget.value,
  (message) => toast.error(message),
  (agentId, list, version) => {
    const row = agents.value.find((a) => a.id === agentId)
    if (row) {
      // 名册是 query 缓存里那一份、只读：改它会被静默丢掉。换一份新对象指给面板，
      // 这一次保存带的就是翻过页的 version，下一次不用白撞一次 409。
      skillTarget.value = {
        ...row,
        configuration: { ...row.configuration, skills: list },
        version,
      }
    }
  },
  // 409：别人先写了。重读一遍，并把面板目标重指到新读回来的那一行 —— 名单
  // 刷新是整份换成新对象，skillTarget 不跟过去，下一次保存还带旧 version，只会再撞 409。
  async () => {
    await refresh()
    const fresh = agents.value.find((a) => a.id === skillTarget.value?.id)
    if (fresh) skillTarget.value = fresh
  }
)
function openSkills(agent: ProjectAgent) {
  skillTarget.value = agent
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

      <v-card v-for="a in agents" :key="a.id" class="mb-3 pa-4" variant="outlined">
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
            <BaseButton
              v-if="!a.is_default && a.is_active !== false"
              size="sm"
              :loading="settingDefault === a.id"
              @click="setDefault(a)"
            >
              {{ t('work.projectSettings.agents.setDefault') }}
            </BaseButton>
            <BaseButton v-if="a.is_active !== false" size="sm" @click="openSkills(a)">{{
              t('work.room.skills.button')
            }}</BaseButton>
            <BaseButton size="sm" @click="openEdit(a)">{{ t('work.projectSettings.agents.edit') }}</BaseButton>
            <BaseButton v-if="a.is_active !== false" size="sm" @click="deactivateTarget = a">
              {{ t('work.projectSettings.agents.deactivate') }}
            </BaseButton>
          </div>
        </div>

        <div class="agent-facts t-meta c-muted mt-3">
          <span>{{ modelOf(a) }}</span>
          <span>{{ t('work.projectSettings.agents.rowEffort', { effort: effortLabel(a.configuration.effort) }) }}</span>
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
      :editing-blocked="skills.editingBlocked.value"
      :editing-by="skills.editingBy.value"
      :loading="skills.loading.value"
      :scanning="skills.scanning.value"
      :saving="skills.saving.value"
      :can-manage="skills.canManage.value"
      @install="(payload) => void skills.fetchWebSkill(payload.url, payload.kind)"
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
