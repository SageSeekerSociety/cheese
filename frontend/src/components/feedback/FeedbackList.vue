<script setup lang="ts">
import BaseButton from '@/components/base/BaseButton.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { t } from '@/i18n'

// 反馈列表那一块：**一张面、头发丝分隔的行**（§4.3 的密度那一档）。
//
// 以前一条反馈是一张独立卡片（各自一圈描边 + 圆角 + 16px 间距），一屏九条；管理端的
// 队列行是同一张面上的分隔行，一屏十三行。两处说的是同一件事（一串要扫的条目），
// 长得却像两个产品。现在列表这几页也走「一张面 + 分隔行」：外壳给 `--surface` 和一圈
// 描边，行之间 1px `--line`，行自己不带边框。
//
// 抽成组件是因为这些规则原先在中心页和「我的」各写了一份（`.fb-list` / `.fb-empty` /
// `.fb-more` / `.fb-foot`）—— 两份拷贝的漂开方式是「两页的间距差 8px」，而没有人会
// 同时看着两页。
//
// 空态走调用方的 `#empty` 槽（内容各页不一样：中心页有四种「没有」，我的只有两种），
// 但**外边距和四态骨架是同一份**：`AdminEmptyState` 就是那副骨架。
defineOptions({ name: 'FeedbackList' })

withDefaults(
  defineProps<{
    loading: boolean
    /** 手上几条。0 就是空 —— 空态由调用方给。 */
    count: number
    hasMore?: boolean
    loadingMore?: boolean
    /** 翻页那一行的两个数：手上几条 / 服务端一共几条。 */
    shown?: number
    total?: number
  }>(),
  { hasMore: false, loadingMore: false, shown: 0, total: 0 }
)

const emit = defineEmits<{ more: [] }>()
</script>

<template>
  <!-- 骨架**不放进 .fb-list**：那一层是行与行之间的分隔线所在的盒子，骨架行自带下边距，
     两处一叠就是 16px，到货那一刻列表会往上收一截 —— 骨架存在的意义正是不让这件事发生。 -->
  <LoadingSkeleton v-if="loading" variant="feedback" :rows="3" />

  <template v-else>
    <div v-if="count" class="fb-list">
      <slot />
    </div>
    <slot v-else name="empty" />

    <!-- 翻页那一行只在**真的还有下一页**时出现（`hasMore` 比的是手上条数和总数）。
         到底了不画「已到底」：那一行字只是在告诉读者「这个按钮你按不了了」。 -->
    <div v-if="count && hasMore" class="fb-more">
      <BaseButton kind="secondary" size="sm" :loading="loadingMore" @click="emit('more')">
        {{ t('feedback.center.more.load') }}
      </BaseButton>
      <span class="t-meta-read t-num">{{ t('feedback.center.more.showing', { shown, total }) }}</span>
    </div>

    <!-- 页脚是「读完了、下面是空的」这句话的一部分，跟着骨架一起出现等于提前说了还没到
         的话。 -->
    <p v-if="$slots.foot" class="t-meta-read fb-foot"><slot name="foot" /></p>
  </template>
</template>

<style scoped>
/* 一张面：外壳给底色和描边，行自己不带（见文件头）。`overflow: hidden` 让行的 hover
   底色被圆角裁住。 */
.fb-list {
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
/* 翻页那一行：按钮和计数在同一条中线上，两者之间 12px。在面的**外面** —— 它不是一条
   数据行，画进面里会被那圈描边连成第 N+1 行。 */
.fb-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 16px;
}
.fb-foot {
  margin: 24px 0 0;
  /* 用 token 而不是 1.7：这一档的领值只有 --lh-* 这一份来源，手写的倍数在两个主题、
     两种语言里都不会跟着别处一起调。 */
  line-height: var(--lh-14-loose);
}
</style>
