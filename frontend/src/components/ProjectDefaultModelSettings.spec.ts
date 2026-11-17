// 项目主模型：只吃 props、只往上发事件（A 级）。
//
// 取数与发请求在 `composables/useProjectDefaultModelSettings.ts`（那里另有一份行为测试）。
// 编辑草稿（还没保存的下拉值）留在这里，是这一件自己一眼看得见的事 —— 下面几条验的就是
// 「选中 → 允许保存 → 报上去」和「改回自动」。
import type { ProjectDefaultModel } from '../api'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '../i18n'

import ProjectDefaultModelSettings from './ProjectDefaultModelSettings.vue'

const UI = { plugins: [createVuetify({ components, directives })] }

const choices = [
  { id: 'sonnet', label: 'Claude Sonnet 5', description: '', default: false, allowed: true, requires_plan: null },
  {
    id: 'deepseek-flash',
    label: 'DeepSeek V4.1 Flash',
    description: '',
    default: false,
    allowed: true,
    requires_plan: null,
  },
]

function state(overrides: Partial<ProjectDefaultModel> = {}): ProjectDefaultModel {
  return {
    model: null,
    deployment_default: 'sonnet',
    automatic: {
      conversation: { id: 'kimi-k3', label: 'Kimi K3' },
      task: { id: 'deepseek-flash', label: 'DeepSeek Flash' },
      subagent: { id: 'deepseek-flash', label: 'DeepSeek Flash' },
    },
    choices,
    ...overrides,
  }
}

function mount(
  value: ProjectDefaultModel | null,
  props: { loadError?: string; saving?: boolean; saved?: boolean; saveError?: string } = {}
) {
  return render(ProjectDefaultModelSettings, {
    props: { state: value, loadError: '', saving: false, saved: false, saveError: '', ...props },
    global: UI,
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal('devicePixelRatio', 1)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('names the models automatic runs on when no main model is set', async () => {
  const view = mount(state())
  expect(await view.findByText('自动（对话 Kimi K3，任务 DeepSeek Flash，分身 DeepSeek Flash）')).toBeTruthy()
  expect(view.queryByRole('button', { name: '改回自动' })).toBeNull()
})

it('reports a selected main model and shows it after a reload', async () => {
  const view = mount(state())
  const save = await view.findByRole('button', { name: '保存' })
  expect(save.hasAttribute('disabled')).toBe(true)
  await fireEvent.mouseDown(await view.findByRole('combobox', { name: '项目主模型' }))
  await fireEvent.click(await view.findByRole('option', { name: 'DeepSeek V4.1 Flash' }))
  await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
  await fireEvent.click(save)
  expect(view.emitted('save')).toEqual([['deepseek-flash']])
  view.unmount()

  const reloaded = mount(state({ model: 'deepseek-flash' }))
  expect(await reloaded.findByText('DeepSeek V4.1 Flash')).toBeTruthy()
})

it('shows automatic while staging a reset and reports null', async () => {
  const view = mount(state({ model: 'deepseek-flash' }))
  await fireEvent.click(await view.findByRole('button', { name: '改回自动' }))
  expect(await view.findByText(/^自动（对话 Kimi K3/)).toBeTruthy()
  expect(view.emitted('save')).toBeUndefined()
  await fireEvent.click(view.getByRole('button', { name: '保存' }))
  expect(view.emitted('save')).toEqual([[null]])
})

it('chooses automatic from the list', async () => {
  const view = mount(state({ model: 'deepseek-flash' }))
  await fireEvent.mouseDown(await view.findByRole('combobox', { name: '项目主模型' }))
  await fireEvent.click(await view.findByRole('option', { name: /^自动（对话 Kimi K3/ }))
  await fireEvent.click(view.getByRole('button', { name: '保存' }))
  expect(view.emitted('save')).toEqual([[null]])
})

it('names the plan a model needs and does not let it be chosen', async () => {
  const view = mount(
    state({
      deployment_default: 'deepseek-flash',
      choices: [
        {
          id: 'sonnet',
          label: 'Claude Sonnet 5',
          description: '',
          default: false,
          allowed: false,
          requires_plan: 'Reserve',
        },
        {
          id: 'deepseek-flash',
          label: 'DeepSeek V4.1 Flash',
          description: '',
          default: false,
          allowed: true,
          requires_plan: null,
        },
      ],
    })
  )
  const save = await view.findByRole('button', { name: '保存' })
  await fireEvent.mouseDown(await view.findByRole('combobox', { name: '项目主模型' }))
  const option = await view.findByRole('option', { name: /Claude Sonnet 5/ })
  expect(option.textContent).toContain('Reserve')
  await fireEvent.click(option)
  expect(save.hasAttribute('disabled')).toBe(true)
  expect(view.emitted('save')).toBeUndefined()
})

it('shows a saved model that is no longer offered as unavailable and lets another be chosen', async () => {
  const view = mount(state({ model: 'opus', deployment_default: 'deepseek-flash' }))
  const save = await view.findByRole('button', { name: '保存' })
  expect(view.getByText('opus')).toBeTruthy()
  expect(view.queryByText('DeepSeek V4.1 Flash')).toBeNull()
  expect(view.getByRole('button', { name: '改回自动' })).toBeTruthy()
  await fireEvent.mouseDown(await view.findByRole('combobox', { name: '项目主模型' }))
  const saved = await view.findByRole('option', { name: /opus/ })
  expect(saved.textContent).toContain('已不可用')
  await fireEvent.click(await view.findByRole('option', { name: 'DeepSeek V4.1 Flash' }))
  await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
  await fireEvent.click(save)
  expect(view.emitted('save')).toEqual([['deepseek-flash']])
})

it('shows the save receipt and the reason a save failed', async () => {
  const view = mount(state(), { saved: true })
  expect(await view.findByText('已保存')).toBeTruthy()

  const failed = mount(state({ model: 'deepseek-flash' }), { saveError: '存不进去' })
  expect(await failed.findByText('保存失败：存不进去')).toBeTruthy()
})
