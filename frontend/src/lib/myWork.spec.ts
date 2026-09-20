import type { Project, Topic, WaitingItem } from '@/cx_types'
import type { Shell } from '@/lib/shell'
import type { WorkCard, WorkSignal } from './myWork'

import { describe, expect, it } from 'vitest'

import {
  activityParts,
  activityStamp,
  awaitingByProject,
  joinedSpaces,
  NO_SIGNAL,
  sortCards,
  topicsSignal,
  workGroups,
} from './myWork'

import { DEFAULT_SHELL } from '@/lib/shell'

const topic = (over: Partial<Topic>): Topic =>
  ({
    id: 't',
    project_id: 'p',
    parent_id: null,
    title: '房间',
    kind: 'chat',
    status: 'active',
    created_at: '2026-09-01T00:00:00Z',
    ...over,
  }) as Topic

const waiting = (projectId: string): WaitingItem => ({
  projectId,
  projectName: projectId,
  topicId: 't',
  topicTitle: '房间',
  taskId: null,
  taskTitle: null,
  displayStatus: '待我验收',
  reason: 'reviewer',
  at: '2026-09-01T00:00:00Z',
})

const card = (id: string, signal: Partial<WorkSignal>, project: Partial<Project> = {}): WorkCard => ({
  project: { id, name: id, created_at: '2026-09-01T00:00:00Z', ...project } as Project,
  signal: { ...NO_SIGNAL, ...signal },
  space: null,
})

describe('一个项目的动静', () => {
  it('数在跑的房间，并挑出最后活动时间——不看位置', () => {
    // 故意不按时间排序：它答的是「最新的那条」，不是「第一条」。
    const signal = topicsSignal([
      topic({ id: 'a', running: true, last_activity_at: '2026-09-10T00:00:00Z' }),
      topic({ id: 'b', running: false, last_activity_at: '2026-09-12T00:00:00Z' }),
      topic({ id: 'c', running: true, last_activity_at: '2026-09-11T00:00:00Z' }),
    ])
    expect(signal).toEqual({ running: 2, lastActivityAt: '2026-09-12T00:00:00Z' })
  })

  it('一个房间都没有时是「问不出来」，不是「刚刚」', () => {
    expect(topicsSignal([])).toEqual({ running: 0, lastActivityAt: null })
  })

  it('没有 last_activity_at 的行不参与挑最晚', () => {
    expect(topicsSignal([topic({ id: 'a' })]).lastActivityAt).toBeNull()
  })
})

describe('待我处理按项目分堆', () => {
  it('数每个项目点到我几件事', () => {
    expect(awaitingByProject([waiting('p1'), waiting('p1'), waiting('p2')])).toEqual({ p1: 2, p2: 1 })
  })

  it('一件事都没有时没有键', () => {
    expect(awaitingByProject([])).toEqual({})
  })
})

describe('排序与分组', () => {
  it('最近动过的在前，问不出来的一律排最后', () => {
    const cards = [
      card('old', { lastActivityAt: '2026-09-01T00:00:00Z' }),
      card('unknown', {}),
      card('new', { lastActivityAt: '2026-09-12T00:00:00Z' }),
    ]
    expect(sortCards(cards).map((c) => c.project.id)).toEqual(['new', 'old', 'unknown'])
  })

  it('并列时保持清单本来的顺序', () => {
    const cards = [
      card('a', { lastActivityAt: '2026-09-12T00:00:00Z' }),
      card('b', { lastActivityAt: '2026-09-12T00:00:00Z' }),
    ]
    expect(sortCards(cards).map((c) => c.project.id)).toEqual(['a', 'b'])
  })

  it('报告一个具体时刻，而不是一个数', () => {
    expect(activityStamp(card('a', { lastActivityAt: '2026-09-12T00:00:00Z' }).signal)).toBe(
      new Date('2026-09-12T00:00:00Z').getTime()
    )
    expect(activityStamp(NO_SIGNAL)).toBe(Number.NEGATIVE_INFINITY)
  })

  // 分组的依据是项目行上那个**壳字段**（服务端解析好的声明），不是身份、不是归属。
  const workbench: Shell = { ...DEFAULT_SHELL, name: 'workbench', terms: { project: '工作' } }
  const course: Shell = { ...DEFAULT_SHELL, name: 'course-student', terms: { project: '课程' } }

  it('按壳分组：同一个壳的项目在一组，没声明壳的落进 default', () => {
    const groups = workGroups([
      card('course-a', {}, { shell: course }),
      card('plain', {}),
      card('course-b', {}, { shell: course }),
      card('office', {}, { shell: workbench }),
    ])
    expect(groups.map((g) => g.key)).toEqual(['course-student', 'default', 'workbench'])
    expect(groups[0].cards.map((c) => c.project.id)).toEqual(['course-a', 'course-b'])
  })

  it('组跟着最近动过的那张卡走，不跟一张写死的壳清单走', () => {
    const groups = workGroups([
      card('course-old', { lastActivityAt: '2026-09-01T00:00:00Z' }, { shell: course }),
      card('office-new', { lastActivityAt: '2026-09-12T00:00:00Z' }, { shell: workbench }),
    ])
    expect(groups.map((g) => g.key)).toEqual(['workbench', 'course-student'])
  })

  // 服务端加第五个壳时这一页不发版：这一版前端没听说过的壳自己成一组，
  // 组名读它的词表。
  it('没听说过的壳也自成一组，不并进 default', () => {
    const fifth: Shell = { ...DEFAULT_SHELL, name: 'studio', terms: { project: '作品' } }
    const groups = workGroups([card('x', { lastActivityAt: '2026-09-12T00:00:00Z' }, { shell: fifth })])
    expect(groups.map((g) => g.key)).toEqual(['studio'])
    expect(groups[0].shell.terms.project).toBe('作品')
  })
})

describe('我加入的空间', () => {
  it('按卡片顺序去重，没有空间的项目跳过', () => {
    const spaces = joinedSpaces([
      { ...card('a', {}), space: { id: 7, name: '空间七' } },
      { ...card('b', {}), space: null },
      { ...card('c', {}), space: { id: 7, name: '空间七' } },
      { ...card('d', {}), space: { id: 9, name: '空间九' } },
    ])
    expect(spaces).toEqual([
      { id: 7, name: '空间七' },
      { id: 9, name: '空间九' },
    ])
  })
})

describe('卡片上那一行', () => {
  it('什么在跑、什么在等我、最近什么时候动过', () => {
    const parts = activityParts({ running: 2, awaiting: 1, lastActivityAt: new Date().toISOString() })
    expect(parts.map((p) => p.kind)).toEqual(['running', 'awaiting', 'activity'])
  })

  it('只有「最近动过」的时候就说这一句', () => {
    const parts = activityParts({ running: 0, awaiting: 0, lastActivityAt: new Date().toISOString() })
    expect(parts.map((p) => p.kind)).toEqual(['activity'])
  })

  // 空行和「没加载出来」长得一模一样，所以这一行永远有话说。
  it('三样都答不出来时说「还没开动」，不留空行', () => {
    expect(activityParts(NO_SIGNAL)).toEqual([{ kind: 'quiet' }])
  })
})
