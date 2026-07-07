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

    <div ref="scrollEl" class="pc-scroll">
      <div v-for="m in messages" :key="m.id" class="pc-row" :class="{ 'pc-row--me': isMe(m) }">
        <AgentAvatar :member="memberFor(m)" :size="36" @open-member="openScene" />
        <div class="pc-col">
          <div class="pc-who">{{ authorName(m) }}</div>
          <div class="pc-bubble-line">
            <!-- 飞书式已阅圈：只在自己发的、且群里还有其他成员的消息上出现；点击看已读/未读明细。 -->
            <v-menu v-if="isMe(m) && readInfoFor(m).total > 0" location="top" :close-on-content-click="false">
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
            <div class="pc-bubble" :class="{ 'pc-bubble--other': !isMe(m) }">
              <template v-for="(seg, i) in renderSegments(m.text)" :key="i"
                ><span v-if="seg.mention" class="pc-mention">{{ seg.t }}</span
                ><template v-else>{{ seg.t }}</template></template
              >
            </div>
          </div>
        </div>
      </div>
      <div v-if="!messages.length && !loading" class="pc-empty text-medium-emphasis">
        还没有消息。在右上角 ⋯ 里把一个 agent 拉进群、然后在这里发消息、它会回复你。
      </div>
    </div>

    <div class="pc-composer">
      <!-- @成员选择器：输入 @ 弹出，选中插入 @昵称 并记录 user_id -->
      <v-menu v-model="mentionOpen" :close-on-content-click="false" location="top start" offset="6">
        <template #activator="{ props: _a }">
          <span v-bind="_a" />
        </template>
        <v-card min-width="220" max-height="260" class="pc-mention-list">
          <div
            v-for="(c, i) in mentionCandidates"
            :key="c.user_id"
            class="pc-mention-row"
            :class="{ 'pc-mention-row--active': i === mentionIndex }"
            @click="pickMention(c)"
            @mouseenter="mentionIndex = i"
          >
            <AgentAvatar :member="c" :size="26" />
            <span class="pc-mention-name">{{ c.nickname }}</span>
          </div>
          <div v-if="!mentionCandidates.length" class="pc-mention-empty">无匹配成员</div>
        </v-card>
      </v-menu>

      <v-textarea
        v-model="draft"
        placeholder="发送消息…（Enter 发送，Shift+Enter 换行，@ 提及成员）"
        rows="1"
        auto-grow
        max-rows="6"
        hide-details
        density="comfortable"
        variant="solo-filled"
        flat
        @update:model-value="onDraftInput"
        @keydown="onComposerKeydown"
      >
        <template #append-inner>
          <v-btn icon="mdi-send" size="small" variant="text" color="primary" :disabled="!draft.trim()" @click="send" />
        </template>
      </v-textarea>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import type { Member, Message } from '@/network/api/threads'
import type { Thread } from '@/network/api/threads'
import { ThreadsApi, threadDisplayTitle } from '@/network/api/threads'
import { currentUserId } from '@/services/account'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'
import ReadPie from './ReadPie.vue'

const props = defineProps<{ threadId: number; thread: Thread | null }>()
const ws = useWorkspace()

const messages = ref<Message[]>([])
const members = ref<Member[]>([])
const draft = ref('')
const loading = ref(false)
const scrollEl = ref<HTMLElement | null>(null)
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

// ── @成员 提及 ──
const mentionOpen = ref(false)
const mentionQuery = ref('')
const mentionIndex = ref(0) // 键盘高亮项（↑/↓ 移动，Enter 选中）
// 已选中的提及：user_id → nickname（发送时按 text 中是否仍含 @昵称 过滤）
const pickedMentions = ref<Map<number, string>>(new Map())

const mentionCandidates = computed(() => {
  const q = mentionQuery.value.toLowerCase()
  return members.value.filter((m) => m.user_id !== currentUserId.value && m.nickname.toLowerCase().includes(q))
})
// 候选变化时把高亮夹回有效范围。
watch(mentionCandidates, (list) => {
  if (mentionIndex.value >= list.length) mentionIndex.value = Math.max(0, list.length - 1)
})

// 检测 draft 末尾的 "@查询"，决定是否弹出选择器。
function onDraftInput(val: string): void {
  const m = /(?:^|\s)@([^\s@]*)$/.exec(val)
  if (m) {
    mentionQuery.value = m[1]
    mentionOpen.value = true
    mentionIndex.value = 0
  } else {
    mentionOpen.value = false
  }
}

// 微信/飞书式：@ 菜单打开时，↑/↓ 选人、Enter 确认选中、Esc 关闭；否则 Enter 发送。
function onComposerKeydown(e: KeyboardEvent): void {
  if (mentionOpen.value && mentionCandidates.value.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      mentionIndex.value = (mentionIndex.value + 1) % mentionCandidates.value.length
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      const n = mentionCandidates.value.length
      mentionIndex.value = (mentionIndex.value - 1 + n) % n
      return
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      pickMention(mentionCandidates.value[mentionIndex.value])
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      mentionOpen.value = false
      return
    }
  }
  // 普通回车发送（Shift+Enter 换行）。
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void send()
  }
}

function pickMention(c: Member): void {
  // 把末尾的 "@查询" 替换成 "@昵称 "
  draft.value = draft.value.replace(/(^|\s)@([^\s@]*)$/, `$1@${c.nickname} `)
  pickedMentions.value.set(c.user_id, c.nickname)
  mentionOpen.value = false
  mentionQuery.value = ''
}

// 发送时解析真正出现在文本里的提及 user_id（含手动输入 @昵称 的兜底）。
function resolveMentions(text: string): number[] {
  const ids = new Set<number>()
  for (const [uid, nick] of pickedMentions.value) {
    if (text.includes(`@${nick}`)) ids.add(uid)
  }
  for (const m of members.value) {
    if (text.includes(`@${m.nickname}`)) ids.add(m.user_id)
  }
  return [...ids]
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

function openScene(member: Member): void {
  if (member.sid && member.device_id) {
    ws.openSceneScreen({ sid: member.sid, device_id: member.device_id, agent_user_id: member.user_id })
  }
}

async function reset(): Promise<void> {
  messages.value = []
  members.value = []
  lastId = 0
  readReported = 0
  loading.value = true
  await Promise.all([pollMessages(), pollMembers()])
  loading.value = false
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

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || !props.threadId) return
  const mentions = resolveMentions(text)
  draft.value = ''
  mentionOpen.value = false
  pickedMentions.value = new Map()
  try {
    await ThreadsApi.postMessage(props.threadId, text, mentions)
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
onUnmounted(stopPolling)
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
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin-bottom: 3px;
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
</style>
