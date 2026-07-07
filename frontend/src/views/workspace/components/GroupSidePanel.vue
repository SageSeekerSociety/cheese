<!-- 群设置侧栏（飞书/微信式）：从聊天右上角 ⋯ 拉出、贴右侧滑入。一个统一的成员列表同时
     列出人类与 agent（都是 Member，头像走 getAvatarUrl，agent 带机器人徽标）。admin/owner
     可改群名、踢人、拉人（候选人类+agent 混排，仅用头像徽标区分；拉 agent 转为待审批邀请）。
     点 agent 头像开现场。发消息仍走群聊、这里只做管理。 -->
<template>
  <teleport to="body">
    <transition name="gsp-fade">
      <div v-if="ws.state.panelOpen" class="gsp-backdrop" @click="ws.setPanel(false)" />
    </transition>
    <transition name="gsp-slide">
      <aside v-if="ws.state.panelOpen" class="gsp">
        <header class="gsp-head">
          <template v-if="editingTitle">
            <v-text-field
              v-model="titleDraft"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              class="gsp-title-edit"
              @keydown.enter.prevent="saveTitle"
              @keydown.esc="editingTitle = false"
            />
            <v-btn icon="mdi-check" size="small" variant="text" :loading="renaming" @click="saveTitle" />
            <v-btn icon="mdi-close" size="small" variant="text" @click="editingTitle = false" />
          </template>
          <template v-else>
            <span class="gsp-head__title">{{ headerTitle }}</span>
            <v-btn
              v-if="isAdmin && thread?.kind !== 1"
              icon="mdi-pencil"
              size="x-small"
              variant="text"
              title="改群名"
              @click="startEditTitle"
            />
            <v-spacer />
            <v-btn icon="mdi-close" size="small" variant="text" @click="ws.setPanel(false)" />
          </template>
        </header>

        <div class="gsp-body">
          <section class="gsp-sec">
            <div class="gsp-sec__title">
              成员 <span class="gsp-count">{{ members.length }}</span>
              <v-spacer />
              <v-btn
                v-if="isOwner && thread?.kind !== 1"
                size="small"
                variant="text"
                class="mr-1"
                @click="manageMode = !manageMode"
              >
                {{ manageMode ? '完成' : '管理' }}
              </v-btn>
              <v-menu
                v-if="isAdmin"
                v-model="addMenu"
                :close-on-content-click="false"
                location="bottom end"
                :z-index="2600"
              >
                <template #activator="{ props: mp }">
                  <v-btn v-bind="mp" size="small" color="primary" variant="tonal">＋ 添加成员</v-btn>
                </template>
                <v-card min-width="260" class="gsp-add">
                  <v-text-field
                    v-model="candQuery"
                    placeholder="搜索用户或 agent…"
                    density="compact"
                    variant="solo-filled"
                    flat
                    hide-details
                    prepend-inner-icon="mdi-magnify"
                    autofocus
                  />
                  <div v-if="pendingHint" class="gsp-hint">{{ pendingHint }}</div>
                  <div class="gsp-cand-list">
                    <div
                      v-for="c in candidates"
                      :key="c.user_id"
                      class="gsp-row gsp-row--pick"
                      @click="addCandidate(c)"
                    >
                      <AgentAvatar :member="c" :size="30" />
                      <span class="gsp-name">{{ c.nickname }}</span>
                      <v-spacer />
                      <v-progress-circular v-if="adding === c.user_id" size="16" width="2" indeterminate />
                      <v-icon v-else icon="mdi-plus" size="small" />
                    </div>
                    <div v-if="!candidates.length" class="gsp-empty">无候选</div>
                  </div>
                </v-card>
              </v-menu>
            </div>
            <div v-if="error" class="gsp-err">{{ error }}</div>

            <div v-for="m in members" :key="m.user_id" class="gsp-row gsp-row--member">
              <AgentAvatar :member="m" :size="32" @open-member="openScene" />
              <div class="gsp-meta">
                <span class="gsp-name">{{ m.nickname }}</span>
              </div>
              <v-spacer />
              <!-- 管理态：提升/降级 + 踢出；平时：右侧对齐的身份标签 -->
              <template v-if="manageMode">
                <v-btn
                  v-if="isOwner && (m.role ?? 0) < 1"
                  size="x-small"
                  variant="text"
                  color="primary"
                  :loading="roleBusy === m.user_id"
                  @click="setRole(m, 1)"
                >
                  提升为管理员
                </v-btn>
                <v-btn
                  v-else-if="isOwner && (m.role ?? 0) === 1"
                  size="x-small"
                  variant="text"
                  color="warning"
                  :loading="roleBusy === m.user_id"
                  @click="setRole(m, 0)"
                >
                  降为成员
                </v-btn>
                <v-btn
                  v-if="isAdmin && (m.role ?? 0) < 2 && m.user_id !== selfId"
                  size="x-small"
                  variant="text"
                  color="error"
                  :loading="kicking === m.user_id"
                  @click="kick(m)"
                >
                  踢出
                </v-btn>
              </template>
              <div v-else class="gsp-tags">
                <span v-if="roleLabel(m)" class="gsp-tag">{{ roleLabel(m) }}</span>
                <AttentionSlider
                  v-if="m.is_agent"
                  :mode="attnFor(m.user_id).mode"
                  :interval="attnFor(m.user_id).interval_minutes"
                  :busy="attnBusy === m.user_id"
                  @change="(mode, mins) => applyAttn(m.user_id, mode, mins)"
                />
              </div>
            </div>
            <div v-if="!members.length" class="gsp-empty">暂无成员</div>
          </section>

          <!-- 任务4 — 已邀请成员（群主/管理员可见）-->
          <section v-if="isAdmin && applications.length" class="gsp-sec">
            <div class="gsp-sec__title">
              已邀请成员 <span class="gsp-count">{{ applications.length }}</span>
            </div>
            <div v-for="a in applications" :key="a.id" class="gsp-row gsp-row--member">
              <AgentAvatar :member="a.user" :size="32" />
              <div class="gsp-meta">
                <span class="gsp-name">{{ a.user.nickname }}</span>
                <span class="gsp-tag">待同意</span>
              </div>
              <v-spacer />
              <v-btn
                size="x-small"
                variant="text"
                color="error"
                :loading="cancelingApp === a.id"
                @click="cancelApp(a)"
              >
                撤回
              </v-btn>
            </div>
          </section>

        </div>
        <!-- 任务2 — 解散群：始终固定在最底部 -->
        <div v-if="isOwner && thread?.kind !== 1" class="gsp-foot">
          <v-btn
            block
            variant="tonal"
            size="small"
            color="error"
            prepend-icon="mdi-delete-outline"
            @click="dissolveConfirm = true"
          >
            解散群
          </v-btn>
        </div>
      </aside>
    </transition>

    <!-- 任务2 — 解散群确认 -->
    <v-dialog v-model="dissolveConfirm" width="380">
      <v-card>
        <v-card-title>解散群</v-card-title>
        <v-card-text>
          解散后本群及所有成员关系将被移除，且不可恢复。确定解散「{{ headerTitle }}」吗？
          <div v-if="dissolveError" class="gsp-err">{{ dissolveError }}</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dissolveConfirm = false">取消</v-btn>
          <v-btn color="error" variant="flat" :loading="dissolving" @click="dissolve">解散</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 现场弹窗（尽量大）；由 store 驱动，聊天里点头像也能打开 -->
    <v-dialog :model-value="!!ws.state.sceneScreen" width="96vw" @update:model-value="ws.closeSceneScreen">
      <v-card v-if="ws.state.sceneScreen" class="gsp-scene">
        <div class="gsp-scene__bar">
          <span>agent#{{ ws.state.sceneScreen.agent_user_id }} · 现场</span>
          <v-spacer />
          <v-btn icon="mdi-fullscreen" variant="text" size="small" title="全屏页面（新标签）" @click="maximize" />
          <v-btn icon="mdi-close" variant="text" size="small" @click="ws.closeSceneScreen" />
        </div>
        <div class="gsp-scene__term">
          <SceneTerminal
            :key="ws.state.sceneScreen.sid"
            :session-id="ws.state.sceneScreen.sid"
            :device-id="ws.state.sceneScreen.device_id"
          />
        </div>
      </v-card>
    </v-dialog>
  </teleport>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'

