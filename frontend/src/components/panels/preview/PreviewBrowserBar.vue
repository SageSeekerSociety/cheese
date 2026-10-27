<script setup lang="ts">
// 网页预览那一格的「小浏览器」一行：后退、前进、刷新、地址栏、连通链。
//
// 地址栏显示的是帧里那一页此刻的地址（应用里点链接、SPA 改路由都跟着变），由帧里的运行
// 时桥报上来；只显示路径——预览的域名是平台给的一串随机名字，读者看了没用。没装桥的页面
// 报不上地址，地址栏退回显示入口的名字，也不能敲。
//
// 连通链和原来那颗「出问题才亮」的小圆点合成了这一处：宽时三段平铺（平台 › 通道 ›
// 应用），窄时收成一颗点；哪一段不通那段变灰，点开看每一段的原因。判据在
// `lib/previewChain`。
import type { ChainSegment } from '@/lib/previewChain'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { chainBroken, isPageSegment, PAGE_ERROR } from '@/lib/previewChain'
import { ROOM_FILE_PATH } from '@/lib/previewSession'

const props = defineProps<{
  /** 帧报来的此刻地址（完整 URL）；没装桥就是空。 */
  href: string
  /** 报不上地址时地址栏里放的那句：入口文件名或应用名。 */
  fallback: string
  canBack: boolean
  canForward: boolean
  /** 地址栏能不能敲：帧里有桥才跳得过去。 */
  navigable: boolean
  segments: ChainSegment[]
  /** 链点开后最下面那几行技术标识（资源、实例、读到的版本）：平时不读，要查时还在。 */
  details: string[]
  compact: boolean
}>()

const emit = defineEmits<{
  back: []
  forward: []
  reload: []
  navigate: [path: string]
}>()

/** 把地址里那一串转义读成人话——但只在读回来还能转回去的时候。
 *
 *  `%2F`、`%3F`、`%23` 解出来会变成真的分隔符，读者原样再按一次回车就跳到别处去了；所以
 *  解一步、用 `encodeURI` 转回去对比，一样才显示解好的那一版（房间路径常是中文，`%E6…`
 *  解出来是文件名的本来样子），不一样就照原样显示转义写法——地址栏里看到什么，按下去就
 *  跳什么。 */
function readable(spec: string): string {
  try {
    const decoded = decodeURIComponent(spec)
    return encodeURI(decoded) === spec ? decoded : spec
  } catch {
    return spec
  }
}

/** 地址栏里显示的那一截：路径、查询、片段。房间文件挂在 `/_cheese/room/` 下，把这层平台
 *  自己的前缀去掉，读者看到的是文件在房间里的路径。
 *
 *  只剥显示用的这一层：桥那边 `new URL(path, location.href)` 是相对内容域解析的，把剥过的
 *  路径原样递回去会丢掉 `/_cheese/room/` 这一层（房间文件里敲 `/pages/b.html` 会跳到
 *  内容域的根下）。所以提交时按它原来是哪一档补回前缀，返回两个值。 */
function displayPath(href: string): { shown: string; room: boolean } {
  try {
    const url = new URL(href)
    const room = url.pathname.startsWith(ROOM_FILE_PATH)
    const path = room ? '/' + url.pathname.slice(ROOM_FILE_PATH.length) : url.pathname
    return { shown: readable(path + url.search + url.hash), room }
  } catch {
    return { shown: href, room: false }
  }
}

const display = computed(() => (props.href ? displayPath(props.href) : { shown: props.fallback, room: false }))
const shown = computed(() => display.value.shown)
const draft = ref(shown.value)
const editing = ref(false)
watch(shown, (value) => {
  if (!editing.value) draft.value = value
})

