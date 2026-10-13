// 「等你处理 / 卡住了」那一列里，一行「关谁的事」（比如卡住的卡由谁验收）要画成
// 一张脸 + 一个名，不是一串 handle。这一段钉的就是那颗身份真的落在了副标题那一行，
// 而且没有 `who` 的行不多画一张脸。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import AdminActionList from './AdminActionList.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
})

const plugins = [createVuetify({ components, directives }), i18n]

describe('AdminActionList · 一行「关谁的事」', () => {
  it('给了 who 就画共用身份：脸 + 名，不只是一串 handle', () => {
    const { container } = render(AdminActionList, {
      props: {
        title: '卡住了',
        empty: '今天没有卡住的事',
        rows: [
          {
            id: 'c1',
            title: '合并 #1',
            subtitle: 'need_review',
            who: { handle: 'lin', name: '林一', avatarId: 9, agent: false },
          },
        ],
      },
      global: { plugins },
    })

    const sub = container.querySelector('.aal__sub') as HTMLElement
    expect(sub.querySelector('.identity-view'), '副标题那一行没画身份').not.toBeNull()
    expect(sub.querySelector('.identity-view__name')?.textContent).toContain('林一')
    expect(sub.querySelector('img')?.getAttribute('src')).toContain('/avatars/9')
    // 卡住的码和身份同一行，截断落在文字那一段（`.aal__sub-text`）。
    expect(sub.querySelector('.aal__sub-text')?.textContent).toContain('need_review')
  })

  it('没有 who 的行不凭空画一张脸', () => {
    const { container } = render(AdminActionList, {
      props: {
        title: '任务',
        empty: '没有',
        rows: [{ id: 't1', title: '合并 #2', subtitle: '任务' }],
      },
      global: { plugins },
    })

    expect(container.querySelector('.identity-view')).toBeNull()
    expect(container.querySelector('.aal__sub-text')?.textContent).toContain('任务')
  })
})
