<script setup lang="ts">
// 公开首页（`/`）。**这是一只薄容器**：画面在同目录 `LandingView.vue` 里，只吃 props。
//
// 留在这里的只有两件容器该做的事：
//
//   - 谁登录了。`AccountService` 是会话，画面不该自己去问 —— 外壳那颗「进入」按它换字。
//   - 故事里那间房的一行消息用哪只组件画。产品里就是 `RoomMessage`，但它会经
//     `AttachmentImage` 去取图片字节（raw 端点只认 Authorization 头），于是"哪一行"
//     这件事成了一项取数。画的那一半不该知道这个，所以由这里选好、当 prop 递下去。
//
// 页面的其余部分（滚动、打字动画、语言切换后重来）都是这一页自己的表现，跟着画面走。
import { computed } from 'vue'

import LandingView from './LandingView.vue'

import RoomMessage from '@/components/room/RoomMessage.vue'
import AccountService from '@/services/account'

defineOptions({ name: 'Landing' })

const loggedIn = computed(() => AccountService.loggedIn)
</script>

<template>
  <LandingView :logged-in="loggedIn" :message-component="RoomMessage" />
</template>
