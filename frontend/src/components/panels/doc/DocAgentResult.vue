<script setup lang="ts">
// 输入框交出去以后的那张卡：排着队、在改或在答（都能停）；改好了说改了几处，能撤销，
// 也能接着说；撤销了能恢复；答了就是回答本身，能转成评论。改了什么不在卡上，在正文
// 里标着。
import type { AgentPhase } from '../../../composables/useDocAgent'

import { computed, ref } from 'vue'

import CheeseAvatar from '../../CheeseAvatar.vue'
import MarkdownView from '../../common/MarkdownView.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

const props = defineProps<{
  agentName: string
  phase: AgentPhase
  /** 这一次是要它改，还是只问。 */
  kind: 'edit' | 'ask'
  answer: string
  /** 改了几处。 */
  changed: number
  busy: boolean
  /** 能转成评论。 */
  commentable: boolean
  mentionNames: Record<string, string>
}>()
const emit = defineEmits<{
  (e: 'stop'): void
  (e: 'undo'): void
  (e: 'redo'): void
  (e: 'say', text: string): void
  (e: 'comment'): void
  (e: 'close'): void
}>()

const more = ref('')
function onKey(e: KeyboardEvent) {
  if (e.isComposing || e.key !== 'Enter') return
  e.preventDefault()
  if (more.value.trim()) emit('say', more.value.trim())
  more.value = ''
}
/** 回答按 Markdown 读：列表、加粗、代码照样排出来，点名读成名字。 */
const names = computed(() => ({ mentionNames: props.mentionNames, topicTitles: {} }))
</script>

<template>
  <div class="doc-agent-result" role="status">
    <template v-if="phase === 'queued' || phase === 'working'">
      <div class="doc-agent-result__row">
        <CheeseAvatar :size="18" :name="agentName" state="think" />
        <span class="doc-agent-result__text doc-agent-result__text--muted">
          {{
            phase === 'queued'
              ? t('work.room.docAgent.queued', { agent: agentName })
              : kind === 'edit'
                ? t('work.room.docAgent.working', { agent: agentName })
                : t('work.room.docAgent.answering', { agent: agentName })
          }}
        </span>
        <DocEditButton @click="emit('stop')">
          {{ phase === 'queued' ? t('work.room.docAgent.cancel') : t('work.room.docAgent.stop') }}
        </DocEditButton>
      </div>
      <MarkdownView
        v-if="kind === 'ask' && answer"
        class="doc-agent-result__answer doc-agent-result__answer--streaming md-content"
        dir="auto"
        :source="answer"
        as="chat"
        :names="names"
      />
    </template>

    <template v-else-if="phase === 'done' || phase === 'undone'">
      <div class="doc-agent-result__row">
        <span class="doc-agent-result__text">
          {{ phase === 'done' ? t('work.room.docAgent.changed', { n: changed }) : t('work.room.docAgent.undone') }}
        </span>
        <DocEditButton :disabled="busy" @click="phase === 'done' ? emit('undo') : emit('redo')">
          {{ phase === 'done' ? t('work.room.docAgent.undo') : t('work.room.docAgent.redo') }}
        </DocEditButton>
      </div>
      <div v-if="phase === 'done'" class="doc-agent-result__row doc-agent-result__more">
        <CheeseAvatar :size="18" :name="agentName" />
        <input
          v-model="more"
          class="doc-agent-result__input"
          autocomplete="off"
          :aria-label="t('work.room.docAgent.followUp')"
          :placeholder="t('work.room.docAgent.followUp')"
          @keydown="onKey"
        />
      </div>
    </template>

    <template v-else-if="phase === 'answered'">
      <div class="doc-agent-result__row doc-agent-result__row--top">
        <CheeseAvatar :size="20" :name="agentName" />
        <MarkdownView
          v-if="answer"
          class="doc-agent-result__answer md-content"
          dir="auto"
          :source="answer"
          as="chat"
          :names="names"
        />
        <div v-else class="doc-agent-result__answer" dir="auto">
          {{ kind === 'edit' ? t('work.room.docAgent.noChange') : t('work.room.docAgent.noIssue') }}
        </div>
      </div>
      <div class="doc-agent-result__actions">
        <button v-if="commentable && answer" type="button" class="doc-agent-result__link" @click="emit('comment')">
          {{ t('work.room.docAgent.toComment') }}
        </button>
        <button type="button" class="doc-agent-result__link doc-agent-result__link--quiet" @click="emit('close')">
          {{ t('work.room.docAgent.dismiss') }}
        </button>
      </div>
      <div class="doc-agent-result__row doc-agent-result__more">
        <input
          v-model="more"
          class="doc-agent-result__input"
          autocomplete="off"
          :aria-label="t('work.room.docAgent.askMore')"
          :placeholder="t('work.room.docAgent.askMore')"
          @keydown="onKey"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.doc-agent-result {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.doc-agent-result__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
}
.doc-agent-result__row--top {
  align-items: flex-start;
  padding: 12px 12px 4px;
}
.doc-agent-result__more {
  border-top: 1px solid var(--line);
}
.doc-agent-result__text {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-agent-result__text--muted {
  color: var(--muted);
  animation: docAgentBreathe 1.4s ease-in-out infinite;
}
@keyframes docAgentBreathe {
  50% {
    opacity: 0.5;
  }
}
/* 回答还在一个字一个字地来：末尾跟着一个闪的光标。 */
.doc-agent-result__answer--streaming > :last-child::after {
  content: '';
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--muted);
  animation: docAgentCaret 1s steps(1) infinite;
}
@keyframes docAgentCaret {
  50% {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .doc-agent-result__text--muted,
  .doc-agent-result__answer--streaming > :last-child::after {
    animation: none;
  }
}
.doc-agent-result__answer {
  flex: 1 1 auto;
  min-width: 0;
  max-height: 320px;
  overflow-y: auto;
  padding: 0 12px 8px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  overflow-wrap: anywhere;
}
.doc-agent-result__answer:not(.md-content) {
  white-space: pre-wrap;
}
.doc-agent-result__row--top .doc-agent-result__answer {
  padding: 0;
}
.doc-agent-result__actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 4px 12px 10px;
}
.doc-agent-result__link {
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-agent-result__link--quiet {
  color: var(--muted);
}
.doc-agent-result__link:focus-visible,
.doc-agent-result__input:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.doc-agent-result__input {
  flex: 1 1 auto;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
}
.doc-agent-result__input::placeholder {
  color: var(--faint);
}
</style>
