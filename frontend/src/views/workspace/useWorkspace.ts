// Shared reactive state for the workspace shell. Kept as a small composable
// rather than a Pinia store: one workspace is open at a time, and the state is
// ephemeral view state (which thread is active, what the context pane shows,
// whether a 现场 popup is open). Business truth lives in the backend.

import { reactive, ref } from 'vue'

import type { BlockRef, Document, Message, Thread, UserSummary, WorkItem } from '@/network/api/workspace'
import { WorkspaceApi } from '@/network/api/workspace'

export type WorkspaceSection = 'threads' | 'documents' | 'workitems'

export type ContextTarget =
  | { type: 'document'; id: number }
  | { type: 'workitem'; id: number }
  | { type: 'block'; id: number }
  | null

interface WorkspaceState {
  projectId: number
  // which list the middle column shows (driven by the far-left rail)
  section: WorkspaceSection
  threads: Thread[]
  activeThreadId: number | null
  messages: Message[]
  loadingThreads: boolean
  loadingMessages: boolean
  // right-hand context pane: the business object currently being looked at
  context: ContextTarget
  // 现场: the agent whose live session popup is open (null = closed)
  sceneAgent: UserSummary | null
  // 现场 for a real connector agent (clicking an avatar opens it)
  sceneScreen: { sid: string; device_id: string; agent_user_id: number } | null
  // right slide-out panel (群成员 / agents 管理), Feishu/WeChat style
  panelOpen: boolean
  // trees cached for the 事项 / 文档 rails
  workItems: WorkItem[]
  documents: Document[]
}

const state = reactive<WorkspaceState>({
  projectId: 0,
  section: 'threads',
  threads: [],
  activeThreadId: null,
  messages: [],
  loadingThreads: false,
  loadingMessages: false,
  context: null,
  sceneAgent: null,
  sceneScreen: null,
  panelOpen: false,
  workItems: [],
  documents: [],
})

// bump to force child panels (document / workitem) to reload after a mutation
const contextVersion = ref(0)

export function useWorkspace() {
  async function init(projectId: number) {
    state.projectId = projectId
    state.section = 'threads'
    state.context = null
    state.sceneAgent = null
    await loadThreads()
    if (state.threads.length > 0) await selectThread(state.threads[0].id)
    void refreshWorkItems()
    void refreshDocuments()
  }

  function setSection(section: WorkspaceSection) {
    state.section = section
  }

  async function refreshDocuments() {
    const res = await WorkspaceApi.listDocuments(state.projectId)
    state.documents = res.data.documents
  }

  async function loadThreads() {
    state.loadingThreads = true
    try {
      const res = await WorkspaceApi.listThreads(state.projectId)
      state.threads = res.data.threads
    } finally {
      state.loadingThreads = false
    }
  }

  async function refreshWorkItems() {
    const res = await WorkspaceApi.listWorkItems(state.projectId)
    state.workItems = res.data.workItems
  }

  async function selectThread(threadId: number) {
    state.activeThreadId = threadId
    state.loadingMessages = true
    try {
      const res = await WorkspaceApi.listMessages(threadId, { pageSize: 100 })
      state.messages = res.data.messages
    } finally {
      state.loadingMessages = false
    }
  }

  async function sendMessage(content: string, refs: BlockRef[] = []) {
    if (!state.activeThreadId || !content.trim()) return
    const res = await WorkspaceApi.postMessage(state.activeThreadId, { content: content.trim(), refs })
    state.messages.push(res.data.message)
    const thread = state.threads.find((t) => t.id === state.activeThreadId)
    if (thread) thread.lastMessage = res.data.message
  }

  // clicking a reference in a message navigates the context pane (对话是过程,点引用看状态)
  function openRef(ref: BlockRef) {
    state.context = { type: ref.targetType, id: ref.targetId }
  }

  function openContext(target: ContextTarget) {
    state.context = target
  }

  function closeContext() {
    state.context = null
  }

  function openScene(agent: UserSummary) {
    state.sceneAgent = agent
  }

  function closeScene() {
    state.sceneAgent = null
  }

  function openSceneScreen(s: { sid: string; device_id: string; agent_user_id: number }) {
    state.sceneScreen = s
  }
  function closeSceneScreen() {
    state.sceneScreen = null
  }

  function togglePanel() {
    state.panelOpen = !state.panelOpen
  }

  function setPanel(open: boolean) {
    state.panelOpen = open
  }

  function notifyContextChanged() {
    contextVersion.value += 1
    void refreshWorkItems()
  }

  return {
    state,
    contextVersion,
    init,
    setSection,
    loadThreads,
    selectThread,
    sendMessage,
    openRef,
    openContext,
    closeContext,
    openScene,
    closeScene,
    openSceneScreen,
    closeSceneScreen,
    togglePanel,
    setPanel,
    refreshWorkItems,
    refreshDocuments,
    notifyContextChanged,
  }
}
