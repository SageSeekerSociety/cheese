import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '../../i18n'

import TaskForm from './TaskForm.vue'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => {
  setLocale('zh-CN')
  // happy-dom 不给 visualViewport，而 Vuetify 的对话框定位会真的去读它，
  // 缺了会在浮层挂载时抛异常、弹窗内容根本画不出来。
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal('devicePixelRatio', 1)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  // v-dialog 传送到 body，cleanup 收不走，留着会污染下一个用例的汉字扫描。
  document.body.innerHTML = ''
})

const text = (element: Element) => (element.textContent ?? '').replace(/\s+/g, ' ')

// 词表 §5 第 4 条：团队/队伍/小队三个词的中文还没统一，那 7 条 team 键故意没有英文，
// 英文界面上会回落到中文。它们不是这次改动引入的，扫描时按字面剔掉，
// 其余的汉字一个都不许剩。
const PENDING_TEAM_FALLBACKS = ['小队']
const withoutPendingTeam = (page: string) =>
  PENDING_TEAM_FALLBACKS.reduce((acc, word) => acc.split(word).join(''), page)

// v-switch 不暴露 role=switch，按它所在 .v-input 里的标签文字找 input；
// 而且 Vuetify 把模型更新挂在原生 input 事件上（VSelectionControl.onInput），
// fireEvent.click 不会让它翻面。
async function toggleSwitch(root: Element, label: string, enabled: string) {
  const input = Array.from(root.querySelectorAll('input[type="checkbox"]')).find((element) =>
    element.closest('.v-input')?.textContent?.includes(label)
  ) as HTMLInputElement | undefined
  expect(input, `开关「${label}」`).toBeTruthy()
  input!.checked = !input!.checked
  await fireEvent.input(input!)
  await vi.waitFor(() => expect(text(root), '开关翻开后').toContain(enabled))
}

interface Rendered {
  baseElement: Element
  getByText(value: string): Element
}

