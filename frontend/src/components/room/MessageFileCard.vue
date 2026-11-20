<script setup lang="ts">
// 对话里一份能翻页的文档（PDF、Word、幻灯片）或一张表格：画成一张富卡片。
//
// 只有一个文件名时，读者得点开才知道是不是要的那一份，也不知道它后来改没改过。卡片
// 一眼答这几件：长什么样（文档的封面和开头两行、幻灯片的前几页、表格左上角一块），
// 多大、第几版，这条消息之后又改过没有（表格还说改了几格、是哪几格）。底下一排
// 「在右侧打开 / 会话内展开 / 下载」。
//
// 这些都要下字节、转格式，所以等卡片滚进视野才做：一段长对话里几十份文件，没人往上翻
// 的那些不该一份份去转（Word、幻灯片要平台转一次 PDF，约 2.5 秒，按内容哈希缓存）。
// 浏览器没有 IntersectionObserver 时直接做：宁可多转一份，也不能让卡片一直空着。
import { computed, inject, nextTick, ref, watch } from 'vue'
import { useIntersectionObserver } from '@vueuse/core'

import { useSlidesPdf } from '@/composables/useSlidesPdf'

import FileCardSheet from './fileCard/FileCardSheet.vue'
import { useFileFacts } from './fileCard/useFileFacts'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { FILE_FACTS_SOURCE, NO_FILE_FACTS } from '@/lib/fileFactsSource'
import { fileIcon, NEEDS_CONVERSION, suffixOf } from '@/lib/fileKind'
import { fmtFileSize } from '@/lib/usageFormat'

const props = defineProps<{
  topicId: string | null
  path: string
  name: string
  /** 「Word 文档」「表格」这样的类型说法，和原来那颗小卡片同一个来源。 */
  kind: string
  /** 这条消息什么时候发的：拿它判断之后又改过没有。 */
  postedAt?: string
  /** 能不能下载（有一份真文件可下）。 */
  downloadable?: boolean
}>()
const emit = defineEmits<{ open: []; download: [] }>()

/** 幻灯片卡片上排几页。 */
const STRIP_PAGES = 6
/** 会话内展开画几页；再多就该去右侧看了。 */
const INLINE_PAGES = 2

const SLIDES = new Set(['pptx', 'ppt', 'odp'])
const SHEETS = new Set(['xlsx', 'xlsm'])
const family = computed(() => {
  const suffix = suffixOf(props.path)
  return SHEETS.has(suffix) ? 'sheet' : SLIDES.has(suffix) ? 'slides' : 'doc'
})

const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)
const facts = inject(FILE_FACTS_SOURCE, NO_FILE_FACTS)

const card = ref<HTMLElement | null>(null)
const seen = ref(false)
const { stop, isSupported } = useIntersectionObserver(card, ([entry]) => {
  if (!entry?.isIntersecting) return
  seen.value = true
  stop()
})
if (!isSupported.value) seen.value = true

const { latest, atPost, newer } = useFileFacts(
  facts,
  () => ({ topicId: props.topicId, path: props.path, postedAt: props.postedAt }),
  seen
)

// ---- 能翻页的那几种：一份 PDF 字节，封面、开头、缩略图、展开都从它画 ----
const pdfBytes = ref<ArrayBuffer | null>(null)
const pdfFailed = ref(false)
const pdf = useSlidesPdf(pdfBytes)
const snippet = ref('')

watch(
  () => [seen.value, props.topicId, props.path] as const,
  async ([visible, topicId, path]) => {
    pdfBytes.value = null
    pdfFailed.value = false
    if (!visible || !topicId || !path || family.value === 'sheet') return
    try {
      pdfBytes.value = NEEDS_CONVERSION.has(suffixOf(path))
        ? await source.documentPdf(topicId, path)
        : await source.fileBytes(topicId, path)
    } catch {
      pdfFailed.value = true
    }
  },
  { immediate: true }
)

const cover = ref<HTMLElement | null>(null)
const strip = ref<HTMLElement[]>([])
const inline = ref<HTMLElement[]>([])
const expanded = ref(false)

const stripCount = computed(() => Math.min(pdf.count.value, STRIP_PAGES))
const inlineCount = computed(() => Math.min(pdf.count.value, INLINE_PAGES))

