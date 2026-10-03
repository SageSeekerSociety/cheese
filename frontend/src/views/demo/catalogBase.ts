/**
 * 组件预览站里「基础组件」那一组：`src/components/base/` 下的件。
 *
 * 业务代码写按钮、弹窗、卡片时只用这一层（docs/design-system.md）。单放一份是因为
 * `catalog.ts` 离一千行的上限已经很近。条目的规矩见 `catalog.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import BaseButton from '@/components/base/BaseButton.vue'

const UI: CatalogNeed[] = ['vuetify']

export const BASE_ENTRIES: CatalogEntry[] = [
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
]
