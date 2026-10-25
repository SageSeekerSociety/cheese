<script setup lang="ts">
// 一份房间文件上的评论：上面写一条新的，下面是一串串已有的。回复、解决、重开和文档
// 里那一套是同一张卡（DocThreadCard），因为它们本来就是同一种东西——差的是「评的那
// 几个字在哪」：文档里那是正文里的一个标记，文件上只能记「哪一版 + 哪个位置」。
//
// 这一只只认 props：线程和写入动作都是宿主递进来的（`useFileThreads`），发一条新的
// 也往上发（`send`），因为锚点要从当前预览的选中那一处来。
import type { DocComment, DocThreadActions, DocThreadState, ThreadPlace } from '../../../lib/docThreadTypes'
import type { QuotedContext } from '../../../lib/quotedContext'

import { computed, nextTick, ref, watch } from 'vue'

import DocThreadCard from '../doc/DocThreadCard.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { storageGet, storageRemove, storageSet } from '@/lib/browserStorage'

const props = defineProps<{
  /** 在哪间房。 */
  topicId: string | null
  /** 哪一份文件（房间里的地址）。 */
  path: string
  /** 这一份文件现在的版本：评论写的是哪一版，和它比就知道位置还作不作数。 */
  version: string | null
  /** 我自己的 handle。 */
  author: string
  threadState: DocThreadState
  threadActions: DocThreadActions
  /** 现在指着的这一处（没有就是 null）：发新评论时连同它一起留下。 */
  pick: { quote: string; anchor: QuotedContext | null } | null
  /** 要把它打开的那一条：读者从正文上的标记点进来的。 */
  focusId?: string | null
  /** 能不能写（归档的房间不能）。 */
  writable: boolean
  /** 发出这一条；回执回来才算数，发不出去的那句话写在框下面。 */
  post: (payload: { content: string; quote: string | null; anchor: QuotedContext | null }) => Promise<unknown>
  /** 这间房队友的名字：卡片上「谁在答」用（文件上的评论现在还没有人答）。 */
  agentName?: string
  /** handle 读成名字。必填：这一栏的每一张卡都写作者名，缺了会静默显示成 handle。 */
  nameOf: (handle: string) => string
  /** handle 读成他挑过的头像地址。必填：缺了会静默退回色块——这一栏曾经就是这样，
   *  谁也没发现。名册上没他、或他没挑过，给空串（由 UserAvatar 画首字母）。 */
  avatarOf: (handle: string) => string
}>()
const emit = defineEmits<{
  (e: 'clear-pick'): void
  (e: 'close'): void
  /** 现在看的是哪一条（读者点了另一张卡、或把这一张收起来）。正文上那颗标记要靠它
   *  知道该把哪一处画重些——不然读者在栏里换了一条看，正文还亮着上一条。 */
  (e: 'update:openId', id: string | null): void
}>()

/** 正在看的那一条：只有它在看时才给回复框和解决（和文档里那一栏同一条理由——
 *  一屏上几张卡都摊着输入框，读起来分不清在回哪一条）。 */
const openId = ref<string | null>(null)
// 读者点了正文上那一处的标记：把那一张卡打开（和文档那边点标记一样，点开的是那一条）。
//
// `immediate`：栏是关着的时候点标记才把它打开的，也就是**挂载时 `focusId` 就已经是
// 那一条了**——只在它之后变化时才处理的话，这一条路一次都接不到，栏开了却没有任何一张
// 卡是打开的。开了之后读者再去点别的标记，才是「变化」那一种。
watch(
  () => props.focusId,
  (id) => {
    if (id) openId.value = id
  },
  { immediate: true }
)
// 反过来也要说一声：读者在栏里点了别的卡、或把这张收起来，正文上那颗标记得跟着换。
// 不回声的话，栏里看的是第 2 条、正文还亮着第 1 条。
watch(openId, (id) => emit('update:openId', id))

const text = ref('')
/** 发不出去的那句话：写在这儿，别让读者点了「评论」却什么都没有发生。 */
const error = ref('')
const draftKey = computed(() => `cheese.file-comments.draft.v1:${props.author}:${props.topicId}:${props.path}`)
// 存不下就只留在这一页上：草稿是随手写的东西，丢了不报错。
if (draftKey.value) text.value = storageGet(draftKey.value) ?? ''
watch(draftKey, (key) => {
  text.value = storageGet(key) ?? ''
  // 上一份文件发失败的那句话，不该挂在下一份文件上。
  error.value = ''
})
watch(text, (value) => {
  const key = draftKey.value
  if (!key) return
  if (value) storageSet(key, value)
  else storageRemove(key)
})

const input = ref<HTMLTextAreaElement | null>(null)
function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}
watch(text, () => void nextTick(grow))

const sending = ref(false)
async function send() {
  const body = text.value.trim()
  if (!body || !props.writable || sending.value) return
  sending.value = true
  error.value = ''
  try {
    await props.post({ content: body, quote: props.pick?.quote.trim() || null, anchor: props.pick?.anchor ?? null })
    text.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('work.room.comments.postFailed')
  } finally {
    sending.value = false
  }
}
function onKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229 || e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  void send()
}

