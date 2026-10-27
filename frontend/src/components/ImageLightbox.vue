<script setup lang="ts">
// 一张图放大看：铺满窗口的一层，滚轮或双指缩放、按住拖动，点空白或 Esc 关掉。
//
// 为什么不在新标签页里打开：这张图的字节是这一页 fetch 回来、转成 object URL 的，
// 地址只活在当前这份文档里 —— 新标签页打开它是一片空白（真实附件那个地址只认
// Authorization 头，浏览器自己发的请求带不上，见 AttachmentImage）。
//
// 点开的那张，地址由调用方给（`src`），调用方负责在预览关掉之前不 revoke 它（见
// AttachmentImage 的 release）。在对话里打开时（有人注入了 CHAT_IMAGE_ACTIONS）还能
// 左右切到这段对话里的别的图，那些字节由这一层自己取、自己还（useLightboxGallery）。
//
// 顶上一条工具：缩放、旋转、翻转、裁剪、亮度饱和度只改屏幕上的样子；要留下就「另存」：
// 按同一套数重画成一张新图，下载，或放进输入框随下一条消息发出——和右侧面板里涂画
// 完的图一样作为附件进对话，原图不动。写字和打码右侧面板已有整套工具，按钮把图交过去
// 并选好那件工具，不在这里另做一套。
import type { CropBox, ImageAdjust } from '@/lib/imageEdit'

import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import LightboxCrop from './lightbox/LightboxCrop.vue'
import LightboxToolbar from './lightbox/LightboxToolbar.vue'
import { useLightboxGallery } from './lightbox/useLightboxGallery'
import { useZoomPan } from './lightbox/useZoomPan'

import { t } from '@/i18n'
import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { CHAT_IMAGE_ACTIONS } from '@/lib/chatImages'
import {
  carryCrop,
  cssClip,
  cssFilter,
  editedName,
  isEdited,
  ORIGINAL,
  renderAdjusted,
  sideways,
} from '@/lib/imageEdit'

const props = defineProps<{
  /** 图片的 object URL：调用方取好字节转出来的那一个。 */
  src: string
  /** 读屏和取不到图时说的那个名字，通常是文件名。 */
  alt: string
  /** 这张图在哪个话题、工作区里的哪条路径：有了它们才能切到同一段对话里的别的图。 */
  topicId?: string | null
  path?: string | null
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)
const actions = inject(CHAT_IMAGE_ACTIONS, null)
const gallery = useLightboxGallery(props, source, actions)
/** 「放进输入框」只在输入框此刻收得了文件时出现：归档的频道、已关闭的任务里它不在，
 *  点了会排进一个看不见的队，看着像交付成功却没有去处。 */
const canAttach = computed(() => !!actions?.canAttach())

const stage = ref<HTMLElement | null>(null)
const figure = ref<HTMLImageElement | null>(null)
const toolbar = ref<InstanceType<typeof LightboxToolbar> | null>(null)

/** 每张图各自的调整：切走再切回来，还是刚才调成的样子。按路径记而不是按第几张：
 *  对话来了新消息，位置会挪，调整跟着位置走就落到别人身上了。 */
const adjustments = reactive(new Map<string, ImageAdjust>())
const adjust = computed<ImageAdjust>(() => adjustments.get(gallery.currentPath.value) ?? ORIGINAL)
function change(next: Partial<ImageAdjust>) {
  adjustments.set(gallery.currentPath.value, { ...adjust.value, ...next })
}

/** 图一倍时四周让出来的地方：顶上工具条，底下缩略图条（有好几张时），左右切换按钮。 */
const margins = computed(() => {
  const several = gallery.total.value > 1
  return { top: 60, bottom: several ? 92 : 24, side: several ? 76 : 24 }
})
const view = useZoomPan(
  stage,
  figure,
  () => sideways(adjust.value),
  () => margins.value
)
const { scale, offset, fitted, moving } = view

/** 正在框裁剪范围：这时图不动，指针归裁剪框。 */
const cropping = ref(false)
const draftCrop = ref<CropBox>({ x: 0.1, y: 0.1, w: 0.8, h: 0.8 })
/** 图在浮层里占的那块，裁剪框铺在上面。 */
const area = ref({ left: 0, top: 0, width: 0, height: 0 })
const saving = ref(false)
const notice = ref('')

