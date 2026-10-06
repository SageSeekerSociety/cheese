/**
 * 提交反馈那一页的**画面那一半**。
 *
 * 这一份钉的是容器和画面之间的那道缝：草稿是**只读**传进来的，改哪一栏都只往上发事件，
 * 这一层自己一个字都不改。缝漏了的表现是「字打进去了、松手就弹回去」或者「改了一栏、
 * 草稿没动」—— 两种在画面上都看不出是这里坏了（前者像卡顿，后者像没保存）。
 *
 * 表单那些事件（patch / add-tag / remove-tag / discard-draft / submit）这一层**没有声明**，
 * 它们是 `$attrs` 透过去的，所以这里用 `vi.fn()` 接：`emitted()` 记的是**这一个组件自己**
 * 发出来的事件，透传的那些不算它的（真断了的话两边都收不到，正好也是这条用例要钉的）。
 * `cancel` 是这一页自己的事件（页头的返回箭头和表单里那颗取消是同一件事），它照常走
 * `emitted()`。
 */
import type { Component } from 'vue'
import type { FeedbackKind } from '@/cx_types'
import type { FeedbackDraft } from '@/stores/feedback'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FeedbackSubmitPageView from './FeedbackSubmitPageView.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

function draft(extra: Partial<FeedbackDraft> = {}): FeedbackDraft {
  return {
    kind: 'bug',
    title: '',
    body: '',
    repro: '',
    expectation: '',
    tags: [],
    attachContext: false,
    visibility: 'public',
    ...extra,
  }
}

/** 画面要的那几栏。默认是一张**空草稿**：这样每条用例只为自己多说的那一件负责。 */
type ViewProps = {
  draft: FeedbackDraft
  kinds: FeedbackKind[]
  restoredNotice: boolean
  askRepro: boolean
  askExpectation: boolean
  tagSuggestions: string[]
  submitting: boolean
  error: string | null
  canSubmit: boolean
}

function mount(extra: Partial<ViewProps> = {}) {
  const handlers = {
    onPatch: vi.fn(),
    onAddTag: vi.fn(),
    onRemoveTag: vi.fn(),
    onDiscardDraft: vi.fn(),
    onSubmit: vi.fn(),
  }
  const props: ViewProps = {
    draft: draft(),
    kinds: ['bug', 'suggestion', 'other'],
    restoredNotice: false,
    askRepro: true,
    askExpectation: true,
    tagSuggestions: [],
    submitting: false,
    error: null,
    canSubmit: false,
    ...extra,
  }
  // `i18n` 不是可选的：表单里的 `BaseField` 自己 `useI18n()`。
  const utils = render(FeedbackSubmitPageView as Component, {
    props: { ...props, ...handlers },
    global: { plugins: [vuetify, i18n] },
  })
  return { ...utils, ...handlers }
}

/** 提交按钮：`type="submit"` 那一颗。按名字找不行 —— 页面标题和按钮上是同一句话。
 *  收 `Element`（而不是 `HTMLElement`）：挂载出来的 `container` 就是 `Element`，这里
 *  也只用到 `querySelector`，两样都在 `Element` 上。 */
function submitButton(container: Element): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>('button[type="submit"]')
  if (!button) throw new Error('这一页上没有提交按钮')
  return button
}

beforeEach(() => setLocale('zh-CN'))

