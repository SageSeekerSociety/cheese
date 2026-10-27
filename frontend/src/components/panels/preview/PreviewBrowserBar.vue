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
import { chainBroken } from '@/lib/previewChain'

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

/** 地址栏里显示的那一截：路径、查询、片段。房间文件挂在 `/_cheese/room/` 下，把这层平台
 *  自己的前缀去掉，读者看到的是文件在房间里的路径。 */
function displayPath(href: string): string {
  try {
    const url = new URL(href)
    const path = url.pathname.startsWith('/_cheese/room/')
      ? '/' + url.pathname.slice('/_cheese/room/'.length)
      : url.pathname
    return decodeURIComponent(path) + url.search + url.hash
  } catch {
    return href
  }
}

const shown = computed(() => (props.href ? displayPath(props.href) : props.fallback))
const draft = ref(shown.value)
const editing = ref(false)
watch(shown, (value) => {
  if (!editing.value) draft.value = value
})

function submit(event: KeyboardEvent) {
  const value = draft.value.trim()
  ;(event.target as HTMLInputElement).blur()
  if (value && value !== shown.value) emit('navigate', value)
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
/** 一段的颜色档：通的绿，页面报错的红，其余（断开、等前面那段）灰。 */
function tone(segment: ChainSegment): 'ok' | 'error' | 'off' {
  if (segment.state === 'ok') return 'ok'
  return segment.reason === 'work.room.preview.chain.pageError' ? 'error' : 'off'
}
const dotTone = computed(() => {
  const tones = props.segments.map(tone)
  return tones.includes('error') ? 'error' : tones.includes('off') ? 'off' : 'ok'
})
function segmentLabel(segment: ChainSegment): string {
  return t(
    segment.key === 'app' && props.segments.length === 2
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
      @focus="
        editing = true
        navigable && ($event.target as HTMLInputElement).select()
      "
      @blur="
        editing = false
        draft = shown
      "
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
.browser-chain__seg--off {
  color: var(--muted);
  background: var(--fill-2);
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
.browser-chain__dot--off {
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
.browser-chain__icon--off {
  color: var(--faint);
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
