<!-- 群聊/私聊消息回路（真回路）。发消息转发进本 thread 的 agent 成员的 Claude、agent 用
     cheese api post-note 回复、这里轮询显示。消息带真实头像（getAvatarUrl）：用户与 agent
     都显示头像；agent 头像带机器人徽标、忙时外圈有动画环、驱动解析出的 elapsed/tokens 显示
     在头像下方。点 agent 头像开现场。右上 ⋯ 打开群设置。 -->
<template>
  <div class="pc">
    <div class="pc-head">
      <v-icon :icon="headIcon" class="mr-2" />
      <span class="pc-title">{{ title }}</span>
      <v-spacer />
      <v-btn icon="mdi-dots-horizontal" size="small" variant="text" title="群成员与 agents" @click="ws.togglePanel()" />
    </div>

    <!-- 置顶消息条：列出本群所有置顶消息，点击滚动到原消息，✕ 取消置顶。 -->
    <div v-if="pins.length" class="pc-pins">
      <v-icon icon="mdi-pin" size="14" class="pc-pins-ic" />
      <div class="pc-pins-list">
        <div v-for="p in pins" :key="p.id" class="pc-pin-item" @click="scrollToMessage(p.id)">
          <span class="pc-pin-who">{{ nameForId(p.author_id) }}:</span>
          <span class="pc-pin-text">{{ p.deleted ? '（已删除）' : p.text }}</span>
          <v-btn
            icon="mdi-close"
            size="x-small"
            variant="text"
            density="compact"
            class="pc-pin-x"
            title="取消置顶"
            @click.stop="unpin(p)"
          />
        </div>
      </div>
    </div>

    <div ref="scrollEl" class="pc-scroll">
      <div
        v-for="m in messages"
        :key="m.id"
        class="pc-row"
        :class="{
          'pc-row--me': isMe(m),
          'pc-row--hl': highlightId === m.id,
          'pc-row--sel': selectMode && selected.has(m.id),
          'pc-row--active': openMenuId === m.id,
        }"
        :data-mid="m.id"
        @click="selectMode && !m.deleted && toggleSelect(m.id)"
      >
        <v-checkbox
          v-if="selectMode"
          :model-value="selected.has(m.id)"
          :disabled="m.deleted"
          hide-details
          density="compact"
          class="pc-sel-box"
          @click.stop
          @update:model-value="toggleSelect(m.id)"
        />
        <AgentAvatar :member="memberFor(m)" :size="36" @open-member="openScene" />
        <div class="pc-col">
          <div class="pc-who">
            <span class="pc-who-name">{{ authorName(m) }}</span>
            <!-- 悬停时在用户名「内侧」（朝向消息内容一侧）淡入时间；opacity 切换，预留宽度不跳版。 -->
            <span class="pc-time">{{ formatTime(m.ts) }}</span>
          </div>
          <!-- 引用预览：点击滚动到/高亮原消息；原消息被删/跨群时显示占位。 -->
          <div
            v-if="m.quoted"
            class="pc-quote"
            :class="{ 'pc-quote--gone': m.quoted.deleted }"
            @click="!m.quoted.deleted && scrollToMessage(m.reply_to_id ?? m.quoted.id)"
          >
            <v-icon icon="mdi-format-quote-close" size="12" class="pc-quote-ic" />
            <template v-if="m.quoted.deleted">
              <span class="pc-quote-gone">原消息已删除</span>
            </template>
            <template v-else>
              <span class="pc-quote-who">{{ nameForId(m.quoted.author_id) }}</span>
              <span class="pc-quote-text">{{ m.quoted.excerpt }}</span>
            </template>
          </div>
          <div class="pc-bubble-line">
            <!-- 飞书式已阅圈：只在自己发的、且群里还有其他成员的消息上出现；点击看已读/未读明细。 -->
            <v-menu
              v-if="!m.deleted && isMe(m) && readInfoFor(m).total > 0"
              location="top"
              :close-on-content-click="false"
            >
              <template #activator="{ props: mp }">
                <span v-bind="mp" class="pc-pie">
                  <ReadPie :read="readInfoFor(m).read" :total="readInfoFor(m).total" :size="15" />
                </span>
              </template>
              <v-card min-width="220" max-width="300" class="pc-read-card">
                <div class="pc-read-head">
                  已读 {{ readDetailFor(m).read.length }} · 未读 {{ readDetailFor(m).unread.length }}
                </div>
                <div v-if="readDetailFor(m).read.length" class="pc-read-sec">
                  <div class="pc-read-label">已读</div>
                  <div v-for="u in readDetailFor(m).read" :key="`r${u.user_id}`" class="pc-read-row">
                    <AgentAvatar :member="u" :size="24" :show-meta="false" />
                    <span class="pc-read-name">{{ u.nickname }}</span>
                  </div>
                </div>
                <div v-if="readDetailFor(m).unread.length" class="pc-read-sec">
                  <div class="pc-read-label">未读</div>
                  <div v-for="u in readDetailFor(m).unread" :key="`u${u.user_id}`" class="pc-read-row">
                    <AgentAvatar :member="u" :size="24" :show-meta="false" />
                    <span class="pc-read-name">{{ u.nickname }}</span>
                  </div>
                </div>
              </v-card>
            </v-menu>
            <div class="pc-bubble-wrap">
              <!-- 墓碑：软删除后的占位。 -->
              <div v-if="m.deleted" class="pc-bubble pc-bubble--tomb">
                <v-icon icon="mdi-cancel" size="13" class="mr-1" />该消息已删除
              </div>
              <div v-else class="pc-bubble" :class="{ 'pc-bubble--other': !isMe(m) }">
                <template v-for="(seg, i) in renderSegments(m.text)" :key="i"
                  ><span v-if="seg.mention" class="pc-mention">{{ seg.t }}</span
                  ><template v-else>{{ seg.t }}</template></template
                >
              </div>

              <!-- 飞书式悬停操作条：消息右上角 表情回应 / 回复 / 转发 / 更多 -->
              <div v-if="!m.deleted && !selectMode" class="pc-actions">
                <v-menu
                  location="top"
                  :close-on-content-click="true"
                  :model-value="openEmojiId === m.id"
                  @update:model-value="(v: boolean) => setMenu('emoji', m.id, v)"
                >
                  <template #activator="{ props: ep }">
                    <v-btn v-bind="ep" icon="mdi-emoticon-happy-outline" size="x-small" variant="text" title="表情回应" />
                  </template>
                  <v-card class="pc-emoji-pop">
                    <button
                      v-for="e in emojiPalette"
                      :key="e"
                      class="pc-emoji-btn"
                      @click="toggleReaction(m, e)"
                    >
                      {{ e }}
                    </button>
                  </v-card>
                </v-menu>
                <v-btn icon="mdi-message-reply-text-outline" size="x-small" variant="text" title="回复" @click="startReply(m)" />
                <v-btn icon="mdi-share" size="x-small" variant="text" title="转发" @click="openForward([m.id])" />
                <v-menu
                  location="top"
                  :model-value="openMoreId === m.id"
                  @update:model-value="(v: boolean) => setMenu('more', m.id, v)"
                >
                  <template #activator="{ props: mp }">
                    <v-btn v-bind="mp" icon="mdi-dots-horizontal" size="x-small" variant="text" title="更多" />
                  </template>
                  <v-list density="compact" min-width="140">
                    <v-list-item prepend-icon="mdi-checkbox-multiple-marked-outline" title="多选" @click="enterSelect(m.id)" />
                    <v-list-item
                      :prepend-icon="m.pinned ? 'mdi-pin-off-outline' : 'mdi-pin-outline'"
                      :title="m.pinned ? '取消置顶' : '置顶'"
                      @click="togglePin(m)"
                    />
                    <v-list-item prepend-icon="mdi-file-export-outline" title="导出到文档" @click="openExport([m.id])" />
                    <v-list-item prepend-icon="mdi-delete-outline" title="删除" @click="deleteMessages([m.id])" />
                  </v-list>
                </v-menu>
              </div>
            </div>
          </div>

          <!-- 表情回应 chips：emoji + 人数，me 高亮，点击 toggle。 -->
          <div v-if="!m.deleted && reactionsFor(m.id).length" class="pc-reactions">
            <button
              v-for="r in reactionsFor(m.id)"
              :key="r.emoji"
              class="pc-react-chip"
              :class="{ 'pc-react-chip--me': r.me }"
              @click="toggleReaction(m, r.emoji)"
            >
              <span class="pc-react-emoji">{{ r.emoji }}</span>
              <span class="pc-react-count">{{ r.count }}</span>
            </button>
          </div>
        </div>
      </div>
      <div v-if="!messages.length && !loading" class="pc-empty text-medium-emphasis">
        还没有消息。在右上角 ⋯ 里把一个 agent 拉进群、然后在这里发消息、它会回复你。
      </div>
    </div>

    <!-- 多选批量操作条 -->
    <div v-if="selectMode" class="pc-batchbar">
      <span class="pc-batch-count">已选 {{ selected.size }} 条</span>
      <v-spacer />
      <v-btn size="small" variant="text" prepend-icon="mdi-share" :disabled="!selected.size" @click="openForward([...selected])">转发</v-btn>
      <v-btn size="small" variant="text" prepend-icon="mdi-file-export-outline" :disabled="!selected.size" @click="openExport([...selected])">导出到文档</v-btn>
      <v-btn size="small" variant="text" color="error" prepend-icon="mdi-delete-outline" :disabled="!selected.size" @click="deleteMessages([...selected])">删除</v-btn>
      <v-btn size="small" variant="text" @click="exitSelect">取消</v-btn>
    </div>

    <!-- 转发对话框：选择另一个会话。 -->
    <v-dialog v-model="forwardOpen" max-width="420">
      <v-card>
        <v-card-title class="text-subtitle-1">转发到</v-card-title>
        <v-card-text class="pc-fwd-body">
          <div v-if="!forwardTargets.length" class="text-medium-emphasis text-body-2">没有其它会话可转发。</div>
          <v-list v-else density="compact">
            <v-list-item
              v-for="t in forwardTargets"
              :key="t.id"
              :title="threadDisplayTitle(t, currentUserId ?? null)"
              prepend-icon="mdi-forum-outline"
              :disabled="forwarding"
              @click="doForward(t.id)"
            />
          </v-list>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="forwardOpen = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 导出到文档对话框：选项目 + 可选标题。 -->
    <v-dialog v-model="exportOpen" max-width="440">
      <v-card>
        <v-card-title class="text-subtitle-1">导出到文档</v-card-title>
        <v-card-text>
          <v-select
            v-model="exportProjectId"
            :items="exportProjects"
            item-title="name"
            item-value="id"
            label="目标项目"
            density="comfortable"
            hide-details
            class="mb-4"
          />
          <v-text-field
            v-model="exportTitle"
            label="文档标题（可选）"
            density="comfortable"
            hide-details
            placeholder="留空则自动命名"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="exportOpen = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="exporting" :disabled="!exportProjectId" @click="doExport">导出</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 底部输入区拆成独立子组件：draft 状态下沉，打字不再触发本组件（消息列表）重渲染。 -->
    <ChatComposer
      :members="members"
      :current-user-id="currentUserId ?? null"
      :reply-to="replyTo"
      @send="onComposerSend"
      @cancel-reply="replyTo = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import type { Member, Message, ReactionSummary } from '@/network/api/threads'
