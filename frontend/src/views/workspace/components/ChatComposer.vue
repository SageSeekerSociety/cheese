<!-- 聊天底部输入区：textarea + @提及菜单 + 引用条 + 发送。
     draft（草稿）状态完全下沉到本组件，只在「发送」时 emit 给父组件。
     这样每敲一个键只重渲染这个很小的组件，而不会触发父组件里那条（可能很长的）
     消息列表 + 每条消息的 hover 栏/popover/AgentAvatar 的 diff——输入延迟不再随消息数增长。 -->
<template>
  <div class="pc-composer">
    <!-- 正在引用：显示被引用消息的作者 + 摘录，✕ 取消。 -->
    <div v-if="replyTo" class="pc-reply-chip">
      <v-icon icon="mdi-reply" size="14" class="pc-reply-ic" />
      <span class="pc-reply-who">{{ replyAuthorName }}</span>
      <span class="pc-reply-text">{{ replyExcerpt }}</span>
      <v-spacer />
      <v-btn icon="mdi-close" size="x-small" variant="text" title="取消引用" @click="emit('cancel-reply')" />
    </div>

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
          <AgentAvatar :member="c" :size="26" :show-meta="false" />
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
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { Member, Message } from '@/network/api/threads'

import AgentAvatar from './AgentAvatar.vue'

const props = defineProps<{
  members: Member[]
  currentUserId: number | null
  replyTo: Message | null
}>()
const emit = defineEmits<{
  // 发送：把最终文本、解析出的提及 user_id、引用目标 id 交给父组件去调接口。
  (e: 'send', payload: { text: string; mentions: number[]; replyToId: number | null }): void
  (e: 'cancel-reply'): void
}>()

const draft = ref('')

// ── 引用条展示 ──
const replyAuthorName = computed(() => {
  const m = props.replyTo
  if (!m) return ''
  if (m.author_id === props.currentUserId) return '我'
  return props.members.find((x) => x.user_id === m.author_id)?.nickname ?? m.author?.nickname ?? `#${m.author_id}`
})
const replyExcerpt = computed(() => {
  const t = props.replyTo?.text.trim() ?? ''
  return t.length > 140 ? `${t.slice(0, 140)}…` : t
})

// ── @成员 提及 ──
const mentionOpen = ref(false)
const mentionQuery = ref('')
const mentionIndex = ref(0) // 键盘高亮项（↑/↓ 移动，Enter 选中）
// 已选中的提及：user_id → nickname（发送时按 text 中是否仍含 @昵称 过滤）
const pickedMentions = ref<Map<number, string>>(new Map())

// 只在 @ 菜单打开时才计算候选，避免每次普通输入都做 O(成员数) 过滤。
const mentionCandidates = computed(() => {
  if (!mentionOpen.value) return []
  const q = mentionQuery.value.toLowerCase()
  return props.members.filter((m) => m.user_id !== props.currentUserId && m.nickname.toLowerCase().includes(q))
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
    send()
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
  for (const m of props.members) {
    if (text.includes(`@${m.nickname}`)) ids.add(m.user_id)
  }
  return [...ids]
}

function send(): void {
  const text = draft.value.trim()
  if (!text) return
  const mentions = resolveMentions(text)
  const replyToId = props.replyTo?.id ?? null
  // 先清本地草稿状态，再把内容交给父组件发送。
  draft.value = ''
  mentionOpen.value = false
  pickedMentions.value = new Map()
  emit('send', { text, mentions, replyToId })
}
</script>

<style scoped>
.pc-composer {
  padding: 10px 14px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  position: relative;
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