function focus(event: FocusEvent) {
  editing.value = true
  if (props.navigable) (event.target as HTMLInputElement).select()
}
function blur() {
  editing.value = false
  draft.value = shown.value
}
function submit(event: KeyboardEvent) {
  // 输入法正在拼字时那一下 Enter 是选字的，不是跳转——房间路径常是中文，半截路径跳不得。
  if (event.isComposing || event.keyCode === 229) return
  const value = draft.value.trim()
  ;(event.target as HTMLInputElement).blur()
  if (!value || value === shown.value) return
  // 房间文件那一档补回被剥掉的前缀：否则 `new URL('/pages/b.html', …)` 会离开房间。
  // 带着前缀的原样递回去（地址栏的提示里就是完整地址，照抄一份下来不该跳到 `/_cheese/
  // room/_cheese/room/…` 那个 404 上）；不是以 `/` 开头的（相对、或完整 URL）本来就不加。
  if (!display.value.room || !value.startsWith('/') || value.startsWith(ROOM_FILE_PATH)) {
    emit('navigate', value)
    return
  }
  emit('navigate', ROOM_FILE_PATH + value.slice(1))
}
function revert(event: KeyboardEvent) {
  draft.value = shown.value
  ;(event.target as HTMLInputElement).blur()
}

const broken = computed(() => chainBroken(props.segments))
const chainTitle = computed(() =>
  props.segments
    .map((segment) => `${t(`work.room.preview.chain.${segment.key}`)} ${segment.state === 'ok' ? '✓' : '·'}`)
    .join(' › ')
)
/** 一段的颜色档：通的绿，页面报错的红，其余（断开、等前面那段）琥珀——和原型一样，
 *  睡着的那两段是「在等」而不是「没有」，用跟小圆点同一档的警示色，不用灰。 */
function tone(segment: ChainSegment): 'ok' | 'error' | 'wait' {
  if (segment.state === 'ok') return 'ok'
  return segment.reason === PAGE_ERROR ? 'error' : 'wait'
}
const dotTone = computed(() => {
  const tones = props.segments.map(tone)
  return tones.includes('error') ? 'error' : tones.includes('wait') ? 'wait' : 'ok'
})
function segmentLabel(segment: ChainSegment): string {
  return t(
    segment.key === 'app' && isPageSegment(props.segments)
      ? 'work.room.preview.chain.page'
      : `work.room.preview.chain.${segment.key}`
  )
}
</script>

<template>
  <div class="browser-bar" :class="{ 'browser-bar--compact': compact }">
    <BaseButton
      kind="ghost"
      icon="mdi-arrow-left"
      size="sm"
      density="comfortable"
      data-testid="browser-back"
      :disabled="!canBack"
      :title="t('work.room.preview.browser.back')"
      :aria-label="t('work.room.preview.browser.back')"
      @click="emit('back')"
    />
    <BaseButton
      v-if="!compact || canForward"
      kind="ghost"
      icon="mdi-arrow-right"
      size="sm"
      density="comfortable"
      data-testid="browser-forward"
      :disabled="!canForward"
      :title="t('work.room.preview.browser.forward')"
      :aria-label="t('work.room.preview.browser.forward')"
      @click="emit('forward')"
    />
    <BaseButton
      kind="ghost"
      icon="mdi-refresh"
      size="sm"
      density="comfortable"
      data-testid="refresh-preview"
      :title="t('work.room.preview.refresh')"
      :aria-label="t('work.room.preview.refresh')"
      @click="emit('reload')"
    />
    <input
      v-model="draft"
      class="browser-bar__url"
      data-testid="browser-address"
      spellcheck="false"
      autocomplete="off"
      :readonly="!navigable"
      :title="[href || fallback, ...details].join(' · ')"
      :aria-label="t('work.room.preview.browser.address')"
      @focus="focus"
      @blur="blur"
      @keydown.enter.prevent="submit"
      @keydown.esc.stop.prevent="revert"
    />
    <v-menu location="bottom end" :close-on-content-click="false">
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          type="button"
          class="browser-chain"
          :class="{ 'browser-chain--dot': compact }"
          :data-testid="broken ? 'preview-runtime-alert' : 'preview-chain'"
          :title="broken ? t('work.room.preview.runtimeAlert') : chainTitle"
          :aria-label="broken ? t('work.room.preview.runtimeAlert') : chainTitle"
        >
          <span
            v-if="compact"
            class="browser-chain__dot"
            :class="`browser-chain__dot--${dotTone}`"
            aria-hidden="true"
          />
          <template v-for="(segment, index) in compact ? [] : segments" :key="segment.key">
            <v-icon v-if="index > 0" size="12" class="browser-chain__sep" aria-hidden="true">mdi-chevron-right</v-icon>
            <span
              class="browser-chain__seg"
              :class="`browser-chain__seg--${tone(segment)}`"
              :data-state="segment.state"
              :title="t(segment.reason, segment.params ?? {})"
            >
              {{ segmentLabel(segment) }}<template v-if="segment.state === 'ok'"> ✓</template>
            </span>
          </template>
        </button>
      </template>
      <div class="browser-chain__detail" role="status" data-testid="preview-chain-detail">
        <div v-for="segment in segments" :key="segment.key" class="browser-chain__row" :data-state="segment.state">
          <v-icon size="16" :class="`browser-chain__icon--${tone(segment)}`" aria-hidden="true">{{
            tone(segment) === 'ok'
              ? 'mdi-check-circle'
              : tone(segment) === 'error'
                ? 'mdi-alert-circle'
                : segment.state === 'down'
                  ? 'mdi-minus-circle-outline'
                  : 'mdi-circle-outline'
          }}</v-icon>
          <div>
            <div class="browser-chain__name">{{ segmentLabel(segment) }}</div>
            <div class="browser-chain__reason" :role="tone(segment) === 'error' ? 'alert' : undefined">
              {{ t(segment.reason, segment.params ?? {}) }}
            </div>
          </div>
        </div>
        <div v-if="details.length" class="browser-chain__ids">{{ details.join(' · ') }}</div>
      </div>
    </v-menu>
  </div>
