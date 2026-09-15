<script setup lang="ts">
import type { FileContent, PreviewInfo } from '../../cx_types'

import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { useFullscreen } from '@vueuse/core'

import {
  attachmentRawUrl,
  downloadFile,
  getPreview,
  previewDocumentPdf,
  previewFileBytes,
  PreviewRendererUnavailable,
  readPreviewFile,
  requestPreviewSession,
} from '../../api'
import { postPreviewSession } from '../../lib/previewSession'

import PreviewPages from './preview/PreviewPages.vue'
import PreviewSheet from './preview/PreviewSheet.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    active?: boolean
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)
const emit = defineEmits<{
  (e: 'loaded', artifactId: string | null): void
  /** 读者指着文档里的一处提了一句话，交给房间的对话。 */
  (e: 'locate', message: string): void
}>()
const panelElement = ref<HTMLElement | null>(null)
const frameName = `cheese-preview-${useId()}`
const {
  isFullscreen: previewFull,
  isSupported: fullscreenSupported,
  toggle: toggleFullscreen,
} = useFullscreen(panelElement)
const fullscreenError = ref('')
const loading = ref(false)
const refreshing = ref(false)
const previewFile = ref<FileContent | null>(null)
const previewMime = ref('text/html')
const previewNamed = ref(false)
const previewUrl = ref<string | null>(null)
const previewAppNote = ref('')
const previewTunnelUp = ref(false)
const previewNamedPath = ref('')
const previewError = ref<string | null>(null)
const previewReadError = ref<string | null>(null)
let loadedArtifact: string | null = null
let generation = 0

function openPreviewInNewTab() {
  if (props.topicId) window.open(`/previews/${encodeURIComponent(props.topicId)}`, '_blank', 'noopener')
}

