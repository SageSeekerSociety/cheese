<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import { computed } from 'vue'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import FeedbackCardRow from '@/components/feedback/FeedbackCard.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import FeedbackList from '@/components/feedback/FeedbackList.vue'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'

// 我的反馈 (/feedback/mine) 的**画面那一半**。
//
// 为什么要有这一页：反馈中心解决的是「有没有人提过同一件事」（搜索 + 支持），
// 它回答不了「我上周提的那条现在到哪了」。没有这一页时那条路只剩两个走法 —— 记住
// 详情页的链接，或者在一屏十几条里翻。
//
// 清单的边界由服务端定：我提的 + 我替谁提的（agent 报的、提交人是我）+ 指派给我的。
// 这里不按来源分栏：那需要每条带一个「为什么它在我的清单里」，服务端还没有这个字段，
// 而按 handle 在前端猜一遍等于把可见性规则抄第二份。
//
// 版面走和反馈中心同一副骨架（`FeedbackPageShell` + `FeedbackList` + `AdminEmptyState`）：
// 两页的滚动、页边距、内容宽度、列表的分隔行、空态的形状因此只有一份实现。
//
// ## 这一半一行取数都没有
//
// 列表、总数、未读游标的推进全在容器 `FeedbackMinePage.vue` 里（它调 store、读地址）。
// 这里只收 props、只发事件：列表从 `items` 来，点支持报 `support`、加载更多报 `more`、
// 空态那颗按钮报 `empty-action`。所以这一半能单独摆进组件预览站看。
//
// 行直接用反馈中心那一行（`FeedbackCard`）：同一条反馈在哪一页都应该长一样，两套
// 卡片迟早会在状态、标签、私密标记上分叉。支持按钮也照用 —— store 里三份列表都会
// 被 `_find` / `_patch` 找到，所以在这一页点支持跟中心页是同一个动作（请求由容器发）。
defineOptions({ name: 'FeedbackMinePageView' })

const props = defineProps<{
  items: FeedbackCard[]
  total: number
  loading: boolean
  loadingMore: boolean
  /** 还有没有下一页。 */
  hasMore: boolean
  /** 服务端的原话；`null` 表示没失败。 */
  error: string | null
}>()

const emit = defineEmits<{
  /** 空态那颗按钮：拉挂了就问服务端再要一次，还没提过就带人去提交页（哪一件由容器定）。 */
  'empty-action': []
  /** 列表尾巴上那颗「加载更多」。 */
  more: []
  'dismiss-error': []
  /** 某一行点了支持 / 取消支持。请求在容器里发。 */
  support: [id: string]
}>()

/** 空列表有两种，说的话不一样：「还没有」和「没拉到」。写成同一句「暂无反馈」的话，
 *  拉挂的那一次看起来就像「平台把你的反馈弄丢了」。
 *  失败那一句走 i18n（和反馈中心共用同一条文案 —— 同一个失败，说不出两种话）；「还没
 *  提过」那一句说的是这一页特有的边界（我提的 / 我替谁提的 / 指派给我的），中心页那句
 *  「你提交的反馈会出现在这里」在这里是错的。 */
const emptyState = computed(() =>
  props.error
    ? {
        icon: 'mdi-alert-circle-outline',
        tone: 'error' as const,
        title: t('feedback.center.error.title'),
        desc: t('feedback.center.error.desc'),
        action: t('feedback.center.error.retry'),
      }
    : {
        icon: 'mdi-inbox-outline',
        tone: 'neutral' as const,
        title: t('feedback.mine.empty.title'),
        desc: '',
        action: t('feedback.mine.empty.action'),
      }
)
</script>

<template>
  <FeedbackPageShell :title="t('feedback.mine.title')">
    <template #actions>
      <BaseButton size="sm" to="/feedback">
        {{ t('feedback.mine.back') }}
      </BaseButton>
      <BaseButton kind="primary" prepend-icon="mdi-plus" :to="{ name: 'FeedbackSubmit' }">
        {{ t('feedback.mine.submit') }}
      </BaseButton>
    </template>

    <!-- 这一页的说明。**总数只在拉到之后才说**：加载中写「共 0 条」是在报一个还不知道
         的数。 -->
    <template #sub>
      <p class="t-meta-read t-num fb-lede">
        {{ t('feedback.mine.lede') }}
        <span v-if="!loading && !error">
          {{ t('feedback.mine.total', { n: total }) }}
        </span>
        {{ t('feedback.mine.ledeRest') }}
      </p>
    </template>

    <!-- 列表非空时的失败也要画出来，理由同反馈中心：不画的话，一次失败（比如在一条
         办完的反馈上点支持，服务端回 412）会让页面看着像什么都没发生。 -->
    <FeedbackErrorBanner v-if="error && items.length" :message="error" @dismiss="emit('dismiss-error')" />

    <!-- 骨架 3 行，和反馈中心同一档（§9.4）：两页的行一样高，一页画 4 行一页画 3 行
         会让「列表有多长」看起来是两个数。 -->
    <FeedbackList
      :loading="loading"
      :count="items.length"
      :has-more="hasMore"
      :loading-more="loadingMore"
      :shown="items.length"
      :total="total"
      @more="emit('more')"
    >
      <!-- 打开详情那条链接在行自己身上（`router-link`），这里不再接一个 `@open`
           去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。
           点支持在卡片上报上来（带 id），请求由容器发给服务端。 -->
      <FeedbackCardRow v-for="item in items" :key="item.id" :item="item" @support="emit('support', $event)" />

      <template #empty>
        <AdminEmptyState
          :title="emptyState.title"
          :desc="emptyState.desc || undefined"
          :icon="emptyState.icon"
          :tone="emptyState.tone"
          :action="emptyState.action"
          @action="emit('empty-action')"
        >
          <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，这一句说的
               是「这一次为什么没成」。 -->
          <p v-if="error" class="fb-empty__raw t-meta-read">{{ error }}</p>
        </AdminEmptyState>
      </template>

      <template #foot>{{ t('feedback.mine.foot') }}</template>
    </FeedbackList>
  </FeedbackPageShell>
</template>

<style scoped>
/* 这一页的说明。行距用 token 而不是 1.7：这一档的领值只有 --lh-* 这一份来源，手写的
   倍数在两个主题、两种语言里都不会跟着别处一起调。 */
.fb-lede {
  margin: 0 0 16px;
  line-height: var(--lh-14-loose);
}
/* 空态里那句服务端的原话。空态那副骨架（`AdminEmptyState`）把动作放在这一句上面，
   所以这里只补一点上边距。 */
.fb-empty__raw {
  margin: 8px 0 0;
}
</style>
