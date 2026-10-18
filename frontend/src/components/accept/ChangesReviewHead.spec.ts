/** 审阅重点：交付的人写的那几条，在卡上按一份编号列出来。 */
import type { Component } from 'vue'
import type { AcceptCard } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
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

function withCredit(ownerCredit: 'none' | 'expired' | 'ok' | null) {
  return render(ChangesReviewHead as unknown as Component, {
    props: acceptHeadProps({
      prChecks: { available: true, pr_number: 7, checks: [], owner_credit: ownerCredit },
    }),
    global: { plugins: [createVuetify({ components, directives })], stubs: ['router-link'] },
  })
}

describe('负责人自己的 PR 由 AI 队友创建时', () => {
  it('还没关联 GitHub：给一个就地关联的入口', async () => {
    const { container, emitted } = withCredit('none')
    const line = container.querySelector('[data-testid="owner-credit"]')
    expect(line).not.toBeNull()
    await fireEvent.click(line!.querySelector('button')!)
    expect(emitted()['link-github']).toHaveLength(1)
  })

  it('授权失效：入口是重新授权', async () => {
    const { container, emitted } = withCredit('expired')
    await fireEvent.click(container.querySelector('[data-testid="owner-credit"] button')!)
    expect(emitted()['link-github']).toHaveLength(1)
  })

  it('已经关联：只说结果，没有可点的', () => {
    const { container } = withCredit('ok')
    expect(container.querySelector('[data-testid="owner-credit"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="owner-credit"] button')).toBeNull()
  })

  it('不是负责人、或者 PR 是他自己开的：这一行不出现', () => {
    const { container } = withCredit(null)
    expect(container.querySelector('[data-testid="owner-credit"]')).toBeNull()
  })
})
