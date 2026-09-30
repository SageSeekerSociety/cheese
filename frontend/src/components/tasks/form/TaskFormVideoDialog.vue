<script setup lang="ts">
// 「视频链接提示」那一段：填了一个解析不了的地址时弹出来问一句 —— 现在只有 B 站能
// 嵌着放，别的站存下来就是一条点开能看、但题面里放不出来的链接。
//
// 它只画这一段话，点哪颗按钮往外报一声（`confirm` 继续保存 / `cancel` 不存了）。
// 判断那个地址算不算数、以及这份提交随后怎么办，都在 `useTaskForm.ts` 里（第二道
// 闸门），所以这一件自己不知道什么是 Bilibili。
const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()
</script>

<template>
  <v-dialog v-model="open" max-width="450" persistent>
    <v-card>
      <v-card-title class="text-h6">视频链接提示</v-card-title>
      <v-card-text>
        <v-alert type="warning" variant="tonal" class="mb-0">
          无法解析该视频链接，视频链接可能有误。当前仅支持 Bilibili 视频嵌入播放，是否继续保存？
        </v-alert>
      </v-card-text>
      <v-card-actions class="pa-4 pt-0">
        <v-spacer></v-spacer>
        <v-btn variant="text" @click="emit('cancel')">取消</v-btn>
        <v-btn color="primary" variant="flat" @click="emit('confirm')">继续保存</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
