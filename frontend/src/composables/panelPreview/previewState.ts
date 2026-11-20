// 「预览」这一格现在摆的是哪一件，和由它算出来的那几样。两处取数都往这一份上写——房间里
// 当前的那一件（`currentPreview.ts`）和指定的那一份文件（`namedFile.ts`）——所以它单独放
// 一处，而不是挂在组合根或任何一半身上。
//
// ---- 这一份是什么 ----
// 「三种查看器怎么分派」「什么时候该把字节交给 iframe」的判据都在这一小撮里，画的那一半
// 要的也是它们，所以一并递出去，而不是两边各算一遍：一个文件是哪一种类型只能有一个答案。
import type { FileContent } from '../../cx_types'

import { computed, ref } from 'vue'

import { DOCUMENT_TYPES, IMAGE_SUFFIXES, suffixOf, videoMimeOf } from '../../lib/fileKind'

/** 这一格现在摆的是哪一件，以及路上那一次属于哪一代。 */
export function createPreviewState() {
  const loading = ref(false)
  const refreshing = ref(false)
  const previewFile = ref<FileContent | null>(null)
  const previewMime = ref('text/html')
  const previewNamed = ref(false)
  const previewUrl = ref<string | null>(null)
  const previewAppNote = ref('')
  const previewTunnelUp = ref(false)
  // 应用的端口有应答（后端给了地址）：连通链「应用」那一段。
  const previewAppUp = ref(false)
  // 摆着的是哪一件产物：快照记在它名下。
  const previewArtifactId = ref<string | null>(null)
  const previewNamedPath = ref('')
  const previewError = ref<string | null>(null)
  const previewReadError = ref<string | null>(null)
  // 刚跟着重启后的应用自动重载过：那一闪需要一句解释，否则看着像面板自己坏了。
  const autoReloaded = ref(false)
  // 路上那一次属于哪一代：话题换了、或者又按了一次刷新，先前那一次的结果就不再算数
  // （它带的是上一份内容，落下来就是「刚切换的这一格显示着上一格的东西」）。
  let generation = 0

  const documentSuffix = computed(() => suffixOf(previewFile.value?.path ?? ''))
  const documentType = computed(() => DOCUMENT_TYPES[documentSuffix.value] ?? null)
  const documentName = computed(() => previewFile.value?.path.split('/').pop() ?? '')
  const isImageArtifact = computed(() => IMAGE_SUFFIXES.has(documentSuffix.value))
  const isVideoArtifact = computed(() => !!videoMimeOf(previewFile.value?.path ?? '', previewMime.value))

  /** 开一次取数：报回它落在哪一代，落下来时拿这个数问还作不作数。 */
  function begin(): number {
    return ++generation
  }
  /** 把在路上的那一次作废：话题换了、这一格从屏幕上下去了、或者组件拆了。 */
  function invalidate() {
    generation += 1
  }
  /** 路上那一次现在回来时还作不作数。 */
  function stillCurrent(current: number): boolean {
    return current === generation
  }

  return {
    loading,
    refreshing,
    previewFile,
    previewMime,
    previewNamed,
    previewUrl,
    previewAppNote,
    previewTunnelUp,
    previewAppUp,
    previewArtifactId,
    previewNamedPath,
    previewError,
    previewReadError,
    autoReloaded,
    documentSuffix,
    documentType,
    documentName,
    isImageArtifact,
    isVideoArtifact,
    begin,
    invalidate,
    stillCurrent,
  }
}

/** 「预览」这一格摆着的那一份，和它的几样派生。 */
export type PreviewState = ReturnType<typeof createPreviewState>
