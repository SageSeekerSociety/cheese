import type { AnalyticsApproveType } from '@/network/api/spaces/types'
import type { SpaceTaskVisibilityStatus } from '@/network/api/spaces/types'

import dayjs from 'dayjs'

export const formatCount = (value?: null | number) => new Intl.NumberFormat('zh-CN').format(value || 0)

export const formatPercent = (value?: null | number, digits = 1) => `${((value || 0) * 100).toFixed(digits)}%`

export const formatDateTime = (value?: null | number) => {
  if (!value) return '暂无'
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

export const formatDeadline = (value?: null | number) => {
  if (!value) return '无截止时间'

  const now = dayjs()
  const deadline = dayjs(value)
  const diffDays = deadline.startOf('day').diff(now.startOf('day'), 'day')

  if (diffDays < 0) return `已于 ${deadline.format('MM-DD')} 截止`
  if (diffDays === 0) return '今日截止'
  if (diffDays === 1) return '明日截止'
  if (diffDays < 7) return `${diffDays} 天后截止`
  return deadline.format('MM-DD 截止')
}

export const approvalText = (value: AnalyticsApproveType) => {
  if (value === 'APPROVED') return '已通过'
  if (value === 'DISAPPROVED') return '未通过'
  return '待审核'
}

export const approvalColor = (value: AnalyticsApproveType) => {
  if (value === 'APPROVED') return 'success'
  if (value === 'DISAPPROVED') return 'error'
  return 'warning'
}

export const visibilityStatusText = (value: SpaceTaskVisibilityStatus) => {
  if (value === 'ENDED') return '已结项'
  if (value === 'APPROVED_VISIBLE') return '已通过且可见'
  if (value === 'APPROVED_HIDDEN') return '已通过但隐藏'
  if (value === 'REJECTED') return '已驳回'
  return '待审核'
}

export const visibilityStatusColor = (value: SpaceTaskVisibilityStatus) => {
  if (value === 'ENDED') return 'secondary'
  if (value === 'APPROVED_VISIBLE') return 'success'
  if (value === 'APPROVED_HIDDEN') return 'warning'
  if (value === 'REJECTED') return 'error'
  return 'info'
}