watch(pdf.revision, async () => {
  if (!pdf.count.value) return
  await nextTick()
  if (family.value === 'doc' && cover.value) {
    void pdf.paint(1, cover.value, { width: 72, height: 96 })
    // 摘要取不到就是没有摘要，和版本一样是「能读到才说」：卡片照常能打开、能下载。
    // 这一步在 watch 回调里，抛出去会被时间线的 ErrorBoundary 接走——整段对话换成
    // 「无法显示这块内容」，只为了两行可读可不读的字。取文字失败的原因（内核缺某个
    // 接口、这一页的字体读不出来、内存不够）都不归这张卡片管。
    try {
      const text = (await pdf.pageText(1)) ?? ''
      snippet.value = text.replace(/\s+/g, ' ').trim().slice(0, 140)
    } catch {
      snippet.value = ''
    }
  }
  if (family.value === 'slides')
    strip.value.forEach((host, i) => void pdf.paint(i + 1, host, { width: 120, height: 68 }))
})

async function toggleExpand() {
  expanded.value = !expanded.value
  if (!expanded.value) return
  await nextTick()
  const width = Math.max(120, (card.value?.clientWidth ?? 420) - 26)
  await Promise.all(inline.value.map((host, i) => pdf.paint(i + 1, host, { width })))
  // 展开是为了读：页面画出来后把卡片顶端带进视野，不然对话贴着底部，读者看到的是第二页的末尾。
  card.value?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

// ---- 表格 ----
const sheetChanges = ref<number | null>(null)
const sheetNames = ref<string[]>([])

// ---- 头上那一行字和那颗标记 ----
const meta = computed(() => {
  const parts = [props.kind]
  if (latest.value) parts.push(fmtFileSize(latest.value.size, 1))
  if (family.value !== 'sheet' && pdf.count.value) parts.push(t('work.room.fileCard.pages', { n: pdf.count.value }))
  if (family.value === 'sheet' && sheetNames.value.length > 1)
    parts.push(t('work.room.fileCard.sheets', { n: sheetNames.value.length }))
  if (latest.value && !newer.value) parts.push(t('work.room.fileCard.version', { n: latest.value.seq }))
  return parts.join(' · ')
})
const badge = computed(() => {
  if (!newer.value || !latest.value) return ''
  if (family.value === 'sheet' && sheetChanges.value)
    return t('work.room.fileCard.cellsChanged', { n: sheetChanges.value })
  return t('work.room.fileCard.updated', { n: latest.value.seq })
})
const badgeHint = computed(() =>
  atPost.value && latest.value
    ? t('work.room.fileCard.updatedHint', { from: atPost.value.seq, to: latest.value.seq })
    : undefined
)
</script>

<template>
  <div ref="card" class="fcard" :class="`fcard--${family}`" data-testid="message-file-card">
    <!-- 整块能点：在右侧打开。键盘走底下那颗「在右侧打开」。 -->
    <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- the 在右侧打开 button below is the keyboard path -->
    <div class="fcard__main" :title="t('work.room.message.openFile', { name })" @click="emit('open')">
      <span v-if="family === 'doc'" class="fcard__cover">
        <span ref="cover" class="fcard__page" />
        <v-icon v-if="!pdf.count.value" size="28" class="fcard__type">{{ fileIcon(path) }}</v-icon>
      </span>
      <span class="fcard__text">
        <span class="fcard__title">
          <v-icon size="18" class="fcard__type">{{ fileIcon(path) }}</v-icon>
          <span class="fcard__name">{{ name }}</span>
        </span>
        <span class="fcard__meta">
          <span>{{ meta }}</span>
          <span v-if="badge" class="fcard__badge" data-testid="file-card-badge" :title="badgeHint">{{ badge }}</span>
        </span>
        <span v-if="family === 'doc' && snippet" class="fcard__snippet">{{ snippet }}</span>
      </span>
    </div>

    <div v-if="family === 'slides' && stripCount" class="fcard__strip">
      <button
        v-for="n in stripCount"
        :key="n"
        type="button"
        class="fcard__slide"
        :aria-label="t('work.room.fileCard.slide', { n })"
        @click="emit('open')"
      >
        <span :ref="(el) => (strip[n - 1] = el as HTMLElement)" class="fcard__slide-page" />
        <span class="fcard__slide-no">{{ n }}</span>
      </button>
    </div>
    <span v-else-if="family === 'slides' && !pdfFailed" class="fcard__strip-placeholder" aria-hidden="true" />

    <FileCardSheet
      v-if="family === 'sheet'"
      class="fcard__sheet"
      :topic-id="topicId"
      :path="path"
      :seen="seen"
      :base-version-id="newer && atPost ? atPost.id : null"
      @changes="sheetChanges = $event"
      @sheets="sheetNames = $event"
    />

    <!-- Inline expand belongs to documents, so it sits under the snippet: every file card keeps the
         same two buttons at the bottom (open on the right, download). -->
    <button
      v-if="family === 'doc' && pdf.count.value"
      type="button"
      class="fcard__expand"
      :aria-expanded="expanded"
      @click="toggleExpand"
    >
      <v-icon size="14">{{ expanded ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
      {{ expanded ? t('work.room.fileCard.collapse') : t('work.room.fileCard.expand') }}
    </button>

    <div v-if="expanded" class="fcard__inline" data-testid="file-card-inline">
      <span
        v-for="n in inlineCount"
        :key="n"
        :ref="(el) => (inline[n - 1] = el as HTMLElement)"
        class="fcard__inline-page"
      />
      <button v-if="pdf.count.value > inlineCount" type="button" class="fcard__more" @click="emit('open')">
        {{ t('work.room.fileCard.morePages', { n: pdf.count.value - inlineCount }) }}
      </button>
    </div>

    <div class="fcard__actions">
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-dock-right" @click="emit('open')">
        {{ t('work.room.message.openInPanel') }}
      </BaseButton>
      <BaseButton
        v-if="downloadable"
        size="sm"
        prepend-icon="mdi-download-outline"
        :title="t('work.room.message.downloadFile', { name })"
        @click="emit('download')"
      >
        {{ t('work.room.fileCard.download') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.fcard {
  display: flex;
  flex-direction: column;
  width: 420px;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  /* 原型里这张卡是浮在对话上的（--shadow-1）。不加它，白底卡片在白底对话里只剩
     一圈线，比原型平。 */
  box-shadow: var(--shadow-1);
}
.fcard__main {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 12px 10px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.fcard__main:hover {
  background: var(--fill);
}
/* 封面是竖着的一页纸，按页面比例画进 72×96 的方格。 */
.fcard__cover {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 96px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--fill);
}
.fcard__page {
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
}
.fcard__type {
  flex: none;
  color: var(--muted);
}
.fcard__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.fcard__title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.fcard__name {
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.fcard__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  /* 类型、大小、版本是读者要看的字：用 --muted，不用 --faint。 */
  color: var(--muted);
}
/* 「改过了」那颗：琥珀浅底配琥珀字。 */
.fcard__badge {
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  color: var(--accent-ink);
  background: var(--accent-wash);
  white-space: nowrap;
}
.fcard__snippet {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.fcard__strip {
  display: flex;
  gap: 8px;
  padding: 0 12px 10px;
  overflow-x: auto;
}
.fcard__slide {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 120px;
  height: 68px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--fill);
  cursor: pointer;
}
.fcard__slide:hover {
  border-color: var(--faint);
}
.fcard__slide:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.fcard__slide-no {
  position: absolute;
  right: 4px;
  bottom: 2px;
  padding: 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: var(--surface);
  border-radius: var(--radius-sm);
}
.fcard__strip-placeholder {
  height: 68px;
  margin: 0 12px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
}
.fcard__sheet {
  padding: 0 12px 10px;
}
.fcard__inline {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 0 12px 10px;
}
.fcard__inline-page {
  border: 1px solid var(--line);
  background: var(--surface);
}
.fcard__more {
  padding: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--accent-ink);
  background: none;
  border: 0;
  cursor: pointer;
}
.fcard__expand {
  display: inline-flex;
  align-self: flex-start;
  align-items: center;
  gap: 2px;
  margin: 0 12px 8px;
  padding: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: none;
  border: 0;
  cursor: pointer;
}
.fcard__expand:hover {
  color: var(--ink);
}
.fcard__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
}
</style>
