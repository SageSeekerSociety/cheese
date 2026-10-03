import type { AskGroupScope } from '../lib/askGroup'
import type { AskGroupState } from '../lib/askGroupState'

import { describe, expect, it } from 'vitest'

import { reactive } from 'vue'

import { emptyAskDraft } from '../lib/askState'

import { useAskTakeover } from './useAskTakeover'

function group(id: string, asked: string, answered = false): AskGroupState {
  const scope: AskGroupScope = { topic_id: 'room', asked_by: 'agent', id, members: [`${id}-q`], total: 1 }
  const blockId = `${id}-q`
  return reactive({
    scope,
    anchor: blockId,
    pending: null,
    busy: false,
    fresh: true,
    error: null,
    storageBlocked: false,
    confirm: false,
    conflict: false,
    unavailable: false,
    forms: {
      [blockId]: {
        draft: emptyAskDraft(),
        pending: null,
        editing: false,
        busy: false,
        fresh: true,
        saved: false,
        error: null,
        conflict: false,
        storageBlocked: false,
      },
    },
    data: {
      group: scope,
      settlement: null,
      receipt: null,
      blocks: [
        {
          id: blockId,
          topic_id: 'room',
          kind: 'message',
          author_type: 'participant',
          author: 'agent',
          content: `问 ${id}`,
          created_at: '2026-10-03T00:00:00Z',
          meta: {
            asked,
            options: [{ text: 'A' }, { text: 'B' }],
            ...(answered ? { answer_log: [{ kind: 'option', option: 'A', by: asked, at: '' }] } : {}),
          },
        },
      ],
    },
  } as never)
}

describe('提问接管输入框', () => {
  it('多组同时待答：接管最后出现的那一组，其余不丢', () => {
    const groups = { a: group('a', 'alice'), b: group('b', 'alice') }
    const t = useAskTakeover({ groups, viewer: () => 'alice' })
    expect(t.askTakeover.value?.scope.id).toBe('b')
  })

  it('收起一组之后，另一组照样能接管，计数不把它算进去', () => {
    const groups = { a: group('a', 'alice'), b: group('b', 'alice') }
    const t = useAskTakeover({ groups, viewer: () => 'alice' })
    t.dismissAsk(groups.b!.scope)
    expect(t.askTakeover.value?.scope.id).toBe('a')
    expect(t.askReturn.value).toBe(1)
  })

  it('全部收起之后靠 restoreAsk 一次收回，问题没有被永久藏掉', () => {
    const groups = { a: group('a', 'alice'), b: group('b', 'alice') }
    const t = useAskTakeover({ groups, viewer: () => 'alice' })
    t.dismissAsk(groups.b!.scope)
    t.dismissAsk(groups.a!.scope)
    expect(t.askTakeover.value).toBeNull()
    expect(t.askReturn.value).toBe(2)
    t.restoreAsk()
    expect(t.askTakeover.value).not.toBeNull()
  })

  it('不是问我的、以及已经答过的，都不接管', () => {
    const groups = { a: group('a', 'bob'), b: group('b', 'alice', true) }
    const t = useAskTakeover({ groups, viewer: () => 'alice' })
    expect(t.askTakeover.value).toBeNull()
    expect(t.askReturn.value).toBe(0)
  })
})
