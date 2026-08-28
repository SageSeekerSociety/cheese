<script setup lang="ts">
// 预览 tab (spec §9.1): the artifact 芝士 pointed at (`cheese artifact`),
// rendered by its mimeType. The platform NEVER guesses a preview.
//
// The 「有新内容」 dot does NOT live here: it has to be right even while this tab
// is closed, which makes it a signal, and signals belong to WorkPanel. This
// component only reports the artifact id it just rendered (`loaded`), and the
// container decides what that means for the dot.
import type { FileContent, PreviewInfo } from '../../cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { BASE as API_BASE, getPreview, readFile } from '../../api'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    active?: boolean
    // Bumped by WorkPanel when a turn ends — the moment 芝士 has just finished
    // pointing at things. Silent re-fetch.
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)

const emit = defineEmits<{
  // The artifact this tab has now actually shown the reader (null = none).
  (e: 'loaded', artifactId: string | null): void
}>()

const loading = ref(false)
// A background re-fetch: spins only the 刷新 button, never replaces the panel.
const refreshing = ref(false)

// 预览: the artifact 芝士 pointed at — its file, mimeType, and whether it was
// AI-designated.
const previewFile = ref<FileContent | null>(null)
const previewMime = ref<string>('text/html')
const previewNamed = ref(false)
// What 芝士 named — so a read failure can say WHICH artifact broke.
const previewNamedPath = ref<string>('')
// Failures, kept apart from "nothing is set". Collapsing them (the old
// `.catch(() => null)` on both calls) reported every backend error and every
// unreadable file as "芝士还没有指定预览" — a broken panel that looked idle, so
// nobody reported it.
const previewError = ref<string | null>(null)
const previewReadError = ref<string | null>(null)

// 全屏预览 (Claude Artifacts style): the same content, workspace-covering. It is
// a Vuetify dialog, so the overlay stack owns its z-index and Esc — the old
// hand-rolled `position: fixed; z-index: 2400` + window keydown listener was
// re-implementing both, badly (a plain div is never focused, so its own
// @keydown.esc could not fire).
const previewFull = ref(false)

function openPreviewInNewTab() {
  if (previewFile.value && props.topicId) {
    // Served with CSP sandbox (opaque origin) — a real tab, not our origin.
    window.open(`${API_BASE}/topics/${props.topicId}/preview/raw`, '_blank', 'noopener')
  }
}

async function load(opts: { silent?: boolean } = {}) {
  const tid = props.topicId
  const pid = props.projectId
  if (!tid || !pid) return
  if (opts.silent) refreshing.value = true
  else loading.value = true
  try {
    // A silent re-fetch must NOT blank these first: each value below is only
    // assigned when it actually changed, so the artifact on screen is not
    // re-rendered (losing whatever state the reader built up in it) every tick.
    if (!opts.silent) {
      previewError.value = null
      previewReadError.value = null
      previewNamedPath.value = ''
    }
    let art: PreviewInfo | null
    try {
      art = await getPreview(tid)
    } catch (e) {
      if (props.topicId !== tid) return
      // "The backend errored" is its own state — not "nothing is set".
      previewNamed.value = false
      previewFile.value = null
      previewError.value = e instanceof Error ? e.message : '加载失败'
      return
    }
    if (props.topicId !== tid) return
    previewError.value = null
    emit('loaded', art?.artifact_id ?? null)
    if (art) {
      previewNamed.value = true
      previewNamedPath.value = art.path
      previewMime.value = art.mime || 'text/html'
      try {
        const content = await readFile(pid, art.path, tid)
        // Guard against a topic switch mid-flight — this await was the one fetch
        // in the drawer without it, so a slow read could paint topic A's
        // artifact into topic B's panel.
        if (props.topicId !== tid) return
        previewReadError.value = null
        // Same anti-flicker rule: an identical string reassigned would still
        // rebind `srcdoc` and reload the artifact, losing whatever state the
        // reader had built up inside it.
        if (content.content !== previewFile.value?.content || content.path !== previewFile.value?.path) {
          previewFile.value = content
        }
      } catch (e) {
        if (props.topicId !== tid) return
        previewFile.value = null
        previewReadError.value = e instanceof Error ? e.message : '读不到这个文件'
      }
    } else {
      previewNamed.value = false
      previewFile.value = null
    }
  } finally {
    if (props.topicId === tid) {
      loading.value = false
      refreshing.value = false
    }
  }
}

