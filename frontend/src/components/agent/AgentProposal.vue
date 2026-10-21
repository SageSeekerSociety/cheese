<template>
  <!-- What 芝士 drafted for the person to send: a message, or a task with its brief. -->
  <div class="aprop" :class="`aprop--${proposal.state}`" data-testid="agent-proposal">
    <div class="aprop__head">
      <template v-if="proposal.state === 'pending'">{{ t('tasks.assistant.proposal.waiting') }}</template>
      <template v-else-if="proposal.state === 'dismissed'">{{ t('tasks.assistant.proposal.dismissed') }}</template>
      <template v-else-if="proposal.kind === 'task'">{{
        t('tasks.assistant.proposal.opened', { title: proposal.title })
      }}</template>
      <template v-else>{{ t('tasks.assistant.proposal.sent', { where: proposal.where }) }}</template>
    </div>
    <dl class="aprop__rows">
      <dt>{{ proposal.kind === 'task' ? t('tasks.assistant.proposal.channel') : t('tasks.assistant.proposal.to') }}</dt>
      <dd>{{ proposal.where }}</dd>
      <template v-if="proposal.kind === 'task'">
        <dt>
          <label :for="`aprop-title-${proposal.id}`">{{ t('tasks.assistant.proposal.title') }}</label>
        </dt>
        <dd>
          <input
            v-if="pending"
            :id="`aprop-title-${proposal.id}`"
            v-model="title"
            class="aprop__input"
            autocomplete="off"
            maxlength="300"
          />
          <span v-else>{{ proposal.title }}</span>
        </dd>
      </template>
      <dt>
        <label :for="`aprop-text-${proposal.id}`">{{
          proposal.kind === 'task' ? t('tasks.assistant.proposal.brief') : t('tasks.assistant.proposal.message')
        }}</label>
      </dt>
      <dd>
        <textarea
          v-if="pending"
          :id="`aprop-text-${proposal.id}`"
          v-model="text"
          class="aprop__input aprop__text"
          autocomplete="off"
          rows="3"
        />
        <span v-else class="aprop__said">{{ proposal.text }}</span>
      </dd>
    </dl>
    <div v-if="pending" class="aprop__act">
      <BaseButton kind="primary" size="sm" :loading="busy" :disabled="!ready" @click="send">
        {{ proposal.kind === 'task' ? t('tasks.assistant.proposal.openTask') : t('tasks.assistant.proposal.send') }}
      </BaseButton>
      <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('dismiss', proposal.id)">
        {{ t('tasks.assistant.proposal.dismiss') }}
      </BaseButton>
    </div>
    <div v-else-if="proposal.task" class="aprop__act">
      <BaseButton kind="ghost" size="sm" @click="emit('open', proposal.task)">
        {{ t('tasks.assistant.proposal.view') }}
      </BaseButton>
    </div>
  </div>
</template>

<script setup lang="ts">
// 芝士替人起草、等人确认的一件事：一条要发到项目里的消息，或一个要开的任务。确认前正文
// （和任务的标题）可以改；点了才以这个人的名义发出去，怎么发由外面做。发出、不用了以后
// 卡上只写结果。
import type { PanelProposal } from '@/lib/assistantView'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const props = withDefaults(defineProps<{ proposal: PanelProposal; busy?: boolean }>(), { busy: false })

const emit = defineEmits<{
  send: [id: number, edited: { title?: string; text: string }]
  dismiss: [id: number]
  open: [task: string]
}>()

const { t } = useI18n()

const text = ref(props.proposal.text)
const title = ref(props.proposal.title ?? '')
watch(
  () => props.proposal,
  (now) => {
    text.value = now.text
    title.value = now.title ?? ''
  }
)

const pending = computed(() => props.proposal.state === 'pending')
const ready = computed(() => text.value.trim() !== '' && (props.proposal.kind !== 'task' || title.value.trim() !== ''))

function send() {
  emit('send', props.proposal.id, {
    text: text.value.trim(),
    ...(props.proposal.kind === 'task' ? { title: title.value.trim() } : {}),
  })
}
</script>

<style scoped>
.aprop {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.aprop__head {
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  color: var(--accent-ink);
}

.aprop--sent .aprop__head,
.aprop--dismissed .aprop__head {
  color: var(--muted);
}

.aprop__rows {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 12px;
  align-items: baseline;
  margin: 0;
}

.aprop__rows dt {
  color: var(--muted);
}

.aprop__rows dd {
  min-width: 0;
  margin: 0;
  color: var(--ink);
}

.aprop__said {
  white-space: pre-line;
}

.aprop__input {
  box-sizing: border-box;
  width: 100%;
  padding: 4px 8px;
  font: inherit;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.aprop__input:focus {
  outline: none;
  border-color: var(--accent);
}

.aprop__text {
  resize: vertical;
}

.aprop__act {
  display: flex;
  gap: 8px;
}
</style>
