<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import { computed } from 'vue'

import FeedbackAuthorAvatar from './FeedbackAuthorAvatar.vue'
import FeedbackStatusChip from './FeedbackStatusChip.vue'

import { isClosed, SOURCE_LABEL } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈中心列表里的一行。
//
// 左边那一列是**支持**，不是点赞：支持数决定排序（热门 Tab），也是管理员判断该先
// 看哪一条的依据。所以它在卡片最左边、是这张卡上唯一一个带底色的按钮，其余全是文字。
//
// 「已支持」用**中性色**（tonal + secondary），不用琥珀：一屏里琥珀只给唯一的主操作
// （这一页是「提交反馈」），支持是一个可反复切换的状态，它变琥珀会让主操作不再是唯一
// 那个显眼的东西（docs/design-system.md §0）。状态本身有三个不依赖颜色的信号：实心
// 的拇指图标、文字、以及计数变 --ink。
//
// **开这条详情的是一个真链接**（`<router-link>`），不是 `@click` 加 `router.push`。
// 那条路以前是整张卡一个 `@click`：鼠标能用，键盘和读屏完全够不着 —— 一张卡不是一个
// 可聚焦的东西，Tab 键会整段跳过去，中键点开、右键复制链接也都没有。现在正文那一块
// 是一条真的 `<a href="/feedback/…">`，浏览器原生那套（Tab、回车、中键、新标签页）
// 全都回来了。
//
// 链接**只包正文，不包左边那一列**：`<a>` 里不能嵌 `<button>`（HTML 规范里那是
// 「交互内容套交互内容」），而支持按钮就在左边那一列。分开之后两边各自是自己那一格，
// 点支持不会顺手打开详情，也不需要 `@click.stop` 去挡。
//
// **不能公开的条目**（私密，或管理员标了安全问题）走另一套：那一条不该让人知道它
// 存在，所以它没有支持按钮 —— 支持是公开表态，它决定「热门」怎么排、管理员先看哪
// 条。图标占着支持那一列的位置，是为了不让这张卡的正文比上下每一张都左移 40px：
// 那看起来像排版坏了，而不是「这一条不一样」。
//
// 数据是 `FeedbackCard`（后端 `schemas.FeedbackCard`）本身，**不在这里转成第二种
// 形状**：一个中间层会让「这个字段到底是哪个」变成每次读代码都要回去查一遍的事。
const props = defineProps<{ item: FeedbackCard }>()

const store = useFeedbackStore()

/** 办完了的反馈在列表里不再喊人支持：它已经做完了。收尾是两级（修复、上线），
 *  判断走 lib/feedbackMeta 的 `isClosed`，别在这里再写一次「不是 resolved」。 */
const supportable = computed(() => !isClosed(props.item.status))
const isPrivate = computed(() => props.item.visibility === 'private')
/** 支持按钮能不能出现。私密和安全问题都不行 —— 理由见上面那段注释。 */
const supportShown = computed(() => !isPrivate.value && !props.item.security)
const PRIVATE_HINT = '私密反馈：只有你、平台管理员、以及提出它时在那个房间里的人能看到，其他人看不到它'
const SECURITY_HINT = '安全问题：只有你、平台管理员、以及提出它时在那个房间里的人能看到它，其他人看不到它'
/** 支持那一列留白时摆什么。
 *  两种「不能公开」要**分开说**：以前这里写死一把锁加一句「私密反馈」，于是被标成
 *  安全问题的公开条目也顶着那句 —— 一句和这条对不上的说明，比没有说明更坏。 */
const withheld = computed(() =>
  isPrivate.value
    ? { icon: 'mdi-lock-outline', hint: PRIVATE_HINT }
    : { icon: 'mdi-shield-alert-outline', hint: SECURITY_HINT }
)
/** 正文那一块整块是一条去详情页的链接。走路由名，路径字符串不在这里再写一遍。 */
const to = computed(() => ({ name: 'FeedbackDetail', params: { id: props.item.id } }))
</script>

