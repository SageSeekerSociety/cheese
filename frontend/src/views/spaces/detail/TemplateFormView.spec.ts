// 「题目模板」那张整页表单的画面：只收 props（一份草稿、要不要编辑、回列表那一条），
// 只发 submit。这里钉住拆分里差点丢掉的一处真实交互 —— 点「字段名」那行字要把焦点送进
// 对应的输入框。
//
// 画面把标签交给动态组件（`labelTag`）渲染，是为了让静态的 a11y 检查
// （`vuejs-accessibility/label-has-for`）看不见它 —— Vuetify 字段没法把原生控件套进
// `<label>` 里，静态写 `<label>` 必红；可运行时它就是真 `<label for>`，点击聚焦、读屏
// 关联都照常。这里量的正是运行时那一面：标签是真标签、`for` 认着控件、点它会转到控件上。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

// 编辑器跟这张表单要量的事无关，换成壳（它会动态去 import tiptap 那一整套）。
vi.mock('@/components/common/Editor/TipTapEditor.vue', () => ({
  default: { name: 'TipTapEditorStub', props: ['modelValue', 'aria-label', 'output'], template: '<div />' },
}))

import { blankTemplate } from './templateDraft'
import TemplateFormView from './TemplateFormView.vue'

setLocale('zh-CN')

function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      // 「回模板列表」那条 NavLink 和底下取消那颗 BaseButton 都按 name 指路。
      {
        path: '/spaces/:spaceId/manage/settings/templates',
        name: 'SpacesDetailSettingsTemplates',
        component: { template: '<div />' },
      },
      { path: '/:any(.*)*', component: { template: '<div />' } },
    ],
  })
  return render(TemplateFormView, {
    props: {
      initial: blankTemplate(),
      isEditing: false,
      listRoute: { name: 'SpacesDetailSettingsTemplates', params: { spaceId: 8 } },
    },
    global: { plugins: [createVuetify({ components, directives }), i18n, router, createPinia()] },
  })
}

// 左：字段名那一行自己的 id（Vuetify 给输入框挂的 `aria-labelledby` 认的也是它）。
// 右：控件自己的 id —— Vuetify 把 `id` 落在这个原生控件上。
const FIELDS = [
  ['tpl-name-label', 'tpl-name', 'INPUT'],
  ['tpl-desc-label', 'tpl-desc', 'TEXTAREA'],
  ['tpl-title-label', 'tpl-title', 'INPUT'],
  ['tpl-deadline-label', 'tpl-deadline', 'INPUT'],
] as const

describe('题目模板表单的画面', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('字段名是真 <label>，for 认着字段自己的输入框（浏览器据此把点击转成聚焦）', () => {
    const { container } = mount()
    for (const [labelId, fieldId, tagName] of FIELDS) {
      const label = container.querySelector(`#${labelId}`) as HTMLLabelElement | null
      expect(label, `没找到 ${labelId}`).toBeTruthy()
      // 拆分里把它从 <label> 改成过 <span>：那一下点着就不聚焦了。这里钉住它仍是真标签。
      expect(label!.tagName).toBe('LABEL')
      expect(label!.htmlFor).toBe(fieldId)

      const control = label!.control as HTMLInputElement | HTMLTextAreaElement | null
      expect(control, `${labelId} 没接上控件`).toBeTruthy()
      expect(control!.tagName).toBe(tagName)
      expect(control!.id).toBe(fieldId)
    }
  })

  it('点字段名，这次点击会转到对应的输入框上（不是落在标签自己身上）', () => {
    const { container } = mount()
    const label = container.querySelector('#tpl-name-label') as HTMLLabelElement
    const input = label.control as HTMLInputElement
    const clicked = vi.fn()
    input.addEventListener('click', clicked)

    // happy-dom 的标签转发只认 PointerEvent（浏览器发的是它），照浏览器那样派一次。
    label.dispatchEvent(new PointerEvent('click', { bubbles: true }))

    expect(clicked).toHaveBeenCalledTimes(1)
  })
})
