<script setup lang="ts">
import AdminQueuePage from '@/views/admin/AdminQueuePage.vue'

// `/admin/feedback` —— 上一代那一整页反馈管理，现在是**一层薄壳**（§10.2）。
//
// 内容全部搬去了 `views/admin/AdminQueuePage.vue`（队列、总表、四态、键盘），这一页
// 只剩一句话：这里还是那条队列。
//
// **为什么留着这条地址、不是删掉或重定向。** 这一页的地址在别人的书签里、在聊天记录
// 里、在工单模板里 —— 那些链接当时指的是「管理后台里管反馈的那一块」，今天它仍然指
// 同一件事。404 会让一条老链接变成一次求助；重定向到 `/admin/queue` 则是在地址栏里
// 不声不响地把人换个地方（他复制回聊天里的地址和点进去的那个对不上）。留一条同名的
// 薄壳最省事，也最不会骗人：地址不变、内容就是队列。
//
// 这个文件**不再有自己的状态**：搜索、筛选、分诊、键盘全在 `AdminQueuePage` 里，
// 两边共用同一个 store，在这儿再抄一份过滤逻辑必然有第二份真相。
defineOptions({ name: 'AdminFeedbackPage' })
</script>

<template>
  <AdminQueuePage />
</template>
