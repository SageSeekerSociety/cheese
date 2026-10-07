/** 任务的「相关」：转出它的那句话照对话里的样子读，@ 过谁就写谁的名字，不露出 token。 */
import type { TaskRelated } from '@/types/taskOrigin'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskRelatedList from './TaskRelatedList.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

function related(content: string): TaskRelated {
  return {
    origin: {
      conversation_id: 'c1',
      room_id: 'r1',
      root: { block_id: 'b1', author: 'andy', content, created_at: '2026-10-07T07:52:42Z' },
      reply_count: 0,
    },
    materials: [],
  }
}

describe('TaskRelatedList', () => {
  it('reads a mention in the original message as the name it mentions', () => {
    const view = render(TaskRelatedList, {
      props: {
        related: related('<@cheese-d22f1886fd86> 请在项目里新建一个 hello.md'),
        memberNames: { andy: 'Andy', 'cheese-d22f1886fd86': 'Nova' },
      },
      global: { plugins: [vuetify] },
    })

    const quote = view.getByTestId('task-related').textContent ?? ''
    expect(quote).toContain('Andy：@Nova 请在项目里新建一个 hello.md')
    expect(quote).not.toContain('<@')
  })

  it('falls back to the handle for someone the roster does not name', () => {
    const view = render(TaskRelatedList, {
      props: { related: related('问一下 <@bob>'), memberNames: {} },
      global: { plugins: [vuetify] },
    })

    expect(view.getByTestId('task-related').textContent).toContain('andy：问一下 @bob')
  })
})
