<script setup lang="ts">
import type { LocatorAnchor } from './locatorAnchor'

import { computed, nextTick, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    target: { label: string; quote: string } | null
    note: string
    /** 待发批注篮里已攒的条数：一次指认多处后合并成一条消息发给芝士。 */
    queuedCount?: number
    /** 这一处指认能不能先加入待发（钉在页上的一处要当场带截图发，不进纯文本待发篮）。 */
    canQueue?: boolean
    /** 上一次发送还没回来。发送要等一会儿，这期间再点一次会发出两条消息。 */
    busy?: boolean
    /** 这一份文件能不能挂评论：资料库原件和消息附件不能。不能时只给「发送」一条出口。 */
    canComment?: boolean
    /** 挂评论没成功的那一句。发不出去时不说的话，读者点了「评论」看起来像什么都没发生。 */
    commentError?: string
    /** 指的那一处在面板里的位置（面板内容坐标）。有就把框弹在它旁边；没有（键盘选中、
     *  那一处滚出了视野、面板太窄）就退回贴在可见区底边。 */
    anchor?: LocatorAnchor | null
  }>(),
  {
    queuedCount: 0,
    canQueue: true,
    busy: false,
    canComment: false,
    commentError: undefined,
    anchor: null,
  }
)
const emit = defineEmits<{
  'update:note': [note: string]
  send: []
  /** 把当前这一处先加入待发列表，选好多处后一并发送。 */
  queue: []
  /** 把待发列表里的批注全部合并发送。 */
  sendQueued: []
  /** 清空待发列表。 */
  clearQueued: []
  /** 把这句话连同这一处挂成一条评论，而不是发进对话。 */
  comment: []
  cancel: []
}>()
const recipient = ref<'agent' | 'comment'>('agent')
watch(
  () => props.canComment,
  (allowed) => {
    if (!allowed) recipient.value = 'agent'
  }
)

// 弹在指的那一处旁边：先放在它下面，下面放不下就翻到上面；左右夹在面板里。高度要等
// 框画出来才量得到，量到之前先按一个常见的高度放，免得第一帧跳一下。
const card = ref<HTMLElement | null>(null)
const height = ref(132)
const GAP = 8
const EDGE = 12
const floating = computed(() => !!props.anchor && !!props.target)
const placement = computed(() => {
  const a = props.anchor
  if (!a) return null
  const width = Math.min(400, a.panelWidth - EDGE * 2)
  const left = Math.min(Math.max(a.left, EDGE), a.panelWidth - width - EDGE)
  const below = a.bottom + GAP
  const fitsBelow = below + height.value <= a.viewBottom - EDGE
  const top = fitsBelow ? below : Math.max(a.viewTop + EDGE, a.top - GAP - height.value)
  return { left, top, width, side: fitsBelow ? 'below' : 'above' }
})
const style = computed(() =>
  floating.value && placement.value
    ? { left: `${placement.value.left}px`, top: `${placement.value.top}px`, width: `${placement.value.width}px` }
    : undefined
)
watch(
  () => [props.anchor, props.target, props.queuedCount, props.commentError, props.canComment],
  async () => {
    await nextTick()
    if (card.value?.offsetHeight) height.value = card.value.offsetHeight
  },
  { immediate: true }
)

/** 不在弹出时抢焦点。这个框是读者选中一段原文之后出来的，而页面上的选中只在窗口里
 *  焦点还留在正文上时才画——把光标交给输入框，橙色就跟着灭了，读者看不到自己选的是
 *  哪一段。要打字，点一下就进来。 */
function send(event: KeyboardEvent | MouseEvent) {
  if (props.busy) return
  if ('isComposing' in event && (event.isComposing || event.keyCode === 229)) return
  event.preventDefault()
  if (props.canComment && recipient.value === 'comment') {
    emit('comment')
    return
  }
  emit('send')
}
</script>

