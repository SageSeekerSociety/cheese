<script setup lang="ts">
import type { FileContent, PreviewInfo } from '../../cx_types'

import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { useFullscreen } from '@vueuse/core'

import { getPreview, readFile, requestPreviewSession } from '../../api'
import { postPreviewSession } from '../../lib/previewSession'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    active?: boolean
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)
const emit = defineEmits<{ (e: 'loaded', artifactId: string | null): void }>()
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
        const content = await readFile(pid, art.path, tid)
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
</style>
