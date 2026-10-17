// 在新标签页打开这一格看着的东西。
//
// 给了 `path` 就是房间里的某一份文件（自由区的一个页签）——它不跟着当前预览走，所以要
// 把它自己的地址带过去；不给就是当前预览，那一条路本来就落在预览域根上。三处用它：
// 顶栏那一条、指定了文件的那一格自己的那条，以及「这一份读不成文本」下面的按钮。
import type { PreviewFrame } from '@/composables/usePreviewFrames'

import { roomFileDestination } from '@/lib/previewSession'

export function openPreviewInNewTab(
  topicId: string | null,
  displayedFrame: PreviewFrame | null | undefined,
  path?: string | null
): void {
  if (!topicId) return
  // Bind the displayed file path, not latest metadata. This still serves mutable
  // room resources; it is not an immutable version or a fixed app instance.
  const displayedPath = displayedFrame && !displayedFrame.live ? displayedFrame.label : null
  const targetPath = displayedPath || path
  const query = targetPath ? `?path=${encodeURIComponent(roomFileDestination(targetPath))}` : ''
  window.open(`/previews/${encodeURIComponent(topicId)}${query}`, '_blank', 'noopener')
}
