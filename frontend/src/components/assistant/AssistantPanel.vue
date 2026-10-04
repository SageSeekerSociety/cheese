<template>
  <section class="ap" :aria-label="t('tasks.assistant.label')">
    <header class="ap__head">
      <span class="ap__mark" aria-hidden="true"><CheeseAvatar :size="22" /></span>
      <v-menu location="bottom start" :disabled="busy">
        <template #activator="{ props: menu }">
          <button v-bind="menu" type="button" class="ap__title" :disabled="busy">
            <span class="ap__title-text">{{ title || t('tasks.assistant.newConversation') }}</span>
            <v-icon size="16" class="ap__title-caret">mdi-chevron-down</v-icon>
          </button>
        </template>
        <v-list density="compact" class="ap__list" :aria-label="t('tasks.assistant.conversations')">
          <v-list-subheader>{{ t('tasks.assistant.conversations') }}</v-list-subheader>
          <v-list-item
            v-if="currentId === null"
            :active="true"
            :title="t('tasks.assistant.newConversation')"
            :subtitle="t('tasks.assistant.noQuestions')"
          />
          <v-list-item
            v-for="c in conversations"
            :key="c.id"
            :active="c.id === currentId"
            :title="c.title || t('tasks.assistant.newConversation')"
            :subtitle="listSubtitle(c)"
            @click="!busy && emit('select', c.id)"
          />
        </v-list>
      </v-menu>
      <span class="ap__grow" />
      <BaseButton
        icon="mdi-pencil-outline"
        size="sm"
        :disabled="busy"
        :title="t('tasks.assistant.newConversation')"
        :aria-label="t('tasks.assistant.newConversation')"
        @click="!busy && emit('new')"
      />
      <BaseButton
        icon="mdi-close"
        size="sm"
        :title="t('tasks.assistant.close')"
        :aria-label="t('tasks.assistant.close')"
        @click="emit('close')"
      />
    </header>

    <div ref="scroller" class="ap__chat">
      <template v-if="messages.length === 0 && streaming === null">
        <p class="ap__ctx">{{ t('tasks.assistant.reads') }}</p>
        <div class="ap__a">
          <span class="ap__avatar" aria-hidden="true"><CheeseAvatar :size="26" /></span>
          <p class="ap__text">{{ t('tasks.assistant.greeting') }}</p>
        </div>
        <div class="ap__starters">
          <button v-for="s in starters" :key="s" type="button" class="ap__starter" @click="emit('send', s)">
            {{ s }}
          </button>
        </div>
      </template>

      <template v-for="(m, i) in messages" :key="i">
        <p v-if="dayOf(i)" class="ap__day">{{ dayOf(i) }}</p>
        <p v-if="m.role === 'user'" class="ap__q">{{ m.text }}</p>
        <div v-else class="ap__a">
          <span class="ap__avatar" aria-hidden="true"><CheeseAvatar :size="26" /></span>
          <div class="ap__text">
            <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
            <div v-if="m.text" class="ap__md" v-html="render(m.text)" />
            <p v-if="m.stopped" class="ap__stopped">{{ t('tasks.assistant.stopped') }}</p>
          </div>
        </div>
      </template>

      <div v-if="streaming !== null" class="ap__a">
        <span class="ap__avatar" aria-hidden="true"
          ><CheeseAvatar :size="26" :state="streaming ? null : 'think'"
        /></span>
        <div class="ap__text">
          <p v-if="!streaming" class="ap__looking">{{ queued ? t('tasks.assistant.queued') : toolLabel }}</p>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
          <div v-else class="ap__md" v-html="render(streaming)" />
        </div>
      </div>

      <p v-if="notice" class="ap__notice" role="status">
        {{ notice }}
        <NavLink v-if="creditRefused" :to="{ name: 'UserSettingsUsage' }" class="ap__usage">{{
          t('usage.viewUsage')
        }}</NavLink>
      </p>
    </div>

    <footer class="ap__foot">
      <div class="ap__box" :class="{ 'ap__box--ready': ready }">
        <textarea
          ref="input"
          v-model="draft"
          class="ap__input"
          rows="2"
          autocomplete="off"
          :placeholder="t('tasks.assistant.placeholder')"
          :aria-label="t('tasks.assistant.placeholder')"
          @keydown="onKeydown"
          @input="grow"
        />
        <div class="ap__acts">
          <BaseButton
            v-if="busy"
            class="ap__send"
            icon="mdi-stop"
            size="sm"
            :title="t('tasks.assistant.stop')"
            :aria-label="t('tasks.assistant.stop')"
            @click="emit('stop')"
          />
          <BaseButton
            v-else
            class="ap__send"
            icon="mdi-send"
            size="sm"
            :kind="ready ? 'primary' : 'ghost'"
            :disabled="!ready"
            :title="t('tasks.assistant.send')"
            :aria-label="t('tasks.assistant.send')"
            @click="submit"
          />
        </div>
      </div>
      <p class="ap__hint">{{ t('tasks.assistant.hint') }}</p>
    </footer>
  </section>
</template>

