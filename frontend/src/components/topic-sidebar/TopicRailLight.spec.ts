// 绿灯两档要画得出来：有活在往前走的那一盏在动，做完了没看过的那一盏不动。
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import TopicRailLight from './TopicRailLight.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

describe('侧栏行尾那盏灯', () => {
  it('呼吸绿戴 breathe 那一类，长绿不戴', () => {
    const breathing = render(TopicRailLight, {
      props: { light: { kind: 'green', breathe: true } },
    })
    expect(breathing.container.querySelector('.row-light--breathe')).not.toBeNull()
    expect(breathing.container.querySelector('.row-light')?.getAttribute('title')).toBe('有任务正在进行')

    const delivered = render(TopicRailLight, {
      props: { light: { kind: 'green', breathe: false } },
    })
    expect(delivered.container.querySelector('.row-light--breathe')).toBeNull()
    expect(delivered.container.querySelector('.row-light')?.getAttribute('title')).toBe('做完了，你还没看过')
  })
})
