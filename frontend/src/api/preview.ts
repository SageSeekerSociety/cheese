import type { PreviewSelection, PreviewSession } from '../types/preview'

import { request } from '../api'

export type { PreviewSelection, PreviewSession } from '../types/preview'

// Use the existing authenticated fetch path, including token refresh and errors.
export function requestPreviewSession(topicId: string, selection?: PreviewSelection): Promise<PreviewSession> {
  return request<PreviewSession>(`/topics/${encodeURIComponent(topicId)}/preview-session`, {
    method: 'POST',
    ...(selection ? { body: JSON.stringify(selection) } : {}),
  })
}
