<script setup lang="ts">
import NotificationPanel from '@/components/common/Notification/NotificationPanel.vue'

// 「待办」——手机底栏三格之一（设计见 docs/plans/2026-08-18-mobile-shell-design.md）。
//
// 它最终要聚合的是「等你的事」：awaits_me 的话题、@ 我的消息、待验收的交付卡、
// 入队申请。那需要一个跨项目的聚合端点（设计文档 §8.3），今天还没有——所以这一格
// 先接顶栏铃铛那份数据，让格子从第一天就有真实内容，而不是先摆一个空壳。
defineOptions({ name: 'InboxView' })
</script>

<template>
  <div class="inbox-page">
    <NotificationPanel page />
  </div>
</template>

<style scoped>
.inbox-page {
  height: 100%;
  overflow-y: auto;
}
</style>
