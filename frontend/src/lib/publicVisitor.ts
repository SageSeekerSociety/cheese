// 公共页（首页、方案页、下载页）要知道看的人是不是已经登录：登录了那颗按钮写「进入
// 工作区」，没登录写「开始使用」，导航里回首页也换成 /about。这个状态在
// services/account.ts 里，而那个模块连着后端。
//
// 为什么是一个注入口，而不是页面自己去读：公共页共用一只外壳（views/home/LandingShell.vue），
// 外壳一旦 import services/account，它和拿它包着用的那几页就都离不开后端
// （.claude/scripts/frontend_grade.py 判 C），目录站里也挂不起来。所以外壳只认这个
// 接口，真的由外壳组件在 App.vue 里注入（composables/usePublicVisitor.ts）；没人注入
// 的树——组件目录、单独挂载的测试——拿到 ANONYMOUS_VISITOR：当作一位没登录的访客，
// 画「开始使用」。
//
// 这里只放类型、键和那个兜底，不 import store、路由或 api：import 了，公共页又会被拖
// 回「连着后端」。
import type { InjectionKey, Ref } from 'vue'

import { ref } from 'vue'

export interface PublicVisitor {
  /** 眼下这个访问者有没有登录。 */
  signedIn: Readonly<Ref<boolean>>
}

/** 没人注入时的样子：一位没登录的访客。 */
export const ANONYMOUS_VISITOR: PublicVisitor = { signedIn: ref(false) }

export const PUBLIC_VISITOR: InjectionKey<PublicVisitor> = Symbol('publicVisitor')