const style = computed(() => ({
  width: fitted.value.w ? `${fitted.value.w}px` : undefined,
  height: fitted.value.h ? `${fitted.value.h}px` : undefined,
  transform:
    `translate(${offset.value.x}px, ${offset.value.y}px) scale(${scale.value})` +
    ` rotate(${adjust.value.rotate}deg) scaleX(${adjust.value.flip ? -1 : 1})`,
  filter: cssFilter(adjust.value),
  // 框着裁剪范围时整张图都要看得见，好挪框。
  clipPath: cropping.value ? undefined : cssClip(adjust.value),
}))

function measureArea() {
  const el = figure.value
  const box = stage.value
  if (!el || !box) return
  const a = el.getBoundingClientRect()
  const b = box.getBoundingClientRect()
  area.value = { left: a.left - b.left, top: a.top - b.top, width: a.width, height: a.height }
}

/** 裁剪期间一直量，直到框能落在屏幕上那块为止。
 *
 *  框是照这一刻图的位置摆的，而这一刻之前有一小段过渡（图从放大的样子回到一倍），
 *  这期间还可能有别的事动它：窗口缩放、左右按钮和缩略图条随着对话变化出来或收起来、
 *  图节点换了一个、字节这才到。写死一个毫秒数会和过渡时长悄悄走散，只盯着几个事件
 *  又总漏掉下一个触发点——所以不去数触发点：每帧量一次，量到一个非零的大小、并且连着
 *  两帧都是它，就算图不动了（零是「图还没到」，不是「不动」）；再挂一个 ResizeObserver
 *  兜住尺寸本身的变化。 */
let settleFrame = 0
let settleObserver: ResizeObserver | null = null
function followUntilSettled() {
  let last = ''
  const tick = () => {
    measureArea()
    const now = JSON.stringify(area.value)
    const el = figure.value
    // 字节取不到（元素没了，或者加载完了还是没有宽高）时没什么可等的，别空转。
    const gone = !el || (el.complete && !el.naturalWidth)
    const settled = now === last && area.value.width > 0 && area.value.height > 0
    settleFrame = gone || settled ? 0 : requestAnimationFrame(tick)
    last = now
  }
  settleFrame = requestAnimationFrame(tick)
  if (typeof ResizeObserver !== 'undefined') {
    settleObserver = new ResizeObserver(measureArea)
    if (figure.value) settleObserver.observe(figure.value)
    if (stage.value) settleObserver.observe(stage.value)
  }
}
function stopFollowing() {
  if (settleFrame) cancelAnimationFrame(settleFrame)
  settleFrame = 0
  settleObserver?.disconnect()
  settleObserver = null
}

// 图节点换了一个（换图、列表变了、字节这才到）：旧的那个连同挂在它身上的观察者一起
// 摘走了。别只量一下就完——新节点要等字节到了、`@load` 之后才定下大小，那之前量到的
// 是零。重新跟着量到它不动，和刚进裁剪时走同一条路。
watch(figure, () => {
  if (!cropping.value) return
  stopFollowing()
  followUntilSettled()
})

function rotate() {
  const next = { ...adjust.value, rotate: (adjust.value.rotate + 90) % 360 }
  change({ rotate: next.rotate, crop: adjust.value.crop && carryCrop(adjust.value.crop, adjust.value, next) })
  void nextTick(view.fit)
}
function flip() {
  const next = { ...adjust.value, flip: !adjust.value.flip }
  change({ flip: next.flip, crop: adjust.value.crop && carryCrop(adjust.value.crop, adjust.value, next) })
}

async function startCrop() {
  if (cropping.value) return finishCrop(false)
  view.reset()
  draftCrop.value = adjust.value.crop ?? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }
  cropping.value = true
  await nextTick()
  followUntilSettled()
}
function finishCrop(apply: boolean) {
  if (apply) change({ crop: draftCrop.value })
  cropping.value = false
  stopFollowing()
}

function restore() {
  adjustments.delete(gallery.currentPath.value)
  cropping.value = false
  stopFollowing()
  void nextTick(view.fit)
}

