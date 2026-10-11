/** 审阅重点：交付的人写的那几条，在卡上按一份编号列出来。 */
import type { Component } from 'vue'
import type { AcceptCard } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import ChangesReviewHead from './ChangesReviewHead.vue'

import { setLocale } from '@/i18n'
import { ACCEPT_ONE, acceptHeadProps } from '@/views/demo/catalogFixtures'

function focusShown(focus: string): string[] {
  const card = { ...ACCEPT_ONE, focus } as AcceptCard
  const { container } = render(ChangesReviewHead as unknown as Component, {
    props: acceptHeadProps({ card }),
    global: { plugins: [createVuetify({ components, directives })], stubs: ['router-link'] },
  })
  return [...container.querySelectorAll('ol li')].map((li) => li.textContent?.trim() ?? '')
}

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('zh-CN')
})

describe('审阅重点', () => {
  it('交付的人自己写了编号时，不再编第二遍', () => {
    expect(focusShown('1. 迁移会改通知表\n2、回执落点改成了任务\n（3）旧卡照旧能读')).toEqual([
      '迁移会改通知表',
      '回执落点改成了任务',
      '旧卡照旧能读',
    ])
  })

  it('写成「- 」列表的也一样', () => {
    expect(focusShown('- 迁移会改通知表\n• 回执落点改成了任务')).toEqual(['迁移会改通知表', '回执落点改成了任务'])
  })

  it('以数字开头的正文不当编号去掉', () => {
    expect(focusShown('1.5 倍的提速只在冷启动上\n2 个接口改了返回形状')).toEqual([
      '1.5 倍的提速只在冷启动上',
      '2 个接口改了返回形状',
    ])
  })
})
