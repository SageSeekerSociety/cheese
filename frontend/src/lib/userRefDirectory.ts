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

/**
 * 名册上这个人的身份：一颗 chip 要画「头像 + 姓名」，就靠这三样。名册
 * （`ProjectMemberRow`）是唯一同时知道它们的地方。
 *
 * 查不到这个人（不在这份名册里：切到了别的项目、这个人离开了项目或团队）时给 null，
 * 意思是「这份名册不知道这是谁」，由调用方定降级（`useUserRef`）：退回 handle +
 * 彩色首字母，是不是队友再按 handle 命名规则兜底（Design §3.14）——名册缺席时的 AI
 * 又落回 `cheese` / `cheese-*` 那颗脸，别把它画成真人；认不出的人仍一律当人。
 */
export interface UserRefIdentity {
  /** 显示名；名册不记名字时给 null，chip 退回 handle。 */
  name: string | null
  /** 这个人自己挑过的头像素材 id；null = 没挑过（画彩色首字母）。 */
  avatarId: number | null
  /** 是不是 AI 队友，照名册上说。 */
  agent: boolean
}

export interface UserRefDirectory {
  /** 名册里这个人的身份；查不到给 null，chip 退回 handle。 */
  identity(handle: string): UserRefIdentity | null
  /**
   * 点这个人去哪。projectId 不传（undefined）时取眼下所在的项目；传 null 表示这句话
   * 不属于眼下这个项目（例如全局通知），去个人主页。没有去处给 null。
   */
  target(handle: string, projectId?: string | null): UserRefTarget | null
  /** 跳过去。 */
  navigate(target: UserRefTarget): void
}

/** 没人注入时的样子：只画 handle，没有去处，也不知道谁是谁（是不是队友由上层的
 *  handle 命名规则兜底，见 `useUserRef`）。 */
export const OFFLINE_USER_REF_DIRECTORY: UserRefDirectory = {
  identity: () => null,
  target: () => null,
  navigate: () => {},
}

export const USER_REF_DIRECTORY: InjectionKey<UserRefDirectory> = Symbol('userRefDirectory')
