<script setup lang="ts">
// 发题表单里每一张卡都长这样：左上角一个圆底图标，旁边一行标题，底下一栏内容。
//
// 标题和图标都从外面进来 —— 这一件不说自己在画哪一张卡、也不翻任何一句话，所以它
// 能单独摆在预览站里（`catalogTaskForm.ts`）。表单那七张卡共用它，改一遍就够了。
defineProps<{
  /** 左上角那个图标（MDI 名字）。 */
  icon: string
  /** 卡片标题：调用方已经翻好的那句话。 */
  title: string
}>()
</script>

<template>
  <v-card flat rounded="lg" class="mb-4 form-card">
    <v-card-item>
      <template #prepend>
        <div class="me-3">
          <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
            <v-icon color="primary" size="28">{{ icon }}</v-icon>
          </v-avatar>
        </div>
      </template>
      <v-card-title class="text-h5 ps-0">{{ title }}</v-card-title>
    </v-card-item>

    <v-card-text class="pt-2">
      <slot />
    </v-card-text>
  </v-card>
</template>

<style scoped src="./task-form.css"></style>