import type { Thread } from '@/network/api/threads'
import { ThreadsApi, threadDisplayTitle } from '@/network/api/threads'
import { ProjectsApi } from '@/network/api/projects'
import { messageFailed, messageSucceed } from '@/network/utils/showMessage'
import { currentUserId } from '@/services/account'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'
import ChatComposer from './ChatComposer.vue'
import ReadPie from './ReadPie.vue'

const props = defineProps<{ threadId: number; thread: Thread | null }>()
const ws = useWorkspace()

const messages = ref<Message[]>([])
const members = ref<Member[]>([])
const loading = ref(false)
const scrollEl = ref<HTMLElement | null>(null)
// 引用消息：当前正在回复的目标消息（null = 普通发送）；高亮的消息 id（点引用预览时短暂高亮）。
const replyTo = ref<Message | null>(null)
const highlightId = ref<number | null>(null)
let highlightTimer = 0
let lastId = 0

const memberMap = computed(() => {
  const map = new Map<number, Member>()
  for (const m of members.value) map.set(m.user_id, m)
  return map
})

const activeThread = computed(() => props.thread)
const title = computed(() =>
  activeThread.value ? threadDisplayTitle(activeThread.value, currentUserId.value ?? null, members.value) : '聊天'
)
const headIcon = computed(() => (activeThread.value?.kind === 1 ? 'mdi-account-outline' : 'mdi-forum-outline'))

