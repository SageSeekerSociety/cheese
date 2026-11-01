<script setup lang="ts">
import type { ConversationFile } from '@/composables/useConversationFiles'
import type { Topic } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useConversationFiles } from '@/composables/useConversationFiles'
import { useEscapeLayer } from '@/composables/useEscapeStack'
import { useKeepAudience } from '@/composables/useKeepAudience'
import { useTopicPanel } from '@/composables/useTopicPanel'

import { attachmentRawUrl, downloadFile, getPrivateChat, previewDocumentPdf, previewFileBytes } from '@/api'
import ChatPanel from '@/components/ChatPanel.vue'
import KeepToLibraryConfirm from '@/components/room/KeepToLibraryConfirm.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import PrivateChatFiles from '@/components/room/PrivateChatFiles.vue'
import { t } from '@/i18n'
import { memberName, teammateName } from '@/lib/agentNames'
import { agentHandleOf } from '@/lib/dm'
import { openThreadDocument } from '@/lib/libraryDocumentNavigation'
import { proposalPage } from '@/lib/proposalNavigation'
import { refRoute } from '@/lib/refDirectory'
import { userRefRoute } from '@/lib/userRef'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { agentsQuery } from '@/query/project'
import { usePageTitleStore } from '@/stores/title'
import { useWorkspaceStore } from '@/stores/workspace'
import DmViewView from '@/views/workspace/DmViewView.vue'
import { useTopicPanes } from '@/views/workspace/useTopicPanes'

// 私聊 (飞书私聊): a normal 1:1 chat in the content area — no document, no PR
// header, no accept box. `peer` is the URL's own word for who you are talking
// to: `agent:<handle>` for an AI teammate, otherwise a person's handle (see
// lib/dm.ts for why a teammate does not get the bare handle). It lives in the
// address bar so a DM can be refreshed, shared and navigated back to like
// anything else; it used to exist only as a component ref, which is why
// reloading the page dropped you back into a topic.
defineOptions({ name: 'DmView' })

const props = defineProps<{ projectId: string; peer: string }>()
const router = useRouter()
const store = useWorkspaceStore()
const { mdAndUp } = useDisplay()
const pageTitle = usePageTitleStore()

const agentHandle = computed<string | null>(() => agentHandleOf(props.peer))
const peerHandle = computed<string | null>(() => (agentHandle.value === null ? props.peer : null))

const topic = ref<Topic | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
// The teammate's own name, once the room is open — a project may have several
// and 「芝士」 for all of them would make the header a lie.
const agentRow = ref<{ display_name: string; name_source?: string } | null>(null)
const agentName = computed(() =>
  agentRow.value ? teammateName(agentRow.value.display_name, agentRow.value.name_source) || null : null
)

// Header label: the peer's name for a person DM, else this teammate's.
const title = computed<string>(() => {
  if (agentHandle.value !== null) return agentName.value || agentHandle.value
  const m = store.members.find((x) => x.user_handle === peerHandle.value)
  return memberName(m) || peerHandle.value || ''
})

// 手机上只有一条顶栏：它写对方的名字，← 回名册（路由的 backTo），页内那条头就
// 不再画——两条头叠着，上面写「私聊」、下面写「← 成员 · 名字」，说的是同一件事。
watch(title, (name) => pageTitle.setDynamicTitle(name, 'workspace-dm'), { immediate: true })
onBeforeUnmount(() => pageTitle.clearDynamicTitle('workspace-dm'))

async function load() {
  const pid = props.projectId
  const peer = peerHandle.value
  const agent = agentHandle.value
  const me = myHandle()
  loading.value = true
  error.value = null
  topic.value = null
  agentRow.value = null
  try {
    const fetched = await getPrivateChat(pid, me, peer ?? undefined, agent ?? undefined)
    if (props.projectId !== pid || peerHandle.value !== peer) return
    topic.value = fetched
    if (agent !== null) void loadAgentName(pid, agent)
    // Opening a DM = reading it — without this its badge lights up and never clears.
    store.markDmRead(fetched.id, props.peer)
  } catch (e) {
    if (props.projectId === pid && peerHandle.value === peer) {
      error.value = e instanceof Error ? e.message : t('work.topic.dmFailed')
    }
  } finally {
    if (props.projectId === pid && peerHandle.value === peer) loading.value = false
  }
}