import type { AttentionAgent, AttentionMode, Member, Thread, ThreadApplication } from '@/network/api/threads'
import { ThreadsApi, threadDisplayTitle } from '@/network/api/threads'
import { currentUserId } from '@/services/account'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'
import AttentionSlider from './AttentionSlider.vue'
import SceneTerminal from './SceneTerminal.vue'

const props = defineProps<{ threadId: number | null; thread: Thread | null }>()
const emit = defineEmits<{
  (e: 'renamed', thread: Thread): void
  (e: 'dissolved', threadId: number): void
}>()
const ws = useWorkspace()

const members = ref<Member[]>([])
const error = ref('')
const selfId = computed(() => currentUserId.value ?? null)

const selfRole = computed(() => members.value.find((m) => m.user_id === selfId.value)?.role ?? 0)
const isAdmin = computed(() => selfRole.value >= 1)
const isOwner = computed(() => selfRole.value >= 2)

const headerTitle = computed(() =>
  props.thread ? threadDisplayTitle(props.thread, selfId.value, members.value) : '群设置'
)

function roleLabel(m: Member): string {
  if (m.role === 2) return '群主'
  if (m.role === 1) return '管理员'
  return ''
}

async function refresh(): Promise<void> {
  if (!props.threadId) return
  try {
    const { members: fresh } = await ThreadsApi.listMembers(props.threadId)
    members.value = fresh
  } catch {
    /* transient */
  }
  if (isAdmin.value) void loadApplications()
  void loadAttention()
}