// descriptionFormat 固定成 markdown：tiptap 那支会拉起整个富文本编辑器，
// 而这个用例要看的是表单外框的文案。
function mountForm(...markers: string[]) {
  const view = render(TaskForm, {
    props: {
      classificationTopics: [{ id: 1, name: 'Web' }],
      categories: [
        {
          id: 1,
          name: 'Web',
          description: null,
          displayOrder: 0,
          createdAt: 0,
          updatedAt: 0,
          archivedAt: null,
        },
      ],
      descriptionFormat: 'markdown' as const,
      // 必填项都填齐：表单校验过了才会走到实名那个确认弹窗。
      initialData: {
        name: 'Example challenge',
        submitterType: 'USER' as const,
        rank: 1,
        categoryId: 1,
        deadline: new Date('2026-06-01').getTime(),
        defaultDeadline: 30,
      },
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  return vi
    .waitFor(() => {
      const page = text(view.baseElement)
      for (const marker of markers) expect(page, marker).toContain(marker)
    })
    .then(() => ({ view, page: () => text(view.baseElement) }))
}

// 表单本身是有效的（initialData 把必填项填齐了），勾上实名开关再提交，
// 就会走到「实名信息隐私保护」那个确认弹窗。
async function openPrivacyDialog(
  view: Rendered,
  { switchLabel, enabled, submit, dialog }: { switchLabel: string; enabled: string; submit: string; dialog: string }
) {
  await toggleSwitch(view.baseElement, switchLabel, enabled)
  // 提交按钮是 type=submit，jsdom 里点它不会真的走表单提交，直接对 form 发 submit 事件。
  const form = view.baseElement.querySelector('form')
  expect(form, '表单').toBeTruthy()
  await fireEvent.submit(form!)
  expect(view.getByText(submit), '提交按钮').toBeTruthy()
  await vi.waitFor(() => expect(text(view.baseElement), '隐私弹窗').toContain(dialog))
}

const ZH_DIALOG = {
  switchLabel: '要求参与者提供实名信息',
  enabled: '您已选择要求实名信息',
  submit: '提交',
  dialog: '实名信息隐私保护',
}
const EN_DIALOG = {
  switchLabel: 'Require participants to provide their real name',
  enabled: 'You are requiring real-name details',
  submit: 'Submit',
  dialog: 'How we protect real-name details',
}

it('讲中文：每张卡片的标题和字段标签都是中文', async () => {
  const { page } = await mountForm('基本信息', '时间设置')
  const rendered = page()
  expect(rendered).toContain('分类标签')
  expect(rendered).toContain('实名信息要求')
  expect(rendered).toContain('视频链接')
  expect(rendered).toContain('参与者人数限制')
  expect(rendered).toContain('赛题详情（Markdown 格式）')
  expect(rendered).toContain('视频链接（选填）')
  expect(rendered).toContain('支持 Bilibili 视频链接')
  expect(rendered).toContain('天')
  expect(rendered).toContain('未启用实名认证要求，参与者可匿名参与此赛题')
  expect(rendered).toContain('提交')
  // 默认是个人参与，队伍那一块的文案按词表 §5 第 4 条先不动，也不该在这儿出现。
  expect(rendered).not.toContain('最小队伍人数')
})

it('讲中文：实名开关打开后，隐私弹窗整篇是中文', async () => {
  const { view, page } = await mountForm('基本信息')
  expect(page()).toContain('要求参与者提供实名信息')
  expect(page()).toContain('实名信息包括学生的真实姓名、学号、年级等信息')

  await openPrivacyDialog(view, ZH_DIALOG)
  const dialog = page()
  expect(dialog).toContain('为保护参与者隐私，我们对需要实名信息的赛题采取了多重保护措施：')
  expect(dialog).toContain('匿名参与')
  expect(dialog).toContain('用途限制')
  expect(dialog).toContain('加密存储')
  expect(dialog).toContain('访问记录')
  expect(dialog).toContain('信息使用场景')
  expect(dialog).toContain('身份验证')
  expect(dialog).toContain('项目认证')
  expect(dialog).toContain('评奖评优')
  expect(dialog).toContain('合规承诺')
  expect(dialog).toContain('了解并接受')
  expect(dialog).toContain('取消')
})

it('整个表单在英文下不留一个汉字', async () => {
  setLocale('en')
  const { view, page } = await mountForm('Basic info', 'Schedule')
  const rendered = page()
  expect(rendered).toContain('Classification')
  expect(rendered).toContain('Real-name requirement')
  expect(rendered).toContain('Video link')
  expect(rendered).toContain('Participant limit')
  expect(rendered).toContain('Challenge details (Markdown)')
  expect(rendered).toContain('Video link (optional)')
  expect(rendered).toContain('Bilibili video links are supported')
  expect(rendered).toContain('days after claiming the challenge')
  expect(rendered).toContain('Real-name verification is off, so participants can join this challenge anonymously')
  expect(rendered).toContain('Submit')
  expect(CJK.test(withoutPendingTeam(rendered)), rendered).toBe(false)

  await openPrivacyDialog(view, EN_DIALOG)
  const dialog = page()
  expect(dialog).toContain('Anonymous participation')
  expect(dialog).toContain('Encrypted storage')
  expect(dialog).toContain('Our commitment')
  expect(dialog).toContain('Understand and accept')
  expect(dialog).toContain('Cancel')
  expect(CJK.test(withoutPendingTeam(dialog)), dialog).toBe(false)
})

it('切成英文后，已经画出来的标题和按钮立刻跟着换', async () => {
  const { view, page } = await mountForm('基本信息')
  expect(page()).toContain('赛题详情（Markdown 格式）')

  setLocale('en')
  await vi.waitFor(() => expect(page(), '卡片标题').toContain('Basic info'))
  const switched = page()
  expect(switched).toContain('Schedule')
  expect(switched).toContain('Challenge details (Markdown)')
  expect(switched).toContain('Submit')
  expect(CJK.test(withoutPendingTeam(switched)), switched).toBe(false)

  await openPrivacyDialog(view, EN_DIALOG)
  expect(page()).toContain('Anonymous participation')
  expect(CJK.test(withoutPendingTeam(page())), page()).toBe(false)
})