/** 这一串评还在不在原处：写它的时候是哪一版，现在还是不是那一版。 */
function placeOf(thread: DocComment): ThreadPlace {
  const anchor = thread.anchor as { version?: string } | null | undefined
  if (!anchor?.version) return null
  return anchor.version === props.version ? 'marked' : 'placed'
}

const threads = computed(() => props.threadState.threads)
const cards = ref<Record<string, InstanceType<typeof DocThreadCard> | null>>({})

async function reply(id: string, body: string) {
  try {
    await props.threadActions.reply(id, body)
    cards.value[id]?.sent(body)
  } catch {
    // 错误写在那张卡上（threadState.errors）。
  }
}
async function act(id: string, action: 'resolve' | 'reopen') {
  try {
    await props.threadActions[action](id)
  } catch {
    // 同上。
  }
}
async function stopAgent(id: string) {
  try {
    await props.threadActions.stopAgent(id)
  } catch {
    // 同上。
  }
}
async function resend(id: string) {
  try {
    const sent = await props.threadActions.recover(id)
    if (sent) cards.value[id]?.sent(sent.reply)
  } catch {
    // 同上。
  }
}

defineExpose({ focus: () => input.value?.focus() })
</script>

<template>
  <aside class="file-comments" :aria-label="t('work.room.comments.title')">
    <div class="file-comments__head">
      <span class="file-comments__title">{{ t('work.room.comments.title') }}</span>
      <span class="t-meta">{{ threads.length }}</span>
      <!-- 这一栏是浮层，盖住右边：开它的那颗按钮就在它底下，关不掉。
           所以关闭入口得长在栏自己身上，和文档那一栏同一个位置（右上角）。 -->
      <button
        type="button"
        class="file-comments__close"
        :aria-label="t('work.room.comments.close')"
        :title="t('work.room.comments.close')"
        @click="emit('close')"
      >
        <v-icon size="18">mdi-close</v-icon>
      </button>
    </div>

    <div v-if="writable" class="file-comments__draft">
      <div v-if="pick?.quote" class="file-comments__quote">
        <span>{{ pick.quote }}</span>
        <button
          type="button"
          class="file-comments__clear"
          :title="t('work.room.preview.cancel')"
          @click="emit('clear-pick')"
        >
          ×
        </button>
      </div>
      <textarea
        ref="input"
        v-model="text"
        :disabled="sending"
        rows="2"
        autocomplete="off"
        class="file-comments__input"
        :aria-label="t('work.room.comments.write')"
        :placeholder="t('work.room.comments.placeholder')"
        :title="t('work.room.comments.keyHint')"
        @keydown="onKey"
      />
      <p v-if="error" class="file-comments__error" role="alert">{{ error }}</p>
      <div class="file-comments__actions">
        <BaseButton kind="primary" size="sm" :disabled="sending || !text.trim()" @click="send()">
          {{ t('work.room.comments.comment') }}
        </BaseButton>
      </div>
    </div>

    <div class="file-comments__list">
      <p v-if="!threads.length" class="file-comments__empty">{{ t('work.room.comments.emptyOpen') }}</p>
      <DocThreadCard
        v-for="thread in threads"
        :key="thread.comment.id"
        :ref="(card) => (cards[thread.comment.id] = card as InstanceType<typeof DocThreadCard> | null)"
        :thread="thread"
        :active="openId === thread.comment.id"
        :place="placeOf(thread.comment)"
        :activity="thread.answering ? { state: thread.answering } : undefined"
        :busy="threadState.busy"
        :unknown="threadState.unknown === thread.comment.id"
        :error="threadState.errors[thread.comment.id]"
        :agent-name="agentName ?? ''"
        :mention-names="{}"
        :name-of="nameOf"
        :avatar-of="avatarOf"
        :writable="writable"
        :draft-key="`cheese.file-thread.draft.v1:${author}:${topicId}:${thread.comment.id}`"
        @select="openId = openId === thread.comment.id ? null : thread.comment.id"
        @locate="openId = thread.comment.id"
        @reply="reply(thread.comment.id, $event)"
        @resolve="act(thread.comment.id, 'resolve')"
        @reopen="act(thread.comment.id, 'reopen')"
        @resend="resend(thread.comment.id)"
        @stop-agent="stopAgent(thread.comment.id)"
      />
    </div>
  </aside>
</template>

<style scoped>
.file-comments {
  display: flex;
  flex-direction: column;
  min-height: 0;
  gap: 10px;
  padding: 12px;
  border-left: 1px solid var(--line);
  background: var(--surface);
  overflow: auto;
}
.file-comments__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.file-comments__close {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  margin-left: auto;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.file-comments__close:hover {
  background: var(--fill);
}
.file-comments__close:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.file-comments__title {
  font-weight: 600;
}
.file-comments__draft {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.file-comments__quote {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-left: 3px solid var(--line-2);
  color: var(--muted);
  font-size: 12px;
}
.file-comments__quote > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-comments__clear {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.file-comments__input {
  resize: none;
  padding: 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
}
.file-comments__actions {
  display: flex;
  justify-content: flex-end;
}
.file-comments__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}
.file-comments__error {
  margin: 0;
  color: var(--danger);
  font-size: 12px;
}
.file-comments__empty {
  color: var(--muted);
  font-size: 12px;
}
</style>