// ── 任务3 角色管理 ──
const manageMode = ref(false)
const roleBusy = ref<number | null>(null)
async function setRole(m: Member, role: number): Promise<void> {
  if (!props.threadId) return
  roleBusy.value = m.user_id
  error.value = ''
  try {
    await ThreadsApi.changeRole(props.threadId, m.user_id, role)
    await refresh()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    roleBusy.value = null
  }
}

// ── 任务4 已邀请成员 ──
const applications = ref<ThreadApplication[]>([])
const cancelingApp = ref<number | null>(null)
async function loadApplications(): Promise<void> {
  if (!props.threadId) return
  try {
    const { applications: fresh } = await ThreadsApi.listThreadApplications(props.threadId)
    applications.value = fresh
  } catch {
    applications.value = []
  }
}
async function cancelApp(a: ThreadApplication): Promise<void> {
  if (!props.threadId) return
  cancelingApp.value = a.id
  error.value = ''
  try {
    await ThreadsApi.cancelApplication(props.threadId, a.id)
    await loadApplications()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    cancelingApp.value = null
  }
}

// ── 任务2 解散群 ──
const dissolveConfirm = ref(false)
const dissolving = ref(false)
const dissolveError = ref('')
async function dissolve(): Promise<void> {
  if (!props.threadId) return
  dissolving.value = true
  dissolveError.value = ''
  try {
    const tid = props.threadId
    await ThreadsApi.deleteThread(tid)
    dissolveConfirm.value = false
    ws.setPanel(false)
    emit('dissolved', tid)
  } catch (e) {
    dissolveError.value = (e as Error).message
  } finally {
    dissolving.value = false
  }
}

// ── 任务5 关注频率（成员行内的三态滑块）──
const attnAgents = ref<AttentionAgent[]>([])
const attnBusy = ref<number | null>(null)
const attnMap = computed(() => new Map(attnAgents.value.map((a) => [a.user_id, a])))
function attnFor(userId: number): { mode: AttentionMode; interval_minutes?: number } {
  return attnMap.value.get(userId) ?? { mode: 'MENTION' } // agent 默认：仅当@时
}
async function loadAttention(): Promise<void> {
  if (!props.threadId) return
  try {
    const { agents } = await ThreadsApi.listAttention(props.threadId)
    attnAgents.value = agents
  } catch {
    attnAgents.value = []
  }
}
async function applyAttn(userId: number, mode: AttentionMode, minutes?: number): Promise<void> {
  if (!props.threadId) return
  attnBusy.value = userId
  error.value = ''
  const interval = mode === 'INTERVAL' ? (minutes ?? attnFor(userId).interval_minutes ?? 15) : undefined
  try {
    const { agent } = await ThreadsApi.setAttention(props.threadId, userId, {
      mode,
      ...(interval !== undefined ? { interval_minutes: interval } : {}),
    })
    const idx = attnAgents.value.findIndex((x) => x.user_id === userId)
    if (idx >= 0) attnAgents.value[idx] = agent
    else attnAgents.value.push(agent)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    attnBusy.value = null
  }
}

// ── rename ──
const editingTitle = ref(false)
const titleDraft = ref('')
const renaming = ref(false)
function startEditTitle(): void {
  titleDraft.value = props.thread?.title ?? ''
  editingTitle.value = true
}
async function saveTitle(): Promise<void> {
  const title = titleDraft.value.trim()
  if (!props.threadId || !title) return
  renaming.value = true
  error.value = ''
  try {
    const { thread } = await ThreadsApi.renameThread(props.threadId, title)
    emit('renamed', thread)
    editingTitle.value = false
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    renaming.value = false
  }
}

// ── kick ──
const kicking = ref<number | null>(null)
async function kick(m: Member): Promise<void> {
  if (!props.threadId) return
  kicking.value = m.user_id
  error.value = ''
  try {
    await ThreadsApi.removeMember(props.threadId, m.user_id)
    await refresh()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    kicking.value = null
  }
}

// ── add member ──
const addMenu = ref(false)
const candQuery = ref('')
const candidates = ref<Member[]>([])
const adding = ref<number | null>(null)
const pendingHint = ref('')
let candTimer = 0