describe('提交反馈页 · 画面', () => {
  it('传进来的草稿画在各栏上，改一栏只往上报一次', async () => {
    const given = draft({ title: '导出要四十秒', body: '每次都要重跑全量聚合。', tags: ['导出'] })
    const { container, onPatch } = mount({ draft: given })

    const title = container.querySelector<HTMLInputElement>('#sb-title')!
    const body = container.querySelector<HTMLTextAreaElement>('#sb-body')!
    expect(title.value).toBe('导出要四十秒')
    expect(body.value).toBe('每次都要重跑全量聚合。')

    await fireEvent.update(title, '导出要四十秒（缓存）')

    expect(onPatch).toHaveBeenCalledTimes(1)
    expect(onPatch).toHaveBeenCalledWith({ title: '导出要四十秒（缓存）' })
    // 传进来的那份草稿没被这一层动过：写回去是外面那一半的事。
    expect(given.title).toBe('导出要四十秒')
  })

  it('类型那一排改的是草稿里的 kind', async () => {
    const { getByRole, onPatch } = mount()

    await fireEvent.click(getByRole('radio', { name: '建议' }))

    expect(onPatch).toHaveBeenCalledWith({ kind: 'suggestion' })
  })

  it('可见范围是一次改一档的 patch', async () => {
    const { container, onPatch } = mount()

    const priv = container.querySelector<HTMLInputElement>('input[type="radio"][value="private"]')!
    await fireEvent.update(priv, 'private')

    expect(onPatch).toHaveBeenCalledWith({ visibility: 'private' })
  })

  it('标签：回车加一个、候选点一下加一个、芯片上的叉去掉一个', async () => {
    const { container, getByRole, onAddTag, onRemoveTag } = mount({
      draft: draft({ tags: ['登录'] }),
      tagSuggestions: ['移动端'],
    })

    const tagInput = container.querySelector<HTMLInputElement>('#sb-tag-input')!
    await fireEvent.update(tagInput, '后台')
    await fireEvent.keyDown(tagInput, { key: 'Enter' })
    expect(onAddTag).toHaveBeenCalledTimes(1)
    expect(onAddTag).toHaveBeenCalledWith('后台')

    // 半截的词不算标签：空输入按回车什么都不发。
    await fireEvent.update(tagInput, '   ')
    await fireEvent.keyDown(tagInput, { key: 'Enter' })
    expect(onAddTag).toHaveBeenCalledTimes(1)

    await fireEvent.click(getByRole('button', { name: '移动端' }))
    expect(onAddTag).toHaveBeenCalledTimes(2)
    expect(onAddTag).toHaveBeenLastCalledWith('移动端')

    await fireEvent.click(getByRole('button', { name: '移除标签 登录' }))
    expect(onRemoveTag).toHaveBeenCalledWith('登录')
  })

  it('按类型出现的那两栏跟着 props 走，不是看草稿里有没有内容', () => {
    const { container } = mount({ askRepro: false, askExpectation: false })

    expect(container.querySelector('#sb-repro')).toBeNull()
    expect(container.querySelector('#sb-expectation')).toBeNull()
  })

  it('提交按钮跟着 canSubmit 走，交上去的是 submit', async () => {
    const off = mount({ canSubmit: false })
    expect(submitButton(off.container).disabled).toBe(true)
    expect(off.onSubmit).not.toHaveBeenCalled()

    const on = mount({ canSubmit: true })
    const submit = submitButton(on.container)
    expect(submit.disabled).toBe(false)
    // 按 `type="submit"` 那颗按钮就是交这一张表单（jsdom 不替浏览器做这一步，所以这里
    // 直接交表单本身 —— 门槛在按钮上、动作在表单上，两件分开钉）。
    await fireEvent.submit(on.container.querySelector('form.sb-form')!)
    expect(on.onSubmit).toHaveBeenCalledTimes(1)
  })

  it('两个出口都报 cancel：页头的返回箭头和表单里那颗取消', async () => {
    const { container, getByRole, emitted } = mount()

    await fireEvent.click(getByRole('button', { name: '返回' }))
    await fireEvent.click(container.querySelector<HTMLButtonElement>('.sb-actions button')!)

    expect(emitted().cancel).toHaveLength(2)
  })

  it('恢复提示画出来，「丢弃草稿」报 discard-draft', async () => {
    const plain = mount()
    expect(plain.queryByText('已恢复上次没写完的草稿')).toBeNull()

    const restored = mount({ restoredNotice: true })
    expect(restored.getByText('已恢复上次没写完的草稿')).toBeTruthy()
    await fireEvent.click(restored.getByRole('button', { name: '丢弃草稿' }))
    expect(restored.onDiscardDraft).toHaveBeenCalledTimes(1)
  })

  it('服务端的原话原样画在提交按钮上方', () => {
    const { getByText } = mount({ error: '这一条已经办完了' })

    expect(getByText('这一条已经办完了')).toBeTruthy()
  })
})