<template>
  <v-card class="fb-card">
    <div class="fb-card__support">
      <template v-if="!supportShown">
        <!-- 装饰性图标：同一条信息在右边的元信息行里有带字的「私密」/「安全」，
             读屏再念一遍这里只是重复。`title` 是留给鼠标的。 -->
        <v-icon size="18" class="fb-card__withheld" :title="withheld.hint" aria-hidden="true">{{
          withheld.icon
        }}</v-icon>
      </template>
      <template v-else>
        <v-btn
          icon
          size="small"
          :variant="item.supported ? 'tonal' : 'outlined'"
          color="secondary"
          :disabled="!supportable"
          :aria-label="item.supported ? '取消支持' : '支持这个反馈'"
          :title="supportable ? (item.supported ? '取消支持' : '支持') : '已办完，无需再支持'"
          @click.stop="store.toggleSupport(item.id)"
        >
          <v-icon size="18">{{ item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
        </v-btn>
        <span class="fb-card__count" :class="{ 'c-muted': !item.supported }">{{ item.supports }}</span>
      </template>
    </div>

    <router-link class="fb-card__body" :to="to">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <span class="fb-card__title">{{ item.title }}</span>
        <FeedbackStatusChip :status="item.status" />
      </div>
      <p class="fb-card__summary">{{ item.summary }}</p>
      <div class="d-flex align-center flex-wrap ga-3">
        <span class="t-meta d-inline-flex align-center ga-1">
          <FeedbackAuthorAvatar
            :handle="item.author_handle"
            :is-agent="item.author_is_agent"
            :avatar-id="item.author_avatar_id"
            :size="18"
          />
          {{ item.author_handle }} · {{ relTime(item.created_at) }}
        </span>
        <!-- 中性色，不是警告色：私密是一个事实（这条只有我、平台管理员、和提出它时在那个房间里的人能看见），不是一件
             需要被纠正的事。写在作者名之后，因为它回答的正是「这条谁看得见」，
             和旁边的「谁提的」是同一类信息。 -->
        <span v-if="isPrivate" class="chip-neutral" :title="PRIVATE_HINT">
          <v-icon size="12">mdi-lock-outline</v-icon>私密
        </span>
        <span v-if="item.security" class="chip-neutral"> <v-icon size="12">mdi-shield-alert-outline</v-icon>安全 </span>
        <span class="t-meta d-inline-flex align-center ga-1">
          <v-icon size="13">mdi-comment-outline</v-icon>{{ item.comments }}
        </span>
        <span v-if="item.author_is_agent" class="chip-neutral">
          <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
        </span>
        <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
      </div>
    </router-link>
  </v-card>
</template>

<style scoped>
/* 这里**不写** border-radius：VCard 的默认 `rounded="xl"`（24px）走的是
   `.rounded-xl { border-radius: 24px !important }`，scoped 里写的 12px 压不过它，
   写了只是死代码（曾经就有一行这样的）。改圆角得改 plugins/vuetify.ts 的默认值，
   那是全仓 v-card 的事，不在这个组件里做。 */
.fb-card {
  display: flex;
  gap: 12px;
  padding: 16px;
}
/* hover 只换底色和描边色，不位移：列表一屏十几行，每行抬 2px 会看成整列在跳
   （docs/design-system.md §9.1）。 */
.fb-card:hover {
  border-color: var(--line-2);
  background: var(--fill);
}
.fb-card__support {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding-top: 4px;
}
/* 图标占的是支持按钮那一格，所以它得跟着那一格居中。--faint 是刻意的：它是「这里
   没有按钮」的说明，不是提示，比正文更轻才对。 */
.fb-card__withheld {
  color: var(--faint);
}
.fb-card__count {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
/* 正文那一块就是那条链接。`color: inherit` + 去掉下划线是必须的：a 的默认样式会把
   整块正文染成链接蓝并加下划线，而这里读起来应该仍是一条正文，只是恰好能点开。 */
.fb-card__body {
  min-width: 0;
  flex: 1;
  display: block;
  color: inherit;
  text-decoration: none;
}
.fb-card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  line-height: var(--lh-15);
}
.fb-card__summary {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  /* 摘要只给两行：列表是用来扫的，一条把四行读完就没有列表的意义了。 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
