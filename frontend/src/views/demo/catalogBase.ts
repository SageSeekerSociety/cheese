/**
 * 组件预览站里「基础组件」那一组：`src/components/base/` 下的件。
 *
 * 业务代码写按钮、弹窗、卡片时只用这一层（docs/design-system.md）。单放一份是因为
 * `catalog.ts` 离一千行的上限已经很近。条目的规矩见 `catalog.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'

const UI: CatalogNeed[] = ['vuetify']
const TEXT: CatalogNeed[] = ['i18n']

export const BASE_ENTRIES: CatalogEntry[] = [
  {
    id: 'base-field',
    title: 'BaseField',
    about: '一个控件的壳：标签在框外、必填/选填标记、提示、报错和字数，id 与 aria 从插槽属性交给控件。',
    file: 'src/components/base/BaseField.vue',
    component: BaseField,
    needs: TEXT,
    states: [
      {
        name: '标签在框外',
        note: '标签 13/500 --text，和 AccountField 一致。控件放默认插槽。',
        props: { label: '标题' },
        slot: '（控件）',
        expect: '标题',
      },
      {
        name: '必填',
        note: '一个 aria-hidden 的 *，加一句读屏才念的「必填」。',
        props: { label: '标题', required: true },
        slot: '（控件）',
        expect: '标题',
      },
      {
        name: '选填',
        note: '标签后面跟一段「（选填）」。',
        props: { label: '简介', optional: true },
        slot: '（控件）',
        expect: '（选填）',
      },
      {
        name: '提示',
        note: '控件下面那行提示，它的 id 会挂进 aria-describedby。',
        props: { label: '标题', hint: '最多 20 个字' },
        slot: '（控件）',
        expect: '最多 20 个字',
      },
      {
        name: '报错',
        note: '报错用 --danger-ink 写字，有它时 aria-invalid 为 true。',
        props: { label: '标题', error: '填写标题' },
        slot: '（控件）',
        expect: '填写标题',
      },
      {
        name: '字数',
        note: 'counter 给 current/max，画成「12/200」。',
        props: { label: '标题', counter: { current: 12, max: 200 } },
        slot: '（控件）',
        expect: '12/200',
      },
    ],
  },
  {
    id: 'settings-row',
    title: 'SettingsRow',
    about: '设置页的一行：左边标签（+说明），右边控件按预设宽度靠右；容器窄于 672px 时标签换到上面。',
    file: 'src/components/base/SettingsRow.vue',
    component: SettingsRow,
    needs: [],
    states: [
      {
        name: '文本宽度',
        note: 'text 340px：一行文本输入。宽容器里标签占 180px 一列。',
        props: { label: '显示名称', width: 'text' },
        slot: '（控件）',
        expect: '显示名称',
      },
      {
        name: '下拉宽度',
        note: 'select-wide 280px / select 192px。',
        props: { label: '默认模型', width: 'select-wide' },
        slot: '（控件）',
        expect: '默认模型',
      },
      {
        name: '带说明',
        note: '标签下面那句说明用 --muted 12px。',
        props: { label: '默认模型', description: '新任务用哪一个', width: 'select' },
        slot: '（控件）',
        expect: '新任务用哪一个',
      },
      {
        name: '短值',
        note: 'code 160px：数量、标识符这种短值。',
        props: { label: '批准人数', width: 'code' },
        slot: '（控件）',
        expect: '批准人数',
      },
      {
        name: '自由宽度',
        note: 'list：控件自己撑满剩下的一栏，一列名单或多行文本用它。',
        props: { label: '放行名单', width: 'list' },
        slot: '（控件）',
        expect: '放行名单',
      },
      {
        name: '满宽',
        note: 'none：不留右边那一栏，标签在上、控件占满整行。',
        props: { label: '说明', width: 'none' },
        slot: '（控件）',
        expect: '说明',
      },
    ],
  },
  {
    id: 'base-button',
    title: 'BaseButton',
    about: '知是的按钮：调用处只说角色（主操作 / 次要 / 轻量 / 危险）和大小，颜色与样式由角色推出。',
    file: 'src/components/base/BaseButton.vue',
    component: BaseButton,
    needs: UI,
    states: [
      {
        name: '主操作',
        note: '一块区域里让事情往下走的那一颗，琥珀实心。同一组并排按钮里只有一颗。',
        props: { kind: 'primary' },
        slot: '提交',
        expect: '提交',
      },
      {
        name: '次要',
        note: '独立出现、要被看见、但不是主操作：设置行里的「修改」「添加」。描边用 --line-2，字用 --text。',
        props: { kind: 'secondary' },
        slot: '修改',
        expect: '修改',
      },
      {
        name: '轻量',
        note: '「取消」「返回」、工具条、列表行、卡片角落里的操作。无底无框，字用 --muted，悬停变深。',
        props: { kind: 'ghost' },
        slot: '取消',
        expect: '取消',
      },
      {
        name: '危险',
        note: '直接生效、不再确认的破坏性操作。字用 --danger-ink。会先弹确认的入口用轻量。',
        props: { kind: 'danger' },
        slot: '清空记录',
        expect: '清空记录',
      },
      {
        name: '危险（确认）',
        note: '只用在确认弹窗里那一颗「删除」：实心红。',
        props: { kind: 'danger', solid: true },
        slot: '删除',
        expect: '删除',
      },
      {
        name: '小号',
        note: '28px：列表行、工具条、卡片内。字号 13。',
        props: { kind: 'secondary', size: 'sm' },
        slot: '重试',
        expect: '重试',
      },
      {
        name: '大号',
        note: '44px：门口页面的单个大按钮、手机上整行宽的提交。',
        props: { kind: 'primary', size: 'lg' },
        slot: '登录',
        expect: '登录',
      },
      {
        name: '纯图标',
        note: '传 icon，必须同时给 aria-label。轻量样式，图标 20px（小号 18px）。',
        props: { kind: 'ghost', icon: 'mdi-dots-horizontal', 'aria-label': '更多' },
      },
      {
        name: '加载中',
        note: 'loading 原样透传给 v-btn：转圈时按钮宽度不变、不可再点。',
        props: { kind: 'primary', loading: true },
        slot: '保存',
      },
    ],
  },
  {
    id: 'confirm-dialog',
    title: 'ConfirmDialog',
    about: '确认框：一句话、两颗按钮，桌面和手机都是居中的 420px 小框，没有 ✕，点遮罩不关。',
    file: 'src/components/base/ConfirmDialog.vue',
    component: ConfirmDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    states: [
      {
        name: '可撤销',
        note: '确认键是琥珀主操作，字写动作本身。',
        props: { modelValue: true, title: '用新版本替换这份文件？', confirmLabel: '替换' },
        slot: '旧版本会留在历史记录里。',
        expect: '替换',
      },
      {
        name: '不可撤销',
        note: '确认键实心红。标题用问句说清对谁做什么，正文只写后果。',
        props: { modelValue: true, title: '把爱丽丝移出项目？', confirmLabel: '移出', danger: true },
        slot: '她将看不到这个项目的话题和资料。',
        expect: '移出',
      },
    ],
  },
]
