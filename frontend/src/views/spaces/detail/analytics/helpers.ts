import type { RouteLocationNamedRaw } from 'vue-router'
import type { AnalyticsDistribution, AnalyticsDistributionItem, SpaceLearningExcerpt } from '@/network/api/spaces/types'

import dayjs from 'dayjs'

export const formatCount = (value?: number | null) => new Intl.NumberFormat('zh-CN').format(value || 0)

export const formatPercent = (value?: number | null, digits = 1) => `${((value || 0) * 100).toFixed(digits)}%`

/** 表里的日期：只到日，数字写法，中英文一样。 */
export const formatDate = (value: number) => dayjs(value).format('YYYY-MM-DD')

export const withDistributionPercent = (distribution?: AnalyticsDistribution | null) => {
  const items = distribution?.items || []
  const total = items.reduce((sum, item) => sum + item.count, 0)

  return items.map<AnalyticsDistributionItem>((item) => ({
    ...item,
    percentage: item.percentage ?? (total > 0 ? item.count / total : 0),
  }))
}

/**
 * 后端按状态码分组（APPROVED、NO_PARTICIPANTS、WITH_REAL_NAME …），画出来要换成人读的
 * 名字；分类、年级这类本来就是名字的原样留着。
 */
export const labelDistributionCodes = (
  items: AnalyticsDistributionItem[],
  t: (key: string) => string,
  te: (key: string) => boolean
) =>
  items.map((item) =>
    te(`spaces.analytics.distribution.${item.label}`)
      ? { ...item, label: t(`spaces.analytics.distribution.${item.label}`) }
      : item
  )

// ---------------------------------------------------------------------------
// 学习那一格: 没归类、时间、勾选顺序、原文坐标
// ---------------------------------------------------------------------------

/** 没挂到任何知识点上的发言与卡点 —— 后端把这一类回来成 null。 */
export const LEARNING_UNCLASSIFIED = '未归类'

export const knowledgePointLabel = (value?: string | null) => value || LEARNING_UNCLASSIFIED

export const formatLearningTime = (value?: null | number) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '暂无')

/**
 * 提纲里的讲次顺序 = 勾选顺序（后端按传过去的 blockIds 先后分节），所以这里**只
 * 去重、不排序** —— 重排一次就等于把管理员排好的讲次打乱。
 */
export const dedupeBlockIds = (blockIds: string[]) => [...new Set(blockIds)]

/**
 * 一条发言点回原文的坐标: 成员项目 + 话题，外加那条消息自己。
 *
 * `blockId` 一起带过去是因为它是后端给的坐标，房间页现在还没有「按消息定位」的
 * 能力（消息是分页加载的，锚点可能根本不在列表里），所以摘要在列表里就已经给
 * 全，点开是把人放进那个房间。
 */
export const buildSourceLink = (
  excerpt: Pick<SpaceLearningExcerpt, 'blockId' | 'projectId' | 'topicId'>
): RouteLocationNamedRaw => ({
  name: 'workspace-topic',
  params: { projectId: excerpt.projectId, topicId: excerpt.topicId },
  query: { blockId: excerpt.blockId },
})
