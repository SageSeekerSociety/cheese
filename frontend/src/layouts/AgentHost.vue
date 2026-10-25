<template>
  <template v-if="available">
    <!-- Desktop, pinned: a column on the right that the page makes room for. -->
    <v-navigation-drawer
      v-if="mdAndUp && agentOpen && docked"
      :model-value="true"
      permanent
      location="right"
      width="380"
      class="agent-host__dock"
      data-testid="agent-host"
      data-agent-select="off"
    >
      <AssistantPanel v-bind="panelProps" v-on="panelEvents" />
    </v-navigation-drawer>
    <!-- Desktop, floating: a card at the bottom right, over the page, which keeps its width. -->
    <Transition name="agent-host">
      <div
        v-if="mdAndUp && agentOpen && !docked"
        class="agent-host__float"
        data-testid="agent-host"
        data-agent-select="off"
      >
        <AssistantPanel v-bind="panelProps" v-on="panelEvents" />
      </div>
    </Transition>
    <!-- Phone: a sheet from the bottom; the page stays visible above it. -->
    <v-bottom-sheet v-if="!mdAndUp" v-model="agentOpen" class="agent-host__sheet">
      <div class="agent-host__sheet-body" data-testid="agent-host" data-agent-select="off">
        <AssistantPanel v-bind="panelProps" v-on="panelEvents" />
      </div>
    </v-bottom-sheet>
    <!-- Selected text anywhere but an editor: 问芝士 beside it. -->
    <AgentSelectionAsk :enabled="agentAvailable" :touch="!mdAndUp" @ask="selectForAgent" />
    <!-- Over the form the page registered: 芝士's pointer, the field it writes, the ones it filled. -->
    <Teleport v-if="formHost" :to="formHost">
      <AgentLayer
        :pointer="layer.pointer.value"
        :outline="layer.outline.value"
        :marks="layer.marks.value"
        :selection="layer.selection.value"
        :name="t('work.agentAvatar.defaultName')"
        @undo="(key) => form?.undoMark(key)"
      />
    </Teleport>
  </template>
</template>

<script setup lang="ts">
// 个人芝士的面板，全站只挂这一处（应用外壳里）。在哪一页打开都是同一条对话；这一页登记了
// 什么（`stores/agent.ts`）决定芝士读到的「当前页面」、面板上的问候和开场问题，以及芝士写进
// 这一页时页面怎么动。
//
// 桌面上默认停在右边，把页面挤窄（芝士往表单里写时，表单和面板并排看得见）；取消固定就是
// 右下角的一张浮层，页面宽度不变。手机上从底下升起来。
//
// 项目里也是同一个芝士：替这个人读、起草要发的话、把活派给项目里的 AI 队友（发出去要他
// 确认），记他自己的额度。
//
// 这一页登记了表单（`AgentSurface.form`）时，芝士那一层也由这里画，送进表单的外框里：
// 指针、正在写的那一格的描边、「芝士已填写」和撤销。
import type { AssistantHanded } from '@/stores/agentThread'
import type { Citable } from '@/types/citable'