<template>
  <Transition name="locator">
    <div
      v-if="target || queuedCount"
      ref="card"
      class="locator"
      :class="{ 'locator--floating': floating }"
      :data-side="placement?.side"
      :style="style"
    >
      <div v-if="queuedCount && !target" class="locator__queue">
        <span class="locator__queue-badge t-meta">{{
          t('work.room.preview.queuedCount', { count: queuedCount })
        }}</span>
        <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('clearQueued')">
          {{ t('work.room.preview.clearQueued') }}
        </BaseButton>
        <BaseButton kind="primary" size="sm" :disabled="busy" @click="emit('sendQueued')">
          {{ t('work.room.preview.sendQueued', { count: queuedCount }) }}
        </BaseButton>
      </div>
      <template v-if="target">
        <div class="locator__head">
          <div class="locator__where">
            <span class="locator__label t-meta" :title="target.label">{{ target.label }}</span>
            <span class="locator__quote" :title="target.quote">{{ target.quote }}</span>
          </div>
          <BaseButton
            icon="mdi-close"
            size="sm"
            class="locator__close"
            :title="t('work.room.preview.cancel')"
            :aria-label="t('work.room.preview.cancel')"
            @click="emit('cancel')"
          />
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
        <div class="locator__foot">
          <!-- 去处只有一处可选：发进对话交给队友改，还是只挂成这一处的评论给人看。
               挂不了评论的文件（资料库原件、消息附件）没有这个选择，下拉不出现。 -->
          <select
            v-show="canComment"
            v-model="recipient"
            class="locator__recipient"
            data-testid="locator-recipient"
            :aria-label="t('work.room.preview.recipientLabel')"
            :disabled="busy"
          >
            <option value="agent">{{ t('work.room.preview.recipientAgent') }}</option>
            <option value="comment">{{ t('work.room.preview.recipientComment') }}</option>
          </select>
          <span class="locator__spacer" />
          <BaseButton
            v-if="canQueue && recipient === 'agent'"
            kind="secondary"
            size="sm"
            :title="t('work.room.preview.queueHint')"
            :disabled="!note.trim() || busy"
            @click="emit('queue')"
          >
            {{ t('work.room.preview.queue') }}
          </BaseButton>
          <BaseButton kind="primary" size="sm" :disabled="!note.trim() || busy" @click="send">
            {{
              canComment && recipient === 'comment'
                ? t('work.room.comments.comment')
                : canQueue && queuedCount
                  ? t('work.room.preview.sendQueued', { count: queuedCount + 1 })
                  : t('work.room.preview.send')
            }}
          </BaseButton>
        </div>
      </template>
      <p v-if="commentError" class="locator__error" role="alert">{{ commentError }}</p>
    </div>
  </Transition>
</template>

<style scoped>
/* 两种摆法，同一张卡片：
   - 弹出：指的那一处在视野里时，卡片绝对定位在它旁边（坐标由面板给，见 locatorAnchor）。
     面板本身在滚，卡片跟着内容一起走，不会压在别处。
   - 贴底：键盘选中、那一处滚出了视野，或者面板太窄时，跟着面板排版再贴住可见区底边
     （sticky，不是 absolute：absolute 钉住的是内容的某一处，滚也滚不开）。 */
.locator {
  position: sticky;
  bottom: 12px;
  z-index: var(--z-raised);
  flex: none;
  margin: 0 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: var(--raised);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.locator--floating {
  position: absolute;
  bottom: auto;
  margin: 0;
}
.locator__head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.locator__where {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.locator__close {
  flex: none;
  margin: -4px -6px 0 0;
}
.locator__queue,
.locator__foot {
  display: flex;
  align-items: center;
  gap: 8px;
}
/* 按钮的字随待发条数变长（「全部发送（3）」）：放不下时整行折下去，不伸出卡片。 */
.locator__foot {
  flex-wrap: wrap;
  row-gap: 6px;
}
.locator__queue-badge {
  flex: 1;
  color: var(--accent-ink);
  font-weight: 600;
}
.locator__spacer {
  flex: 1;
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
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.locator__input {
  width: 100%;
  min-width: 0;
  padding: 7px 10px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.locator__input:focus {
  border-color: var(--accent);
}
/* 去处做成一颗胶囊：原生下拉（手机上弹系统选择器、读屏念得出），外观收成和按钮一样高。 */
.locator__recipient {
  flex: none;
  max-width: 50%;
  height: 28px;
  padding: 0 26px 0 10px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background-color: var(--fill);
  background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%),
    linear-gradient(135deg, var(--muted) 50%, transparent 50%);
  background-position:
    calc(100% - 14px) 12px,
    calc(100% - 10px) 12px;
  background-size: 4px 4px;
  background-repeat: no-repeat;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  appearance: none;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.locator__recipient:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
/* 挂评论失败的那一句。 */
.locator__error {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  /* 文字用 ink 那一档：`--danger` 是标记（点、边框、图标）的颜色，压在浅底上做正文
     对比度不够（`.claude/rules/frontend.md` 里那条）。 */
  color: var(--danger-ink);
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
.locator--floating[data-side='above'].locator-enter-from,
.locator--floating[data-side='above'].locator-leave-to {
  transform: translateY(-6px);
}
</style>
