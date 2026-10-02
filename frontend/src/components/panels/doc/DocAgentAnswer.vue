<script setup lang="ts">
// 问了 AI 队友之后贴在选中的字下面的小卡：先说它在看，回答到了就把回答放上来。问题和
// 回答都在那条评论下面，「在评论里查看」打开它；回答迟迟不到，也说一声会在评论里。
import { plainTokens } from '../../../lib/renderMessage'
import CheeseAvatar from '../../CheeseAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{
  agentName: string
  /** 还在等回答。 */
  waiting: boolean
  /** 回答；等了很久没有时是 null。 */
  answer: string | null
  /** 问题发出去了（有那条评论可看）。 */
  posted: boolean
  mentionNames: Record<string, string>
}>()
const emit = defineEmits<{
  (e: 'open-thread'): void
  (e: 'close'): void
}>()
</script>

<template>
  <div class="doc-agent-answer" role="status">
    <CheeseAvatar :size="20" :name="props.agentName" :state="props.waiting ? 'think' : null" />
    <div class="doc-agent-answer__main">
      <div v-if="props.waiting" class="doc-agent-answer__text doc-agent-answer__text--muted">
        {{ t('work.room.docEdit.thinking', { agent: props.agentName }) }}
      </div>
      <div v-else-if="props.answer" class="doc-agent-answer__text" dir="auto">
        {{ plainTokens(props.answer, { mentionNames: props.mentionNames, topicTitles: {} }) }}
      </div>
      <div v-else class="doc-agent-answer__text doc-agent-answer__text--muted">
        {{ t('work.room.docEdit.noAnswerYet', { agent: props.agentName }) }}
      </div>
      <div class="doc-agent-answer__actions">
        <button v-if="props.posted" type="button" class="doc-agent-answer__link" @click="emit('open-thread')">
          {{ t('work.room.docEdit.openThread') }}
        </button>
        <button type="button" class="doc-agent-answer__link doc-agent-answer__link--quiet" @click="emit('close')">
          {{ t('work.room.docEdit.dismiss') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.doc-agent-answer {
  display: flex;
  gap: 10px;
  box-sizing: border-box;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.doc-agent-answer__main {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-agent-answer__text {
  max-height: calc(var(--lh-14) * 8);
  overflow-y: auto;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-agent-answer__text--muted {
  color: var(--muted);
}
.doc-agent-answer__actions {
  display: flex;
  gap: 16px;
  margin-top: 8px;
}
.doc-agent-answer__link {
  padding: 0;
  border: none;
  background: none;
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.doc-agent-answer__link--quiet {
  color: var(--muted);
}
.doc-agent-answer__link:hover {
  text-decoration: underline;
}
.doc-agent-answer__link:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
