// 一轮没做完的那一行：能重试的都给「重试」；「你们接入的 …余额用完了」那一行还带人去 AI 队友设置。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { agentSettingsTarget, platformNotice } from './platformNotice'

function failed(meta: Record<string, unknown>): Block {
  return {
    id: 'b1',
    conversation_id: 'room',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '这次没做完',
    created_at: '2026-11-01T08:00:00Z',
    meta: { event_type: 'turn_failed', retryable: true, ...meta },
  } as Block
}

describe('去 AI 队友设置', () => {
  it('项目自己接入的模型连接余额用完了：带人去项目设置里队友用哪份连接的那一栏', () => {
    expect(agentSettingsTarget(failed({ code: 'own_out_of_credit' }), 'p1')).toEqual({
      name: 'project-settings',
      params: { projectId: 'p1', section: 'agents' },
    })
  })

  it('调用模型出错这一类不归读的人去换连接，不带', () => {
    expect(agentSettingsTarget(failed({ code: 'model_error' }), 'p1')).toBeNull()
    expect(agentSettingsTarget(failed({}), 'p1')).toBeNull()
  })

  it('不知道是哪个项目时没有地方可去', () => {
    expect(agentSettingsTarget(failed({ code: 'own_out_of_credit' }), null)).toBeNull()
  })
})

describe('只有一句话的失败', () => {
  it('没有展开详情、但能重试的那一句，照样给「重试」', () => {
    const stopped = failed({ event_type: 'turn_timeout' })
    const notice = platformNotice(stopped)
    expect(notice?.mode === 'fold' && notice.retryable).toBe(true)
  })
})
