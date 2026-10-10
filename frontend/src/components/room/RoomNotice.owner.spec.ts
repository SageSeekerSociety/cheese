/** 一行提示后面「谁在处理」的尾标，只说现在：这件事已经往下走了，旧的那一行不再说它。 */
import type { Component } from 'vue'
import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../../i18n'
import { collapseNotices } from '../../lib/platformNotice'

import RoomNotice from './RoomNotice.vue'

const vuetify = createVuetify({ components, directives })

let at = 0
function notice(id: string, content: string, eventType: string, who: string): Block {
  at += 1
  return {
    id,
    conversation_id: 'task-1',
    kind: 'event',
    author_type: 'platform',
    author: 'accept',
    content,
    refs: [],
    created_at: `2026-10-07T08:0${at}:00Z`,
    meta: { source: 'accept', event_type: eventType, severity: 'info', who, detail: '展开看', detail_label: null },
  } as unknown as Block
}

/** 每一行画出来、读者看到的那一句（正文 + 尾标）。 */
function seen(blocks: Block[]): string[] {
  return collapseNotices(blocks).map((row) => {
    const { container } = render(RoomNotice as Component, {
      props: {
        block: row.block,
        notice: row.notice!,
        run: row.run,
        agent: { name: 'Nova', handle: 'nova-a1' },
        time: '16:05',
        agentName: 'Nova',
        refs: { mentionNames: {}, topicTitles: {} },
      },
      global: { plugins: [vuetify] },
    })
    return container.querySelector('summary')!.textContent!.replace(/\s+/g, ' ').trim()
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  at = 0
})

describe('提示行的尾标', () => {
  it('退回之后芝士重新递了卡，退回那一行不再说它正在处理', () => {
    const lines = seen([
      notice('r', 'alice 退回了验收卡', 'card_rejected', 'cheese'),
      notice('f', '递了验收卡，等 alice 采纳', 'card_filed', 'human'),
    ])

    expect(lines[0]).toContain('alice 退回了验收卡')
    expect(lines[0]).not.toContain('正在处理')
    expect(lines[1]).toContain('等人处理')
  })

  it('采纳合并之后，「可以合并了」那一行不再说等人处理', () => {
    const lines = seen([
      notice('r', 'alice 退回了验收卡', 'card_rejected', 'cheese'),
      notice('m', 'PR #1 可以合并了，等 alice 采纳', 'accept_ready', 'human'),
      notice('d', 'alice 采纳了，PR #1 已合并', 'accept_done', 'platform'),
    ])

    expect(lines[0]).not.toContain('正在处理')
    expect(lines[1]).toContain('PR #1 可以合并了')
    expect(lines[1]).not.toContain('等人处理')
    expect(lines[2]).toContain('平台已处理')
  })

  it('一位队友这一轮出的事不归卡管，后面卡又往下走了，它照常说归谁', () => {
    const lines = seen([
      notice('t', 'Nova 这一轮失败了', 'turn_failed', 'human'),
      notice('f', '递了验收卡，等 alice 采纳', 'card_filed', 'human'),
    ])

    expect(lines[0]).toContain('等人处理')
  })

  it('还没人接手的最新一行照常说归谁', () => {
    const lines = seen([notice('r', 'alice 退回了验收卡', 'card_rejected', 'cheese')])

    expect(lines[0]).toContain('Nova正在处理')
  })
})
