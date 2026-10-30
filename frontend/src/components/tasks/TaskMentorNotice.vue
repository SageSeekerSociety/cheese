<script setup lang="ts">
// 开始做这道题时的「这道题要有导师」（#3830）：开始前说清导师是谁、能看到什么、看不到
// 什么、怎么移出。开始做就是用这道题新建项目，导师在那一刻加入，所以在那一刻问：直接开始
// 的题在开始的确认框里，申请制的题在通过之后的开始确认框里，申请时不问。
import { useI18n } from 'vue-i18n'

defineProps<{ names: string }>()

const { t } = useI18n()
</script>

<template>
  <section class="mentor-notice">
    <div class="mentor-notice__head">
      <v-icon size="18" class="mentor-notice__icon">mdi-account-tie-outline</v-icon>
      <span class="mentor-notice__title">{{ t('tasks.verifyDialog.mentor.title', { names }) }}</span>
    </div>
    <ul class="mentor-notice__list">
      <li>{{ t('tasks.verifyDialog.mentor.sees') }}</li>
      <li>{{ t('tasks.verifyDialog.mentor.notSees') }}</li>
      <li>{{ t('tasks.verifyDialog.mentor.remove') }}</li>
    </ul>
  </section>
</template>

<style scoped>
.mentor-notice {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.mentor-notice__head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.mentor-notice__icon {
  color: var(--muted);
}

.mentor-notice__title {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.mentor-notice__list {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.mentor-notice__list li + li {
  margin-top: 4px;
}
</style>
