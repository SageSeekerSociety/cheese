// 「去哪」这个类型只在这里 import vue-router 一次。
//
// 组件收 `to` 这类 prop 时要说清它是什么，而它在 vue-router 里叫 `RouteLocationRaw`。
// 直接 import 那一个类型，在 `components/**` 里就会撞上 .claude/rules/architecture.md
// 的边界闸：那条规则挡的是「组件自己跳转、自己读路由」，可它按 import 判，所以一个
// 纯类型的 import 也算一次违规，还得进基线。
//
// 于是类型从这儿取 —— 和 lib/userRef.ts 的 `UserRefTarget` 同一套做法：组件只认这个
// 别名，路由怎么实现的（名字还是路径、param 叫什么）由把这个 prop 传进来的那一级决定。
import type { RouteLocationRaw } from 'vue-router'

/** 一个目的地：一条路径，或者 `{ name, params }` 这样的描述。组件只把它透给
 *  `NavLink` / `useNavigation().navigate`，自己不解释它。 */
export type NavTarget = RouteLocationRaw
