<script setup lang="ts">
// 一张卡 —— 房间派出去的一件活，连着它自己的对话。
//
// 它替代了「点开一条活就跳到一个新地点」那套。一件活不是地点：做它的分身住在房间
// 的会话里，它没有名册、没有归档、没有自己的一轮。所以点开一张卡不该离开房间——
// 你还在这个房间里，只是从看板往下钻了一层，`?card=` 把这一层写进地址。
//
// 屏幕上每一个状态词都是后端 `presentation` 算好的，这一段一个都不推。
import type { Block, RoomTask, TodoItem } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { ApiError, editMessage, getProgress, getRoomTask, sayOnRoomTask } from '../../api'
import { useStickToBottom } from '../../composables/useStickToBottom'
import { isAgentBlock, isAgentHandle } from '../../lib/authorship'
import { columnDotStyle, phraseLabel } from '../../lib/board'
import { noticeText } from '../../lib/noticeText'
import { type PlatformNotice, platformNotice } from '../../lib/platformNotice'
import { relTime } from '../../lib/relTime'
import { editableText, renderPlain } from '../../lib/renderMessage'
import { eventArg, eventFailed, eventVerb, isNarration } from '../../lib/siteLog'
import { myHandle } from '../../me'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import MarkdownView from '../common/MarkdownView.vue'
import { useRoomSocket } from '../room/composables/useRoomSocket'
import MessageEditor from '../room/MessageEditor.vue'
import RoomNotice from '../room/RoomNotice.vue'
import TopicAcceptCard from '../TopicAcceptCard.vue'

import TodoChecklist from './TodoChecklist.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = withDefaults(
  defineProps<{
    /** 这张卡所在的房间。 */
    roomId: string | null
    /** 哪张卡。 */
    cardId: string | null
    /** 这一格在屏幕上。折起来的时候不去拉。 */
    active?: boolean
    /** 打开时停在这一条（搜索结果、链接里的 `?block=`）。 */
    focusBlock?: string | null
    /** 每有一轮动静就加一 —— 分身干活的每一步都记在这张卡上。 */
    refreshTick?: number
    /** handle → 名字。简报里的 `<@handle>` 和对话里谁说的，都照它换成名字。 */
    memberNames?: Record<string, string>
    /** 名册上查不到的 AI 座位叫什么（做这条活的分身不一定坐在名册上）。 */
    agentName?: string
  }>(),
  {
    active: false,
    focusBlock: null,
    refreshTick: 0,
    memberNames: () => ({}),
    agentName: () => t('work.room.defaultAgentName'),
  }
)

const emit = defineEmits<{
  (e: 'back'): void
  /** 去验收: 把右栏切到「改动」那一格看这份 diff。这里干不了——开在哪一格是
      `TopicView` 的事（它拿着地址），所以照 `back` 那条线一路透上去。 */
  (e: 'review'): void
}>()

const card = ref<(RoomTask & { blocks: Block[] }) | null>(null)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const draft = ref('')
const sending = ref(false)
// 留言没发出去。它和上面那个加载错误分开：能留言时任务已经加载好了，那个错误
// 只在任务没加载出来时才画，写进它的话永远不会出现在屏幕上。
const sendError = ref<string | null>(null)
const timelineRef = ref<HTMLElement | null>(null)
useStickToBottom(timelineRef, 80)

async function load(silent = false) {
  const room = props.roomId
  const id = props.cardId
  if (!room || !id) {
    card.value = null
    return
  }
  if (!silent) loading.value = true
  errorMsg.value = null
  try {
    // limit：卡下的对话是一个分身干活的全过程，一条跑久了的活能有上千块。底部对齐
    // 的窗口和聊天面板同一个道理——先给最近的，够看「它现在在干什么」。点名了一条
    // 的话，窗口往上拉到它为止。
    const focus = props.focusBlock ?? undefined
    // 跟着动静重取时，停在底部的人继续跟着最新的；翻上去在看的人不被拽走。
    const followNewest = !silent && !focus ? true : atBottom()
    let payload: RoomTask & { blocks: Block[] }
    try {
      payload = await getRoomTask(room, id, { limit: 200, through: focus })
    } catch (e) {
      // 链接点名的那一条不在这张卡里：照常打开这张卡。
      if (!(focus && e instanceof ApiError && e.status === 404)) throw e
      payload = await getRoomTask(room, id, { limit: 200 })
    }
    if (props.roomId !== room || props.cardId !== id) return
    card.value = payload
    await nextTick()
    if (!silent && focus && showBlock(focus)) return
    if (followNewest) scrollToBottom()
  } catch {
    if (props.roomId !== room || props.cardId !== id) return
    errorMsg.value = t('work.room.card.loadFailed')
    card.value = null
  } finally {
    if (props.roomId === room && props.cardId === id) loading.value = false
  }
}

