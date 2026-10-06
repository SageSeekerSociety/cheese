import type { FileContent } from '@/cx_types'
import type { DocumentIdentity, DocumentSnapshot } from '@/lib/documentIdentity'
import type { RasterSelection } from './designRegion'

import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { t } from '@/i18n'
import { sameDocumentIdentity } from '@/lib/documentIdentity'
import { imageMimeOf } from '@/lib/fileKind'

interface ImagePreviewProps {
  topicId: string | null
  path?: string | null
  previewFile: FileContent | null
  isImageArtifact: boolean
  documentSuffix: string
  docIdentity?: DocumentIdentity | null
  docSnapshot?: DocumentSnapshot | null
  docBytes: ArrayBuffer | null
  loading: boolean
  refreshing: boolean
  docLoading: boolean
  docError: string
}
interface ImageRegionTarget {
  selection: RasterSelection
  identity: Readonly<DocumentIdentity>
  snapshot: DocumentSnapshot
}

/** One displayed blob and one atomic byte snapshot; metadata alone cannot authorize a region. */
export function usePreviewImageRegion(props: ImagePreviewProps, onRetire: () => void) {
  const imageSrc = ref('')
  const displayed = shallowRef<DocumentSnapshot | null>(null)
  const target = shallowRef<ImageRegionTarget | null>(null)
  const generation = ref(0)
  const namedImage = computed(() => !!props.path && props.isImageArtifact)
  function clear() {
    target.value = null
  }
  function release() {
    if (imageSrc.value) URL.revokeObjectURL(imageSrc.value)
    imageSrc.value = ''
    displayed.value = null
  }
  watch(
    [namedImage, () => props.docSnapshot],
    () => {
      release()
      generation.value += 1
      const snapshot = props.docSnapshot
      if (!namedImage.value || !snapshot) return
      imageSrc.value = URL.createObjectURL(new Blob([snapshot.bytes], { type: imageMimeOf(props.documentSuffix) }))
      displayed.value = snapshot
    },
    { immediate: true, flush: 'sync' }
  )

  const imageIdentity = computed(() => JSON.stringify([props.docIdentity, generation.value]))
  const selectionEnabled = computed(() => {
    const current = props.docIdentity
    const snapshot = displayed.value
    const file = props.previewFile
    return !!(
      namedImage.value &&
      imageSrc.value &&
      current?.version &&
      snapshot &&
      file &&
      !props.loading &&
      !props.refreshing &&
      !props.docLoading &&
      !props.docError &&
      props.topicId === current.topicId &&
      props.path === current.path &&
      file.path === current.path &&
      file.version === current.version &&
      (file.source ?? 'live') === current.source &&
      props.docSnapshot === snapshot &&
      props.docBytes === snapshot.bytes &&
      sameDocumentIdentity(current, snapshot.identity) &&
      snapshot.sourceVersion === current.version
    )
  })
  function matches(selection: RasterSelection) {
    const { region, naturalWidth, naturalHeight } = selection
    return (
      selectionEnabled.value &&
      selection.identity === imageIdentity.value &&
      selection.src === imageSrc.value &&
      [naturalWidth, naturalHeight, region.x, region.y, region.width, region.height].every(Number.isSafeInteger) &&
      naturalWidth > 0 &&
      naturalHeight > 0 &&
      region.x >= 0 &&
      region.y >= 0 &&
      region.width > 0 &&
      region.height > 0 &&
      region.x + region.width <= naturalWidth &&
      region.y + region.height <= naturalHeight
    )
  }
  function capture(selection: RasterSelection): ImageRegionTarget | null {
    if (!matches(selection) || !props.docIdentity || !displayed.value) return null
    return {
      selection: { ...selection, region: { ...selection.region } },
      identity: { ...props.docIdentity },
      snapshot: displayed.value,
    }
  }
  function message(note: string): string | null {
    const captured = target.value
    if (
      !captured ||
      !note.trim() ||
      !matches(captured.selection) ||
      !props.docIdentity ||
      captured.snapshot !== displayed.value ||
      !sameDocumentIdentity(captured.identity, props.docIdentity)
    )
      return null
    return t('design.regionMessage', {
      ...captured.identity,
      task: captured.identity.taskId ?? '',
      ...captured.selection.region,
      naturalWidth: captured.selection.naturalWidth,
      naturalHeight: captured.selection.naturalHeight,
      note: note.trim(),
    })
  }
  watch(
    [imageSrc, imageIdentity, selectionEnabled],
    () => {
      if (target.value) {
        clear()
        onRetire()
      }
    },
    { flush: 'sync' }
  )
  onBeforeUnmount(release)
  return { imageSrc, imageIdentity, selectionEnabled, target, capture, message, clear }
}