// 私聊的地址用的是实例自己的 `handle`，而名册行给的是席位 handle，所以这里按
// `handle` 找的是队友列表。拿不到就退回 handle：一个私聊不该因为标题写不出名字
// 就打不开。
async function loadAgentName(pid: string, handle: string) {
  try {
    const found = (await queryClient.fetchQuery(agentsQuery(pid))).find((a) => a.handle === handle)
    if (props.projectId === pid && agentHandle.value === handle) {
      agentRow.value = found ?? null
    }
  } catch {
    // 标题退回 handle。
  }
}

watch(() => [props.projectId, props.peer], load, { immediate: true })

function handleTurnDone() {
  void store.refreshTopics()
  if (topic.value) store.markDmRead(topic.value.id, props.peer)
  void store.refreshUnread()
}

// 私聊是从名册点进来的，所以 ← 回名册。手机上顶栏那颗 ← 走的是路由 meta 的
// backTo，两边指的是同一个地方。
function backToMembers() {
  void router.push({ name: 'project-members', params: { projectId: props.projectId } })
}

function openTopic(topicId: string, blockId?: string) {
  void router.push(refRoute(props.projectId, topicId, blockId))
}

function handleMentionClick(handle: string) {
  void router.push(userRefRoute(handle, props.projectId))
}

// ---- 右侧面板（#3828）：私聊里只有「文件」这一格，收、开、铺满和频道页同一套 ----
// 看哪一格不写进地址：私聊的面板只有一格，没有「别人发来的链接点名哪一格」这回事。
const panelTab = ref<string | undefined>(undefined)
const viewRef = ref<InstanceType<typeof DmViewView> | null>(null)
const panel = useTopicPanel({
  panes: computed(() => viewRef.value?.panes ?? null),
  tab: panelTab,
  desktop: mdAndUp,
  thread: computed(() => false),
  setTab: (tab) => (panelTab.value = tab),
  showing: () => 'files',
})
const panelOpen = panel.open
const panelFloat = panel.float
function closePanel() {
  panel.hide()
  void nextTick(() => document.querySelector<HTMLElement>('[data-panel-toggle]')?.focus())
}
function togglePanel() {
  if (panelOpen.value) closePanel()
  else panel.show()
}
useEscapeLayer(
  computed(() => panelFloat.value && panelOpen.value),
  closePanel
)
const panes = useTopicPanes({ panelOpen, panelFloat, desktop: mdAndUp })
const { focusMode, focusIcon, focusLabel, docked, chatNews, noteChatNews, toggleFocus, chatStyle } = panes
const { freezeChatWidth, startPaneDrag } = panes

// 这段私聊里的文件：清单、正在看的那一份、保存到资料库（私聊里的东西只有这里的人看得到，
// 保存之前先问）。
const topicId = computed(() => topic.value?.id ?? null)
const files = useConversationFiles(topicId)
const viewing = ref<string | null>(null)
watch(topicId, () => (viewing.value = null))
const fileNames = computed(() =>
  Object.fromEntries(store.members.filter((m) => m.name).map((m) => [m.user_handle, m.name as string]))
)
const keepFailed = ref<string | null>(null)
const asking = ref<ConversationFile | null>(null)
const { people, projectPeople } = useKeepAudience(
  () => topicId.value,
  () => asking.value !== null
)
async function confirmKeep() {
  const row = asking.value
  asking.value = null
  if (!row) return
  keepFailed.value = null
  try {
    await files.keep(row)
  } catch (e) {
    keepFailed.value = e instanceof Error && e.message ? e.message : t('work.room.files.keepFailed')
  }
}
/** 那一份的字节。Word、PowerPoint 这类浏览器画不了的，先在服务端转成 PDF。 */
function readViewing(asPdf: boolean): Promise<ArrayBuffer> {
  const id = topicId.value
  const path = viewing.value
  if (!id || !path) return Promise.resolve(new ArrayBuffer(0))
  return asPdf ? previewDocumentPdf(id, path) : previewFileBytes(id, path)
}
function download(path: string) {
  const id = topicId.value
  if (id) void downloadFile(attachmentRawUrl(id, path), path.split('/').pop() ?? path)
}

