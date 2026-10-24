<script setup lang="ts">
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  target: { label: string; quote: string } | null
  note: string
  /** 上一次发送还没回来。发送要等一会儿，这期间再点一次会发出两条消息。 */
  busy?: boolean
  /** 这一份文件能不能挂评论：资料库原件和消息附件不能。不能时只给「发送」一条出口。 */
  canComment?: boolean
  /** 挂评论没成功的那一句。发不出去时不说的话，读者点了「评论」看起来像什么都没发生。 */
  commentError?: string
}>()
const emit = defineEmits<{
  'update:note': [note: string]
  send: []
  /** 把这句话连同这一处挂成一条评论，而不是发进对话。 */
  comment: []
  cancel: []
}>()
/** 不在弹出时抢焦点。这个框是读者选中一段原文之后出来的，而页面上的选中只在窗口里
 *  焦点还留在正文上时才画——把光标交给输入框，橙色就跟着灭了，读者看不到自己选的是
 *  哪一段。要打字，点一下就进来。 */
function send(event: KeyboardEvent | MouseEvent) {
  if (props.busy) return
  if ('isComposing' in event && (event.isComposing || event.keyCode === 229)) return
  event.preventDefault()
  emit('send')
}

/** 同一句话，另一条出口：挂成这一处的评论，别人能回、能解决。
 *
 *  指认本身不是指令——读者可能想让队友改、也可能只想在那一处留一句话。两条出口并列，
 *  由他点哪个来定（和概览文档那一栏同一套）。 */
function comment(event: MouseEvent) {
  if (props.busy || !props.canComment) return
  event.preventDefault()
  emit('comment')
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
        :value="note"
        class="locator__input"
        autocomplete="off"
        :aria-label="t('work.room.preview.locatorPlaceholder')"
        :placeholder="t('work.room.preview.locatorPlaceholder')"
        @input="emit('update:note', ($event.target as HTMLInputElement).value)"
        @keydown.enter="send"
        @keydown.esc.prevent="emit('cancel')"
      />
      <!-- 两条出口并列：指认只是把位置定下来，接着做什么由读者点哪个按钮决定。
           没有「评论」可挂的文件（资料库原件、消息附件）只露「发送」这一条。 -->
      <BaseButton v-if="canComment" kind="secondary" size="sm" :disabled="!note.trim() || busy" @click="comment">
        {{ t('work.room.comments.comment') }}
      </BaseButton>
      <BaseButton kind="primary" size="sm" :disabled="!note.trim() || busy" @click="send">
        {{ t('work.room.preview.send') }}
      </BaseButton>
      <BaseButton icon="mdi-close" size="sm" :title="t('work.room.preview.cancel')" @click="emit('cancel')" />
      <p v-if="commentError" class="locator__error" role="alert">{{ commentError }}</p>
    </div>
  </Transition>
</template>

<style scoped>
/* 跟着面板一起排版，再贴住可见区的底边。用 absolute 钉在面板底边时，面板自己在
   滚，钉住的是内容的那一处，会压着它下面的内容、滚也滚不开。sticky 时滚到底它就
   落回内容后面。 */
.locator {
  position: sticky;
  bottom: 12px;
  z-index: var(--z-raised);
  flex: none;
  margin: 0 12px 12px;
  display: flex;
  flex-wrap: wrap;
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
/* 挂评论失败的那一句：撑满一行，跟着下面两条按钮排。 */
.locator__error {
  flex-basis: 100%;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger);
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
