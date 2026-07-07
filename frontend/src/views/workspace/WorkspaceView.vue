<!-- 退休：项目级群聊已并入全局聊天（知是 2.0，聊天与项目无关、UI 无 project_id）。
     此路由不再被引用；保留为一个转发到全局 /chat 的薄壳，避免悬空引用旧的项目群聊组件。 -->
<template>
  <ChatView />
</template>

<script setup lang="ts">
import ChatView from '@/views/chat/ChatView.vue'
</script>
