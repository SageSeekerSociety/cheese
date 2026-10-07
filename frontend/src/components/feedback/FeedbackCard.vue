<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import { computed } from 'vue'

import FeedbackAuthorAvatar from './FeedbackAuthorAvatar.vue'
import { kindLabel } from './feedbackLabels'
import FeedbackStatusChip from './FeedbackStatusChip.vue'

import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'
import { isClosed } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 反馈列表里的一**行**（不是一张卡）。
//
// ## 一行，不是一张卡
//
// 改之前每条自带一圈描边、圆角和 16px 间距，一屏九条；管理端的队列行是同一张面上的
// 分隔行，一屏十三行。两者说的是同一件事（一串要扫的条目）。现在外壳（`FeedbackList`）
// 给一张面，行与行之间一条 1px `--line`，行自己不带边框 —— 少掉的是每条上下各 16px
// 的空白和 4px 的圆角弧线，多出来的是一屏能看完的条数。
//
// ## 摘要只在**和标题不一样**的时候画
//
// 提交表单里标题和摘要本来就常是同一句话（后端也没有摘要栏，摘要就是从正文里取的第一
// 段）。两者一模一样时这一行只是把上面那行字再念一遍，占掉 40px 却一个字都没多说 ——
// 而列表的每一行都在抢同一屏的高度。判据是**去掉首尾空白之后相等**：抄一遍改个空格
// 不算「不一样」，而那种差别没有一个读者看得出来。
//
// ## 支持按钮在行的右下角，而不在左边一列
//
// 改之前是**两个地方各画一遍**：左边单独占一根竖栏放那颗 👍，底行又写了一句「支持 N」。
// 同一件事画两遍，既让底行挤，也让左边那根竖栏白白吃掉约 40px 的正文宽度。现在只有
// 一处：底行最右边那颗按钮，图标 + 数字。
//
// 「已支持」用**中性色**（tonal + secondary），不用琥珀：一屏里琥珀只给唯一的主操作
// （这一页是「提交反馈」），支持是一个可反复切换的状态，它变琥珀会让主操作不再是唯一
// 那个显眼的东西（docs/design-system.md §1.6）。状态本身有三个不依赖颜色的信号：实心的
// 拇指图标、数字的颜色、以及可访问名字里的「已支持」。
//
// ## 开详情那条链接**只包正文**
//
// 不是整行 `@click` + `router.push`：那条路以前走过，鼠标能用，键盘和读屏**完全够不着**
// —— 一行不是一个可聚焦的东西，Tab 会整段跳过去，中键开新标签页、右键复制链接也都没
// 有。现在它是一条真的 `<a href="/feedback/…">`，浏览器原生那套全回来了。
//
// 支持按钮**必须在链接外面**：`<a>` 里不能嵌 `<button>`（HTML 规范里那是「交互内容套
// 交互内容」），而且真嵌进去之后点支持会顺手打开详情。
//
// ## 不能公开的条目没有支持按钮
//
// （私密，或管理员标了安全问题）—— 它不该让人知道它存在，而支持是公开表态。
//
// 底行的一串读数是同一档东西（谁提的、什么时候、多少人参与、走到哪一步），一起用
// `.t-meta-read` 淡下去，读者先看到标题和摘要。不用等宽的 `.t-meta`：mono 栈没有
// CJK 字形，「19小时前」会在一格里混两种字体，作者名也读着像代码；数字对齐由 `.t-num` 给。
//
// 数据是 `FeedbackCard`（后端 `schemas.FeedbackCard`）本身，**不在这里转成第二种形状**。
const props = defineProps<{ item: FeedbackCard }>()

const store = useFeedbackStore()

/** 办完了的反馈在列表里不再喊人支持：它已经办完了。收尾有几种（修复、上线、不修复），
 *  判断走 lib/feedbackMeta 的 `isClosed`，别在这里再写一次「不是 resolved」。 */
const supportable = computed(() => !isClosed(props.item.status))
const isPrivate = computed(() => props.item.visibility === 'private')
/** 支持按钮能不能出现。私密和安全问题都不行 —— 理由见上面那段注释。 */
const supportShown = computed(() => !isPrivate.value && !props.item.security)

/** 摘要和标题是同一句话时，这一行不画（见文件头）。 */
const showSummary = computed(() => {
  const summary = props.item.summary?.trim()
  return !!summary && summary !== props.item.title?.trim()
})

