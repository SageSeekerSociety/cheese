import type { AnalyticsApproveType, AnalyticsCompletionType } from '@/network/api/spaces/types'
import type { SpaceTaskVisibilityStatus } from '@/network/api/spaces/types'
import type { TaskSubmitterType } from '@/types'

import dayjs from 'dayjs'

// 这个模块是纯 ts，没有组件实例可挂 i18n 插件，所以拿模块级的 `t`（它不依赖插件，
// 在渲染函数里照样跟着 locale 走）。别改成 useI18n()——那要求调用方装插件。
import { t } from '@/i18n'

export const formatCount = (value?: null | number) => new Intl.NumberFormat('zh-CN').format(value || 0)

export const formatPercent = (value?: null | number, digits = 1) => `${((value || 0) * 100).toFixed(digits)}%`

export const formatDateTime = (value?: null | number) => {
  if (!value) return t('spaces.detail.memberTasks.notAvailable')
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

export const formatDate = (value?: null | number) => {
  if (!value) return t('spaces.detail.memberTasks.notAvailable')
  return dayjs(value).format('YYYY-MM-DD')
}

export const formatDeadline = (value?: null | number) => {
  if (!value) return t('spaces.detail.memberTasks.deadline.none')

  const now = dayjs()
  const deadline = dayjs(value)
  const diffDays = deadline.startOf('day').diff(now.startOf('day'), 'day')

  if (diffDays < 0) return t('spaces.detail.memberTasks.deadline.passed', { date: deadline.format('MM-DD') })
  if (diffDays === 0) return t('spaces.detail.memberTasks.deadline.today')
  if (diffDays === 1) return t('spaces.detail.memberTasks.deadline.tomorrow')
  // 走到这里只剩 2..6 天，英文那条 "Closes in {days} days" 恒是复数，不用写形态。
  if (diffDays < 7) return t('spaces.detail.memberTasks.deadline.inDays', { days: diffDays })
  return t('spaces.detail.memberTasks.deadline.onDate', { date: deadline.format('MM-DD') })
}

export const approvalText = (value: AnalyticsApproveType) => {
  if (value === 'APPROVED') return t('spaces.detail.memberTasks.approval.approved')
  if (value === 'DISAPPROVED') return t('spaces.detail.memberTasks.approval.notApproved')
  return t('spaces.detail.memberTasks.approval.pending')
}

export const approvalColor = (value: AnalyticsApproveType) => {
  if (value === 'APPROVED') return 'success'
  if (value === 'DISAPPROVED') return 'error'
  return 'warning'
}

export const visibilityStatusText = (value: SpaceTaskVisibilityStatus) => {
  if (value === 'ENDED') return t('spaces.detail.memberTasks.visibility.ended')
  if (value === 'APPROVED_VISIBLE') return t('spaces.detail.memberTasks.visibility.approvedVisible')
  if (value === 'APPROVED_HIDDEN') return t('spaces.detail.memberTasks.visibility.approvedHidden')
  if (value === 'REJECTED') return t('spaces.detail.memberTasks.visibility.rejected')
  return t('spaces.detail.memberTasks.approval.pending')
}

export const visibilityStatusColor = (value: SpaceTaskVisibilityStatus) => {
  if (value === 'ENDED') return 'secondary'
  if (value === 'APPROVED_VISIBLE') return 'success'
  if (value === 'APPROVED_HIDDEN') return 'warning'
  if (value === 'REJECTED') return 'error'
  return 'info'
}

export const completionText = (value: AnalyticsCompletionType) => {
  if (value === 'SUCCESS') return t('spaces.detail.memberTasks.completion.success')
  if (value === 'FAILED') return t('spaces.detail.memberTasks.completion.failed')
  if (value === 'PENDING_REVIEW') return t('spaces.detail.memberTasks.completion.pendingReview')
  if (value === 'REJECTED_RESUBMITTABLE') return t('spaces.detail.memberTasks.completion.resubmittable')
  return t('spaces.detail.memberTasks.completion.notSubmitted')
}

export const completionColor = (value: AnalyticsCompletionType) => {
  if (value === 'SUCCESS') return 'success'
  if (value === 'FAILED') return 'error'
  if (value === 'PENDING_REVIEW') return 'info'
  if (value === 'REJECTED_RESUBMITTABLE') return 'warning'
  return 'secondary'
}

export const identityText = (value: TaskSubmitterType) =>
  value === 'TEAM' ? t('spaces.detail.memberTasks.identity.team') : t('spaces.detail.memberTasks.identity.individual')
