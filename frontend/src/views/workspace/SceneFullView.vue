<!-- 现场全屏页：整个页面就是一个 Claude Code、无边框。由现场弹窗的最大化按钮以新标签
     打开（/workspace/scene/:deviceId/:sid?exp=true）。复用 SceneTerminal（同一原始协议、
     同样的 web-claude 观感 + 只读/滚动/交互 + compact + A−/A+）。 -->
<template>
  <div class="scene-full">
    <SceneTerminal :session-id="sid" :device-id="deviceId" />
  </div>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'

import SceneTerminal from './components/SceneTerminal.vue'

const route = useRoute()
const deviceId = route.params.deviceId as string
const sid = route.params.sid as string
</script>

<style scoped>
.scene-full {
  position: fixed;
  inset: 0;
  background: #000;
}
</style>