const kind = computed(() => kindLabel(props.item.kind))
/** 按钮上只有图标和数字，读屏念不出「这是支持、现在几个人」。所以把这两个信息写进
 *  可访问名字里 —— 光靠 `title` 不够，触屏和读屏都读不到它。 */
const supportLabel = computed(() =>
  props.item.supported
    ? t('feedback.card.unsupportAria', { n: props.item.supports })
    : t('feedback.card.supportAria', { n: props.item.supports })
)
/** 正文那一块整块是一条去详情页的链接。走路由名，路径字符串不在这里再写一遍。 */
const to = computed(() => ({ name: 'FeedbackDetail', params: { id: props.item.id } }))
</script>

<template>
  <div class="fb-card">
    <NavLink class="fb-card__body" :to="to">
      <span class="fb-card__title" data-user-content>{{ item.title }}</span>
      <p v-if="showSummary" class="fb-card__summary t-body-readable" data-user-content>{{ item.summary }}</p>
    </NavLink>

    <!-- 底行在链接外面（支持按钮是一颗真按钮，理由见文件头）。左半边是「哪一类、谁提的、
         多少人参与」，右半边是「走到哪一步」和「我能做什么」。 -->
    <div class="fb-card__meta t-meta-read t-num">
      <span v-if="kind" class="chip-neutral fb-card__kind">{{ kind }}</span>
      <FeedbackAuthorAvatar
        class="fb-card__avatar"
        :handle="item.author_handle"
        :is-agent="item.author_is_agent"
        :avatar-id="item.author_avatar_id"
        :size="18"
      />
      <span class="fb-card__byline">{{ item.author_handle }} · {{ relTime(item.created_at) }}</span>
      <span class="fb-card__stat t-num"> <v-icon size="13">mdi-comment-outline</v-icon>{{ item.comments }} </span>
      <!-- 私密 / 安全 / 来源 / 标签。挤不下时**先让这一块省略**，而不是把右边那两颗
           顶出去：状态是这一行里唯一一个每行都必须读到的字段。 -->
      <span class="fb-card__extras">
        <!-- 中性色，不是警告色：私密是一个事实，不是一件需要被纠正的事。 -->
        <span v-if="isPrivate" class="chip-neutral" :title="t('feedback.privateHint')">
          <v-icon size="12">mdi-lock-outline</v-icon>{{ t('feedback.private') }}
        </span>
        <span v-if="item.security" class="chip-neutral">
          <v-icon size="12">mdi-shield-alert-outline</v-icon>{{ t('feedback.security') }}
        </span>
        <span v-if="item.author_is_agent" class="chip-neutral">
          <v-icon size="12">mdi-robot-outline</v-icon>{{ t('feedback.source.agent') }}
        </span>
        <span v-for="tag in item.tags ?? []" :key="tag" class="chip-neutral">{{ tag }}</span>
      </span>
      <FeedbackStatusChip class="fb-card__status" :status="item.status" />
      <!-- 支持。`margin-left: auto` 在状态那一颗上，把它连同这一颗一起推到右边。 -->
      <!-- eslint-disable-next-line vue/no-restricted-syntax -- the support toggle has a selected state BaseButton lacks -->
      <v-btn
        v-if="supportShown"
        class="fb-card__support-btn"
        size="small"
        :variant="item.supported ? 'tonal' : 'text'"
        color="secondary"
        :disabled="!supportable"
        :aria-label="supportLabel"
        :title="
          supportable
            ? item.supported
              ? t('feedback.card.unsupport')
              : t('feedback.card.support')
            : t('feedback.closedHint')
        "
        @click="store.toggleSupport(item.id)"
      >
        <v-icon size="16" start>{{ item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
        <!-- 数字一直在，包括 0：「还没有人支持」本身就是这一栏要读的信息。 -->
        <span class="fb-card__count t-num" :class="{ 'c-muted': !item.supported }">{{ item.supports }}</span>
      </v-btn>
    </div>
  </div>
</template>

<style scoped>
/* 一行。**没有自己的边框和圆角** —— 那两样在外壳（`FeedbackList` 的 `.fb-list`）身上，
   分隔线是行与行之间那一条。写在这里的话，一屏十几行就是十几圈描边叠在一起。 */
.fb-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  /* hover 只换底色，不位移：列表一屏十几行，每行抬 2px 会看成整列在跳
     （docs/design-system.md §9.1）。 */
  transition: background-color 0.12s ease;
}
.fb-card + .fb-card {
  border-top: 1px solid var(--line);
}
.fb-card:hover {
  background: var(--fill);
}
.fb-card__count {
  color: var(--ink);
}
/* 正文那一块就是那条链接。`color: inherit` + 去掉下划线是必须的：a 的默认样式会把整块
   正文染成链接蓝并加下划线，而这里读起来应该仍是一条正文，只是恰好能点开。 */
.fb-card__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
  color: inherit;
  text-decoration: none;
}
/* 标题一行就够：列表是用来扫的，标题折成两行会让每行的高度都不一样。 */
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
  /* 摘要留在列表上（F-08）：提交的人要能确认「我写的东西被收到了」。
     **两行是上限**（原先是三行）：一行 = 标题 + 摘要 + 底行要压到 96px 上下，三行
     摘要一撑就是 118px，一屏差出两条。两行足够对上「我写的那句在不在」，再多的
     正文本来就该点进详情读。 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.fb-card__meta {
  display: flex;
  align-items: center;
  min-width: 0;
  /* 28 而不是 20：底行有真按钮，行高不够时它会被上下裁掉一点（Vuetify 的小按钮量下来
     28px 上下，`align-items: center` 只能让它居中溢出）。 */
  height: 28px;
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
/* `margin-left: auto` 把状态（以及它右边的支持按钮）推到最右边：底行左半边是
   「谁提的 / 多少人参与」，右半边是「走到哪一步 / 我能做什么」。 */
