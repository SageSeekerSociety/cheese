<script setup lang="ts">
import type { LocatorAnchor } from './locatorAnchor'

import { computed, nextTick, onBeforeUnmount, ref, watch, watchEffect } from 'vue'
import { onClickOutside } from '@vueuse/core'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    target: { label: string; quote: string } | null
    note: string
    /** 待发批注篮里已攒的条数：一次指认多处后合并成一条消息发给芝士。 */
    queuedCount?: number
    /** 待发篮在别处列着（话题页输入框上方）：本格不再自己画那条 sticky 条，
     *  同一批批注画两处读者会以为进了两个地方。 */
    pendingElsewhere?: boolean
    /** 这一处指认能不能先加入待发。页上钉的一点、图上框的一块进篮时只带位置，截图只在单独发时带。 */
    canQueue?: boolean
    /** 上一次发送还没回来。发送要等一会儿，这期间再点一次会发出两条消息。 */
    busy?: boolean
    /** 这一份文件能不能挂评论：资料库原件和消息附件不能。不能时只给「发送」一条出口。 */
    canComment?: boolean
    /** 能不能私下问个人芝士：那条是全站一条对话，登录了才有。没有时这一项不列出来
     *  （摆着一条按下去没反应的项比不摆更糟）。 */
    canAskPersonal?: boolean
    /** 挂评论没成功的那一句。发不出去时不说的话，读者点了「评论」看起来像什么都没发生。 */
    commentError?: string
    /** 指的那一处在面板里的位置（面板内容坐标）。有就把框弹在它旁边；没有（键盘选中、
     *  那一处滚出了视野、面板太窄）就退回贴在可见区底边。 */
    anchor?: LocatorAnchor | null
    maxHeight?: number
    /** 这一处是拖选出来的一段字。选字常常只是在读、在复制，不一定要写什么：这时先在
     *  选区下面浮一颗「添加批注」，点了才展开整张卡，不把刚选中的那段盖住。点一格、
     *  钉一点这类动作本身就是在指，直接出卡。 */
    armFirst?: boolean
  }>(),
  {
    queuedCount: 0,
    pendingElsewhere: false,
    canQueue: true,
    busy: false,
    canComment: false,
    canAskPersonal: false,
    commentError: undefined,
    anchor: null,
    maxHeight: undefined,
    armFirst: false,
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
  /** 私下问个人芝士：这句话和这一处只进自己和芝士那一条对话，不进这间房。 */
  personal: []
  cancel: []
}>()

// ---- 去处 ----
type Recipient = 'agent' | 'personal' | 'comment'
const recipient = ref<Recipient>('agent')
// 这一项用不了时把它切回「发给芝士」：这一份换成了挂不了评论的文件、或者这一格换到了
// 没有项目的那几处，停在一个做不了的去处上会让发送键按下去悄没声。
watch(
  () => props.canComment,
  (allowed) => {
    if (!allowed && recipient.value === 'comment') recipient.value = 'agent'
  }
)
watch(
  () => props.canAskPersonal,
  (allowed) => {
    if (!allowed && recipient.value === 'personal') recipient.value = 'agent'
  }
)
const RECIPIENTS: readonly { value: Recipient; icon: string; label: string; hint: string }[] = [
  {
    value: 'agent',
    icon: 'mdi-robot-outline',
    label: 'work.room.preview.recipientAgent',
    hint: 'work.room.preview.recipientAgentHint',
  },
  {
    value: 'personal',
    icon: 'mdi-chat-processing-outline',
    label: 'work.room.preview.recipientPersonal',
    hint: 'work.room.preview.recipientPersonalHint',
  },
  {
    value: 'comment',
    icon: 'mdi-comment-outline',
    label: 'work.room.preview.recipientComment',
    hint: 'work.room.preview.recipientCommentHint',
  },
]
/** 这一份能选的那几项：挂不了评论、或问不了个人芝士时，那两项不列出来（原型里三项
 *  齐全，是因为原型那一份两种情况都成立）。 */
const offered = computed(() =>
  RECIPIENTS.filter(
    (item) => (item.value !== 'comment' || props.canComment) && (item.value !== 'personal' || props.canAskPersonal)
  )
)
const current = computed(() => RECIPIENTS.find((r) => r.value === recipient.value) ?? RECIPIENTS[0])

/** 主按钮写什么。切到评论时它也叫「评论」，而面板上还有一颗开评论栏的「评论」——下面
 *  那颗按钮的 aria-label 就是为这一对写的。 */
