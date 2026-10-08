// Comments on a task's changes, written while it awaits review and sent with a 退回
// (`backend/app/api/routes/review_comments.py`).
import type { ReviewComment, ReviewCommentDraft } from '../types/reviewComment'

import { request } from './http'

export function listReviewComments(taskId: string): Promise<{ comments: ReviewComment[] }> {
  return request(`/topics/${encodeURIComponent(taskId)}/review-comments`)
}

export function writeReviewComment(taskId: string, draft: ReviewCommentDraft): Promise<ReviewComment> {
  return request(`/topics/${encodeURIComponent(taskId)}/review-comments`, {
    method: 'POST',
    body: JSON.stringify(draft),
  })
}

export function editReviewComment(id: string, body: string, suggestion: string | null): Promise<ReviewComment> {
  return request(`/review-comments/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ body, suggestion }),
  })
}

export function deleteReviewComment(id: string): Promise<{ id: string }> {
  return request(`/review-comments/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
