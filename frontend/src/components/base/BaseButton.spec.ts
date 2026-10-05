/** BaseButton 只做两件事：把「角色 + 大小」翻成 v-btn 的样子，其余属性原样交给 v-btn。
 *  这里锁住第二件：透传最容易在没人注意时坏掉，坏了也不会报错。 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import BaseButton from './BaseButton.vue'

const here = dirname(fileURLToPath(import.meta.url))

afterEach(cleanup)

function mount(props: Record<string, unknown>, attrs: Record<string, unknown> = {}, slot = '保存') {
  return render(BaseButton, {
    props,
    attrs,
    slots: { default: slot },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('BaseButton', () => {
  it('passes type, disabled, aria-label and class through to the button', () => {
    const view = mount({ kind: 'primary' }, { type: 'submit', disabled: true, 'aria-label': '提交表单', class: 'mt-2' })
    const button = view.getByRole('button', { name: '提交表单' })
    expect(button.getAttribute('type')).toBe('submit')
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(button.classList.contains('mt-2')).toBe(true)
    expect(button.classList.contains('base-btn--primary')).toBe(true)
  })

  it('renders a link when given href', () => {
    const view = mount({ kind: 'secondary' }, { href: 'https://example.com/a' }, '打开')
    const link = view.getByRole('link', { name: '打开' })
    expect(link.getAttribute('href')).toBe('https://example.com/a')
  })

  it('draws the icon and not the slot text for an icon-only button', () => {
    const view = mount({ icon: 'mdi-dots-horizontal' }, { 'aria-label': '更多' }, '不该出现')
    const button = view.getByRole('button', { name: '更多' })
    expect(button.querySelector('.mdi-dots-horizontal')).not.toBeNull()
    expect(button.textContent).not.toContain('不该出现')
  })

  it('maps roles to variants and sizes to Vuetify sizes', () => {
    const cases: [Record<string, unknown>, string[]][] = [
      [{ kind: 'primary' }, ['v-btn--variant-flat', 'bg-primary']],
      [{ kind: 'secondary' }, ['v-btn--variant-outlined']],
      [{ kind: 'ghost', size: 'sm' }, ['v-btn--variant-text', 'v-btn--size-small']],
      [{ kind: 'primary', size: 'lg' }, ['v-btn--size-large']],
      [{ kind: 'danger', solid: true }, ['v-btn--variant-flat', 'bg-error']],
    ]
    for (const [props, classes] of cases) {
      const button = mount(props).getByRole('button')
      for (const c of classes) expect(button.classList, `${JSON.stringify(props)} → ${c}`).toContain(c)
      cleanup()
    }
  })

  it('leaves the non-solid danger colour to --danger-ink instead of the !important text-error class', () => {
    const button = mount({ kind: 'danger' }, {}, '清空记录').getByRole('button')
    expect(button.classList.contains('text-error')).toBe(false)
    expect(button.classList.contains('base-btn--danger')).toBe(true)
  })

  it('passes density through for buttons that sit inside a field', () => {
    const button = mount(
      { icon: 'mdi-eye-outline', size: 'sm' },
      { density: 'comfortable', 'aria-label': '显示密码' }
    ).getByRole('button')
    expect(button.classList.contains('v-btn--density-comfortable')).toBe(true)
  })

  it('ignores solid on roles other than danger', () => {
    const button = mount({ kind: 'ghost', solid: true }).getByRole('button')
    expect(button.classList.contains('bg-error')).toBe(false)
  })

  it('触屏上自己把能点的范围撑到 44×44，不用每个调用处记着加 .tap-target', () => {
    // jsdom 没有布局引擎，也读不了 pointer: coarse，只能钉源码（同
    // AdminGrid.spec.ts 的「卡片模式的触发条件是容器宽度」那条）。注释先剥掉：
    // 注释里提到 pointer: coarse 和 44px 都不算数，钉的是真的那几条声明。
    const src = readFileSync(join(here, 'BaseButton.vue'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    const at = src.indexOf('@media (pointer: coarse)')
    expect(at, '撑开范围要挂在 pointer: coarse 上：鼠标下撑开会盖住并排的邻居').toBeGreaterThan(-1)
    const block = src.slice(at, src.indexOf('}', src.indexOf('}', at) + 1))
    expect(block).toContain('position: absolute')
    expect(block).toContain('width: max(100%, 44px)')
    expect(block).toContain('height: max(100%, 44px)')
  })
})
