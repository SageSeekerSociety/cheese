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
// 看哪一条的依据。所以它在卡片最左边，是这张卡上唯一一个带底色的按钮，其余全是文字。
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
// 卡片是**描边 + 填充、零阴影**（1px --line / --surface）：--surface 和 --canvas 在
// 浅色主题下只差 1.06:1，没有这圈描边的卡在浅色屏上等于没有边界；阴影是留给菜单、
// 弹窗和抽屉的（docs/design-system.md §3.4）。
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
  <!-- `rounded="lg"` 必须显式写：VCard 自带 `rounded="xl"`（24px）且是 `!important`，
       scoped 里写的 12px 压不过它，不写圆角就静默失效（docs/design-system.md §7.6）。 -->
  <v-card class="fb-card" rounded="lg">
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
          @click="store.toggleSupport(item.id)"
        >
          <v-icon size="18">{{ item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
        </v-btn>
      </template>
    </div>

    <router-link class="fb-card__body" :to="to">
      <span class="fb-card__title">{{ item.title }}</span>
      <p class="fb-card__summary t-body-readable">{{ item.summary }}</p>
      <!-- 底行：谁提的、什么时候、有多少人在等、走到哪一步。支持数从左边那一列挪到
           这里，是因为它和「评论 2」是同一类读数（这条有多少人参与），摆在一起才读得
           出对比；左边那一列留给动作本身。 -->
      <div class="fb-card__meta t-meta-read">
        <FeedbackAuthorAvatar
          :handle="item.author_handle"
          :is-agent="item.author_is_agent"
          :avatar-id="item.author_avatar_id"
          :size="18"
        />
        <span class="fb-card__byline">{{ item.author_handle }} · {{ relTime(item.created_at) }}</span>
        <span v-if="supportShown" class="fb-card__stat">
          支持
          <!-- 支持数在没支持过时用 --muted（元信息那一档），支持过才提到 --ink：
               这是不靠颜色的三个信号里的第三个（另两个是实心图标和文字）。 -->
          <span class="fb-card__count t-num" :class="{ 'c-muted': !item.supported }">{{ item.supports }}</span>
        </span>
        <span class="fb-card__stat t-num"> <v-icon size="13">mdi-comment-outline</v-icon>{{ item.comments }} </span>
        <!-- 私密 / 安全 / 来源 / 标签。挤不下时**先让这一块省略**，而不是把右边那个
             状态 chip 顶出去：状态是这一行里唯一一个每张卡都必须读到的字段。 -->
        <span class="fb-card__extras">
          <!-- 中性色，不是警告色：私密是一个事实（这条只有我、平台管理员、和提出它时
               在那个房间里的人能看见），不是一件需要被纠正的事。 -->
          <span v-if="isPrivate" class="chip-neutral" :title="PRIVATE_HINT">
            <v-icon size="12">mdi-lock-outline</v-icon>私密
          </span>
          <span v-if="item.security" class="chip-neutral">
            <v-icon size="12">mdi-shield-alert-outline</v-icon>安全
          </span>
          <span v-if="item.author_is_agent" class="chip-neutral">
            <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
          </span>
          <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
        </span>
        <FeedbackStatusChip class="fb-card__status" :status="item.status" />
      </div>
    </router-link>
  </v-card>
</template>

<style scoped>
.fb-card {
  display: flex;
  /* 132px 是这张卡的下限（§4.3）：底行和标题各占一行之后，剩下的高度给摘要。
     摘要满三行时卡片会长到装得下三行（3 × --lh-14-loose = 66px）—— 132 装不下
     「标题 + 三行摘要 + 底行」，所以这里写 min-height 而不是 height：写死高度就会
     把第三行裁掉，而这一版摘要从两行改三行（F-08）的全部意义就是让人在列表里读完。 */
  min-height: 132px;
  gap: 12px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  /* hover 只换底色和描边色，不位移：列表一屏十几行，每行抬 2px 会看成整列在跳
     （docs/design-system.md §9.1）。 */
  transition:
    background-color 0.12s ease,
    border-color 0.12s ease;
}
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
/* 图标占的是支持按钮那一格，颜色也跟那颗按钮的图标取同一个（--muted）：它是「这里
   没有按钮」的说明，不该比旁边那些真按钮更显眼。 */
.fb-card__withheld {
  color: var(--muted);
}
.fb-card__count {
  color: var(--ink);
}
/* 正文那一块就是那条链接。`color: inherit` + 去掉下划线是必须的：a 的默认样式会把
   整块正文染成链接蓝并加下划线，而这里读起来应该仍是一条正文，只是恰好能点开。 */
.fb-card__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 12px;
  color: inherit;
  text-decoration: none;
}
/* 标题一行就够：列表是用来扫的，标题折成两行会让每张卡的高度都不一样。 */
.fb-card__title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fb-card__summary {
  margin: 0;
  /* 摘要留在列表上（F-08）：提交的人要能确认「我写的东西被收到了」，点进详情才看得见
     等于没有。三行是上限 —— 再多这条就不再是列表里的一条，而是一篇正文。 */
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.fb-card__meta {
  display: flex;
  align-items: center;
  min-width: 0;
  height: 20px;
  gap: 8px;
  white-space: nowrap;
}
.fb-card__byline {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fb-card__stat {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
}
.fb-card__extras {
  display: inline-flex;
  flex: 1;
  align-items: center;
  min-width: 0;
  gap: 4px;
  overflow: hidden;
}
/* `margin-left: auto` 把状态推到最右边：底行左半边是「谁提的 / 多少人参与」，右半边
   是「走到哪一步」，两件事不该混在同一串文字里。 */
.fb-card__status {
  flex: none;
  margin-left: auto;
}
</style>