<script setup lang="ts">
// 个人芝士在一个地方的面板（#2285）：这里的几段对话、当前这段的内容、输入框。
// 只拿 props、只发事件：说话、换对话、开新对话都由外面那一层去做。
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import BaseButton from '@/components/base/BaseButton.vue'
import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import NavLink from '@/components/common/NavLink.vue'

export interface PanelConversation {
  id: string
  title: string
  lastActiveAt: string
  questions?: number
}

export interface PanelMessage {
  role: 'user' | 'assistant'
  text: string
  /** 回答被停下了，`text` 是停下时写出的部分。 */
  stopped?: boolean
  at: string
}

const props = defineProps<{
  conversations: PanelConversation[]
  currentId: string | null
  title: string
  messages: PanelMessage[]
  /** 正在流进来的回答；没在答是 null，刚开始答、还没有字是空串。 */
  streaming: string | null
  /** 芝士此刻在用的工具。 */
  tool: string | null
  /** 在等会话机空出来，还没开始答。 */
  queued?: boolean
  notice: string | null
  /** 被拒是因为额度不够：提示旁给「查看用量」。 */
  creditRefused?: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
  new: []
  select: [id: string]
  stop: []
  close: []
}>()

const { t } = useI18n()
const markdown = new MarkdownRenderer()
const draft = ref('')
const input = ref<HTMLTextAreaElement | null>(null)
const scroller = ref<HTMLElement | null>(null)

const starters = computed(() => (['basics', 'start', 'hard'] as const).map((k) => t(`tasks.assistant.starters.${k}`)))
const ready = computed(() => draft.value.trim().length > 0 && !props.busy)

const toolLabel = computed(() => {
  switch (props.tool) {
    case 'cheese_docs_search':
    case 'cheese_docs_read':
      return t('tasks.assistant.readingDocs')
    case 'cheese_my_tasks':
      return t('tasks.assistant.readingTasks')
    default:
      return t('tasks.assistant.thinking')
  }
})

function render(text: string): string {
  return markdown.render(text)
}

function day(at: string): string {
  const d = dayjs(at)
  return d.isSame(dayjs(), 'day') ? t('tasks.assistant.today') : d.format(t('tasks.page.dateFormat'))
}

/** 一天里的第一句话上面标一下是哪天。 */
function dayOf(i: number): string {
  const here = day(props.messages[i].at)
  return i === 0 || day(props.messages[i - 1].at) !== here ? here : ''
}

function listSubtitle(c: PanelConversation): string {
  return t('tasks.assistant.listed', { day: day(c.lastActiveAt), n: c.questions ?? 0 })
}

function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function submit() {
  if (!ready.value) return
  emit('send', draft.value.trim())
  draft.value = ''
  nextTick(grow)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}

watch(
  () => [props.messages.length, props.streaming, props.notice],
  () => nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight })),
  { immediate: true }
)
</script>

<style scoped>
.ap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--surface);
}

.ap__head {
  display: flex;
  gap: 4px;
  align-items: center;
  height: 48px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--line);
}

.ap__mark,
.ap__avatar {
  display: inline-flex;
  flex-shrink: 0;
}

.ap__title {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  min-width: 0;
  max-width: 240px;
  padding: 4px 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  border-radius: var(--radius-md);
}

.ap__title:hover:not(:disabled) {
  background: var(--fill);
}

.ap__title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ap__title-caret {
  color: var(--faint);
}

.ap__grow {
  flex: 1;
}

.ap__chat {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 16px;
  overflow-y: auto;
}

.ap__ctx {
  padding: 8px 10px;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-md);
}

.ap__day {
  align-self: center;
  margin: 0;
  font-size: 12px;
  color: var(--faint);
}

.ap__q {
  align-self: flex-end;
  max-width: 85%;
  padding: 8px 12px;
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
  background: var(--fill-2);
  border-radius: var(--radius-lg);
}

.ap__a {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.ap__text {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--text);
}

.ap__md :deep(p) {
  margin: 0 0 8px;
}

.ap__md :deep(ul),
.ap__md :deep(ol) {
  padding-left: 18px;
  margin: 0 0 8px;
}

.ap__looking {
  margin: 0;
  color: var(--faint);
}

.ap__stopped {
  margin: 4px 0 0;
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
}

.ap__starters {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  padding-left: 36px;
}

.ap__starter {
  padding: 6px 10px;
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.ap__starter:hover {
  background: var(--fill);
}

.ap__usage {
  margin-left: 8px;
  color: var(--accent-ink);
  font-weight: 600;
  text-decoration: none;
}

.ap__usage:hover {
  text-decoration: underline;
}

.ap__notice {
  padding: 10px 12px;
  margin: 0;
  font-size: 13px;
  color: var(--warn-ink);
  background: var(--warn-wash);
  border-radius: var(--radius-md);
}

.ap__foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
}

.ap__box {
  display: flex;
  flex-direction: column;
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ap__box:focus-within,
.ap__box--ready {
  border-color: var(--faint);
}

.ap__input {
  min-height: 40px;
  padding: 6px 0 2px;
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  resize: none;
  outline: none;
  background: transparent;
  border: 0;
}

.ap__acts {
  display: flex;
  justify-content: flex-end;
  min-height: 28px;
  margin-top: 2px;
}

.ap__send {
  width: 28px;
  height: 28px;
}

.ap__hint {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
</style>
