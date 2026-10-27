<script setup lang="ts">
// 对话里芝士摆出来的一个正在跑的应用（`cheese serve`）：一张带运行状态的卡片。
//
// 只写一句「Vue dev server」时，读者点开才知道它还在不在：机器可能睡了，应用可能退出了，
// 也可能芝士后来又摆了一个新的、这一个已经不是右侧会打开的那个。卡片进了视野就问一次
// 房间当前的预览，答这件事。状态是那一刻探到的，不在后台反复问。
import { computed, inject, ref, watch } from 'vue'
import { useIntersectionObserver } from '@vueuse/core'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { FILE_FACTS_SOURCE, NO_FILE_FACTS } from '@/lib/fileFactsSource'

const props = defineProps<{
  /** 摆出它的那段对话（任务里是任务自己，不是它所在的频道）：预览是按对话记的。 */
  conversationId: string | null
  /** 摆出这个应用的那条消息。 */
  blockId: string
  /** 芝士给这个应用写的那一句说明。 */
  description: string
}>()
const emit = defineEmits<{ open: [] }>()

const facts = inject(FILE_FACTS_SOURCE, NO_FILE_FACTS)

type State = 'checking' | 'running' | 'no-app' | 'offline' | 'replaced' | 'unknown'
const state = ref<State>('checking')

const card = ref<HTMLElement | null>(null)
const seen = ref(false)
const { stop, isSupported } = useIntersectionObserver(card, ([entry]) => {
  if (!entry?.isIntersecting) return
  seen.value = true
  stop()
})
if (!isSupported.value) seen.value = true

watch(
  () => [seen.value, props.conversationId, props.blockId] as const,
  async ([visible, conversationId, blockId]) => {
    if (!visible || !conversationId) return
    state.value = 'checking'
    try {
      const live = await facts.livePreview(conversationId)
      if (!live || live.artifactId !== blockId) state.value = 'replaced'
      else state.value = live.running ? 'running' : live.tunnelUp ? 'no-app' : 'offline'
    } catch {
      state.value = 'unknown'
    }
  },
  { immediate: true }
)

const status = computed(
  () =>
    ({
      checking: t('work.room.fileCard.appChecking'),
      running: t('work.room.fileCard.appRunning'),
      'no-app': t('work.room.fileCard.appNoApp'),
      offline: t('work.room.fileCard.appOffline'),
      replaced: t('work.room.fileCard.appReplaced'),
      unknown: '',
    })[state.value]
)
</script>

<template>
  <div ref="card" class="appcard" data-testid="message-app-card" :data-state="state">
    <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- the 在右侧打开 button below is the keyboard path -->
    <div class="appcard__frame" @click="emit('open')">
      <span class="appcard__bar" aria-hidden="true"><i /><i /><i /></span>
      <span class="appcard__body">
        <v-icon size="28" class="appcard__icon">mdi-application-outline</v-icon>
        <span class="appcard__desc">{{ description }}</span>
      </span>
    </div>
    <div class="appcard__status">
      <span class="appcard__dot" :class="`appcard__dot--${state}`" aria-hidden="true" />
      <b>{{ t('work.room.fileCard.app') }}</b>
      <span v-if="status" class="appcard__state">{{ status }}</span>
    </div>
    <div class="appcard__actions">
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-dock-right" @click="emit('open')">
        {{ t('work.room.message.openInPanel') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.appcard {
  display: flex;
  flex-direction: column;
  width: 420px;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
/* 一个小浏览器窗口的样子：顶上三个点，里面是芝士那句说明。 */
.appcard__frame {
  margin: 12px 12px 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.appcard__frame:hover {
  border-color: var(--faint);
}
.appcard__bar {
  display: flex;
  gap: 5px;
  padding: 7px 10px;
  background: var(--fill);
  border-bottom: 1px solid var(--line);
}
.appcard__bar i {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}
.appcard__body {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 12px;
}
.appcard__icon {
  color: var(--muted);
}
.appcard__desc {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  overflow-wrap: anywhere;
}
.appcard__status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
}
.appcard__state {
  color: var(--muted);
}
.appcard__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}
.appcard__dot--running {
  background: var(--ok);
}
.appcard__dot--offline,
.appcard__dot--no-app {
  background: var(--danger);
}
.appcard__actions {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
}
</style>
