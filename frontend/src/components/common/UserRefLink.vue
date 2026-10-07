<script setup lang="ts">
// 句子里那个人的「实体」：名册查询、当前项目判断、点击跳转，都在这里接上。
// 画的那一半在 UserRef.vue，只认 props。
//
// 为什么不把查询摊到 48 个使用方里：每个使用方都写一遍「查名册、判断当前项目、
// 推路由」是同一段代码抄 48 遍，而且每处都得自己记得处理「没有 handle / 没装
// 路由」的兜底。所以留这一只薄容器，使用方只改 import 一行。
//
// 名册和路由都不在这里：useUserRef 从外壳注入的 UserRefDirectory 问
// （lib/userRefDirectory.ts）。没人注入时它照样画出 @handle，只是不能点，所以这只
// chip 和渲染它的组件都能离开后端单独挂起来。
import { useUserRef } from '@/composables/useUserRef'

import UserRef from './UserRef.vue'

const props = defineProps<{
  handle?: string | null
  name?: string | null
  /** 不传时取当前路由上的 projectId：在项目里就去项目里的成员页。传 null 表示这句话
   *  不属于眼下这个项目（例如全局通知），去个人主页。 */
  projectId?: string | null
}>()

const { label, to, navigate } = useUserRef(
  () => props.handle,
  () => props.projectId
)
</script>

<template>
  <UserRef :handle="handle" :name="name || label" :to="to" @navigate="navigate" />
</template>