const sendLabel = computed(() => {
  if (props.canComment && recipient.value === 'comment') return 'work.room.comments.comment'
  // 「全部发送（N+1）」只在去处是「发给芝士」时说得通：那一下确实把篮里的一起发了。
  // 私下问个人芝士只发当前这一处、篮里那几条原地不动，这时还写「全部发送（3）」就是骗人。
  if (props.canQueue && recipient.value === 'agent' && props.queuedCount) {
    return 'work.room.preview.sendQueued'
  }
  return 'work.room.preview.send'
})

/** 主按钮上的计数参数：只有「全部发送」那一句带它。 */
const sendCount = computed(() => props.queuedCount + 1)

/** 底下那行提示：这一下按下去会发生什么。 */
const hintLabel = computed(() => {
  if (recipient.value === 'personal') return 'work.room.preview.locatorHintPersonal'
  if (props.canQueue && recipient.value === 'agent') return 'work.room.preview.locatorHintQueue'
  return 'work.room.preview.locatorHintSend'
})
const menuOpen = ref(false)
const picker = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const options = ref<HTMLElement[]>([])
onClickOutside(picker, () => {
  menuOpen.value = false
})
/** 开菜单时焦点落在当前选中的那一项上，上下键在两项间移动，Esc 关掉回到按钮。 */
async function openMenu() {
  if (props.busy) return
  menuOpen.value = true
  await nextTick()
  const at = offered.value.findIndex((r) => r.value === recipient.value)
  options.value[Math.max(at, 0)]?.focus()
}
function closeMenu() {
  menuOpen.value = false
  trigger.value?.focus()
}
function moveFocus(event: KeyboardEvent, step: number) {
  event.preventDefault()
  const list = options.value
  const at = list.indexOf(document.activeElement as HTMLElement)
  // 焦点可能还在列表本身上（点了一下它的空白处）：从两端往里走，别一跳又回到第一项。
  const from = at < 0 ? (step > 0 ? list.length - 1 : 0) : at
  list[(from + step + list.length) % list.length]?.focus()
}
function choose(value: Recipient) {
  // 发着的时候不换去处：卡上的按钮和这一条要对得上。
  if (props.busy) return
  recipient.value = value
  closeMenu()
}

// ---- 先出按钮，还是直接出卡 ----
// 打开那一刻定下来，之后不随位置变：选区滚出视野时按钮先藏起来、滚回来再出现，不会
// 中途变成一整张卡又变回去。面板太窄（框只能贴底）时外面不会给 armFirst，直接出卡。
const expanded = ref(!props.armFirst)
watch(
  () => [props.target, props.armFirst] as const,
  ([target, armFirst]) => {
    expanded.value = !target || !armFirst
    menuOpen.value = false
  }
)
const armed = computed(() => !!props.target && !expanded.value)

const field = ref<HTMLTextAreaElement | null>(null)
async function expand() {
  expanded.value = true
  await nextTick()
  // 点了「添加批注」就是要写：这一下把光标交过去（直接弹出的卡不抢，见 send 上面那段）。
  field.value?.focus()
}

// ---- 弹在哪 ----
// 弹在指的那一处旁边：先放在它下面，下面放不下就翻到上面；左右夹在面板里。高度要等
// 框画出来才量得到，量到之前先按一个常见的高度放，免得第一帧跳一下。
const card = ref<HTMLElement | null>(null)

/** 卡关掉时把焦点还给打开它的那一处。
 *
 *  卡自己不抢焦点（见 `send` 上面那段：一抢，页面上的选区就没了），所以「打开它的那一处」
 *  是读者按下去那一刻拿着焦点的元素——图上那颗「选择图片区域」、表格里那一格、正文里那一段。
 *  不还的话焦点掉到 body，键盘读者要按很多下 Tab 才能回到刚才的位置。
 *
 *  只在这一刻焦点**还在卡里**时才还：读者点去别处把卡关掉的那一下，焦点该留在他点的地方，
 *  把他拽回来比不还更烦人。这一条是从图片那一档原来的自造卡里搬过来的，判据就是它原来的。
 *
 *  放在共用卡里而不是各档自己接：这段行为本来就该一样，而图片那档以前是自己接的（并进来
 *  时差点丢掉；焦点看不见，掉了没人报错）。 */