// Opening the tab loads it, exactly like opening the drawer used to.
watch(
  () => props.active,
  (on) => {
    if (on) void load()
  },
  { immediate: true }
)

// A turn ended: 芝士 repointed the preview, or the app it started died.
watch(
  () => props.refreshTick,
  () => {
    if (props.active) void load({ silent: true })
  }
)

// It also goes stale while you watch it, so re-fetch on a timer while on screen.
const REFRESH_MS = 20_000
let refreshTimer: ReturnType<typeof setInterval> | null = null
function stopAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
}
watch(
  () => props.active,
  (on) => {
    stopAutoRefresh()
    if (!on) return
    refreshTimer = setInterval(() => {
      // A hidden tab polling forever is pure waste — it re-fetches on the next
      // tick after it comes back anyway.
      if (typeof document !== 'undefined' && document.hidden) return
      void load({ silent: true })
    }, REFRESH_MS)
  },
  { immediate: true }
)
onBeforeUnmount(stopAutoRefresh)

// Topic switch: a stale frame or error would otherwise be attributed to the
// topic just opened.
watch(
  () => props.topicId,
  () => {
    previewNamedPath.value = ''
    previewFile.value = null
    previewError.value = null
    previewReadError.value = null
    previewNamed.value = false
    previewFull.value = false
    if (props.active) void load()
  }
)
</script>

<template>
  <div class="panel-preview">
    <div class="preview-head">
      <v-spacer />
      <template v-if="previewFile">
        <v-btn
          icon="mdi-open-in-new"
          size="small"
          variant="text"
          class="c-muted"
          title="在新标签页打开"
          @click="openPreviewInNewTab"
        />
        <v-btn
          icon="mdi-arrow-expand-all"
          size="small"
          variant="text"
          class="c-muted"
          title="全屏预览"
          @click="previewFull = true"
        />
      </template>
      <v-btn
        icon="mdi-refresh"
        size="small"
        variant="text"
        class="c-muted"
        title="刷新"
        :loading="refreshing"
        @click="load({ silent: true })"
      />
    </div>

    <div v-if="loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>

    <div v-if="previewError" class="text-center text-medium-emphasis py-8">
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
    <div v-else-if="previewFile" class="preview-wrap">
      <div class="preview-bar text-caption px-3 pt-2">
        <span class="text-medium-emphasis">{{ previewFile.path }}</span>
        <v-chip v-if="previewNamed" size="x-small" color="primary" variant="tonal" class="ms-2">芝士指定</v-chip>
        <v-chip size="x-small" variant="outlined" class="ms-1">
          {{ previewMime }}
        </v-chip>
      </div>
      <!-- allow-scripts WITHOUT allow-same-origin (Claude Artifacts posture):
           interactive artifacts run their JS, but in an opaque origin that
           cannot touch the platform page. -->
      <iframe class="preview-frame" :srcdoc="previewFile.content ?? ''" sandbox="allow-scripts" />
    </div>
    <div v-else class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
      <div>暂无预览</div>
      <div class="text-caption mt-1">芝士做出网页、图表等可看的成果时，会放到这里。</div>
    </div>

    <!-- 全屏预览: same artifact, workspace-covering. Vuetify's overlay owns the
         stacking and the Esc key. -->
    <v-dialog v-model="previewFull" fullscreen transition="dialog-bottom-transition">
      <div class="preview-full">
        <div class="preview-full__bar">
          <span class="preview-full__title">{{ previewFile?.path }}</span>
          <v-spacer />
          <v-btn
            icon="mdi-open-in-new"
            size="small"
            variant="text"
            class="c-muted"
            title="在新标签页打开"
            @click="openPreviewInNewTab"
          />
          <v-btn icon="mdi-close" size="small" variant="text" class="c-muted" @click="previewFull = false" />
        </div>
        <iframe
          v-if="previewFile"
          class="preview-full__frame"
          :srcdoc="previewFile.content ?? ''"
          sandbox="allow-scripts"
        />
      </div>
    </v-dialog>
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
.preview-full {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
}
.preview-full__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line-2);
}
.preview-full__title {
  font-size: 0.85rem;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-full__frame {
  flex: 1;
  border: 0;
  width: 100%;
}
</style>
