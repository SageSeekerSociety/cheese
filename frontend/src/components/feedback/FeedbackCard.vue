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
// ## 支持按钮在**卡片右下角**，而不在左边一列
//
// 改之前是**两个地方各画一遍**：左边单独占一根竖栏放那颗 👍，底行又写了一句「支持 N」。
// 同一件事画两遍，既让底行挤，也让左边那根竖栏白白吃掉约 40px 的正文宽度 —— 而它对
// 「一屏能看几行」没有任何贡献（行是横着排的，竖栏占的是宽度不是高度）。
//
// 现在只有一处：底行最右边那颗按钮，图标 + 数字。它和「评论 2」是同一类读数（这条有
// 多少人参与），摆在同一条线上才读得出对比；而按按钮的位置是**卡的右边**，这是列表里
// 「我能对这条做什么」的常规落点。
//
// 「已支持」用**中性色**（tonal + secondary），不用琥珀：一屏里琥珀只给唯一的主操作
// （这一页是「提交反馈」），支持是一个可反复切换的状态，它变琥珀会让主操作不再是唯一
// 那个显眼的东西（docs/design-system.md §0）。状态本身有三个不依赖颜色的信号：实心的
// 拇指图标、数字的颜色、以及可访问名字里的「已支持」。
//
// ## 开详情那条链接**只包标题和摘要**
//
// 不是整张卡 `@click` + `router.push`：那条路以前走过，鼠标能用，键盘和读屏**完全够不着**
// —— 一张卡不是一个可聚焦的东西，Tab 会整段跳过去，中键开新标签页、右键复制链接也都没
// 有。现在它是一条真的 `<a href="/feedback/…">`，浏览器原生那套全回来了。
//
// 支持按钮**必须在链接外面**：`<a>` 里不能嵌 `<button>`（HTML 规范里那是「交互内容套
// 交互内容」），而且真嵌进去之后点支持会顺手打开详情。所以底行整行都在链接之外，链接只
// 包正文 —— 卡片上最该被读到、也最该被点开的那两行仍然整块可点。
//
// ## 不能公开的条目没有支持按钮
//
// （私密，或管理员标了安全问题）—— 它不该让人知道它存在，而支持是公开表态：它决定
// 「热门」怎么排、管理员先看哪条。这类卡片底行右侧就只剩状态那一颗。
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
/** 按钮上只有图标和数字，读屏念不出「这是支持、现在几个人」。所以把这两个信息写进
 *  可访问名字里 —— 光靠 `title` 不够，触屏和读屏都读不到它。 */
const supportLabel = computed(() => {
  const count = `当前 ${props.item.supports} 人支持`
  return props.item.supported ? `取消支持，${count}` : `支持这个反馈，${count}`
})
/** 正文那一块整块是一条去详情页的链接。走路由名，路径字符串不在这里再写一遍。 */
const to = computed(() => ({ name: 'FeedbackDetail', params: { id: props.item.id } }))
</script>

