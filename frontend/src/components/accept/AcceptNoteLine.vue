<script setup lang="ts">
// 后端写在卡上的那条 note（PR 有新提交、GitHub 拒绝合并、凭据失效 ……），照原样
// 念出来。轻重由后端下发的 tone 定，不是从文案开头那个字符猜的 —— 所以这里画一个
// 真的图标：颜色是唯一信号的话，色觉障碍和灰度截图上就什么都没有了。
//
// 待采纳和交付中两张脸共用这一行；由调用方决定什么时候该念（没有 note 就不画）。
defineProps<{
  text: string
  tone: 'error' | 'info'
}>()
</script>

<template>
  <div
    class="d-flex align-start ga-1 text-caption mb-2"
    :class="tone === 'error' ? 'text-error' : 'text-medium-emphasis'"
  >
    <v-icon v-if="tone === 'error'" icon="mdi-alert-circle-outline" size="14" class="mt-1" />
    <span>{{ text }}</span>
  </div>
</template>
