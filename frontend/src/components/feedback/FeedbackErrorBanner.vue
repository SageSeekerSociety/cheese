<script setup lang="ts">
import { t } from '@/i18n'

// 列表**非空**时的一条失败提示。反馈中心和「我的反馈」两页共用这一块。
//
// 它解决的是一件以前没有落脚点的事：失败原先只画在「一条也没有」那块空态里，于是从
// 列表上点「支持」失败（办完了的条目回 412、别人删掉的条目回 404）时页面上什么都不动
// —— 按钮按得下去、有按下效果、数字一动不动、一句话也没有，和「这个按钮坏了」长得
// 一模一样。
//
// 形状是**一条中性色的横条**，不是一块红底：它说的是「刚才那一下没成」，而列表本身
// 还在、还能用。服务端那句话照直画出来（`412 的「已经不能支持了」`），比在客户端另编
// 一句「操作失败」有用得多。
//
// 抽成组件而不是两页各写一份：这两份拷贝的漂开方式是一个人把底色从 `--warn-wash` 改成
// `--danger-wash`，而另一个人永远看不到自己那一页变了。
defineOptions({ name: 'FeedbackErrorBanner' })

defineProps<{
  /** 服务端的原话。空着不画（调用方一般用 `v-if` 挡着，这里再挡一次是因为它是唯一
   *  让这条横条该消失的信号）。 */
  message?: string
}>()

const emit = defineEmits<{ dismiss: [] }>()
</script>

<template>
  <div v-if="message" class="fb-banner" role="alert">
    <v-icon size="16" aria-hidden="true">mdi-alert-outline</v-icon>
    <span class="fb-banner__text">{{ message }}</span>
    <button
      type="button"
      class="fb-banner__x"
      :aria-label="t('feedback.center.error.dismiss')"
      @click="emit('dismiss')"
    >
      <v-icon size="14" aria-hidden="true">mdi-close</v-icon>
    </button>
  </div>
</template>

<style scoped>
.fb-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 8px 8px 12px;
  color: var(--warn-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  background: var(--warn-wash);
  border-radius: var(--radius-md);
}
.fb-banner__text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.fb-banner__x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-left: auto;
  padding: 0;
  color: inherit;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.fb-banner__x:hover {
  background: var(--fill);
}
</style>
