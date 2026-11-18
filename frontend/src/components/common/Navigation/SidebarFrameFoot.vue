<template>
  <!-- 侧栏最底下一行：我的头像（带昵称，点开用户菜单）+ 问芝士。原来在左边那一列的底部
       （appNav.ts）。固定在底部，不随列表滚。 -->
  <div class="frame-foot" data-testid="sidebar-frame-foot">
    <AccountButton placement="sidebar" show-name />
    <AgentRailEntry class="frame-foot__agent" />
  </div>
</template>

<script setup lang="ts">
import AccountButton from './AccountButton.vue'

import AgentRailEntry from '@/components/assistant/AgentRailEntry.vue'
</script>

<style scoped>
.frame-foot {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  padding: 6px 8px 8px;
  border-top: 1px solid var(--line);
}

.frame-foot :deep(.agent-rail) {
  width: 36px;
  height: 36px;
  margin: 0;
}
</style>
