<script setup lang="ts">
// 「外部」：这个人不是团队的一员，只被邀请进了这一个项目。像飞书给外部联系人挂的
// 那个标：名字旁边一眼看得出，说话、@ 他、看他资料时都知道他是谁。中性色——它是一
// 条事实，不是提醒，更不是主操作的琥珀色。
import { t } from '@/i18n'
</script>

<template>
  <span class="chip-neutral external-tag" :title="t('work.external.tagHint')">{{ t('work.external.tag') }}</span>
</template>

<style scoped>
.external-tag {
  flex-shrink: 0;
}
</style>