function isMe(m: Message): boolean {
  return m.author_id === currentUserId.value
}
// 聊天时间戳：今天显示 HH:mm，更早显示 M月D日 HH:mm（本地时区）。
function formatTime(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const hm = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  return sameDay ? hm : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
}
function authorName(m: Message): string {
  if (isMe(m)) return '我'
  return memberMap.value.get(m.author_id)?.nickname ?? m.author?.nickname ?? `#${m.author_id}`
}
// Merge the message author with the live member row so the avatar shows the current
// agent status / elapsed / tokens (polled fast) rather than a stale snapshot.
function memberFor(m: Message): Member {
  const live = memberMap.value.get(m.author_id)
  return { ...m.author, ...(live ?? {}) }
}

// 已阅统计：群里除作者外的其他成员，有多少人的已读水位 ≥ 这条消息的 id。
// 人的已读=在网页里看到了这条；agent 的已读=这条被真正喂进它的 cheeselet。
function readInfoFor(m: Message): { read: number; total: number } {
  let read = 0
  let total = 0
  for (const mem of members.value) {
    if (mem.user_id === m.author_id) continue
    total += 1
    if ((mem.last_read_block_id ?? 0) >= m.id) read += 1
  }
  return { read, total }
}
// 点开已阅圈时用：把其他成员分成已读 / 未读两组（含头像用的完整 Member）。
function readDetailFor(m: Message): { read: Member[]; unread: Member[] } {
  const read: Member[] = []
  const unread: Member[] = []
  for (const mem of members.value) {
    if (mem.user_id === m.author_id) continue
    if ((mem.last_read_block_id ?? 0) >= m.id) read.push(mem)
    else unread.push(mem)
  }
  return { read, unread }
}

