<script setup lang="ts">
import type { Block } from '../../cx_types'

import { computed, onUnmounted, ref } from 'vue'

import { noticeText } from '../../lib/noticeText'
import { displayRecord, recordParts } from '../../lib/sessionRecords'
import { toolLabel } from '../../lib/toolLabels'
import MarkdownView from '../common/MarkdownView.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    block: Block
    raw: boolean
    loadImage: (digest: string) => Promise<string>
    /** 名册 handle → 名字：平台写的一句话里点到人（`<@handle>`）时照它换成名字。 */
    memberNames?: Record<string, string>
    /** 这一行是此刻才到的（不是打开时画出来的、也不是往上翻出来的）：淡入一下。 */
    arriving?: boolean
  }>(),
  { memberNames: () => ({}), arriving: false }
)
const emit = defineEmits<{ (e: 'settle'): void }>()

/** 淡入演完就把这一行从「才到的」里拿掉：class 一直挂着的话，它的动画会压住之后
 *  要演的那一下。Vue 给 scoped 的 keyframes 名字加了后缀，所以比前缀。
 *  `MarkdownView` 里面自己的动画也会冒上来，按名字挡掉。 */
function settleArrival(e: AnimationEvent) {
  if (e.animationName.startsWith('site-arrive')) emit('settle')
}
const parts = computed(() => recordParts(props.block).filter((p) => props.raw || p.kind !== 'thinking'))
const json = computed(() =>
  props.block.meta?.before_launch
    ? undefined
    : JSON.stringify(displayRecord(props.block.meta?.record ?? props.block), null, 2)
)
const kind = computed(() => {
  const record = props.block.meta?.record as Record<string, unknown> | undefined
  return String(record?.method ?? record?.type ?? props.block.meta?.event_type ?? '')
})
// 认不出的 handle 留 handle，不留 `<@…>` 记号。
const platformText = computed(() =>
  noticeText(props.block).replace(/<@([^>\s]+)>/g, (_, handle: string) => props.memberNames[handle] ?? handle)
)
const images = ref<Record<string, string>>({})
const imageError = ref(false)
const long = (text = '') => text.length > 900 || text.split('\n').length > 12
const head = (text = '') => text.split('\n').slice(0, 3).join('\n').slice(0, 300)
async function openImage(digest: string) {
  if (images.value[digest]) return
  try {
    images.value[digest] = await props.loadImage(digest)
  } catch {
    imageError.value = true
  }
}
onUnmounted(() => Object.values(images.value).forEach((url) => URL.revokeObjectURL(url)))
</script>

