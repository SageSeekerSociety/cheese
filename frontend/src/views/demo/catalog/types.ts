/**
 * 目录那三条形状：一条条目（`CatalogEntry`）、它的一格（`CatalogState`）、一格要
 * 在什么环境里才长得对（`CatalogNeed`）。
 *
 * 从 `../catalog.ts` 拆出来的一册：类型一个字没改。分册（`./common.ts` 那一批）
 * 和门面（`../catalog.ts`）都从这里取，所以「条目长什么样」只有一处定义。
 */
import type { Component } from 'vue'

/**
 * 这一件要在什么环境里才长得对。
 *
 * 记下来是有用的：目录页把这几颗画成标签（「它需要什么」是读的人第一个问题），
 * 测试照着它装 —— 只装这一件真正要的那几样。所以「能单独渲染」在预览站里是一条
 * 可执行的断言，而不是注释里的一句话。
 *
 * 一条都不写的（`needs: []`）是真的一件都不要：`UserRef` 只吃 props，`NavLink`
 * 没有路由就退化成一行静态的字。
 */
export type CatalogNeed = 'vuetify' | 'i18n' | 'router' | 'pinia'

export interface CatalogState {
  /** 这一格的名字（页面上那张小标题）。 */
  name: string
  /** 这一格在讲什么，一句话。 */
  note: string
  props: Record<string, unknown>
  /** 默认插槽里的那段字（`NavLink`、`AppPage` 这些靠插槽才有内容）。 */
  slot?: string
  /** 这一格的环境，不写就跟条目走。 */
  needs?: CatalogNeed[]
  /** 画出来之后该看得见的一句话（测试按它判「真的画出来了」）。挑**只有这一格才有**的
   *  那句：几格都有的字（顶栏、标题）证明不了画的是这一格。 */
  expect?: string
  /** 字分不出这一格时（几格的字一样、只差状态或形状，比如清单的勾、收着的文件夹、骨架），
   *  再给一个只在这一格命中的选择器，测试断言它在画出来的东西里找得到。只给测试用，
   *  页面不读它；和 `expect` 可以同时给。 */
  expectSelector?: string
  /** 这一格要**先点一下**才露出要看的那个状态（比如「看原文」里那条截断说明）：给测试
   *  用，按键是 `data-testid`。点了之后才按 `expect` / `expectSelector` 判。 */
  click?: string
}

export interface CatalogEntry {
  /** 地址里那一段：`/demo/catalog/<id>`。 */
  id: string
  title: string
  about: string
  /** 源码路径，相对 `frontend/`。 */
  file: string
  component: Component
  needs: CatalogNeed[]
  /** 每一格共用的参数（CSF 的 `args`），一格的 `props` 叠在它上面。 */
  args?: Record<string, unknown>
  /** 要坐在 Vuetify 布局里的（底栏、底部动作面板本来就长在 layout 里）。 */
  layout?: boolean
  /** 画出来的东西不在容器里（浮层传送到 body）。 */
  teleport?: boolean
  states: CatalogState[]
}
