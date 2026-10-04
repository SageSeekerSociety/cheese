/** BaseField 的全部意义是「控件自己做不到的那几件」：把标签的 for 认到控件上、把
 *  aria-describedby / aria-invalid / aria-required 交给控件、画必填标记和字数。这些
 *  都是**写错了不会报错、只是悄悄不对**的东西（Vuetify 自己就只挂 describedby、不挂
 *  aria-invalid），所以每一件都在这里钉死。 */
import { h } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import BaseField from './BaseField.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

/** 一个把插槽属性原样挂到原生 `<input>` 上的控件，正好是文档里那段用法。 */
function control(props: Record<string, unknown>) {
  return h('input', {
    id: props.id,
    'aria-describedby': props.describedby,
    'aria-invalid': props.invalid,
    'aria-required': props.required,
  })
}

function mount(props: Record<string, unknown> = {}) {
  return render(BaseField, {
    props,
    slots: { default: control },
    global: { plugins: [i18n] },
  })
}

describe('BaseField', () => {
  it('把标签的 for 认到控件上,而且同一个页面里两个实例的 id 不同', () => {
    const view = render(
      {
        render: () => [
          h(BaseField, { label: '标题' }, { default: control }),
          h(BaseField, { label: '标题' }, { default: control }),
        ],
      },
      { global: { plugins: [i18n] } }
    )
    const labels = Array.from(view.container.querySelectorAll('.base-field__label'))
    const inputs = Array.from(view.container.querySelectorAll('input')) as HTMLInputElement[]
    expect(labels).toHaveLength(2)
    expect(inputs[0].id).not.toBe('')
    expect(inputs[0].id).not.toBe(inputs[1].id)
    expect(labels[0].getAttribute('for')).toBe(inputs[0].id)
    expect(labels[1].getAttribute('for')).toBe(inputs[1].id)
  })

  it('id 可以由外面给', () => {
    const view = mount({ label: '标题', id: 'my-title' })
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.id).toBe('my-title')
    expect(view.getByText('标题').getAttribute('for')).toBe('my-title')
  })

  it('required 画一个 aria-hidden 的星号,读屏念到的是「必填」两个字', () => {
    const view = mount({ label: '标题', required: true })
    const star = view.container.querySelector('.base-field__req') as HTMLElement
    expect(star.getAttribute('aria-hidden')).toBe('true')
    expect(star.textContent).toBe('*')
    // 星号本身进不了无障碍树,但它旁边那句「必填」进——所以整体还是读得到的。
    expect(view.container.querySelector('.visually-hidden')?.textContent).toBe('必填')
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-required')).toBe('true')
  })

  it('optional 在标签后面跟一段「（选填）」', () => {
    const view = mount({ label: '简介', optional: true })
    expect(view.container.textContent).toContain('（选填）')
    // optional 不是必填,控件上不该有 aria-required。
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-required')).toBe('false')
  })

  it('hint 画出来,并把它的 id 挂进 aria-describedby', () => {
    const view = mount({ label: '标题', hint: '最多 20 个字' })
    const hint = view.getByText('最多 20 个字')
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-describedby')).toBe(hint.id)
  })

  it('error 画出来、invalid 为 true,error 的 id 也在 aria-describedby 里', () => {
    const view = mount({ label: '标题', error: '填写标题' })
    const error = view.getByText('填写标题')
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toContain(error.id)
  })

  it('没有报错时 invalid 是 false', () => {
    const view = mount({ label: '标题' })
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-invalid')).toBe('false')
  })

  it('counter 画成 current/max,并把它的 id 挂进 aria-describedby', () => {
    const view = mount({ label: '标题', counter: { current: 3, max: 20 } })
    const counter = view.container.querySelector('.base-field__counter') as HTMLElement
    expect(counter.textContent).toBe('3/20')
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-describedby')).toContain(counter.id)
  })

  it('提示、报错、字数一起给时,三个 id 都在 aria-describedby 里', () => {
    const view = mount({
      label: '标题',
      hint: '最多 20 个字',
      error: '填写标题',
      counter: { current: 21, max: 20 },
    })
    const input = view.container.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('aria-describedby')!.split(' ')).toHaveLength(3)
  })

  it('不给 label 时只围住控件,照样带提示和字数', () => {
    const view = mount({ hint: '最多 20 个字', counter: { current: 1, max: 20 } })
    expect(view.container.querySelector('.base-field__label')).toBeNull()
    expect(view.container.querySelector('input')).not.toBeNull()
    expect(view.getByText('1/20')).toBeTruthy()
  })
})