function scrollToBottom() {
  const el = timelineRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function atBottom(): boolean {
  const el = timelineRef.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

// 停到一条上，并让它闪一下：滚动停下来的那一刻，眼睛要知道落在哪一行。
const flashId = ref<string | null>(null)
let flashTimer: ReturnType<typeof setTimeout> | undefined
function showBlock(id: string): boolean {
  const row = timelineRef.value?.querySelector(`[data-mid="${id}"]`)
  if (!row) return false
  row.scrollIntoView({ block: 'center' })
  clearTimeout(flashTimer)
  flashId.value = id
  flashTimer = setTimeout(() => (flashId.value = null), 1600)
  return true
}
onBeforeUnmount(() => clearTimeout(flashTimer))

// 同一张卡上换了点名的那一条（又从面板跳了一次）：在就滚过去，不在就重取到它。
watch(
  () => props.focusBlock,
  (id, was) => {
    if (!id || id === was || !card.value) return
    void nextTick(() => {
      if (!showBlock(id)) void load()
    })
  }
)

watch(
  () => [props.roomId, props.cardId, props.active, props.refreshTick] as const,
  ([, , isActive], prev) => {
    const cardChanged = prev?.[1] !== props.cardId
    if (cardChanged) card.value = null
    // 换了卡要给加载态，同一张卡跟着房间的动静重取就不要——闪一下空白比不刷新更糟。
    if (isActive) void load(!cardChanged)
  },
  { immediate: true }
)

// 做这张卡的分身写下的步骤清单（`todo_write` 带着卡的 id）。它存在卡上、推在卡自己
// 的频道上，房间那条频道收不到——所以打开卡先读一次存下的，再订这张卡的频道跟着它
// 改。和房间的清单一样整份替换：屏幕上永远是分身最后说的那一份。
const checklist = ref<TodoItem[]>([])
const checklistDone = computed(() => checklist.value.filter((i) => i.status === 'completed').length)
// 读存下的那份还没回来，频道上先到了一份新的：那一份更新，读回来的旧的不能盖掉它。
let liveSeq = 0

async function loadChecklist() {
  const room = props.roomId
  const id = props.cardId
  if (!room || !id) return
  const seq = liveSeq
  try {
    const progress = await getProgress(room, id)
    if (props.roomId !== room || props.cardId !== id || seq !== liveSeq) return
    checklist.value = progress.items ?? []
  } catch {
    // 清单是背景信息，拿不到就不画，不为它报错。
  }
}

const cardSocket = useRoomSocket({
  topicId: () => (props.active ? props.cardId ?? undefined : undefined),
  onFrame(frame) {
    if (frame.type === 'todo') {
      liveSeq += 1
      checklist.value = frame.items
    } else if (frame.type === 'assistant_block' || frame.type === 'event_block' || frame.type === 'user_block') {
      // 卡下的块发在这条活自己的频道上（后端 publish 的就是卡的 id），所以这条
      // socket 收得到。以前只认清单和 `block_updated`，于是分身干活时新落下的话和
      // 步骤块全被丢掉——只有离开这张卡再进来（重新 fetch）才看得见。
      applyLive(() => mergeBlock(frame.block))
    } else if (frame.type === 'block_updated') {
      // 块不一定已经在列表里：它可能落在初次取回的那一页之外。那时也要插进来，
      // 不能像以前那样空操作。
      applyLive(() => mergeBlock(frame.block))
    } else if (frame.type === 'retract_block') {
      applyLive(() => removeBlock(frame.block_id))
    } else if (frame.type === 'error' && cardSocket.isConnectRefusal(frame.code)) {
      cardSocket.connectRefused.value = true
    }
  },
  onOpen: () => {},
  // 断线期间可能漏掉了几次改动：重读一次，再连回去。
  reconnect(id) {
    void loadChecklist()
    cardSocket.open(id)
  },
  // 这条频道只用来跟清单，断线不值得在卡上挂一条横幅；重连照常。
  errorMsg: ref(null),
})

watch(
  () => [props.roomId, props.cardId, props.active] as const,
  ([room, id, isActive], prev) => {
    if (prev?.[1] !== id) checklist.value = []
    if (!room || !id || !isActive) {
      cardSocket.close()
      return
    }
    cardSocket.connectRefused.value = false
    cardSocket.open(id)
    void loadChecklist()
  },
  { immediate: true }
)

const dotStyle = computed(() => (card.value ? columnDotStyle(card.value.presentation.column) : {}))

// 这张卡的时间线：说过的话，和话与话之间它做过的事。
//
// 只放话的话，一条跑了半小时的活在这里就是一句「开始了」和一句「做完了」，中间
// 发生过什么只能去「现场」翻——而现场是整个房间的，分不出哪几步是这一件。所以
// 两句话之间连着的几步操作并成一行「N 步操作」，默认折着，点开是流水账。
// 芝士自言自语的那种事件（没显式发布的输出）是话，不是操作。
type Entry =
  | { kind: 'say'; block: Block }
  | { kind: 'notice'; block: Block; notice: PlatformNotice }
  | { kind: 'steps'; key: string; blocks: Block[] }

const entries = computed<Entry[]>(() => {
  const out: Entry[] = []
  for (const b of card.value?.blocks ?? []) {
    const notice = b.meta?.event_type && !b.meta?.tool ? platformNotice(b) : null
    if (notice && !['hidden', 'action', 'turn-summary'].includes(notice.mode)) {
      out.push({ kind: 'notice', block: b, notice })
    } else if (notice?.mode === 'hidden') {
      continue
    } else if (b.kind === 'event' && !isNarration(b.meta)) {
      const last = out[out.length - 1]
      if (last?.kind === 'steps') last.blocks.push(b)
      else out.push({ kind: 'steps', key: b.id, blocks: [b] })
    } else if ((b.kind === 'message' || b.kind === 'event') && (b.content || '').trim()) {
      out.push({ kind: 'say', block: b })
    }
  }
  return out
})

// 摊开了哪几段。按那一段头一步的 id 记，重拉之后同一段还是摊开的。
const openSteps = ref(new Set<string>())
function toggleSteps(key: string) {
  const next = new Set(openSteps.value)
  if (!next.delete(key)) next.add(key)
  openSteps.value = next
}

// 简报和结论是人（或芝士）写给人读的 markdown，和对话栏走同一条路：点名换成名字
// 的 chip，而不是把 `<@caisongyang>` 原样摊在标题里。
const refs = computed<RefNames>(() => ({ mentionNames: props.memberNames, topicTitles: {} }))

// 谁说的：名册上的名字；查不到的 AI 座位叫它的角色名，别露 `cheese-c82aeb40555a`。
function whoSaid(b: Block): string {
  return props.memberNames[b.author] || (isAgentHandle(b.author) ? props.agentName : b.author)
}

// 改自己在这张卡上说过的话：和房间里一样，正文原地换成输入框。
const editingId = ref<string | null>(null)
const editSaving = ref(false)
const editError = ref<string | null>(null)
function canEdit(b: Block): boolean {
  return b.kind === 'message' && b.author === myHandle() && !isAgentBlock(b)
}
// 卡上落下的每一块都从这里进：已经在列表里的原地换掉，不在的按 `created_at` 插进
// 去。不能一律推到最后——帧到的顺序和初次取回来的那一段各自是有序的，合到一起却
// 不保证还齐（断线重连补回来的那几条会晚到，落到末尾就成了「刚说的话」）。
function mergeBlock(updated: Block) {
  const blocks = card.value?.blocks
  if (!blocks) return
  const at = blocks.findIndex((b) => b.id === updated.id)
  if (at >= 0) {
    blocks.splice(at, 1, updated)
    return
  }
  // 同一份后端序列化出来的时间戳，字符串比大小就是时间比大小。
  const next = blocks.findIndex((b) => b.created_at > updated.created_at)
  if (next < 0) blocks.push(updated)
  else blocks.splice(next, 0, updated)
}

function removeBlock(id: string) {
  const blocks = card.value?.blocks
  if (!blocks) return
  const at = blocks.findIndex((b) => b.id === id)
  if (at >= 0) blocks.splice(at, 1)
}

/** 时间线变了一下：停在底部的人继续跟着最新的，翻上去看历史的不被拽走。 */
function applyLive(mutate: () => void) {
  const stick = atBottom()
  mutate()
  if (stick) void nextTick(scrollToBottom)
}
async function saveEdit(b: Block, text: string) {
  const content = text.trim()
  if (editSaving.value || !content) return
  if (content === editableText(b.content, refs.value).trim()) {
    editingId.value = null
    return
  }
  editSaving.value = true
  editError.value = null
  try {
    mergeBlock(await editMessage(b.id, content))
    if (editingId.value === b.id) editingId.value = null
  } catch {
    editError.value = t('work.room.message.saveFailed')
  } finally {
    editSaving.value = false
  }
}

async function send() {
  const room = props.roomId
  const id = props.cardId
  const text = draft.value.trim()
  if (!room || !id || !text || sending.value) return
  sending.value = true
  sendError.value = null
  try {
    await sayOnRoomTask(room, id, text)
    draft.value = ''
    await load(true)
  } catch {
    sendError.value = t('work.room.card.sendFailed')
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <section class="panel-card">
    <header class="panel-card__head">
      <button type="button" class="panel-card__back t-meta" @click="emit('back')">
        <v-icon size="14">mdi-chevron-left</v-icon>
        <span>{{ t('work.room.taskProgress.title') }}</span>
      </button>
    </header>

    <LoadingSkeleton v-if="loading && !card" variant="brief" />

    <div v-else-if="!card" class="px-3 py-4 t-body c-muted">
      {{ errorMsg ?? t('work.room.card.notFound') }}
      <BaseButton v-if="errorMsg" kind="secondary" size="sm" class="ms-1" @click="load()">{{
        t('work.room.card.retry')
      }}</BaseButton>
    </div>

    <template v-else>
      <div class="panel-card__title">
        <span class="board-dot" :style="dotStyle" aria-hidden="true" />
        <span class="t-body panel-card__name">{{ taskTitle(card) }}</span>
      </div>
      <TopicAcceptCard :topic-id="card.room_id" :task-id="card.id" topic-status="active" @review="emit('review')" />
      <div class="panel-card__meta t-meta">
        <span data-testid="card-status">{{ phraseLabel(card.presentation.phrase) }}</span>
        <span class="panel-card__sep">·</span>
        <UserRef v-if="card.owner_handle" :handle="card.owner_handle" />
        <span v-else class="c-faint">{{ t('work.board.noAssignee') }}</span>
        <span class="panel-card__sep">·</span>
        <span>{{ relTime(card.updated_at) }}</span>
        <a
          v-if="card.pr_number && card.pr_url"
          class="panel-card__pr"
          :href="card.pr_url"
          target="_blank"
          rel="noopener noreferrer"
          >PR #{{ card.pr_number }}</a
        >
      </div>

      <!-- 简报和结论是卡自己的两列，不是对话里的两条消息：派它出去时说的那份要求，
           和它交回来的那句话。放在对话上面，因为读一张卡的顺序就是「要它做什么 →
           它说做完了什么 → 过程」。 -->
      <div v-if="card.brief" class="panel-card__block">
        <div class="panel-card__block-head t-meta">{{ t('work.room.card.brief') }}</div>
        <MarkdownView class="panel-card__block-body card-markdown t-body" :source="card.brief" :names="refs" />
      </div>
      <div v-if="card.conclusion" class="panel-card__block" data-testid="card-conclusion">
        <div class="panel-card__block-head t-meta">{{ t('work.room.card.conclusion') }}</div>
        <MarkdownView class="panel-card__block-body card-markdown t-body" :source="card.conclusion" :names="refs" />
      </div>
      <!-- 分身的步骤清单，排在过程上面：先看它打算怎么做、做到了哪一步，再往下翻
           它具体做过什么。一项都没有就整段不画。 -->
      <div v-if="checklist.length" class="panel-card__block" data-testid="card-progress">
        <div class="panel-card__block-head panel-card__progress-head t-meta">
          <span>{{ t('work.room.progress.title') }}</span>
          <span class="panel-card__tally">
            {{ t('work.room.progress.tally', { done: checklistDone, total: checklist.length }) }}
          </span>
        </div>
        <TodoChecklist :items="checklist" />
      </div>

      <div ref="timelineRef" class="panel-card__timeline">
        <div v-if="!entries.length" class="px-1 py-2 t-meta c-muted">{{ t('work.room.card.noMessages') }}</div>
        <template v-for="e in entries" :key="e.kind === 'steps' ? e.key : e.block.id">
          <div
            v-if="e.kind === 'say'"
            class="card-msg"
            :class="{ 'card-msg--flash': flashId === e.block.id }"
            :data-mid="e.block.id"
          >
            <div class="card-msg__head">
              <span class="card-msg__who t-meta">{{ whoSaid(e.block) }}</span>
              <button
                v-if="canEdit(e.block) && editingId !== e.block.id"
                type="button"
                class="card-msg__edit t-meta"
                @click="editingId = e.block.id"
              >
                {{ t('work.room.message.edit') }}
              </button>
            </div>
            <MessageEditor
              v-if="editingId === e.block.id"
              :text="editableText(e.block.content, refs)"
              :saving="editSaving"
              @save="saveEdit(e.block, $event)"
              @cancel="editingId = null"
            />
            <MarkdownView
              v-else-if="isAgentBlock(e.block)"
              class="card-msg__text card-markdown t-body"
              :source="e.block.content"
              as="chat"
              :names="refs"
            />
            <span
              v-else
              class="card-msg__text t-body"
              v-html="renderPlain(e.block.kind === 'event' ? noticeText(e.block) : e.block.content, refs)"
            />
            <span v-if="e.block.meta?.edited_at && editingId !== e.block.id" class="card-msg__edited">{{
              t('work.room.message.edited')
            }}</span>
            <span v-if="editingId === e.block.id && editError" class="card-msg__error" role="alert">{{
              editError
            }}</span>
          </div>
          <RoomNotice
            v-else-if="e.kind === 'notice'"
            :block="e.block"
            :notice="e.notice"
            :run="[e.block]"
            :agent="null"
            :time="relTime(e.block.created_at)"
            :agent-name="t('work.room.card.subagent')"
            :refs="refs"
          />
          <div v-else class="card-steps">
            <button
              type="button"
              class="card-steps__head t-meta"
              :aria-expanded="openSteps.has(e.key)"
              @click="toggleSteps(e.key)"
            >
              <v-icon size="14">{{ openSteps.has(e.key) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
              <span>{{ t('work.room.card.steps', { count: e.blocks.length }) }}</span>
              <span v-if="e.blocks.some(eventFailed)" class="card-steps__failed">{{
                t('work.room.card.stepFailed')
              }}</span>
            </button>
            <ol v-if="openSteps.has(e.key)" class="card-steps__list">
              <li v-for="b in e.blocks" :key="b.id" class="card-step" :class="{ 'card-step--failed': eventFailed(b) }">
                <span class="card-step__verb">{{ eventVerb(b) }}</span>
                <span v-if="eventArg(b)" class="card-step__arg" :title="eventArg(b)">{{ eventArg(b) }}</span>
              </li>
            </ol>
          </div>
        </template>
      </div>

      <!-- 卡下能说话，但说出去的话不是直接给分身的：做这条活的分身住在房间的会话
           里，人够不着它。落在这里，房间的芝士被叫来转达。 -->
      <form class="panel-card__say" @submit.prevent="send">
        <input
          v-model="draft"
          autocomplete="off"
          class="panel-card__input t-body"
          type="text"
          :placeholder="t('work.room.card.sayPlaceholder')"
          :disabled="sending"
        />
        <button type="submit" class="panel-card__send t-meta" :disabled="sending || !draft.trim()">
          {{ t('work.room.card.send') }}
        </button>
      </form>
      <p v-if="sendError" class="panel-card__say-error t-meta" role="alert">{{ sendError }}</p>
    </template>
  </section>
</template>

<style scoped>
.panel-card {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  padding: 8px 12px 10px;
  /* 这一格自己就是它的滚动层 —— 同 `.panel-site` / `.panel-preview`。
     标题、验收卡、元信息、简报、结论、发言框的高度都由数据决定，加起来可以比面板
     高；它们又都是 `min-height: auto`，收缩不到内容高度以下。唯一的例外是下面那段
     对话区，于是它先被压扁，压到头之后多出来的部分直接从底部溢出、被外壳裁掉——
     以前这里没有滚动层，那一截既看不见也滚不到（验收卡越长、窗口越矮越明显）。 */
  overflow-y: auto;
}
.panel-card__head {
  display: flex;
  align-items: center;
}
.panel-card__back {
  display: flex;
  align-items: center;
  gap: 2px;
  color: var(--muted);
  cursor: pointer;
}
.panel-card__back:hover {
  color: var(--ink);
}
.panel-card__title {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding-top: 6px;
}
.panel-card__name {
  color: var(--ink);
  font-weight: 600;
}
.panel-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0 8px 18px;
  color: var(--muted);
}
.panel-card__sep {
  color: var(--faint);
}
.panel-card__pr {
  margin-left: auto;
  color: var(--muted);
}
.panel-card__pr:hover {
  color: var(--ink);
  text-decoration: underline;
}
.panel-card__block {
  border-top: 1px solid var(--line);
  padding: 8px 0;
}
.panel-card__block-head {
  color: var(--faint);
  padding-bottom: 2px;
}
.panel-card__progress-head {
  display: flex;
  align-items: center;
}
.panel-card__tally {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.panel-card__block-body {
  color: var(--ink);
  white-space: pre-wrap;
  /* 简报可以是几千字：块内自滚，别把整个面板撑到时间线不可达 */
  max-height: 40vh;
  overflow-y: auto;
}
.panel-card__timeline {
  flex: 1 1 auto;
  /* 曾经是 0：flex 收缩的下限。整个面板不够高时它会一路缩到底，而对话区自己是个
     `overflow-y: auto` 的盒子 —— 高度 0 的滚动盒子里的话，连滚动条都没有，等于
     读不到。留一个下限，再矮就把这一格交给面板自己的滚动条。 */
  min-height: 160px;
  overflow-y: auto;
  border-top: 1px solid var(--line);
  padding-top: 6px;
}
.card-msg {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 0;
}
.card-msg--flash {
  border-radius: var(--radius-sm);
  animation: card-msg-flash 1.6s var(--ease-out);
}
@keyframes card-msg-flash {
  from,
  25% {
    background-color: var(--accent-wash);
  }
}
.card-msg__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.card-msg__who {
  color: var(--faint);
}
/* 自己说的话后面一颗「编辑」：指到这一句才露出来，没有悬停的设备上一直在。 */
.card-msg__edit {
  padding: 0;
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.card-msg:hover .card-msg__edit,
.card-msg__edit:focus-visible {
  opacity: 1;
}
.card-msg__edit:hover {
  color: var(--ink);
}
@media (hover: none) {
  .card-msg__edit {
    opacity: 1;
  }
}
.card-msg__edited {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
.card-msg__error {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--danger-ink);
}
.card-msg__text {
  color: var(--ink);
  white-space: pre-wrap;
}
/* 两句话之间的那几步操作：默认一行，点开是流水账。字比话小一档、颜色淡一档——
   它是过程，不是这张卡要你读的东西。 */
.card-steps {
  padding: 2px 0;
}
.card-steps__head {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px 2px 2px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.card-steps__head:hover {
  background: var(--fill);
}
.card-steps__failed {
  color: var(--danger-ink);
}
.card-steps__list {
  margin: 2px 0 4px 20px;
  padding: 0;
  list-style: none;
}
.card-step {
  display: flex;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.card-step__verb {
  flex: 0 0 auto;
  color: var(--text);
}
.card-step__arg {
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-step--failed .card-step__verb {
  color: var(--danger-ink);
}
/* 简报/结论/芝士的话里的 markdown。它们住在一张卡的窄栏里、正文 14px，所以标题
   不按文档那套放大：浏览器默认的 h1 是 2em，一段「## 背景」会比卡的标题大一倍，
   上下又没留白，看上去就是几块黑字砸在正文里。这里标题只比正文重、不比正文大，
   靠上方留白分段——同对话栏 RoomMessage 的 .md-content。 */
.card-markdown {
  white-space: normal;
  overflow-wrap: anywhere;
}
.card-markdown :deep(> :first-child) {
  margin-top: 0;
}
.card-markdown :deep(> :last-child) {
  margin-bottom: 0;
}
.card-markdown :deep(p) {
  margin: 0 0 8px;
}
.card-markdown :deep(h1),
.card-markdown :deep(h2),
.card-markdown :deep(h3),
.card-markdown :deep(h4),
.card-markdown :deep(h5),
.card-markdown :deep(h6) {
  margin: 14px 0 4px;
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
}
.card-markdown :deep(h1),
.card-markdown :deep(h2) {
  font-size: 15px;
  line-height: var(--lh-15);
}
.card-markdown :deep(strong) {
  font-weight: 600;
  color: var(--ink);
}
.card-markdown :deep(ul),
.card-markdown :deep(ol) {
  padding-left: 20px;
  margin: 4px 0 8px;
}
.card-markdown :deep(li) {
  margin: 2px 0;
}
.card-markdown :deep(li::marker) {
  color: var(--faint);
}
.card-markdown :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
}
.card-markdown :deep(a:hover) {
  text-decoration: underline;
}
/* 行内代码：等宽字、缩一号、压一层浅底，和正文分得开又不跳出来。 */
.card-markdown :deep(code) {
  padding: 0.5px 5px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 0.88em;
}
.card-markdown :deep(pre),
.card-markdown :deep(table) {
  max-width: 100%;
  overflow-x: auto;
}
.card-markdown :deep(table) {
  display: block;
  border-collapse: collapse;
  margin: 4px 0 8px;
}
.card-markdown :deep(th),
.card-markdown :deep(td) {
  padding: 4px 8px;
  border: 1px solid var(--line);
}
.card-markdown :deep(pre) {
  margin: 4px 0 8px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
  white-space: pre;
}
.card-markdown :deep(pre code) {
  padding: 0;
  border: 0;
  background: none;
}
.card-markdown :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
.card-markdown :deep(hr) {
  margin: 12px 0;
  border: 0;
  border-top: 1px solid var(--line);
}
.card-markdown :deep(img) {
  max-width: 100%;
  height: auto;
}
/* 给这条活的分身带一句话。它不是房间的第二个输入框：没有框、没有底色，和上面的对话
   同一层，像在这一串话下面接一行。点进去才有一块浅底，告诉人光标在哪。 */
.panel-card__say {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 2px 4px;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.panel-card__say:focus-within {
  background: var(--fill);
}
.panel-card__input {
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px 4px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink);
}
.panel-card__input::placeholder {
  color: var(--faint);
}
.panel-card__send {
  flex: none;
  padding: 4px 8px;
  border-radius: var(--radius-md);
  color: var(--muted);
  cursor: pointer;
}
.panel-card__send:disabled {
  cursor: default;
  color: var(--faint);
}
.panel-card__send:not(:disabled):hover {
  color: var(--ink);
}
.panel-card__say-error {
  margin: 2px 4px 0;
  color: var(--danger-ink);
}
.board-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
</style>
