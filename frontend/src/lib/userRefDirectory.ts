// 「这个 handle 叫什么、点了去哪」的接口：句子里每一颗人名 chip 都从这里问。
//
// 为什么是一个注入口，而不是 chip 自己去读名册：名册在 workspace store 里，store 连着
// 后端。chip 一旦自己读 store，它和渲染它的每一个组件都没法离开后端单独挂起来
// （.claude/scripts/frontend_grade.py 判 C），而 chip 散在四五十处。所以 chip 只认这个
// 接口，真的实现由外壳在 App.vue 里注入（composables/useUserRefDirectory.ts）；没人
// 注入的树——组件目录、单独挂载的测试——拿到 OFFLINE：照样画出 @handle，只是不能点。
//
// 这里只放类型、键和那个兜底，不 import store、路由或 api：import 了，所有 chip 又会
// 被拖回「连着后端」。
import type { InjectionKey } from 'vue'
import type { UserRefTarget } from '@/lib/userRef'

export interface UserRefDirectory {
  /** 名册里这个人的显示名；查不到给 null，chip 退回 handle。 */
  name(handle: string): string | null
  /**
   * 点这个人去哪。projectId 不传（undefined）时取眼下所在的项目；传 null 表示这句话
   * 不属于眼下这个项目（例如全局通知），去个人主页。没有去处给 null。
   */
  target(handle: string, projectId?: string | null): UserRefTarget | null
  /** 跳过去。 */
  navigate(target: UserRefTarget): void
}

/** 没人注入时的样子：只有名字（handle 本身），没有去处。 */
export const OFFLINE_USER_REF_DIRECTORY: UserRefDirectory = {
  name: () => null,
  target: () => null,
  navigate: () => {},
}

export const USER_REF_DIRECTORY: InjectionKey<UserRefDirectory> = Symbol('userRefDirectory')