async function edited(): Promise<File | null> {
  const el = figure.value
  if (!el?.naturalWidth) return null
  saving.value = true
  try {
    const blob = await renderAdjusted(el, adjust.value)
    return new File([blob], editedName(gallery.name.value, t('work.image.editedSuffix')), { type: 'image/png' })
  } catch {
    notice.value = t('work.image.saveFailed')
    return null
  } finally {
    saving.value = false
  }
}

function save(url: string, name: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
}

/** 下载：改过就下改过的那张，没改就是原图。 */
async function download() {
  if (!isEdited(adjust.value)) {
    if (gallery.current.value) save(gallery.current.value, gallery.name.value)
    return
  }
  const file = await edited()
  if (!file) return
  const url = URL.createObjectURL(file)
  save(url, file.name)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function attach() {
  if (!actions?.canAttach()) return
  const file = await edited()
  if (!file) return
  actions.attach(file)
  emit('close')
}

function handOff(tool: 'select' | 'text' | 'redact') {
  if (!actions || !gallery.currentPath.value) return
  actions.openInPanel(gallery.currentPath.value, tool)
  emit('close')
}

/** 点空白关掉。拖动过的那一下不算点：那是想看细节，不是想走。 */
function onStageClick(event: MouseEvent) {
  if (event.target !== stage.value || view.dragged() || cropping.value) return
  emit('close')
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (cropping.value) finishCrop(false)
    else emit('close')
    return
  }
  if (cropping.value) {
    // 「拖动框选要留下的部分，回车确定」这句说给的是这一层：回车只管裁剪。焦点还在
    // 工具条上时，回车会先把那颗按钮按下去（关掉浮层、或者又进一次裁剪），所以要
    // 拦住它，并且只在这一层收这个键。
    if (event.key === 'Enter') {
      event.preventDefault()
      finishCrop(true)
    }
    return
  }
  // 焦点在滑杆、输入框里时方向键是它们的（亮度的滑杆上，←/→ 是减一档）：别顺手换图，
  // 那会让刚调的那一档落到正在看的另一张上。按钮不算：打开时焦点就在关闭按钮上，
  // 把按钮也排除掉的话，一进来按 ←/→ 就换不了图了（按钮自己也不用方向键）。
  const target = event.target as HTMLElement | null
  if (target && /^(input|select|textarea)$/i.test(target.tagName)) return
  if (event.key === 'ArrowRight') gallery.go(1)
  else if (event.key === 'ArrowLeft') gallery.go(-1)
}

// 四周让出来的地方变了（多了一张图，左右按钮和缩略图条出来、底下那条边距翻倍）：
// 重新按新的那块地方摆一次。这件事以前靠「位置一变就换个 <img> 节点、新节点 load
// 一次」顺带做了；现在节点按地址换，只有位置变了的时候不再换，就得自己跟上。
watch(margins, () => void nextTick(view.fit))

// 换了一张：缩放回一倍，裁剪框收起。按地址而不是按第几张：正在看的这张被删掉时，
// 后面那张会顶到同一个位置上，位置没变、看的却已经是别人了。框是不是在裁剪中，这里
// 也得停掉量的那一套——缩略图条在裁剪中照样能点，点走之后是这条路把 `cropping` 关掉的。
watch(gallery.currentPath, () => {
  cropping.value = false
  stopFollowing()
  notice.value = ''
  view.reset()
})

// 返回手势 / 返回键关掉的是这一层，不是整页：打开时在历史里压一格（地址不变，
// 只在 state 上做个记号），返回把它弹掉，这里听到就关。用别的办法关掉（点空白、
// 右上角、Esc）时，那一格还在，自己退掉它，不在身后留一格空的——否则下一次返回
// 什么也不做。state 照抄当前那一份再加记号：路由靠 state 里的字段认位置。
const HISTORY_MARK = 'imageLightbox'
let historyPushed = false

function onPopState() {
  if ((history.state as Record<string, unknown> | null)?.[HISTORY_MARK]) return
  historyPushed = false
  emit('close')
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  history.pushState({ ...(history.state as object | null), [HISTORY_MARK]: true }, '')
  historyPushed = true
  window.addEventListener('popstate', onPopState)
  toolbar.value?.closeButton?.focus()
})
onBeforeUnmount(() => {
  stopFollowing()
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('popstate', onPopState)
  // 只退自己压的那一格：已经被返回弹掉了，或者这期间页面又往前走了一步，都不碰。
  if (historyPushed && (history.state as Record<string, unknown> | null)?.[HISTORY_MARK]) history.back()
})
</script>