async function loadCandidates(): Promise<void> {
  if (!props.threadId) return
  try {
    const { candidates: fresh } = await ThreadsApi.listCandidates(props.threadId, candQuery.value.trim())
    candidates.value = fresh
  } catch {
    candidates.value = []
  }
}
watch(candQuery, () => {
  clearTimeout(candTimer)
  candTimer = window.setTimeout(loadCandidates, 250)
})
watch(addMenu, (open) => {
  if (open) {
    pendingHint.value = ''
    void loadCandidates()
  }
})

async function addCandidate(c: Member): Promise<void> {
  if (!props.threadId) return
  adding.value = c.user_id
  error.value = ''
  pendingHint.value = ''
  try {
    const res = await ThreadsApi.addMember(props.threadId, c.user_id)
    if ('pending' in res) {
      pendingHint.value = `已向「${c.nickname}」的主人发出邀请，待主人同意后入群。`
    } else {
      await refresh()
    }
    void loadCandidates()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    adding.value = null
  }
}

function openScene(m: Member): void {
  if (m.sid && m.device_id) {
    ws.openSceneScreen({ sid: m.sid, device_id: m.device_id, agent_user_id: m.user_id })
  }
}

function maximize(): void {
  const s = ws.state.sceneScreen
  if (!s) return
  window.open(`/workspace/scene/${s.device_id}/${s.sid}?exp=true`, '_blank')
}

let timer = 0
watch(
  () => props.threadId,
  () => {
    manageMode.value = false
  }
)
watch(
  () => [ws.state.panelOpen, props.threadId] as const,
  ([open]) => {
    if (open) void refresh()
  },
  { immediate: true }
)
timer = window.setInterval(() => {
  if (ws.state.panelOpen) void refresh()
}, 4000)
onUnmounted(() => {
  clearInterval(timer)
  clearTimeout(candTimer)
})
</script>

<style scoped>
.gsp-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  z-index: 2390;
}
.gsp {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  width: 340px;
  max-width: 92vw;
  z-index: 2400;
  background: rgb(var(--v-theme-surface));
  border-left: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  box-shadow: -8px 0 28px rgba(0, 0, 0, 0.16);
  display: flex;
  flex-direction: column;
}
.gsp-head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 12px 8px 12px 16px;
  font-weight: 700;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.gsp-head__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gsp-title-edit {
  flex: 1;
}
.gsp-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
}
.gsp-sec {
  margin-bottom: 18px;
}
.gsp-sec__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin: 6px 0;
}
.gsp-count {
  opacity: 0.5;
  font-weight: 400;
}
.gsp-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 4px;
  border-radius: 6px;
}
.gsp-row--member:hover,
.gsp-row--pick:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.gsp-row--pick {
  cursor: pointer;
}
.gsp-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.gsp-name {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gsp-tag {
  font-size: 11px;
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-primary));
  padding: 1px 6px;
  border-radius: 4px;
  flex: none;
}
.gsp-tag--agent {
  background: rgba(var(--v-theme-secondary), 0.16);
  color: rgb(var(--v-theme-secondary));
}
/* Identity tags sit on the right, but their text is left-aligned and lines up across
   rows (fixed column) so 群主 / agent read as a tidy column. */
.gsp-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
  flex: none;
}
.gsp-foot {
  flex: none;
  padding: 10px 12px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.gsp-empty {
  font-size: 12px;
  opacity: 0.5;
  padding: 6px 4px;
}
.gsp-err {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  padding: 4px;
}
.gsp-add {
  padding: 8px;
}
.gsp-cand-list {
  max-height: 300px;
  overflow-y: auto;
  margin-top: 6px;
}
.gsp-hint {
  font-size: 12px;
  color: rgb(var(--v-theme-primary));
  padding: 6px 4px;
}
.gsp-attn {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.gsp-attn__col {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  padding: 8px;
  min-height: 80px;
}
.gsp-attn__hd {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.gsp-attn__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 6px 0;
  border-radius: 6px;
}
.gsp-attn__card:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.gsp-attn__name {
  font-size: 12px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gsp-attn__mins {
  width: 100%;
}
.gsp-attn__move {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
}
.gsp-attn__empty {
  font-size: 12px;
  opacity: 0.4;
  text-align: center;
}
.gsp-scene {
  display: flex;
  flex-direction: column;
  height: 90vh;
}
.gsp-scene__bar {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  font-weight: 600;
}
.gsp-scene__term {
  flex: 1;
  min-height: 0;
}
.gsp-slide-enter-active,
.gsp-slide-leave-active {
  transition: transform 0.22s ease;
}
.gsp-slide-enter-from,
.gsp-slide-leave-to {
  transform: translateX(100%);
}
.gsp-fade-enter-active,
.gsp-fade-leave-active {
  transition: opacity 0.22s ease;
}
.gsp-fade-enter-from,
.gsp-fade-leave-to {
  opacity: 0;
}
</style>