// 文档类交付物: a report, a deck or a budget is what the room was asked for, and
// it is shown here rather than offered as a download. A tab called 预览 that
// hands over a file instead of displaying it is the same as having no tab.
//
// Two viewers, and which one a file gets is decided by what a reader can point
// at afterwards. A paginated document keeps its text, so the reader points at a
// sentence. A spreadsheet keeps its cell addresses, and `B7` is an address 芝士
// can open directly — paginating it would destroy exactly that.
const DOCUMENT_TYPES: Record<string, { label: string; icon: string; view: 'pages' | 'sheet' }> = {
  pdf: { label: 'PDF', icon: 'mdi-file-pdf-box', view: 'pages' },
  docx: { label: 'Word 文档', icon: 'mdi-file-word-outline', view: 'pages' },
  doc: { label: 'Word 文档', icon: 'mdi-file-word-outline', view: 'pages' },
  odt: { label: '文档', icon: 'mdi-file-document-outline', view: 'pages' },
  rtf: { label: '文档', icon: 'mdi-file-document-outline', view: 'pages' },
  pptx: { label: '幻灯片', icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  ppt: { label: '幻灯片', icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  odp: { label: '幻灯片', icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  xlsx: { label: '表格', icon: 'mdi-file-excel-outline', view: 'sheet' },
  xls: { label: '表格', icon: 'mdi-file-excel-outline', view: 'sheet' },
  csv: { label: 'CSV 表格', icon: 'mdi-file-delimited-outline', view: 'sheet' },
}
//: Formats the browser cannot draw itself, so the platform converts them first.
const NEEDS_CONVERSION = new Set(['docx', 'doc', 'odt', 'rtf', 'pptx', 'ppt', 'odp'])

function suffixOf(path: string): string {
  const name = path.split('/').pop() ?? ''
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

const documentSuffix = computed(() => suffixOf(previewFile.value?.path ?? ''))
const documentType = computed(() => DOCUMENT_TYPES[documentSuffix.value] ?? null)
const documentName = computed(() => previewFile.value?.path.split('/').pop() ?? '')
const downloadError = ref('')

// ---- 文档字节 ----
const docBytes = ref<ArrayBuffer | null>(null)
const docLoading = ref(false)
const docError = ref('')
const rendererMissing = ref(false)
let docGeneration = 0
let loadedDocKey = ''

async function loadDocument() {
  const tid = props.topicId
  const path = previewFile.value?.path
  const type = documentType.value
  if (!tid || !path || !type) return
  // The version changes when 芝士 rewrites the file; re-fetching on every poll
  // would otherwise re-convert a document that has not moved.
  const key = `${tid}:${path}:${previewFile.value?.version ?? ''}:${previewFile.value?.bytes ?? ''}`
  if (key === loadedDocKey && docBytes.value) return
  const mine = ++docGeneration
  docLoading.value = true
  docError.value = ''
  rendererMissing.value = false
  try {
    const bytes = NEEDS_CONVERSION.has(documentSuffix.value)
      ? await previewDocumentPdf(tid, path)
      : await previewFileBytes(tid, path)
    if (mine !== docGeneration) return
    docBytes.value = bytes
    loadedDocKey = key
  } catch (e) {
    if (mine !== docGeneration) return
    // 保留已经在屏幕上的那一份。刷新失败时把它清掉，读者失去的是一份本来好好的
    // 文档，换来一句错误——而这份文档仍然是这个交付物最新的可见状态。
    loadedDocKey = ''
    rendererMissing.value = e instanceof PreviewRendererUnavailable
    docError.value = e instanceof Error ? e.message : '无法显示这个文件'
  } finally {
    if (mine === docGeneration) docLoading.value = false
  }
}

// ---- 指出位置 ----
// 读者指着文档里的一处说「这里不对」，交给芝士的是一句话：文件、位置、原文。
// 不做能长期保留的批注——读者要改的那句话，正是芝士下一轮要改掉的那句话，锚点必然
// 失效。这条评论只在下一轮被读一次，之后它属于对话记录。
const locator = ref<{ label: string; quote: string; address: string } | null>(null)
const locatorNote = ref('')
const locatorInput = ref<HTMLInputElement | null>(null)

function openLocator(label: string, quote: string, address: string) {
  locator.value = { label, quote, address }
  locatorNote.value = ''
  void nextTick(() => locatorInput.value?.focus())
}

function clearLocator() {
  locator.value = null
  locatorNote.value = ''
}

function onQuote(payload: { text: string; page: number }) {
  // 一整页的选中没有指向性，当作没指。
  const quote = payload.text.replace(/\s+/g, ' ').trim()
  if (quote.length < 2) return
  openLocator(`第 ${payload.page} 页`, quote.slice(0, 200), `第 ${payload.page} 页`)
}

function onCell(payload: { address: string; value: string; sheet: string }) {
  openLocator(`${payload.sheet}!${payload.address}`, payload.value || '（空）', `${payload.sheet}!${payload.address}`)
}

function sendLocator() {
  const target = locator.value
  const note = locatorNote.value.trim()
  if (!target || !note) return
  emit('locate', `在 ${previewFile.value?.path ?? ''} 的 ${target.address}（「${target.quote}」）：${note}`)
  clearLocator()
}

watch([documentType, () => previewFile.value?.path, () => previewFile.value?.version], () => {
  if (documentType.value) void loadDocument()
  else {
    docGeneration += 1
    docBytes.value = null
    loadedDocKey = ''
    docError.value = ''
  }
})

async function downloadArtifact() {
  downloadError.value = ''
  const path = previewFile.value?.path
  if (!props.topicId || !path) return
  try {
    await downloadFile(attachmentRawUrl(props.topicId, path), documentName.value || 'file')
  } catch (e) {
    downloadError.value = e instanceof Error ? e.message : '下载失败'
  }
}

async function fullscreen() {
  fullscreenError.value = ''
  try {
    // Fullscreen keeps the same browsing context, including unsaved app state.
    await toggleFullscreen()
  } catch {
    fullscreenError.value = '无法进入全屏，请在新标签页打开'
  }
}

async function load(opts: { silent?: boolean; reload?: boolean } = {}) {
  // Metadata polling must not cancel an explicit refresh's pending grant.
  if (opts.silent && !opts.reload && (loading.value || refreshing.value)) return
  const tid = props.topicId
  const pid = props.projectId
  if (!tid || !pid) return
  const current = ++generation
  const stillCurrent = () => current === generation && props.topicId === tid
  if (opts.silent) refreshing.value = true
  else loading.value = true
  try {
    let art: PreviewInfo | null
    try {
      art = await getPreview(tid)
    } catch (e) {
      if (!stillCurrent()) return
      previewUrl.value = null
      previewError.value = e instanceof Error ? e.message : '加载失败'
      return
    }
    if (!stillCurrent()) return
    previewError.value = null
    previewReadError.value = null
    previewNamed.value = !!art
    previewNamedPath.value = art?.path ?? ''
    emit('loaded', art?.artifact_id ?? null)
    if (!art) {
      previewUrl.value = null
      previewFile.value = null
      previewAppNote.value = ''
      loadedArtifact = null
      return
    }
    const identity = `${art.kind ?? 'file'}:${art.artifact_id ?? art.path}:${art.url ?? ''}:${art.version ?? ''}`
    const unchanged = identity === loadedArtifact && art.url === previewUrl.value
    previewAppNote.value = art.kind === 'app' ? art.path : ''
    previewTunnelUp.value = !!art.tunnel_up
    if (art.kind === 'app') {
      previewFile.value = null
      if (!art.url) {
        previewUrl.value = null
        loadedArtifact = null
        return
      }
    } else {
      previewMime.value = art.mime || 'text/html'
      try {
        const content = await readPreviewFile(tid)
        if (!stillCurrent()) return
        previewFile.value = content
      } catch (e) {
        if (!stillCurrent()) return
        previewUrl.value = null
        previewFile.value = null
        previewReadError.value = e instanceof Error ? e.message : '读不到这个文件'
        return
      }
      if (!stillCurrent()) return
      if (previewFile.value.content === null && !previewFile.value.too_large) {
        previewUrl.value = null
        return
      }
    }
    if (unchanged && !opts.reload) return
    if (!art.url) {
      previewUrl.value = null
      previewError.value = '预览地址暂不可用'
      return
    }
    try {
      const session = await requestPreviewSession(tid)
      if (!stillCurrent()) return
      previewUrl.value = art.url
      // Mount the named frame before POSTing: a missing target opens a new tab.
      loading.value = false
      await nextTick()
      if (!stillCurrent()) return
      postPreviewSession(session, { target: frameName })
      loadedArtifact = identity
    } catch (e) {
      if (!stillCurrent()) return
      previewUrl.value = null
      previewError.value = e instanceof Error ? e.message : '预览授权失败'
    }
  } finally {
    if (stillCurrent()) {
      loading.value = false
      refreshing.value = false
    }
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) void load({ silent: !!previewUrl.value })
  },
  { immediate: true }
)
watch(
  () => props.refreshTick,
  () => {
    if (props.active) void load({ silent: true })
  }
)

// Poll metadata only; an unchanged artifact never receives a new form POST.
let refreshTimer: ReturnType<typeof setInterval> | null = null
function stopAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
}
watch(
  () => props.active,
  (active) => {
    stopAutoRefresh()
    if (!active) return
    refreshTimer = setInterval(() => {
      if (!document.hidden) void load({ silent: true })
    }, 20_000)
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  generation += 1
  stopAutoRefresh()
})
watch(
  () => props.topicId,
  () => {
    generation += 1
    previewUrl.value = null
    previewAppNote.value = ''
    previewNamedPath.value = ''
    previewFile.value = null
    previewError.value = null
    previewReadError.value = null
    previewNamed.value = false
    loadedArtifact = null
    if (props.active) void load()
  }
)
</script>

<template>
  <div ref="panelElement" class="panel-preview">
    <div class="preview-head">
      <v-btn
        v-if="projectId"
        :to="{ name: 'project-delivery', params: { projectId } }"
        size="small"
        variant="text"
        class="c-muted"
      >
        导出与发布
      </v-btn>
      <v-spacer />
      <template v-if="previewUrl || previewFile">
        <v-btn
          icon="mdi-open-in-new"
          size="small"
          variant="text"
          class="c-muted"
          title="在新标签页打开"
          @click="openPreviewInNewTab"
        />
        <v-btn
          v-if="fullscreenSupported && previewUrl"
          :icon="previewFull ? 'mdi-fullscreen-exit' : 'mdi-arrow-expand-all'"
          size="small"
          variant="text"
          class="c-muted"
          :title="previewFull ? '退出全屏' : '全屏预览'"
          @click="fullscreen"
        />
      </template>
      <v-btn
        icon="mdi-refresh"
        size="small"
        variant="text"
        class="c-muted"
        title="刷新"
        :loading="refreshing"
        @click="load({ silent: true, reload: true })"
      />
    </div>

    <v-alert v-if="fullscreenError" type="warning" density="compact">{{ fullscreenError }}</v-alert>

    <div v-if="loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>

    <div v-else-if="previewUrl" class="preview-wrap">
      <div class="preview-bar text-caption px-3 pt-2">
        <span class="text-medium-emphasis">{{ previewAppNote || previewFile?.path }}</span>
        <v-chip v-if="previewAppNote" size="x-small" variant="tonal" class="ms-2">运行中的应用</v-chip>
        <v-chip v-else size="x-small" variant="outlined" class="ms-2">{{ previewMime }}</v-chip>
      </div>
      <!-- The form supplies a scoped grant; neither src nor srcdoc carries content. -->
      <iframe
        :name="frameName"
        class="preview-frame"
        title="话题预览"
        sandbox="allow-scripts allow-forms allow-same-origin"
      />
    </div>
    <div v-else-if="previewError" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-error mb-2">mdi-alert-circle-outline</v-icon>
      <div>预览加载失败</div>
      <div class="text-caption mt-1">平台没能返回这个话题的预览：{{ previewError }}</div>
    </div>
    <div v-else-if="previewReadError" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>指定的文件读不到</div>
      <div class="text-caption mt-1">
        芝士指定了 {{ previewNamedPath || '一个文件' }}，但它现在读不出来：{{ previewReadError }}
      </div>
    </div>
    <div v-else-if="previewNamed && previewAppNote" class="text-center text-medium-emphasis py-8">
      <!-- Two states, and they are not interchangeable: the machine is not
           carrying a preview out at all, or it is and the app behind it is
           gone. Collapsing them told people to summon 芝士 again for a tunnel
           that no summon brings back. -->
      <v-icon size="32" class="text-disabled mb-2">mdi-lan-disconnect</v-icon>
      <div>应用暂时不在线</div>
      <div v-if="previewTunnelUp" class="text-caption mt-1">
        那台机器还连着，但登记的端口上没有服务在应答。芝士启动的服务多半已经退出，再 @ 它一次即可重新拉起。
      </div>
      <div v-else class="text-caption mt-1">
        跑这个话题的机器现在没有把预览通道拨出来（机器离线，或者这一轮还没开始）。再 @ 芝士一次即可重新拉起。
      </div>
    </div>
    <div v-else-if="documentType && previewFile" class="doc">
      <div class="doc__bar">
        <v-icon size="16" class="doc__icon">{{ documentType.icon }}</v-icon>
        <span class="doc__name">{{ documentName }}</span>
        <span class="doc__type t-meta">{{ documentType.label }}</span>
        <v-spacer />
        <v-btn size="small" variant="text" class="c-muted" prepend-icon="mdi-download" @click="downloadArtifact">
          下载
        </v-btn>
      </div>

      <v-alert v-if="downloadError" type="warning" density="compact" class="mx-3 mb-2">
        {{ downloadError }}
      </v-alert>
      <!-- 刷新失败但屏幕上还留着上一版：说清楚看到的不是最新的。 -->
      <v-alert v-else-if="docError && docBytes" type="warning" density="compact" class="mx-3 mb-2">
        这是上一次生成的内容，刷新未能完成：{{ docError }}
      </v-alert>

      <!-- 只在还没有东西可看时转圈。面板每 20 秒重读一次，芝士一存文件版本就变——
           这时候把查看器卸掉重挂，读者的滚动位置和选中都没了，而新的字节本来可以
           直接换进去。 -->
      <div v-if="docLoading && !docBytes" class="doc__state">
        <v-progress-circular indeterminate color="primary" size="24" />
      </div>
      <!-- 两种失败说的不是一回事：一种是这个部署缺服务（换个文件也一样），一种是
           这个文件转换不了（别的文件仍然能看）。 -->
      <div v-else-if="rendererMissing && !docBytes" class="doc__state doc__state--text">
        <v-icon size="28" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
        <div>文档预览未启用</div>
      </div>
      <div v-else-if="docError && !docBytes" class="doc__state doc__state--text">
        <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
        <div>无法显示这个文件</div>
        <div class="t-meta mt-1">{{ docError }}</div>
      </div>
      <PreviewPages v-else-if="documentType.view === 'pages'" :data="docBytes" @quote="onQuote" />
      <PreviewSheet v-else :data="docBytes" @cell="onCell" />

      <!-- 指出位置：读者选中一句话或点中一个格子，这条就是交给芝士的坐标。 -->
      <Transition name="locator">
        <div v-if="locator" class="locator">
          <div class="locator__where">
            <span class="locator__label t-meta">{{ locator.label }}</span>
            <span class="locator__quote">{{ locator.quote }}</span>
          </div>
          <input
            ref="locatorInput"
            v-model="locatorNote"
            class="locator__input"
            autocomplete="off"
            placeholder="说明要改什么"
            @keydown.enter.prevent="sendLocator"
            @keydown.esc.prevent="clearLocator"
          />
          <v-btn size="small" color="primary" variant="flat" :disabled="!locatorNote.trim()" @click="sendLocator">
            发送
          </v-btn>
          <v-btn icon="mdi-close" size="small" variant="text" class="c-muted" title="取消" @click="clearLocator" />
        </div>
      </Transition>
    </div>
    <div v-else-if="previewFile && previewFile.content === null" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>这个文件不是文本</div>
      <div class="text-caption mt-1">{{ previewFile.path }} 无法作为网页显示，可以在新窗口打开</div>
      <v-btn class="mt-3" size="small" variant="tonal" prepend-icon="mdi-open-in-new" @click="openPreviewInNewTab">
        在新窗口打开
      </v-btn>
    </div>
    <div v-else class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
      <div>暂无预览</div>
      <div class="text-caption mt-1">芝士做出网页、图表等可看的成果时，会放到这里。</div>
    </div>
  </div>
</template>

<style scoped>
.panel-preview {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  background: var(--surface);
}
.preview-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
  padding: 2px 6px;
  border-bottom: 1px solid var(--line);
}
.preview-wrap {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.preview-bar {
  display: flex;
  align-items: center;
}
.preview-bar > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-frame {
  flex: 1 1 auto;
  width: 100%;
  border: none;
  min-height: 240px;
  /* Theme-invariant on purpose: the iframe renders arbitrary user HTML that
     assumes a white page (its own text is near-black). Painting the backing
     dark would leave black text on a dark ground wherever that document is
     transparent — the page controls its own colours, we only back it. */
  /* stylelint-disable-next-line color-no-hex -- see the reason above */
  background: #fff;
}
.panel-preview:fullscreen {
  width: 100%;
  height: 100%;
}

/* ---- 文档交付物 ---- */
.doc {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}

.doc__bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--line);
}
.doc__icon {
  color: var(--muted);
}
.doc__name {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc__type {
  color: var(--faint);
  flex: none;
}

.doc__state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  color: var(--muted);
}
.doc__state--text {
  text-align: center;
}

/* 指出位置那一条。它浮在文档之上，所以有投影——第 3.4 节：投影只给浮起来的东西。 */
.locator {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
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
.locator__label {
  color: var(--muted);
}
.locator__quote {
  font-size: 13px;
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
  color: var(--text);
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition:
    border-color 0.12s ease,
    background-color 0.12s ease;
}
.locator__input:focus {
  outline: none;
  background: var(--surface);
  border-color: var(--line-2);
}

.locator-enter-active,
.locator-leave-active {
  transition:
    opacity 0.2s ease,
    transform 0.2s ease;
}
.locator-enter-from,
.locator-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