import { computed, onBeforeUnmount, provide, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import { useAgentCiting } from '@/composables/useAgentCiting'
import { useAgentPointer } from '@/composables/useAgentPointer'
import { useAgentRules } from '@/composables/useAgentRules'

import { downloadFile, hearAssistantVoice, libraryFileRawUrl } from '@/api'
import { defineCommands } from '@/commands'
import AgentLayer from '@/components/agent/AgentLayer.vue'
import AgentSelectionAsk from '@/components/assistant/AgentSelectionAsk.vue'
import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import { t } from '@/i18n'
import { agentRulesKey } from '@/lib/agentRules'
import { onAssistantChange } from '@/lib/inboxLive'
import { leafOf } from '@/lib/libraryFolders'
import { myHandle } from '@/me'
import AccountService from '@/services/account'
import {
  agentAvailable,
  agentContext,
  agentDocked,
  agentFiles,
  agentOpen,
  agentPageOff,
  agentPicked,
  agentSelection,
  agentSurface,
  agentThread,
  agentView,
  askAgent,
  attachToAgent,
  dismissProposal,
  earlierView,
  openAgentConversation,
  pickForAgent,
  projectOfOpenedTask,
  removeAgentFile,
  resetAgent,
  selectForAgent,
  sendingProposal,
  sendProposal,
  toggleAgent,
  undoChange,
} from '@/stores/agent'

const { mdAndUp } = useDisplay()
const route = useRoute()
const router = useRouter()
const docked = agentDocked

const available = computed(() => AccountService._loggedIn.value)
// 退出登录：上一个人的对话不留给下一个人。
watch(
  () => AccountService._loggedIn.value,
  (now) => {
    if (!now) resetAgent()
  }
)
watch(
  available,
  (now) => {
    agentAvailable.value = now
    if (!now) agentOpen.value = false
  },
  { immediate: true }
)

// 芝士建的自动化：回答里那张卡片的状态和取消、暂停、恢复。
const rules = useAgentRules(agentThread.current, agentThread.busy)
provide(agentRulesKey, rules)

// 和芝士的对话在别处说了新的话（另一个标签页、另一台设备、例行事务）：面板跟着读。
onBeforeUnmount(
  onAssistantChange((conversation) => {
    void agentThread.heard(conversation).catch(() => undefined)
    void rules.load()
  })
)

// 面板开着、页面在前台、芝士正在答：告诉服务端有人看着，这一轮答完就不再另发通知。
// 服务端只记几十秒，看着的时候隔一会儿说一次。
const LOOKING_EVERY_MS = 15_000
let lookingTimer: ReturnType<typeof setInterval> | null = null
function sayLooking() {
  if (agentOpen.value && agentThread.busy.value && document.visibilityState === 'visible')
    void agentThread.looking().catch(() => undefined)
}
watch(
  () => [agentOpen.value, agentThread.busy.value, agentThread.current.value] as const,
  ([open, busy]) => {
    if (lookingTimer) clearInterval(lookingTimer)
    lookingTimer = null
    if (!open || !busy) return
    sayLooking()
    lookingTimer = setInterval(sayLooking, LOOKING_EVERY_MS)
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  if (lookingTimer) clearInterval(lookingTimer)
})

// `?assistant=<对话>`：通知、推送和设置页「查看对话」把人带到这里，打开面板里的那段对话，
// 再把这个参数从地址栏拿掉，刷新不会再开一次。
watch(
  () => [route.query.assistant, available.value] as const,
  ([id, ready]) => {
    if (typeof id !== 'string' || !id || !ready) return
    openAgentConversation(id)
    const rest = { ...route.query }
    delete rest.assistant
    void router.replace({ query: rest })
  },
  { immediate: true }
)

onBeforeUnmount(
  defineCommands(() =>
    available.value
      ? [
          {
            id: 'agent.toggle',
            title: t('tasks.assistant.ask'),
            icon: 'mdi-creation-outline',
            shortcut: 'mod+j',
            run: toggleAgent,
          },
        ]
      : []
  )
)

const form = computed(() => agentSurface.value?.form ?? null)
const formHost = computed(() => form.value?.host() ?? null)
const layer = useAgentPointer({
  host: formHost,
  fields: computed(() => form.value?.fields() ?? {}),
  writing: computed(() => form.value?.writing() ?? null),
  marks: computed(() => form.value?.marks() ?? {}),
  active: computed(() => !!form.value && agentThread.busy.value),
  passage: computed(() => form.value?.passage?.() ?? null),
  holding: computed(() => form.value?.holding?.() ?? false),
})

function openTask(id: number) {
  const spaceId = route.params.spaceId
  if (spaceId) void router.push({ name: 'TasksDetail', params: { spaceId, taskId: id } })
}

const panelProps = computed(() => {
  const context = agentContext.value ?? {}
  const view = agentView.value
  return {
    personalTeamHandle: myHandle(),
    messages: view.messages,
    earlier: earlierView(),
    hasOlder: agentThread.hasOlder.value,
    currentEarlier: agentThread.currentEarlier.value,
    streaming: agentThread.streaming.value,
    tool: agentThread.tool.value,
    queued: agentThread.queued.value,
    notice: agentThread.notice.value,
    creditRefused: agentThread.creditRefused.value,
    busy: agentThread.busy.value,
    steps: view.steps,
    changes: view.changes,
    results: view.results,
    proposals: view.proposals,
    handed: agentThread.handed.value,
    sendingProposal: sendingProposal.value,
    files: agentFiles.value.map((f) => ({ name: f.name, mime: f.mime, uploading: f.uploading })),
    context: context.intro ?? '',
    greeting: context.greeting ?? t('tasks.assistant.anywhere.greeting'),
    starters: context.starters ?? [],
    placeholder: context.placeholder ?? t('tasks.assistant.anywhere.placeholder'),
    hint: context.hint ?? t('tasks.assistant.anywhere.hint'),
    canAttach: true,
    hearVoice: hearAssistantVoice,
    closable: true,
    dockable: mdAndUp.value,
    docked: docked.value,
    pageLabel: context.page ? context.label ?? null : null,
    pageOff: agentPageOff.value,
    selection: agentSelection.value,
    citable: citing.items.value,
    recents: citing.recents(),
    project: context.project?.name ?? null,
    cited: Object.fromEntries(
      Object.values(agentPicked.value).map((item) => [item.id, { kind: item.kind, title: item.title }])
    ),
    searchProblems: citing.searchProblems,
    rules: agentThread.rules.value,
  }
})

const citing = useAgentCiting(() => agentContext.value?.project?.id ?? null)

const panelEvents = {
  send: (text: string) => void askAgent(text),
  stop: () => void agentThread.stop().catch(() => undefined),
  new: () => agentThread.startNew(),
  older: () => void agentThread.older().catch(() => undefined),
  olderMessages: (topic: string | null) => void agentThread.earlierMessages(topic).catch(() => undefined),
  close: () => (agentOpen.value = false),
  downloadFile: (file: AssistantHanded) =>
    void downloadFile(libraryFileRawUrl({ team: file.team }, file.path), leafOf(file.path)).catch(() =>
      toast.error(t('work.library.downloadFailed'))
    ),
  dock: (next: boolean) => (docked.value = next),
  undo: (change: number, key: string) =>
    agentSurface.value?.undo ? agentSurface.value.undo(change, key) : void undoChange(change, key),
  undoAll: (rows: { change: number; key: string }[]) =>
    agentSurface.value?.undoAll
      ? agentSurface.value.undoAll(rows)
      : void (async () => {
          for (const row of rows) await undoChange(row.change, row.key)
        })(),
  jump: (key: string) => (agentSurface.value?.jump ?? layer.jump)(key),
  openDraft: (id: number) => agentSurface.value?.openDraft?.(id),
  openTask: (id: number) => (agentSurface.value?.openTask ?? openTask)(id),
  attach: (files: File[]) => void attachToAgent(files),
  sendProposal: (id: number, edited: { title?: string; text: string }) => void sendProposal(id, edited),
  dismissProposal: (id: number) => void dismissProposal(id),
  openPlace: (task: string) => {
    const projectId = projectOfOpenedTask(task)
    if (projectId) void router.push({ name: 'workspace-task', params: { projectId, taskId: task } })
  },
  removeFile: (index: number) => removeAgentFile(index),
  togglePage: () => (agentPageOff.value = !agentPageOff.value),
  clearSelection: () => (agentSelection.value = null),
  citeOpen: () => void citing.load(),
  cite: (item: Citable) => pickForAgent(item),
  openLink: (path: string) => void router.push(path),
}
</script>

<style scoped>
.agent-host__dock :deep(.v-navigation-drawer__content) {
  display: flex;
  flex-direction: column;
}

.agent-host__float {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 1004;
  display: flex;
  flex-direction: column;
  width: 400px;
  height: min(640px, calc(100vh - 96px));
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.agent-host-enter-active,
.agent-host-leave-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.agent-host-enter-from,
.agent-host-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.agent-host__sheet-body {
  display: flex;
  flex-direction: column;
  height: min(78vh, 640px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  overflow: hidden;
  background: var(--surface);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
}
</style>