const returnFocus = ref<HTMLElement | null>(null)
watch(
  () => props.target,
  (target, before) => {
    if (target && !before) {
      const active = document.activeElement
      returnFocus.value = active instanceof HTMLElement && active !== document.body ? active : null
      return
    }
    if (!target && before) {
      const el = returnFocus.value
      returnFocus.value = null
      const active = document.activeElement
      if (!el?.isConnected || !active || !card.value?.contains(active)) return
      void nextTick(() => el.focus?.())
    }
  }
)
const height = ref(190)
const GAP = 8
const EDGE = 12
const CARD_WIDTH = 348
const ARM_HEIGHT = 32
/** 按钮自己的宽（「添加批注」/「Add note」加图标）：贴边时按它夹，别按卡片宽把它拉离选区。 */
const ARM_WIDTH = 132
const floating = computed(() => !!props.anchor && !!props.target)
const placement = computed(() => {
  const a = props.anchor
  if (!a) return null
  const width = Math.min(CARD_WIDTH, a.panelWidth - EDGE * 2)
  const span = armed.value ? ARM_WIDTH : width
  const left = Math.min(Math.max(a.left, EDGE), a.panelWidth - span - EDGE)
  const below = a.bottom + GAP
  const tall = armed.value ? ARM_HEIGHT : height.value
  const fitsBelow = below + tall <= a.viewBottom - EDGE
  const top = fitsBelow ? below : Math.max(a.viewTop + EDGE, a.top - GAP - tall)
  return { left, top, width, side: fitsBelow ? 'below' : 'above' }
})
const style = computed(() => {
  const bounds = props.maxHeight === undefined ? {} : { maxHeight: `${Math.max(0, props.maxHeight - EDGE * 2)}px` }
  if (!floating.value || !placement.value) return bounds
  const { left, top, width } = placement.value
  const at = { left: `${left}px`, top: `${top}px` }
  return armed.value ? at : { ...bounds, ...at, width: `${width}px` }
})
const scrolling = computed(() => props.maxHeight !== undefined && height.value >= props.maxHeight - EDGE * 2)
// 框画出来以后量一次真实高度：量到之前先按一个常见的高度放，免得第一帧跳一下。
function measureCard() {
  if (!armed.value && card.value?.offsetHeight) height.value = card.value.offsetHeight
}
watch(
  () => [!!props.anchor, props.target, props.queuedCount, props.commentError, props.canComment, expanded.value],
  async () => {
    await nextTick()
    measureCard()
  },
  { immediate: true }
)
// 输入框随字往下长（两行到五行），卡片跟着变高：位置按新高度重算，否则往下长时「发送」
// 会被推出可见区，翻到上面时又会盖住刚选中的那一段。
// 用 watchEffect 而不是 watch：卡挂在 v-else-if 的分支上，第一次渲染时这个 ref 就已经
// 指向元素了，非 immediate 的 watch 看不到那一次。
let cardObserver: ResizeObserver | null = null
watchEffect(() => {
  const el = card.value
  cardObserver?.disconnect()
  cardObserver = null
  if (!el || typeof ResizeObserver === 'undefined') return
  cardObserver = new ResizeObserver(measureCard)
  cardObserver.observe(el)
})
onBeforeUnmount(() => cardObserver?.disconnect())

// 两行起步，字多了往下长，长到五行就在框里滚：批注常常是一句话，偶尔是一段要求。
const LINE = 22
function grow() {
  const box = field.value
  if (!box) return
  box.style.height = 'auto'
  box.style.height = `${Math.min(box.scrollHeight, 5 * LINE + 18)}px`
  measureCard()
}
watch(
  () => props.note,
  () => void nextTick(grow)
)
// 展开时把带进来的字先量一次：预填的批注（从待发篮改一条）也按真实行数排版。
watch(
  () => [props.target, expanded.value] as const,
  () => void nextTick(grow)
)

/** 直接弹出的卡不抢焦点。这个框是读者选中一段原文之后出来的，而页面上的选中只在
 *  焦点还留在正文上时才画——把光标交给输入框，橙色就跟着灭了，读者看不到自己选的是
 *  哪一段。要打字，点一下就进来。 */
function send(event: KeyboardEvent | MouseEvent) {
  if (props.busy) return
  if ('isComposing' in event && (event.isComposing || event.keyCode === 229)) return
  // Shift+Enter 换行，Enter 发送。
  if (event instanceof KeyboardEvent && event.shiftKey) return
  event.preventDefault()
  if (props.canComment && recipient.value === 'comment') {
    emit('comment')
    return
  }
  if (props.canAskPersonal && recipient.value === 'personal') {
    emit('personal')
    return
  }
  emit('send')
}
</script>