<template>
  <Teleport to="body">
    <!-- The stage IS the dialog, so it cannot be a button: non-interactive
         by role and holding the click. Keyboard stays on the window
         listener (Escape to close, arrows to step) and the close button takes focus. -->
    <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- the dialog itself; Escape is handled on window -->
    <div
      ref="stage"
      class="lightbox"
      :style="{ paddingTop: `${margins.top}px`, paddingBottom: `${margins.bottom}px` }"
      role="dialog"
      aria-modal="true"
      :aria-label="gallery.name.value"
      @click="onStageClick"
      @wheel="cropping ? undefined : view.onWheel($event)"
      @pointerdown="cropping ? undefined : view.onPointerDown($event)"
      @pointermove="view.onPointerMove"
      @pointerup="view.onPointerUp"
      @pointercancel="view.onPointerUp"
    >
      <LightboxToolbar
        ref="toolbar"
        :name="gallery.name.value"
        :index="gallery.index.value"
        :total="gallery.total.value"
        :scale="scale"
        :adjust="adjust"
        :cropping="cropping"
        :can-hand-off="!!actions && !!gallery.currentPath.value"
        @zoom="view.step"
        @fit="view.reset"
        @rotate="rotate"
        @flip="flip"
        @crop="startCrop"
        @tone="(key, value) => change({ [key]: value })"
        @hand-off="handOff"
        @download="download"
        @close="emit('close')"
      />
      <!-- 换节点也按地址：只是位置变了、图没变时不换节点——裁剪框量的是图上那一块，
           换了节点量过的那块就飘了。 -->
      <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the double-click shortcut; the toolbar and Escape are the keyboard path -->
      <img
        v-if="gallery.current.value"
        :key="gallery.currentPath.value"
        ref="figure"
        class="lightbox__figure"
        :class="{ 'lightbox__figure--moving': moving, 'lightbox__figure--zoomed': scale > 1 }"
        :src="gallery.current.value"
        :alt="gallery.name.value"
        :title="t('work.image.zoomHint')"
        :style="style"
        draggable="false"
        @load="view.fit"
        @dblclick="cropping ? undefined : view.onDoubleClick($event)"
      />
      <span v-else class="lightbox__waiting">
        {{ gallery.currentFailed.value ? t('work.image.failed') : t('work.image.loading') }}
      </span>
      <LightboxCrop v-if="cropping" v-model="draftCrop" :area="area" />

      <template v-if="gallery.total.value > 1 && !cropping">
        <button
          type="button"
          class="lightbox__nav lightbox__nav--prev"
          :title="t('work.image.prev')"
          :aria-label="t('work.image.prev')"
          @click.stop="gallery.go(-1)"
          @pointerdown.stop
        >
          <v-icon size="26">mdi-chevron-left</v-icon>
        </button>
        <button
          type="button"
          class="lightbox__nav lightbox__nav--next"
          :title="t('work.image.next')"
          :aria-label="t('work.image.next')"
          @click.stop="gallery.go(1)"
          @pointerdown.stop
        >
          <v-icon size="26">mdi-chevron-right</v-icon>
        </button>
      </template>

      <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- only stops the stage from reading a click here as "click outside" -->
      <div class="lightbox__foot" @click.stop @pointerdown.stop>
        <div v-if="cropping" class="lightbox__actions" data-testid="lightbox-crop-actions">
          <span class="lightbox__hint">{{ t('work.image.cropHint') }}</span>
          <button type="button" class="lightbox__act" @click="finishCrop(false)">{{ t('work.image.cancel') }}</button>
          <button type="button" class="lightbox__act lightbox__act--primary" @click="finishCrop(true)">
            {{ t('work.image.cropApply') }}
          </button>
        </div>
        <div v-else-if="isEdited(adjust)" class="lightbox__actions" data-testid="lightbox-save">
          <span class="lightbox__hint">{{ notice || t('work.image.editedHint') }}</span>
          <button type="button" class="lightbox__act" @click="restore">{{ t('work.image.restore') }}</button>
          <button type="button" class="lightbox__act" :disabled="saving" @click="download">
            {{ t('work.image.downloadEdited') }}
          </button>
          <button
            v-if="canAttach"
            type="button"
            class="lightbox__act lightbox__act--primary"
            :disabled="saving"
            @click="attach"
          >
            {{ t('work.image.attachEdited') }}
          </button>
        </div>
        <div v-if="gallery.total.value > 1" class="lightbox__film" role="group" :aria-label="t('work.image.film')">
          <button
            v-for="(item, i) in gallery.paths.value"
            :key="item"
            type="button"
            class="lightbox__thumb"
            :class="{ 'lightbox__thumb--on': i === gallery.index.value }"
            :aria-current="i === gallery.index.value"
            :aria-label="t('work.image.goTo', { n: i + 1 })"
            @click="gallery.goTo(i)"
          >
            <img v-if="gallery.urlAt(i)" :src="gallery.urlAt(i)" alt="" />
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 铺满窗口的一层，图和窗口一样大：缩放和拖动都发生在这块面上，所以这里不吃
   手势 —— 触屏上滚页面、双指缩放浏览器，都不是在这个时刻想做的事。
   底色两种主题都是深的（--lightbox-ground）：看图要暗的四周。 */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  color: var(--lightbox-ink);
  background: var(--lightbox-ground);
  touch-action: none;
}
.lightbox__figure {
  border-radius: var(--radius-sm);
  max-width: 100%;
  max-height: 100%;
  user-select: none;
  -webkit-user-drag: none;
  transition: transform var(--dur-quick) var(--ease-out);
}
/* 手指还按着的时候不能有过渡：那会让跟随慢半拍。 */
.lightbox__figure--moving {
  transition: none;
  cursor: grabbing;
}
.lightbox__figure--zoomed {
  cursor: grab;
}
.lightbox__waiting {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--lightbox-muted);
}
.lightbox__nav {
  position: absolute;
  top: 50%;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  color: var(--lightbox-ink);
  background: var(--lightbox-fill);
  border: 0;
  border-radius: var(--radius-pill);
  transform: translateY(-50%);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.lightbox__nav:hover {
  background: var(--lightbox-fill-2);
}
.lightbox__nav:focus-visible,
.lightbox__thumb:focus-visible,
.lightbox__act:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.lightbox__nav--prev {
  left: calc(16px + env(safe-area-inset-left, 0px));
}
.lightbox__nav--next {
  right: calc(16px + env(safe-area-inset-right, 0px));
}
.lightbox__foot {
  position: absolute;
  right: 0;
  bottom: env(safe-area-inset-bottom, 0px);
  left: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 0 12px 10px;
  pointer-events: none;
}
.lightbox__foot > * {
  pointer-events: auto;
}
.lightbox__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 10px 8px 14px;
  background: var(--lightbox-ground);
  border: 1px solid var(--lightbox-fill-2);
  border-radius: var(--radius-lg);
}
.lightbox__hint {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--lightbox-muted);
}
.lightbox__act {
  height: 32px;
  padding: 0 12px;
  color: var(--lightbox-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  background: var(--lightbox-fill);
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
}
.lightbox__act:hover:not(:disabled) {
  background: var(--lightbox-fill-2);
}
.lightbox__act:disabled {
  opacity: 0.5;
  cursor: default;
}
.lightbox__act--primary {
  color: var(--accent-on);
  background: var(--accent);
}
.lightbox__act--primary:hover:not(:disabled) {
  background: var(--accent-press);
}
.lightbox__film {
  display: flex;
  gap: 8px;
  max-width: 100%;
  padding: 2px;
  overflow-x: auto;
}
.lightbox__thumb {
  flex: none;
  width: 42px;
  height: 56px;
  padding: 0;
  overflow: hidden;
  background: var(--lightbox-fill);
  border: 2px solid transparent;
  border-radius: var(--radius-sm);
  opacity: 0.6;
  cursor: pointer;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
.lightbox__thumb:hover,
.lightbox__thumb--on {
  opacity: 1;
}
.lightbox__thumb--on {
  border-color: var(--accent);
}
.lightbox__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