.fb-card__status {
  flex: none;
  margin-left: auto;
}
.fb-card__support-btn {
  flex: none;
  /* 缩小左右内边距：默认的 16px 让「图标 + 一位数」占掉 70px 上下，而它旁边还有状态
     那一颗。上下内边距跟着 `size="small"` 不动 —— 触控目标的高度不该被压。 */
  padding-inline: 8px;
}

/* 窄屏：底行折成**两行网格**。
 *
 * 一条信息都不消失，因为在 360px 下单行根本装不下。判据是五样东西各自落位、一个都
 * 不少：作者是谁 → 第一行；评论多少 → 第一行右端；是不是私密/安全 → 第二行左端；
 * 走到哪一步 → 第二行；我能做什么 → 第二行右端。
 *
 *   第一行（身份）：头像 | 作者名 · 时间（可折行，**不截断**） | 评论数
 *   第二行（属性/状态/动作）：私密/安全/AI队友/标签（可折行） | 状态 | 支持 */
/* 断点收进共享 token：767.98 = $bp-phone（styles/breakpoints.scss）。 */
@media (max-width: 767.98px) {
  .fb-card__meta {
    display: grid;
    grid-template-columns: auto auto minmax(0, 1fr) auto auto;
    /* 类型那一颗**自己一格**（第一行最前面）：它比作者更该先读到（这一条是 bug 还是
       建议）。放进下面那一格会和 `.fb-card__extras` 抢同一个网格区域，两块内容叠在
       一起。 */
    grid-template-areas:
      'kind   avatar byline byline stat'
      'extras extras extras status support';
    align-items: center;
    gap: 8px;
    height: auto;
    /* 放开基类的 `nowrap`：折行的是 byline 那一格，chip 自带 no-wrap 不受影响。 */
    white-space: normal;
  }
  .fb-card__kind {
    grid-area: kind;
  }
  .fb-card__avatar {
    grid-area: avatar;
  }
  .fb-card__byline {
    grid-area: byline;
    white-space: normal;
    overflow: visible;
    overflow-wrap: anywhere;
    text-overflow: clip;
  }
  .fb-card__stat {
    grid-area: stat;
  }
  /* 第二行的左半边：整格给它，chips 自己折行。装不下时**仍然由 `overflow: hidden`
     从尾部裁**（私密 / 安全排在标签前面，所以先让位的永远是标签那一端）。 */
  .fb-card__extras {
    grid-area: extras;
    flex-wrap: wrap;
    overflow: hidden;
  }
  /* 网格里 `margin-left: auto` 不再参与对齐（轨道已经把它放在右端）。 */
  .fb-card__status {
    grid-area: status;
    margin-left: 0;
  }
  /* 这一颗是行上唯一「点一下就完成」的动作，也是拇指最先够到的右下角。 */
  .fb-card__support-btn {
    grid-area: support;
    min-height: 36px;
    padding-inline: 12px;
  }
}
</style>
