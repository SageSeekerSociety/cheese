// 发送键那颗头像说「这条会交给谁」。一条消息给正文里点到的**每一位**队友各记一条投递，
// 头像只叠得下两位、那句话却得说全：少列一位就是承诺一件不会发生的事。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import ComposerSummon from './ComposerSummon.vue'

import { setLocale } from '@/i18n'

const A = { handle: 'cheese-t1', label: '芝士' }
const B = { handle: 'cheese-t2', label: '芝士O' }
const C = { handle: 'cheese-t3', label: '芝士米' }
const Mates = [A, B, C]

function mount(receivers: typeof Mates) {
  setLocale('zh-CN')
  const view = render(ComposerSummon as unknown as Component, {
    props: {
      mates: Mates,
      shown: receivers.slice(0, 2),
      receivers,
      on: true,
      fixed: false,
      ready: true,
      busy: [],
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  const button = view.container.querySelector<HTMLButtonElement>('.summon__btn')!
  return { view, tip: button.getAttribute('aria-label') ?? '' }
}

describe('这条会交给谁：头像上的字说全', () => {
  it('两位时说两位，并说清每位各起一轮', () => {
    const { view, tip } = mount([A, B])
    expect(tip).toContain('芝士、芝士O')
    expect(tip).toContain('各起一轮')
    view.unmount()
  })

  // 头像只叠得下两位，第三位也得说出来——不然屏幕说交给两位，实际叫三位。
  it('三位时头像仍只画两位，字里三位都在', () => {
    const { view, tip } = mount([A, B, C])
    expect(view.container.querySelectorAll('.summon__stack .avatar, .summon__stack svg').length).toBeGreaterThan(0)
    expect(tip).toContain('芝士、芝士O、芝士米')
    view.unmount()
  })
})