</template>

<style scoped>
.browser-bar {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 2px;
  min-width: 0;
}
.browser-bar__url {
  flex: 1 1 auto;
  min-width: 80px;
  height: 28px;
  margin-inline: 4px;
  padding: 0 12px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
  background: var(--canvas);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  outline: none;
  text-overflow: ellipsis;
}
.browser-bar__url:focus {
  color: var(--text);
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--focus-ring);
}
.browser-bar__url[readonly] {
  cursor: default;
}
.browser-bar__url[readonly]:focus {
  border-color: var(--line-2);
  box-shadow: none;
}
button.browser-chain {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 1px;
  padding: 2px;
  font-size: 11px;
  color: var(--faint);
  border-radius: var(--radius-sm);
}
button.browser-chain:hover {
  background: var(--fill);
}
button.browser-chain--dot {
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
}
.browser-chain__seg {
  padding: 1px 6px;
  white-space: nowrap;
  border-radius: 8px;
}
.browser-chain__seg--ok {
  color: var(--ok-ink);
  background: var(--ok-wash);
}
.browser-chain__seg--wait {
  color: var(--warn-ink);
  background: var(--warn-wash);
}
.browser-chain__seg--error {
  color: var(--danger-ink);
  background: var(--danger-wash);
}
.browser-chain__sep {
  color: var(--faint);
}
.browser-chain__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.browser-chain__dot--ok {
  background: var(--ok);
}
.browser-chain__dot--wait {
  background: var(--warn);
}
.browser-chain__dot--error {
  background: var(--danger);
}
.browser-chain__detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(320px, 90vw);
  padding: 12px;
  font-size: 13px;
  color: var(--text);
  background: var(--raised);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.browser-chain__row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.browser-chain__name {
  font-weight: 600;
}
.browser-chain__reason {
  color: var(--muted);
  overflow-wrap: anywhere;
}
.browser-chain__icon--ok {
  color: var(--ok);
}
.browser-chain__icon--wait {
  color: var(--warn);
}
.browser-chain__icon--error {
  color: var(--danger);
}
.browser-chain__ids {
  padding-top: 8px;
  font-size: 12px;
  color: var(--faint);
  border-top: 1px solid var(--line);
  overflow-wrap: anywhere;
}
</style>
