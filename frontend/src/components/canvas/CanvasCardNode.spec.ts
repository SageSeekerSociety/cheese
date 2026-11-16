// 画布上关联了任务的格子：写这件任务此刻的状态词，和全部任务里同一个词。
import type { Component } from 'vue'
import type { BoardPhrase } from '@/cx_types'
import type { CanvasCard } from '@/types/canvas'

import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import CanvasCardNode from './CanvasCardNode.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

function card(phrase: BoardPhrase): CanvasCard {
  return {
    id: 'c1',
    number: 1,
    content: '重建索引',
    x: 0,
    y: 0,
    width: null,
    height: null,
    task: {
      id: 't1',
      number: 12,
      title: '重建搜索索引',
      room_id: 'r1',
      column: 'building',
      phrase,
    },
    task_hidden: false,
    updated_by: 'alice',
    updated_at: '2026-10-21T00:00:00Z',
  }
}

describe('画布格子上的任务状态', () => {
  it('写任务的状态词', () => {
    const view = render(CanvasCardNode as Component, { props: { card: card('gate_running'), editable: false } })
    expect(view.container.textContent).toContain('待审阅')
  })

  it('按读者的语言写', () => {
    setLocale('en')
    const view = render(CanvasCardNode as Component, { props: { card: card('running'), editable: false } })
    expect(view.container.textContent).toContain('In progress')
  })
})
