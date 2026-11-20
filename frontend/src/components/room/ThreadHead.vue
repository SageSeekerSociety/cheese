<script setup lang="ts">
// 支线的页头一行：左边返回支线列表，它挂着的那条消息说的事，右边「转为任务」。桌面上画在
// 右栏「支线」那一格里这条支线的顶上；
// 手机上支线是一整页，这一行就是顶栏的内容（ThreadPane 用 useScreenHeader 交给顶栏），
// 页里不再写第二遍。转成的任务不在这里画，在任务页上看。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ThreadHead' })

defineProps<{
  /** 页头写的那一句：它挂着的那条消息说的事。 */
  heading: string
  /** 能不能转为任务：挂着的那条消息读回来了、频道没归档。 */
  canToTask: boolean
  /** 「转为任务」按下去还没回来。 */
  busy?: boolean
  /** 桌面上左边一颗返回，回到「支线」那一格的列表；手机上返回在顶栏左边。 */
  back?: boolean
  /** 画在手机顶栏里：顶栏自己有高度和底线，这一行不再带。 */
  bar?: boolean
}>()

const emit = defineEmits<{
  (e: 'to-task'): void
  (e: 'close'): void
}>()
</script>

<template>
  <header class="thread-head" :class="{ 'thread-head--bar': bar, 'thread-head--back': back }">
    <BaseButton
      v-if="back"
      icon="mdi-arrow-left"
      size="sm"
      data-testid="thread-back"
      :title="t('work.room.thread.back')"
      :aria-label="t('work.room.thread.back')"
      @click="emit('close')"
    />
    <div class="thread-head__text">
      <h2 class="thread-head__title t-title" :title="heading">{{ heading }}</h2>
    </div>
    <span class="thread-head__gap" />
    <BaseButton
      v-if="canToTask"
      kind="secondary"
      size="sm"
      :loading="busy"
      data-testid="thread-to-task"
      @click="emit('to-task')"
      >{{ t('work.room.thread.toTask') }}</BaseButton
    >
  </header>
</template>

<style scoped>
.thread-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--line);
}
/* 左边那颗返回贴近边上，和右边按钮离边的距离一样。 */
.thread-head--back {
  padding-left: 8px;
}
/* 顶栏里：高度、底线、左右的边距都是顶栏的（← 后面那一格，右边贴着顶栏的内边距）。 */
.thread-head--bar {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  min-height: 0;
  padding: 0 4px 0 0;
  border-bottom: 0;
}
.thread-head__text {
  display: flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.thread-head__title {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.thread-head__gap {
  flex: 1 1 0;
}
.thread-head--bar .thread-head__text {
  flex: 1 1 auto;
}
.thread-head--bar .thread-head__title {
  max-width: 100%;
}
</style>
