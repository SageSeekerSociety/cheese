import type { SpaceLearningExcerpt } from '@/network/api/spaces/types'

import { describe, expect, it } from 'vitest'

import { buildSourceLink, dedupeBlockIds, formatLearningTime, knowledgePointLabel } from '../helpers'
import {
  buildAnalyticsApiParams,
  buildLearningQueueParams,
  normalizeAnalyticsQuery,
  serializeAnalyticsQuery,
} from '../utils'

const NOW = new Date('2026-03-19T12:00:00.000Z').getTime()

const excerpt = (over: Partial<SpaceLearningExcerpt> = {}): SpaceLearningExcerpt => ({
  blockId: 'block-1',
  topicId: 'topic-1',
  projectId: 'project-1',
  student: 'zhangsan',
  studentName: '张三',
  topicTitle: '第一次作业',
  createdAt: new Date('2026-03-01T10:00:00.000Z').getTime(),
  quote: '这个循环为什么停不下来',
  knowledgePoint: '循环',
  ...over,
})

describe('学习的筛选', () => {
  it('成员与知识点进出地址栏', () => {
    const query = normalizeAnalyticsQuery({ student: 'zhangsan', knowledgePoint: '7' }, NOW)

    expect(query.student).toBe('zhangsan')
    expect(query.knowledgePoint).toBe(7)
    expect(serializeAnalyticsQuery(query)).toMatchObject({ student: 'zhangsan', knowledgePoint: '7' })
  })

  it('清掉筛选时不留下空参数', () => {
    const query = normalizeAnalyticsQuery({ student: '', knowledgePoint: 'x' }, NOW)

    expect(query.student).toBeUndefined()
    expect(query.knowledgePoint).toBeUndefined()
    expect(serializeAnalyticsQuery(query)).not.toHaveProperty('student')
    expect(serializeAnalyticsQuery(query)).not.toHaveProperty('knowledgePoint')
  })

  it('学习只带时间 / 成员 / 知识点，不把分类和题目审批状态带过去', () => {
    const filters = normalizeAnalyticsQuery(
      {
        from: '2025-09-20',
        to: '2026-03-19',
        categoryId: '9',
        taskApproved: 'NONE',
        student: 'zhangsan',
        knowledgePoint: '7',
      },
      NOW
    )

    expect(buildAnalyticsApiParams('learning', filters)).toEqual({
      from: new Date('2025-09-20T00:00:00.000Z').getTime(),
      to: new Date('2026-03-19T23:59:59.999Z').getTime(),
      student: 'zhangsan',
      knowledgePoint: 7,
    })
  })

  it('队列不认知识点，只发时间与成员', () => {
    const filters = normalizeAnalyticsQuery({ from: '2025-09-20', to: '2026-03-19', knowledgePoint: '7' }, NOW)

    expect(buildLearningQueueParams(filters)).toEqual({
      from: new Date('2025-09-20T00:00:00.000Z').getTime(),
      to: new Date('2026-03-19T23:59:59.999Z').getTime(),
    })
  })
})

describe('学习的展示与坐标', () => {
  it('没归类的发言写成「未归类」', () => {
    expect(knowledgePointLabel(null)).toBe('未归类')
    expect(knowledgePointLabel('循环')).toBe('循环')
  })

  it('时间用本地可读格式，缺了就写暂无', () => {
    expect(formatLearningTime(new Date('2026-03-01T10:00:00.000Z').getTime())).toMatch(/^2026-03-\d{2} \d{2}:\d{2}$/)
    expect(formatLearningTime(null)).toBe('暂无')
  })

  it('勾选顺序就是讲次顺序，所以只去重不排序', () => {
    expect(dedupeBlockIds(['b2', 'b1', 'b2', 'b3'])).toEqual(['b2', 'b1', 'b3'])
  })

  it('点回原文带齐项目 / 话题 / 消息三样坐标', () => {
    expect(buildSourceLink(excerpt())).toEqual({
      name: 'workspace-topic',
      params: { projectId: 'project-1', topicId: 'topic-1' },
      query: { blockId: 'block-1' },
    })
  })
})