<template>
  <Transition name="locator">
    <button
      v-if="armed && anchor"
      type="button"
      class="locator-arm"
      data-locator-ui
      :data-side="placement?.side"
      :style="style"
      data-testid="locator-arm"
      @mousedown.prevent
      @click="expand"
      @keydown.esc.prevent="emit('cancel')"
    >
      <v-icon size="16">mdi-message-draw</v-icon>
      <span>{{ t('work.room.preview.addNote') }}</span>
    </button>
    <div
      v-else-if="(target && !armed) || (queuedCount && !target)"
      ref="card"
      class="locator"
      data-locator-ui
      :class="{ 'locator--floating': floating, 'locator--scrolling': scrolling }"
      :data-side="placement?.side"
      :style="style"
    >
      <!-- 攒着的那几处在这一格底下留一条：计数加一句「还能接着指」，读者在原地就知道
           自己攒到哪了。待发篮在话题页列着时，那几条例子和两个按钮都在那儿（输入框
           上方），这里只留计数的提示——同一批批注画两遍按钮，读者会以为有两处。
           原型里这两条是同时在的（面板底下这一条 + 输入框上方那一篮）。 -->
      <div v-if="queuedCount && !target" class="locator__queue">
        <span class="locator__queue-badge t-meta">{{
          pendingElsewhere
            ? t('work.room.preview.pendingCount', { count: queuedCount })
            : t('work.room.preview.queuedCount', { count: queuedCount })
        }}</span>
        <span v-if="pendingElsewhere" class="locator__queue-why t-meta">{{
          t('work.room.preview.pendingKeepGoing')
        }}</span>
        <template v-else>
          <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('clearQueued')">
            {{ t('work.room.preview.clearQueued') }}
          </BaseButton>
          <BaseButton kind="primary" size="sm" :disabled="busy" @click="emit('sendQueued')">
            {{ t('work.room.preview.sendQueued', { count: queuedCount }) }}
          </BaseButton>
        </template>
      </div>
      <template v-if="target">
        <div class="locator__head">
          <v-icon size="14" class="locator__pin">mdi-map-marker-outline</v-icon>
          <span class="locator__label t-meta" :title="target.label">{{ target.label }}</span>
          <BaseButton
            icon="mdi-close"
            size="sm"
            class="locator__close"
            :title="t('work.room.preview.cancel')"
            :aria-label="t('work.room.preview.cancel')"
            @click="emit('cancel')"
          />
        </div>
        <p v-if="target.quote" class="locator__quote" :title="target.quote">{{ target.quote }}</p>
        <textarea
          ref="field"
          :value="note"
          rows="2"
          class="locator__input"
          autocomplete="off"
          :aria-label="t('work.room.preview.locatorPlaceholder')"
          :placeholder="t('work.room.preview.locatorPlaceholder')"
          @input="emit('update:note', ($event.target as HTMLTextAreaElement).value)"
          @keydown.enter="send"
          @keydown.esc.prevent="emit('cancel')"
        />
        <div class="locator__foot">
          <!-- 去处：发给芝士让它改、私下问个人芝士、还是只挂成这一处的评论给人看。每一项带
               一句说清会发生什么。这一份挂不了评论、或者没登录问不了个人芝士时，那一项
               不列出来；做得成的事只剩一件时整个下拉都不出现。 -->
          <div v-show="offered.length > 1" ref="picker" class="locator__picker">
            <button
              ref="trigger"
              type="button"
              class="locator__recipient"
              data-testid="locator-recipient"
              aria-haspopup="listbox"
              :aria-expanded="menuOpen"
              :aria-label="t('work.room.preview.recipientLabel')"
              :disabled="busy"
              @click="menuOpen ? closeMenu() : openMenu()"
              @keydown.down.prevent="openMenu"
              @keydown.esc.stop.prevent="menuOpen ? closeMenu() : emit('cancel')"
            >
              <v-icon size="14">{{ current.icon }}</v-icon>
              <span class="locator__recipient-text">{{ t(current.label) }}</span>
              <v-icon size="14">mdi-chevron-down</v-icon>
            </button>
            <ul
              v-if="menuOpen"
              class="locator__menu"
              role="listbox"
              tabindex="-1"
              :aria-label="t('work.room.preview.recipientLabel')"
              @keydown.esc.stop.prevent="closeMenu"
              @keydown.down="moveFocus($event, 1)"
              @keydown.up="moveFocus($event, -1)"
            >
              <li
                v-for="item in offered"
                :key="item.value"
                ref="options"
                role="option"
                class="locator__option"
                :class="{ 'locator__option--on': item.value === recipient }"
                :aria-selected="item.value === recipient"
                :aria-disabled="busy"
                :data-testid="`locator-recipient-${item.value}`"
                tabindex="-1"
                @click="choose(item.value)"
                @keydown.enter.prevent="choose(item.value)"
                @keydown.space.prevent="choose(item.value)"
              >
                <v-icon size="16" class="locator__option-icon">{{ item.icon }}</v-icon>
                <span class="locator__option-text">
                  <span class="locator__option-label">{{ t(item.label) }}</span>
                  <span class="locator__option-hint">{{ t(item.hint) }}</span>
                </span>
              </li>
            </ul>
          </div>
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
          <!-- 切到评论时主按钮也叫「评论」，而面板上还有一颗开评论栏的「评论」：无障碍名说清
               这一颗是「评论这一处」，读屏和按名字找控件时不会撞在一起。 -->
          <BaseButton
            kind="primary"
            size="sm"
            :aria-label="canComment && recipient === 'comment' ? t('work.room.comments.commentHere') : undefined"
            :disabled="!note.trim() || busy"
            @click="send"
          >
            {{ t(sendLabel, { count: sendCount }) }}
          </BaseButton>
        </div>
        <p class="locator__hint">
          {{ t(hintLabel) }}
        </p>
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
  /* Keep note controls above image inspection tools (4), below image editing overlays (6). */
  z-index: var(--z-raised-5);
  flex: none;
  margin: 0 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: var(--raised);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.locator > * {
  flex-shrink: 0;
}
.locator--scrolling {
  overflow: auto;
}
/* Keep an expanded recipient menu in the card's scrollable content. */
.locator--scrolling .locator__menu {
  position: static;
  max-width: 100%;
  margin-top: 6px;
}
.locator--floating {
  position: absolute;
  bottom: auto;
  margin: 0;
}
/* 选中一段之后先出的那一颗：实心琥珀色胶囊，贴在选区左下，不压选区。 */
button.locator-arm {
  position: absolute;
  z-index: var(--z-raised);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 500;
  color: var(--accent-on);
  background: var(--accent);
  border: 0;
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-2);
  cursor: pointer;
  white-space: nowrap;
}
button.locator-arm:hover {
  background: var(--accent-press);
}
button.locator-arm:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.locator__head {
  display: flex;
  align-items: center;
  gap: 4px;
}
.locator__pin {
  flex: none;
  color: var(--muted);
}
.locator__close {
  flex: none;
  margin: -6px -6px -6px auto;
}
.locator__queue,
.locator__foot {
  display: flex;
  align-items: center;
  gap: 8px;
}
/* 一行放下去处、加入待发、发送：放不下时去处那颗先收窄、名字省略，不把「发送」挤到第二行。 */
.locator__foot {
  flex-wrap: nowrap;
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
  flex: 1;
  min-width: 0;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 原文用「」括起，和对话里引用原文的写法一样；括号画在样式里，原文本身一字不动。 */
.locator__quote::before {
  content: '「';
}
.locator__quote::after {
  content: '」';
}
.locator__quote {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.locator__input {
  display: block;
  width: 100%;
  min-width: 0;
  min-height: 62px;
  padding: 8px 10px;
  font: inherit;
  font-size: 14px;
  line-height: 22px;
  color: var(--text);
  background: var(--raised);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  outline: none;
  resize: none;
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.locator__input:focus {
  border-color: var(--accent);
}
.locator__picker {
  position: relative;
  flex: 0 1 auto;
  min-width: 0;
}
/* 去处做成一颗胶囊：图标 + 当前去处 + 下拉箭头，和按钮一样高。 */
button.locator__recipient {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  height: 28px;
  padding: 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  cursor: pointer;
}
button.locator__recipient:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.locator__recipient-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 去处菜单从按钮上方展开：卡片常常贴着面板下沿，往下开会被裁掉。 */
.locator__menu {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  z-index: var(--z-raised-2);
  /* 够一行放下最长的那句说明（「带当前位置私下问你的个人芝士，不发到群里」）：窄了
     三项的说明都折成两行，菜单比原型高出一截（原型 276×172；260 宽时 260×229）。 */
  width: 292px;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--raised);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.locator__option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
}
.locator__option:hover,
.locator__option:focus-visible {
  background: var(--fill);
  outline: none;
}
.locator__option--on {
  background: var(--accent-wash);
}
.locator__option-icon {
  flex: none;
  margin-top: 1px;
  color: var(--muted);
}
.locator__option-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.locator__option-label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.locator__option-hint {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.locator__hint {
  margin: -2px 0 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
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
[data-side='above'].locator-enter-from,
[data-side='above'].locator-leave-to {
  transform: translateY(-6px);
}
</style>
