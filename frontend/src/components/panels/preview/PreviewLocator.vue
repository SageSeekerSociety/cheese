<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  target: { label: string; quote: string } | null
  note: string
  /** 上一次发送还没回来。发送要等一会儿，这期间再点一次会发出两条消息。 */
  busy?: boolean
}>()
const emit = defineEmits<{ 'update:note': [note: string]; send: []; cancel: [] }>()
const input = ref<HTMLInputElement | null>(null)
watch(
  () => props.target,
  () => {
    if (props.target) void nextTick(() => input.value?.focus())
  },
  { immediate: true }
)
function send(event: KeyboardEvent | MouseEvent) {
  if (props.busy) return
  if ('isComposing' in event && (event.isComposing || event.keyCode === 229)) return
  event.preventDefault()
  emit('send')
}
</script>

<template>
  <Transition name="locator">
    <div v-if="target" class="locator">
      <div class="locator__where">
        <span class="locator__label t-meta" :title="target.label">{{ target.label }}</span>
        <span class="locator__quote" :title="target.quote">{{ target.quote }}</span>
      </div>
      <input
        ref="input"
        :value="note"
        class="locator__input"
        autocomplete="off"
        :aria-label="t('work.room.preview.locatorPlaceholder')"
        :placeholder="t('work.room.preview.locatorPlaceholder')"
        @input="emit('update:note', ($event.target as HTMLInputElement).value)"
        @keydown.enter="send"
        @keydown.esc.prevent="emit('cancel')"
      />
      <BaseButton kind="primary" size="sm" :disabled="!note.trim() || busy" @click="send">
        {{ t('work.room.preview.send') }}
      </BaseButton>
      <BaseButton icon="mdi-close" size="sm" :title="t('work.room.preview.cancel')" @click="emit('cancel')" />
    </div>
  </Transition>
</template>

<style scoped>
/* 跟着面板一起排版，再贴住可见区的底边。原来用 absolute 钉在面板底边：面板自己
   在滚，钉住的是内容的那一处，于是它永远压着下面「这个房间里的东西」那几行，滚也
   滚不开。sticky 时滚到底它就落回列表后面。 */
.locator {
  position: sticky;
  bottom: 12px;
  z-index: 1;
  flex: none;
  margin: 0 12px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.locator__where {
  flex: none;
  max-width: 40%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
/* 位置可能是一串很长的标题路径，折成几行会把下面那行原文挤出框外。只占一行。 */
.locator__label {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.locator__quote {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.locator__input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  outline: none;
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.locator__input:focus {
  border-color: var(--accent);
}
@container (max-width: 440px) {
  .locator {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
  }
  .locator__where {
    grid-column: 1 / -1;
    max-width: 100%;
    flex-direction: row;
    align-items: baseline;
    gap: 8px;
  }
  .locator__label {
    flex: none;
  }
  .locator__input {
    width: 100%;
  }
}
.locator-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.locator-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.locator-enter-from,
.locator-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
