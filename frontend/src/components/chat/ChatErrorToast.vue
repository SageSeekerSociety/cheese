<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { failureNoticesHeld } from '@/lib/toast'

// 出错提示：从底部升起，过一会儿自己淡出。新的一条直接顶替旧的，不排队——排着的
// 旧提示说的多半是已经过去的事。页面被告知版本过期之后不再出现：那时每个请求都因
// 同一个原因被拒，「请刷新」那条提示是唯一该说的话（lib/toast.ts）。
// 长相和全站的提示条（style.css 的 .card-snackbar / v-snackbar）是同一块反色，失败由
// 图标的记号色说，不把整条刷成状态色（design-system §2.5、§1.5）。
defineProps<{
  message: string | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const { t } = useI18n()
</script>

<template>
  <!-- A zero-height anchor in the flow right above the composer: the notice rises
       from here, so it floats over the end of the timeline and never covers the
       composer's own text. -->
  <div class="chat-error-anchor">
    <Transition name="toast">
      <div v-if="message && !failureNoticesHeld" :key="message" class="chat-error-toast" role="alert">
        <v-icon class="chat-error-toast__icon" size="18">mdi-cancel</v-icon>
        <span class="chat-error-toast__text">{{ message }}</span>
        <BaseButton
          class="chat-error-toast__close"
          kind="ghost"
          size="sm"
          icon="mdi-close"
          :aria-label="t('work.room.errorToast.close')"
          @click="emit('close')"
        />
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.chat-error-anchor {
  position: relative;
  height: 0;
}
.chat-error-toast {
  position: absolute;
  inset-inline: 0;
  bottom: 12px;
  z-index: var(--z-shell);
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: fit-content;
  max-width: min(560px, calc(100% - 32px));
  margin-inline: auto;
  padding: 10px 8px 10px 12px;
  border-radius: var(--radius-md);
  background: var(--inverse-surface);
  color: var(--inverse-ink);
  box-shadow: var(--shadow-2);
}
/* The icon box is one text line tall, so the mark sits on the first line however
   many lines the sentence takes. --danger on --inverse-surface clears the 3:1
   graphics floor in both themes (measured in style.css beside .card-snackbar). */
.chat-error-toast__icon {
  flex: none;
  height: var(--lh-14);
  color: var(--danger);
}
.chat-error-toast__text {
  min-width: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: start;
  overflow-wrap: anywhere;
}
/* A ghost button whose ink is the notice's own: --muted / --text would vanish on the
   inverse surface. Centred on the first line without making the notice taller. */
.chat-error-toast > .chat-error-toast__close {
  flex: none;
  width: 28px;
  height: 28px;
  margin-block: -4px;
  color: var(--inverse-ink);
  opacity: 0.72;
}
.chat-error-toast > .chat-error-toast__close:hover,
.chat-error-toast > .chat-error-toast__close:focus-visible {
  color: var(--inverse-ink);
  opacity: 1;
}
.toast-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.toast-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.toast-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.toast-leave-to {
  opacity: 0;
}
</style>