// 文件在右边这一栏里看；文档有自己的一页，和支线里点开文档一样跳过去。
function openFile(path: string) {
  panel.show()
  viewRef.value?.showFiles()
  viewing.value = path
}
function openRow(row: ConversationFile) {
  if (row.kind === 'document') void openThreadDocument(router, props.projectId, { id: row.id, title: row.name })
  else openFile(row.id)
}
function openResource(resource: string, _turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) {
  if (resource === 'doc' && document) return void openThreadDocument(router, props.projectId, document, review)
  // 私聊旁没有右侧面板：提议的技能、例行事务去它在项目里的那一页。
  const page = document ? proposalPage(resource, props.projectId, document.id) : null
  if (page) void router.push(page)
}
function onTurnDone() {
  noteChatNews()
  handleTurnDone()
  void files.load()
}
</script>

<template>
  <!-- 取会话这一步不画加载态：头和输入框是 ChatPanel 带进来的，历史那一段由它自己的
       骨架接手，它在消息真正会出现的地方。 -->
  <DmViewView
    ref="viewRef"
    :error="error"
    :ready="!!topic"
    :desktop="mdAndUp"
    :docked="docked"
    :panel-open="panelOpen"
    :panel-float="panelFloat"
    :focus-mode="focusMode"
    :chat-news="chatNews"
    :focus-label="focusLabel"
    :focus-icon="focusIcon"
    :chat-style="chatStyle"
    @toggle-focus="toggleFocus"
    @close-panel="closePanel"
    @start-drag="startPaneDrag"
    @reset-split="store.resetChatPct()"
    @freeze-chat="freezeChatWidth"
  >
    <template v-if="topic" #chat>
      <ChatPanel
        no-upgrade
        private-chat
        class="flex-grow-1"
        style="min-height: 0"
        :topic="topic"
        :pr-header="false"
        :hide-header="!mdAndUp"
        :always-summon="agentHandle !== null"
        :hand-off="agentHandle === null"
        :title-override="title"
        :back-label="t('navigation.project.members')"
        :members="store.members"
        :topic-list="store.topics"
        :show-composer="true"
        @back="backToMembers"
        @turn-done="onTurnDone"
        @mention-click="handleMentionClick"
        @open-topic="openTopic"
        @open-file="openFile"
        @open-resource="openResource"
      >
        <template #header-actions>
          <PanelToggle :open="panelOpen" files @toggle="togglePanel" />
        </template>
      </ChatPanel>
    </template>
    <template v-if="topic" #panel>
      <p v-if="keepFailed" class="dm-keep-failed t-meta" role="alert">{{ keepFailed }}</p>
      <PrivateChatFiles
        :rows="files.rows.value"
        :names="fileNames"
        :loading="files.loading.value"
        :error="files.error.value"
        :keeping="files.keeping.value"
        :truncated="files.truncated.value"
        :viewing="viewing"
        :read="readViewing"
        @open="openRow"
        @keep="asking = $event"
        @retry="files.load"
        @back="viewing = null"
        @download="download"
      />
      <KeepToLibraryConfirm
        :name="asking?.name ?? null"
        :people="people"
        :project-people="projectPeople"
        @confirm="confirmKeep"
        @cancel="asking = null"
      />
    </template>
  </DmViewView>
</template>

<style scoped>
.dm-keep-failed {
  padding: 8px 16px 0;
  color: var(--danger-ink);
}
</style>
