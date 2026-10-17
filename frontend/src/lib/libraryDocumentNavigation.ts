import type { Router } from 'vue-router'
import type { DocReviewRequest, OpenedDocument } from './docReview'

import { routeIds } from './addresses'

interface DocumentCardContext {
  projectId: string
  documentId: string
  from: string
  review?: DocReviewRequest
}

/** Keep the card's review and return destination on this history entry, not in its shared URL. */
export function openThreadDocument(
  router: Router,
  projectId: string,
  document: OpenedDocument,
  review?: DocReviewRequest
) {
  const context: DocumentCardContext = {
    projectId,
    documentId: document.id,
    from: router.currentRoute.value.fullPath,
    review,
  }
  return router.push({
    name: 'project-document',
    params: { projectId, docId: document.id },
    // Card data may be reactive; browser history needs a plain, cloneable value.
    state: { documentCard: JSON.parse(JSON.stringify(context)) },
  })
}

export function documentCardContext(router: Router, projectId: string, documentId: string): DocumentCardContext | null {
  const value = router.options?.history?.state?.documentCard
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const context = value as unknown as DocumentCardContext
  if (
    typeof context.projectId !== 'string' ||
    typeof context.documentId !== 'string' ||
    context.projectId !== projectId ||
    context.documentId !== documentId ||
    typeof context.from !== 'string'
  )
    return null
  let from
  try {
    from = router.resolve(context.from)
  } catch {
    return null
  }
  if (from.name !== 'workspace-thread' || routeIds(from.params).projectId !== projectId) return null
  return context
}