<template>
  <article
    v-if="raw || parts.length || !block.meta?.record"
    class="record-row"
    :class="{ 'record-row--arriving': arriving }"
    :data-record-id="block.id"
    @animationend="settleArrival"
  >
    <header v-if="raw && !block.meta?.before_launch" class="record-row__meta">
      <span>{{ kind }}</span
      ><span>{{ block.meta?.harness }}</span>
      <time :datetime="block.created_at">{{ new Date(block.created_at).toLocaleTimeString() }}</time>
    </header>
    <p v-if="!block.meta?.record" class="record-row__status">
      {{ block.meta?.before_launch ? t('work.room.site.beforeLaunch') : platformText }}
    </p>
    <details v-if="!block.meta?.record && block.meta?.error">
      <summary>{{ t('work.room.site.fullOutput') }}</summary>
      <pre>{{ block.meta.error }}</pre>
    </details>
    <template v-for="(part, index) in parts" :key="index">
      <details v-if="part.kind === 'thinking'" class="record-row__thinking">
        <summary>{{ t(part.text ? 'work.room.site.thinking' : 'work.room.site.noThinking') }}</summary>
        <MarkdownView v-if="part.text" :source="part.text" as="chat" />
      </details>
      <details v-else-if="part.kind === 'tool'" class="record-row__tool" :class="{ 'record-row--failed': part.failed }">
        <summary>
          <strong>{{ part.action ? toolLabel(part.action) : toolLabel(part.name || '', part.input) }}</strong>
          <span>{{ part.preview }}</span>
          <span v-if="part.failed">{{ t('work.room.site.failed') }}</span>
        </summary>
        <pre>{{
          typeof part.input?.command === 'string' ? part.input.command : JSON.stringify(part.input, null, 2)
        }}</pre>
        <pre v-if="part.input?.command && Object.keys(part.input).length > 1">{{
          JSON.stringify(part.input, null, 2)
        }}</pre>
        <details v-if="part.text">
          <summary>{{ t('work.room.site.fullOutput') }}</summary>
          <pre>{{ part.text }}</pre>
        </details>
      </details>
      <template v-else-if="part.kind === 'output'">
        <details :class="{ 'record-row--failed': part.failed }">
          <summary>
            {{ part.failed ? t('work.room.site.failed') : t('work.room.site.fullOutput') }}
            <span class="record-row__call">{{ part.call_id }}</span>
            <span v-if="long(part.text)">{{
              t('work.room.site.expandAll', { count: part.text?.split('\n').length })
            }}</span>
          </summary>
          <pre>{{ part.text }}</pre>
        </details>
        <pre v-if="long(part.text)" class="record-row__preview" :class="{ 'record-row--failed': part.failed }">{{
          head(part.text)
        }}</pre>
      </template>
      <template v-else-if="part.kind === 'image'">
        <button
          v-if="part.source?.sha256 && !images[part.source.sha256]"
          type="button"
          @click="openImage(part.source.sha256)"
        >
          {{ t('work.room.site.showImage') }}
        </button>
        <a v-else-if="part.source?.sha256" :href="images[part.source.sha256]" target="_blank" rel="noopener"
          ><img :src="images[part.source.sha256]" :alt="t('work.room.site.image')"
        /></a>
        <p v-else>
          {{
            part.source?.bytes
              ? t('work.room.site.imageOmitted', { bytes: part.source.bytes })
              : t('work.room.site.imageUnavailable')
          }}
        </p>
        <p v-if="imageError">{{ t('work.room.site.loadFailed') }}</p>
      </template>
      <template v-else-if="part.kind === 'text' && long(part.text)">
        <details>
          <summary>
            {{ t('work.room.site.expandAll', { count: part.text?.split('\n').length }) }}
          </summary>
          <div class="record-row__prose"><MarkdownView :source="part.text || ''" as="chat" /></div>
        </details>
        <pre class="record-row__preview">{{ head(part.text) }}</pre>
      </template>
      <MarkdownView v-else-if="part.kind === 'text'" :source="part.text || ''" as="chat" />
      <p v-else-if="part.kind === 'status'" class="record-row__status">
        {{ t(`work.room.site.status.${part.label}`) }} {{ part.text }}
      </p>
    </template>
    <details v-if="raw && json" class="record-row__json">
      <summary>{{ t('work.room.site.recordJson') }}</summary>
      <pre>{{ json }}</pre>
    </details>
  </article>
</template>

<style scoped>
.record-row {
  min-width: 0;
  padding: 8px 0;
  font-size: 13px;
  color: var(--text);
  /* 这一栏**不**挂「离屏跳过」（content-visibility）。它的重内容（命令输出、思考
     正文）都收在 <details> 里，浏览器本来就不排版收起的那部分——线上量过，一页
     254KB 正文只有 4.4KB 真被排版。加到行上没有可省的，反而让展开详情后滚动更慢
     （布局 70ms → 280ms）。 */
}
/* 此刻才到的一行：淡入并从下面 4px 升到位（见 `arriving`），和对话栏的新消息同一个
   动作。只动透明度和位置——高度不变，底部那个「钉住」就看不见它，读者不会被推走。 */
.record-row--arriving {
  animation: site-arrive var(--dur-base) var(--ease-out);
}
@keyframes site-arrive {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
}
/* 减弱动效不自己写 `animation: none`：全局兜底会把时长压到近 0，而 `animationend`
   照旧会来、这一行的类照旧会被摘掉；自己关掉反而让它一直挂着。 */
.record-row__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--muted);
}
.record-row__meta time {
  margin-left: auto;
}
.record-row__tool summary {
  display: flex;
  gap: 8px;
}
.record-row__tool summary span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
summary,
button {
  cursor: pointer;
  color: var(--muted);
}
summary:focus-visible,
button:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
pre,
.record-row__prose {
  max-height: 360px;
  overflow: auto;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
pre {
  padding: 12px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}
img {
  display: block;
  max-width: 100%;
  max-height: 180px;
  margin-block: 8px;
}
.record-row--failed,
.record-row--failed > summary {
  color: var(--danger-ink);
}
.record-row__json,
.record-row__thinking {
  margin-block: 8px;
}
.record-row__call {
  overflow-wrap: anywhere;
}
details[open] + .record-row__preview {
  display: none;
}
</style>