// 渲染：把消息里匹配到成员昵称的 @提及 高亮。
function renderSegments(text: string): { t: string; mention: boolean }[] {
  const nicks = members.value.map((m) => m.nickname).filter(Boolean)
  if (!nicks.length) return [{ t: text, mention: false }]
  const escaped = nicks
    .sort((a, b) => b.length - a.length)
    .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp(`@(?:${escaped.join('|')})`, 'g')
  const out: { t: string; mention: boolean }[] = []
  let last = 0
  let mm: RegExpExecArray | null
  while ((mm = re.exec(text))) {
    if (mm.index > last) out.push({ t: text.slice(last, mm.index), mention: false })
    out.push({ t: mm[0], mention: true })
    last = mm.index + mm[0].length
  }
  if (last < text.length) out.push({ t: text.slice(last), mention: false })
  return out.length ? out : [{ t: text, mention: false }]
}

// ── 引用消息 ──
// 昵称：优先取实时成员行，退回消息自带作者，最后 #id。null 作者（原消息已删）→ 空。
function nameForId(id: number | null): string {
  if (id == null) return ''
  if (id === currentUserId.value) return '我'
  return memberMap.value.get(id)?.nickname ?? `#${id}`
}
function startReply(m: Message): void {
  replyTo.value = m
}
// 点引用预览：滚动到原消息并短暂高亮；原消息不在当前已加载列表里则忽略。
function scrollToMessage(id: number): void {
  const el = scrollEl.value?.querySelector<HTMLElement>(`[data-mid="${id}"]`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  highlightId.value = id
  clearTimeout(highlightTimer)
  highlightTimer = window.setTimeout(() => {
    if (highlightId.value === id) highlightId.value = null
  }, 1600)
}

function openScene(member: Member): void {
  if (member.sid && member.device_id) {
    ws.openSceneScreen({ sid: member.sid, device_id: member.device_id, agent_user_id: member.user_id })
  }
}

// ── 悬停操作条的弹层归属 ──
// v-menu 是 teleport 出去的：光标离开消息行时 CSS 会把 .pc-actions 隐藏，但已打开的
// 表情面板/更多菜单会被留在屏幕上「悬空」。解决：记录当前哪条消息打开了哪个菜单，
// 该行强制显示操作条（openMenuId → pc-row--active），使弹层弹出期间操作条不消失。
// 注意：不要在行 mouseleave 时清空 openEmojiId/openMoreId —— 弹层是 teleport 出去的，
// 光标从触发按钮移到弹层内容上会「离开」消息行触发 mouseleave，从而把刚打开的弹层关掉。
// 弹层的关闭交给 v-menu 默认的点击外部（click-away）/Esc/选中项处理，与 hover 解耦。
const openEmojiId = ref<number | null>(null)
const openMoreId = ref<number | null>(null)
const openMenuId = computed(() => openEmojiId.value ?? openMoreId.value)
function setMenu(which: 'emoji' | 'more', id: number, v: boolean): void {
  const target = which === 'emoji' ? openEmojiId : openMoreId
  target.value = v ? id : target.value === id ? null : target.value
}

// ── 表情回应 ──
// 一组固定 emoji 面板（不引入重依赖）；点击 toggle（me 为真则移除，否则添加）。
const emojiPalette = ['👍', '❤️', '😂', '🎉', '🙏', '👀', '😮', '😢']
// blockId → 该消息的回应聚合列表（空的块服务端省略，故这里也只含有回应的）。
const reactions = ref<Record<number, ReactionSummary[]>>({})
function reactionsFor(id: number): ReactionSummary[] {
  return reactions.value[id] ?? []
}
// 批量查询当前已加载消息的回应（在 reset / 每轮新消息 / 每次 react 后调用）。
async function reloadReactions(): Promise<void> {
  const tid = props.threadId
  const ids = messages.value.filter((m) => !m.deleted).map((m) => m.id)
  if (!tid || !ids.length) {
    reactions.value = {}
    return
  }
  try {
    const { reactions: r } = await ThreadsApi.queryReactions(tid, ids)
    if (props.threadId !== tid) return
    const out: Record<number, ReactionSummary[]> = {}
    for (const [k, v] of Object.entries(r)) out[Number(k)] = v
    reactions.value = out
  } catch {
    /* transient */
  }
}
async function toggleReaction(m: Message, emoji: string): Promise<void> {
  const tid = props.threadId
  if (!tid) return
  const mine = reactionsFor(m.id).find((r) => r.emoji === emoji)?.me
  try {
    if (mine) await ThreadsApi.removeReaction(tid, m.id, emoji)
    else await ThreadsApi.addReaction(tid, m.id, emoji)
    await reloadReactions()
  } catch (e) {
    messageFailed(e instanceof Error ? e.message : '操作失败')
  }
}

// ── 置顶 ──
const pins = ref<Message[]>([])
async function reloadPins(): Promise<void> {
  const tid = props.threadId
  if (!tid) return
  try {
    const { messages: p } = await ThreadsApi.listPins(tid)
    if (props.threadId !== tid) return
    pins.value = p
  } catch {
    /* transient */
  }
}
// 把服务端返回的最新消息就地更新到列表（删除/置顶后 deleted/pinned 立刻反映）。
function patchMessage(updated: Message): void {
  const i = messages.value.findIndex((m) => m.id === updated.id)
  if (i >= 0) messages.value[i] = { ...messages.value[i], ...updated }
}
async function togglePin(m: Message): Promise<void> {
  const tid = props.threadId
  if (!tid) return
  try {
    const { message } = m.pinned
      ? await ThreadsApi.unpinMessage(tid, m.id)
      : await ThreadsApi.pinMessage(tid, m.id)
    patchMessage(message)
    await reloadPins()
  } catch (e) {
    messageFailed(e instanceof Error ? e.message : '操作失败')
  }
}
async function unpin(m: Message): Promise<void> {
  const tid = props.threadId
  if (!tid) return
  try {
    const { message } = await ThreadsApi.unpinMessage(tid, m.id)
    patchMessage(message)
    await reloadPins()
  } catch (e) {
    messageFailed(e instanceof Error ? e.message : '操作失败')
  }
}

// ── 多选 ──
const selectMode = ref(false)
const selected = ref<Set<number>>(new Set())
function enterSelect(seedId?: number): void {
  selectMode.value = true
  selected.value = new Set(seedId != null ? [seedId] : [])
}
function exitSelect(): void {
  selectMode.value = false
  selected.value = new Set()
}
function toggleSelect(id: number): void {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

// ── 删除（软删除，二次确认）──
async function deleteMessages(ids: number[]): Promise<void> {
  const tid = props.threadId
  if (!tid || !ids.length) return
  if (!window.confirm(ids.length > 1 ? `删除选中的 ${ids.length} 条消息？` : '删除这条消息？')) return
  try {
    for (const id of ids) {
      const { message } = await ThreadsApi.deleteMessage(tid, id)
      patchMessage(message)
    }
    messageSucceed('已删除')
    await reloadPins()
    if (selectMode.value) exitSelect()
  } catch (e) {
    messageFailed(e instanceof Error ? e.message : '删除失败')
  }
}

// ── 转发 ──
const forwardOpen = ref(false)
const forwarding = ref(false)
const forwardTargets = ref<Thread[]>([])
const forwardBlockIds = ref<number[]>([])
async function openForward(ids: number[]): Promise<void> {
  if (!ids.length) return
  forwardBlockIds.value = ids
  forwardOpen.value = true
  try {
    const { threads } = await ThreadsApi.listThreads()
    forwardTargets.value = threads.filter((t) => t.id !== props.threadId)
  } catch {
    forwardTargets.value = []
  }
}
async function doForward(targetThreadId: number): Promise<void> {
  const tid = props.threadId
  if (!tid || forwarding.value) return
  forwarding.value = true
  try {
    for (const id of forwardBlockIds.value) {
      await ThreadsApi.forwardMessage(tid, id, targetThreadId)
    }
    messageSucceed(`已转发 ${forwardBlockIds.value.length} 条消息`)
    forwardOpen.value = false
    if (selectMode.value) exitSelect()
  } catch (e) {
    messageFailed(e instanceof Error ? e.message : '转发失败')
  } finally {
    forwarding.value = false
  }
}

// ── 导出到文档 ──
const exportOpen = ref(false)
const exporting = ref(false)
const exportBlockIds = ref<number[]>([])
const exportProjects = ref<{ id: number; name: string }[]>([])
const exportProjectId = ref<number | null>(null)
const exportTitle = ref('')
async function openExport(ids: number[]): Promise<void> {
  if (!ids.length) return
  exportBlockIds.value = ids
  exportTitle.value = ''
  exportOpen.value = true
  try {
    const res = await ProjectsApi.listMine()
    exportProjects.value = (res.data.projects ?? []).map((p) => ({ id: p.id, name: p.name }))
  } catch {
    exportProjects.value = []
  }
  // 会话若绑定项目则默认它，否则取第一个。
  const bound = props.thread?.project_id ?? null
  exportProjectId.value =
    bound && exportProjects.value.some((p) => p.id === bound)
      ? bound
      : (exportProjects.value[0]?.id ?? null)
}
async function doExport(): Promise<void> {
  const tid = props.threadId
  if (!tid || exportProjectId.value == null || exporting.value) return
  exporting.value = true
  try {
    const { data } = await ThreadsApi.exportToDocument(tid, {
      project_id: exportProjectId.value,
      block_ids: [...exportBlockIds.value].sort((a, b) => a - b),
      ...(exportTitle.value.trim() ? { title: exportTitle.value.trim() } : {}),
    })
    messageSucceed(`已导出到文档「${data.document.title}」`)
    exportOpen.value = false
    if (selectMode.value) exitSelect()
  } catch (e) {
    messageFailed(e instanceof Error ? e.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

async function reset(): Promise<void> {
  messages.value = []
  members.value = []
  replyTo.value = null
  highlightId.value = null
  reactions.value = {}
  pins.value = []
  exitSelect()
  lastId = 0
  readReported = 0
  loading.value = true
  await Promise.all([pollMessages(), pollMembers()])
  loading.value = false
  await Promise.all([reloadReactions(), reloadPins()])
  await scrollToBottom()
}

// Serialize polls so the 2s interval and the poll triggered by send() (or a member
// poll) never run concurrently: two overlapping polls would both read the same `lastId`,
// both fetch the same new block, and both append it — a transient duplicate that a
// refresh clears. Chaining runs them back-to-back (each sees the previous poll's advanced
// `lastId`); the `id > lastId` filter is a belt-and-suspenders guard against re-append.
let pollChain: Promise<void> = Promise.resolve()
function pollMessages(): Promise<void> {
  pollChain = pollChain.then(doPollMessages, doPollMessages)
  return pollChain
}
async function doPollMessages(): Promise<void> {
  const tid = props.threadId
  if (!tid) return
  try {
    const { messages: fresh } = await ThreadsApi.listMessages(tid, lastId)
    // Thread switched (reset() cleared the list + lastId) while this poll was in flight —
    // drop its result so old-thread messages don't leak into the new thread's view.
    if (props.threadId !== tid) return
    const incoming = fresh.filter((m) => m.id > lastId)
    if (incoming.length) {
      messages.value.push(...incoming)
      lastId = incoming[incoming.length - 1].id
      await scrollToBottom()
      void advanceRead(lastId)
      // 新消息可能已带回应（如转发/历史）；重查一遍可见块的回应。
      void reloadReactions()
    }
  } catch {
    /* transient */
  }
}

// The viewer has seen up to `upto`; advance our own read water-mark so other members'
// 已阅 pies fill. Monotonic + de-duped so we don't POST on every 2s poll. Feishu-style:
// having the open thread scrolled to the newest message counts as read.
let readReported = 0
async function advanceRead(upto: number): Promise<void> {
  const tid = props.threadId
  if (!tid || upto <= readReported) return
  readReported = upto
  try {
    await ThreadsApi.markRead(tid, upto)
  } catch {
    readReported = 0 // let a later poll retry
  }
}
async function pollMembers(): Promise<void> {
  if (!props.threadId) return
  try {
    const { members: fresh } = await ThreadsApi.listMembers(props.threadId)
    members.value = fresh
  } catch {
    /* transient */
  }
}

// 子组件 ChatComposer 发送时触发：文本 / 提及 / 引用目标由子组件解析后传入。
async function onComposerSend(payload: { text: string; mentions: number[]; replyToId: number | null }): Promise<void> {
  if (!props.threadId) return
  replyTo.value = null
  try {
    await ThreadsApi.postMessage(props.threadId, payload.text, payload.mentions, payload.replyToId)
  } catch {
    /* surfaced on next poll; keep UI responsive */
  }
  await pollMessages()
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

let msgTimer = 0
let memberTimer = 0
function startPolling(): void {
  stopPolling()
  msgTimer = window.setInterval(pollMessages, 2000)
  // Poll member/agent status fast so the elapsed/tokens badge + working ring track the
  // driver in near-real-time (like web-claude), not the slower message cadence.
  memberTimer = window.setInterval(pollMembers, 1000)
}
function stopPolling(): void {
  clearInterval(msgTimer)
  clearInterval(memberTimer)
}

watch(
  () => props.threadId,
  (tid) => {
    stopPolling()
    if (tid) {
      void reset()
      startPolling()
    }
  },
  { immediate: true }
)
onUnmounted(() => {
  stopPolling()
  clearTimeout(highlightTimer)
})
</script>

<style scoped>
.pc {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  flex: 1;
}
.pc-head {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pc-title {
  font-weight: 600;
}
.pc-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.pc-row {
  display: flex;
  gap: 10px;
  max-width: 82%;
}
.pc-row--me {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.pc-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.pc-row--me .pc-col {
  align-items: flex-end;
}
.pc-who {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin-bottom: 3px;
}
/* 自己的消息名字靠右：把时间放到名字「左侧」（内侧朝向内容）。 */
.pc-row--me .pc-who {
  flex-direction: row-reverse;
}
.pc-time {
  color: rgba(var(--v-theme-on-surface), 0.45);
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.12s;
}
.pc-row:hover .pc-time,
.pc-row--active .pc-time {
  opacity: 1;
}
.pc-bubble {
  padding: 8px 12px;
  border-radius: 10px;
  white-space: pre-wrap;
  word-break: break-word;
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}
.pc-bubble--other {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgb(var(--v-theme-on-surface));
}
.pc-bubble-line {
  display: flex;
  align-items: center;
  gap: 6px;
}
/* 引用按钮：默认隐藏，悬停整行时淡入。 */
.pc-quote-btn {
  opacity: 0;
  transition: opacity 0.12s;
  flex: none;
}
.pc-row:hover .pc-quote-btn {
  opacity: 0.6;
}
.pc-quote-btn:hover {
  opacity: 1 !important;
}
/* 消息上方的引用预览块。 */
.pc-quote {
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  margin-bottom: 4px;
  padding: 3px 8px;
  border-left: 2px solid rgba(var(--v-theme-primary), 0.6);
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.05);
  font-size: 12px;
  cursor: pointer;
  overflow: hidden;
}
.pc-row--me .pc-quote {
  flex-direction: row-reverse;
  border-left: none;
  border-right: 2px solid rgba(var(--v-theme-primary), 0.6);
}
.pc-quote--gone {
  cursor: default;
  opacity: 0.6;
}
.pc-quote-ic {
  flex: none;
  opacity: 0.5;
}
.pc-quote-who {
  flex: none;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.pc-quote-text,
.pc-quote-gone {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
/* 点引用滚动到原消息时的短暂高亮。 */
.pc-row--hl .pc-bubble {
  animation: pc-hl 1.6s ease-out;
}
@keyframes pc-hl {
  0%,
  40% {
    box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0.7);
  }
  100% {
    box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0);
  }
}
/* 组合框上方「正在引用」气泡。 */
.pc-reply-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  padding: 4px 6px 4px 10px;
  border-left: 2px solid rgb(var(--v-theme-primary));
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: 12px;
}
.pc-reply-ic {
  flex: none;
  opacity: 0.6;
}
.pc-reply-who {
  flex: none;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.pc-reply-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.pc-pie {
  flex: none;
  display: inline-flex;
  cursor: pointer;
}
.pc-read-card {
  padding: 10px 12px;
}
.pc-read-head {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.pc-read-sec {
  margin-top: 6px;
}
.pc-read-label {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin: 4px 0;
}
.pc-read-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
}
.pc-read-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-empty {
  margin: auto;
  text-align: center;
  font-size: 13px;
}
.pc-composer {
  padding: 10px 14px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  position: relative;
}
.pc-mention {
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
  background: rgba(var(--v-theme-primary), 0.12);
  border-radius: 4px;
  padding: 0 2px;
}
.pc-bubble .pc-mention {
  color: inherit;
  background: rgba(255, 255, 255, 0.25);
}
.pc-bubble--other .pc-mention {
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.14);
}
.pc-mention-list {
  padding: 4px;
  overflow-y: auto;
}
.pc-mention-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}
.pc-mention-row:hover,
.pc-mention-row--active {
  background: rgba(var(--v-theme-primary), 0.14);
}
.pc-mention-name {
  font-size: 14px;
}
.pc-mention-empty {
  padding: 8px;
  font-size: 12px;
  opacity: 0.5;
}

/* ── 置顶消息条 ── */
.pc-pins {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 16px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgba(var(--v-theme-primary), 0.05);
}
.pc-pins-ic {
  margin-top: 3px;
  color: rgb(var(--v-theme-primary));
  flex: none;
}
.pc-pins-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.pc-pin-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
  padding: 1px 4px;
}
.pc-pin-item:hover {
  background: rgba(var(--v-theme-primary), 0.1);
}
.pc-pin-who {
  flex: none;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.pc-pin-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), 0.75);
}
.pc-pin-x {
  flex: none;
  margin-left: auto;
  opacity: 0.5;
}

/* ── 悬停操作条 ── */
.pc-row {
  position: relative;
}
.pc-bubble-wrap {
  position: relative;
  min-width: 0;
}
.pc-actions {
  position: absolute;
  /* 抬到气泡上边缘之上：可压住圆角/顶部留白，但绝不盖住第一行文字。 */
  top: -28px;
  right: 0;
  display: flex;
  align-items: center;
  gap: 1px;
  padding: 1px 3px;
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.14);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s;
  z-index: 2;
}
.pc-row--me .pc-actions {
  right: auto;
  left: 0;
}
.pc-row:hover .pc-actions,
.pc-row--active .pc-actions {
  opacity: 1;
  pointer-events: auto;
}
/* 墓碑 */
.pc-bubble--tomb {
  display: inline-flex;
  align-items: center;
  background: rgba(var(--v-theme-on-surface), 0.05);
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-style: italic;
  font-size: 13px;
}

/* ── 表情回应 chips ── */
.pc-reactions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}
.pc-row--me .pc-reactions {
  justify-content: flex-end;
}
.pc-react-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  border-radius: 11px;
  font-size: 12px;
  line-height: 18px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  border: 1px solid transparent;
  cursor: pointer;
}
.pc-react-chip--me {
  background: rgba(var(--v-theme-primary), 0.14);
  border-color: rgba(var(--v-theme-primary), 0.5);
}
.pc-react-count {
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.pc-react-chip--me .pc-react-count {
  color: rgb(var(--v-theme-primary));
}
/* emoji 选择面板 */
.pc-emoji-pop {
  display: flex;
  gap: 2px;
  padding: 6px;
}
.pc-emoji-btn {
  font-size: 20px;
  line-height: 1;
  padding: 4px;
  border-radius: 6px;
  cursor: pointer;
  background: transparent;
}
.pc-emoji-btn:hover {
  background: rgba(var(--v-theme-primary), 0.12);
}

/* ── 多选 ── */
.pc-sel-box {
  flex: none;
  align-self: center;
}
.pc-row--sel .pc-bubble {
  outline: 2px solid rgba(var(--v-theme-primary), 0.6);
  outline-offset: 1px;
}
.pc-batchbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 14px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgba(var(--v-theme-surface), 1);
}
.pc-batch-count {
  font-size: 13px;
  font-weight: 600;
}
.pc-fwd-body {
  max-height: 340px;
  overflow-y: auto;
}
</style>