<template>
  <!-- `rounded="lg"` 必须显式写：VCard 自带 `rounded="xl"`（24px）且是 `!important`，
       scoped 里写的 12px 压不过它，不写圆角就静默失效（docs/design-system.md §7.6）。 -->
  <v-card class="fb-card" rounded="lg">
    <router-link class="fb-card__body" :to="to">
      <span class="fb-card__title">{{ item.title }}</span>
      <p class="fb-card__summary t-body-readable">{{ item.summary }}</p>
    </router-link>

    <!-- 底行在链接外面（支持按钮是一颗真按钮，理由见文件头）。左半边是「谁提的、
         多少人参与」，右半边是「走到哪一步」和「我能做什么」。 -->
    <div class="fb-card__meta t-meta-read">
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
           顶出去：状态是这一行里唯一一个每张卡都必须读到的字段。 -->
      <span class="fb-card__extras">
        <!-- 中性色，不是警告色：私密是一个事实（这条只有我、平台管理员、和提出它时
             在那个房间里的人能看见），不是一件需要被纠正的事。 -->
        <span v-if="isPrivate" class="chip-neutral" :title="PRIVATE_HINT">
          <v-icon size="12">mdi-lock-outline</v-icon>私密
        </span>
        <span v-if="item.security" class="chip-neutral"> <v-icon size="12">mdi-shield-alert-outline</v-icon>安全 </span>
        <span v-if="item.author_is_agent" class="chip-neutral">
          <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
        </span>
        <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
      </span>
      <FeedbackStatusChip class="fb-card__status" :status="item.status" />
      <!-- 支持。`margin-left: auto` 在状态那一颗上，把它连同这一颗一起推到右边。
           撑开的那份空白落在标签和状态之间（`--extras` 是 flex:1 且会省略），所以窄屏
           上先被吃掉的是标签，不是这两颗。 -->
      <v-btn
        v-if="supportShown"
        class="fb-card__support-btn"
        size="small"
        :variant="item.supported ? 'tonal' : 'text'"
        color="secondary"
        :disabled="!supportable"
        :aria-label="supportLabel"
        :title="supportable ? (item.supported ? '取消支持' : '支持') : '已办完，无需再支持'"
        @click="store.toggleSupport(item.id)"
      >
        <v-icon size="16" start>{{ item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
        <!-- 数字一直在，包括 0：「还没有人支持」本身就是这一栏要读的信息。 -->
        <span class="fb-card__count t-num" :class="{ 'c-muted': !item.supported }">{{ item.supports }}</span>
      </v-btn>
    </div>
  </v-card>
</template>

<style scoped>
.fb-card {
  display: flex;
  flex-direction: column;
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
.fb-card__count {
  color: var(--ink);
}
/* 正文那一块就是那条链接。`color: inherit` + 去掉下划线是必须的：a 的默认样式会把
   整块正文染成链接蓝并加下划线，而这里读起来应该仍是一条正文，只是恰好能点开。 */
.fb-card__body {
  display: flex;
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
  /* 24 而不是 20：底行现在有真按钮，行高不够时它会被上下裁掉一点（Vuetify 的小按钮
     量下来 28px 上下，`align-items: center` 只能让它居中溢出）。 */
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
   「谁提的 / 多少人参与」，右半边是「走到哪一步 / 我能做什么」，两件事不该混在同一串
   文字里。 */
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
 * 一条信息都不消失，因为在 360px 下单行根本装不下：卡片内容宽 296px（360 − 页边距
 * 2×16 − 卡片内边距 2×16），而底行的固有项（头像 18 + 评论数 ~28 + 状态 chip ~62 +
 * 支持按钮 ~60 + 五个 8px 间隙）就先吃掉 208px，留给「作者名 + 私密/安全/标签」的只剩
 * 约 88px。那一串是这张卡上**唯一一处不写省略号、直接被 `overflow:hidden` 裁没**的
 * 东西 —— 于是标签在手机上等于不存在，而「私密」「安全」那两个标记也跟着一起消失，
 * 那是这张卡上最不能丢的两个词。
 *
 * 所以这里不抠宽度，换布局：
 *
 *   第一行（身份）：头像        | 作者名 · 时间（可折行，**不截断**） | 评论数
 *   第二行（属性/状态/动作）：私密/安全/AI队友/标签（可折行） | 状态 | 支持
 *
 * 判据是五样东西各自落位、一个都不少：作者是谁 → 第一行；评论多少 → 第一行右端；
 * 是不是私密/安全 → 第二行左端；走到哪一步 → 第二行；我能做什么 → 第二行右端。
 * 标题和摘要的省略**留着不动** —— 那是列表里刻意的扫描形态（带省略号），不是这次
 * 要修的「静默消失」。
 *
 * 代价：这些卡在窄屏上高约 30px，一屏少放一张。换来的是上面那两串字真的在。 */
@media (max-width: 599.98px) {
  .fb-card__meta {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    grid-template-areas:
      'avatar byline byline stat'
      'extras extras   status support';
    align-items: center;
    gap: 8px;
    height: auto;
    /* 放开基类的 `nowrap`：折行的是 byline 那一格，chip 自带 no-wrap 不受影响。 */
    white-space: normal;
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
     从尾部裁**（私密 / 安全排在标签前面，所以先让位的永远是标签那一端）。
     这里一度写的是 `overflow: visible`（想「别静默裁掉」），那是错的：chip 是
     `white-space: nowrap` 的，它既折不了行也不会自己缩，放开裁剪的结果是**一个特别长的
     标签直接压到旁边的状态和支持按钮上面** —— 从「静默少一个标签」变成「盖住别人」，
     两个都不对。标签是用户自己填的、没有长度上限（`cleanTags` 只管去重和条数），
     所以这一格必须留着裁剪。真正该消失的从来不是「裁」这件事，而是「裁掉的是私密/安全」
     那一种 —— 现在这两个永远排在最前、永远装得下。 */
  .fb-card__extras {
    grid-area: extras;
    flex-wrap: wrap;
    overflow: hidden;
  }
  /* 网格里 `margin-left: auto` 不再参与对齐（轨道已经把它放在右端），留着会在格子里
     再顶一次。 */
  .fb-card__status {
    grid-area: status;
    margin-left: 0;
  }
  /* 这一颗是卡片上唯一「点一下就完成」的动作，也是拇指最先够到的右下角。全站还没有
     触控尺寸的成文规定（见话题文档），这是给这一处先定的一档：36px，比 `size="small"`
     的 28 高一头。 */
  .fb-card__support-btn {
    grid-area: support;
    min-height: 36px;
    padding-inline: 12px;
  }
}
</style>
