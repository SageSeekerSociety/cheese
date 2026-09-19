<script setup lang="ts">
// 内容在路上时，先画出它的形状。
//
// 判据（工作台的加载态都按这条挑）：**你知道等会儿长什么样，就画骨架；不知道要
// 等多久、也没有形状，就转圈。** 列表、消息流、看板的条目——形状是已知的，画骨架，
// 读的人一眼知道这块地方会长出什么、有多少；而一个居中的转圈只说了「在等」，把整块
// 区域的高度也一并藏了，内容到达时页面会跳一下。
// 反过来，「上传这张图」「保存这篇文档」是一个**动作**在进行，没有形状可画；一块
// 右半边可能是差异、可能是编辑器、可能是一张图、也可能是「二进制文件」提示的面板，
// 同样没有一个形状可画——那两种地方转圈才是对的，别把这个组件用过去。
//
// 形状写在这一个文件里，而不是每个调用点各画各的：几处各自拼骨头，就是几次跑偏的
// 机会。variant 对应的是**我们自己界面里真实存在的行**，不是通用的 article/card——
// 骨架的全部价值在于它和真东西同一个形状，尺寸对不上就只是一块会闪的灰色。所以下面
// 每一处尺寸都跟着它模仿的那一行走，改那一行的时候记得回来改这里。
//
// **一种形态只对一样东西负责。** 一个形态被两处共用，就等于赌这两样东西的行长得一
// 样——赌输了没人会发现，因为骨架只存在一瞬间，而且不会报错。实测过一次代价：看板
// 的卡是 101px 的带框块，「现场」的一条动作是 19–39px 的两行字，支线进度的一行是
// 55px，三者当时共用同一种「圆点 + 两行」的形态，于是看板那儿两条骨架换来一张卡、
// 「现场」那儿一条骨架换来半屏空白。宁可多一个 variant，也不要共用。
withDefaults(
  defineProps<{
    /**
     * 画哪一种行。每一种都指名道姓地对应界面里的一样东西：
     * - `list`   侧栏导航行（`TopicSidebar` 的 `.topic-row`，36px 一行）
     * - `chat`   聊天消息行（`ChatPanel` 的 `.im-row`，28px 头像 + 名字 + 一个气泡）
     * - `roster` 名册行（`TopicMembers` 的 `.roster__item`，26px 头像 + 两行字 + 小标）
     * - `entry`  支线进度（`TaskProgress`：一条分组小标 + 若干 55px 的 `.task-row`）
     * - `card`   看板的卡（`RunningWorkView` 的 `.board-card`，101px 的带框块）
     * - `site`   现场的一条动作（`PanelSite` 的 `.site-act`，8px 圆点 + 动作 + 参数）
     * - `brief`  一条活打开后的头（`PanelCard`：标题 + 一行元信息 + 简报那一段）
     * - `doc`    实况文档的正文（`DocEditor` 的 `.doc-prose`：小标题 + 几段 28.8px 的行）
     * - `feedback` 反馈中心的一条（`FeedbackCard` 的 `.fb-card`：左边支持按钮 + 标题 + 两行摘要 + 元信息一行）
     * - `comment`  反馈详情页的一条评论（`FeedbackCommentsFlat` 的 `.fb-say__item`：作者 + 时间 + 正文）
     * - `text`   一段正文
     */
    variant?: 'list' | 'chat' | 'roster' | 'entry' | 'card' | 'site' | 'brief' | 'doc' | 'feedback' | 'comment' | 'text'
    /** 画几行。默认值按各自最常见的一屏给，调用点通常不用传。 */
    rows?: number
  }>(),
  { variant: 'text', rows: 0 }
)

/** 每种形态默认画几行：够填满它所在的那块区域，又不至于假装内容比实际多。 */
const DEFAULT_ROWS: Record<string, number> = {
  list: 6,
  chat: 4,
  roster: 3,
  entry: 3,
  card: 2,
  site: 5,
  brief: 1,
  doc: 3,
  feedback: 6,
  comment: 2,
  text: 3,
}

/** 每一行的正文宽度都不一样，否则一排等长的灰条看着像表格而不像文字。 */
const WIDTHS = ['92%', '78%', '85%', '64%', '88%', '72%']

function width(i: number): string {
  return WIDTHS[i % WIDTHS.length]
}
</script>

<template>
  <!-- aria-busy + 一句给读屏软件的话：骨架对眼睛说「在加载」，对屏幕阅读器
       什么都没说——它读到的只是一堆空 div。 -->
  <div class="skel" :class="`skel--${variant}`" role="status" aria-busy="true" aria-live="polite">
    <span class="skel__sr">加载中</span>

    <!-- 聊天：消息现在是气泡，骨架也得是气泡形 —— 画一条灰线，等来的是一个带框的
         块，到货那一刻整列会重排一次。自己发的那一侧靠右（隔一条画一条）。 -->
    <template v-if="variant === 'chat'">
      <div
        v-for="i in rows || DEFAULT_ROWS.chat"
        :key="i"
        class="skel__chat"
        :class="{ 'skel__chat--self': i % 3 === 0 }"
        :style="{ '--skel-i': i }"
      >
        <div class="skel__bone skel__bone--avatar" />
        <div class="skel__chat-main">
          <div class="skel__bone skel__bone--name" />
          <div class="skel__bubble" :style="{ width: width(i) }" />
        </div>
      </div>
    </template>

    <template v-else-if="variant === 'roster'">
      <div v-for="i in rows || DEFAULT_ROWS.roster" :key="i" class="skel__roster" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--face" />
        <div class="skel__roster-main">
          <div class="skel__bone skel__bone--name" />
          <div class="skel__bone skel__bone--handle" />
        </div>
        <div class="skel__bone skel__bone--tag" />
      </div>
    </template>

    <!-- 支线进度：真东西是「一条分组小标（施工中 3）+ 那一组的行」。少画那条小标，
         行到齐的时候整段会被它顶下去 28px——实测就是这么跳的。 -->
    <template v-else-if="variant === 'entry'">
      <div class="skel__ehead">
        <div class="skel__bone skel__bone--dot skel__bone--dot-flat" />
        <div class="skel__bone skel__bone--group" />
      </div>
      <div v-for="i in rows || DEFAULT_ROWS.entry" :key="i" class="skel__entry" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--dot" />
        <div class="skel__entry-main">
          <div class="skel__bone skel__bone--line" :style="{ width: width(i) }" />
          <div class="skel__bone skel__bone--meta" :style="{ width: width(i + 3) }" />
        </div>
      </div>
    </template>

    <!-- 看板的卡。这里画的是**一个带框的块**，不是几条灰线：一列卡的轮廓本身就是
         「这儿有几件事」这个信息，只画线的话到货那一刻整列会重排一次。 -->
    <template v-else-if="variant === 'card'">
      <div v-for="i in rows || DEFAULT_ROWS.card" :key="i" class="skel__card" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--line" :style="{ width: width(i) }" />
        <div class="skel__card-row">
          <div class="skel__bone skel__bone--meta" :style="{ width: width(i + 2) }" />
          <div class="skel__bone skel__bone--pill" />
        </div>
        <div class="skel__card-rule" />
        <div class="skel__card-row">
          <div class="skel__bone skel__bone--dot skel__bone--dot-flat" />
          <div class="skel__bone skel__bone--meta skel__bone--phrase" />
          <div class="skel__bone skel__bone--when" />
        </div>
      </div>
    </template>

    <!-- 现场的一条动作：一行里是圆点 + 动词 + 参数。参数那一截照常画——「读了哪
         个文件」「跑了什么命令」是常态，不带参数的工具调用才是例外。时间不画：
         它悬停才出现，画上去就是承诺一个静止时没有的东西。 -->
    <template v-else-if="variant === 'site'">
      <div v-for="i in rows || DEFAULT_ROWS.site" :key="i" class="skel__site" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--sdot" />
        <div class="skel__bone skel__bone--meta skel__bone--verb" />
        <div class="skel__bone skel__bone--meta skel__bone--arg" :style="{ width: width(i) }" />
      </div>
    </template>

    <!-- 一条活打开后的头：标题、一行元信息、简报那一段。结论不画——只有交完活的
         卡才有结论，画上等于对每一张卡都许诺一段它多半没有的东西。 -->
    <template v-else-if="variant === 'brief'">
      <div class="skel__btitle">
        <div class="skel__bone skel__bone--dot" />
        <div class="skel__bone skel__bone--line" style="width: 62%" />
      </div>
      <div class="skel__bmeta">
        <div class="skel__bone skel__bone--meta" style="width: 46%" />
      </div>
      <div v-for="i in rows || DEFAULT_ROWS.brief" :key="i" class="skel__bblock" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--meta skel__bone--blockhead" />
        <div v-for="j in 3" :key="j" class="skel__bone skel__bone--body" :style="{ width: width(i + j) }" />
      </div>
    </template>

    <!-- 实况文档的正文。文档没有固定的形状，但它有固定的**节奏**：一条小标题带
         着几段字。画这个节奏，胜过画一片等长的灰条——更胜过现在这样，正文还在路
         上就先摆出一句「芝士会在这里维护文档」，那句话是说给空文档的。 -->
    <template v-else-if="variant === 'doc'">
      <div v-for="i in rows || DEFAULT_ROWS.doc" :key="i" class="skel__dsec" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--h2" :style="{ width: i % 2 ? '38%' : '30%' }" />
        <div v-for="j in i % 2 ? 3 : 2" :key="j" class="skel__bone skel__bone--p" :style="{ width: width(i + j) }" />
      </div>
    </template>

    <!-- 反馈中心的一条：左边是支持（32px 的正圆按钮 + 计数），右边是标题 + 状态芯片、
         两行摘要、元信息一行。它和 `card` 不是一回事：看板的卡没有左边那一列，也没有
         两行摘要；共用一种形态就会有一边对不上。 -->
    <template v-else-if="variant === 'feedback'">
      <div v-for="i in rows || DEFAULT_ROWS.feedback" :key="i" class="skel__fb" :style="{ '--skel-i': i }">
        <div class="skel__fb-vote">
          <div class="skel__bone skel__bone--vote" />
          <div class="skel__bone skel__bone--votecount" />
        </div>
        <div class="skel__fb-main">
          <div class="skel__fb-titlerow">
            <div class="skel__bone skel__bone--fbtitle" />
            <div class="skel__bone skel__bone--fbchip" />
          </div>
          <div class="skel__bone skel__bone--fbsum" :style="{ width: width(i) }" />
          <!-- 第二行：-webkit-line-clamp 的第二行本来就是半行，所以它固定短一截。 -->
          <div class="skel__bone skel__bone--fbsum skel__bone--fbsum-last" />
          <div class="skel__fb-meta">
            <div class="skel__bone skel__bone--meta skel__bone--fbauthor" />
            <div class="skel__bone skel__bone--fbtag" />
            <div class="skel__bone skel__bone--fbtag" />
          </div>
        </div>
      </div>
    </template>

    <!-- 反馈详情页的一条评论：作者 + 相对时间那一行，加下面的正文。 -->
    <template v-else-if="variant === 'comment'">
      <div v-for="i in rows || DEFAULT_ROWS.comment" :key="i" class="skel__cmt" :style="{ '--skel-i': i }">
        <div class="skel__cmt-head">
          <div class="skel__bone skel__bone--cmtauthor" />
          <div class="skel__bone skel__bone--meta skel__bone--cmttime" />
        </div>
        <div v-for="j in i % 2 ? 2 : 1" :key="j" class="skel__bone skel__bone--line" :style="{ width: width(i + j) }" />
      </div>
    </template>

    <template v-else-if="variant === 'list'">
      <div v-for="i in rows || DEFAULT_ROWS.list" :key="i" class="skel__list" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--line" :style="{ width: width(i) }" />
      </div>
    </template>

    <template v-else>
      <div v-for="i in rows || DEFAULT_ROWS.text" :key="i" class="skel__text" :style="{ '--skel-i': i }">
        <div class="skel__bone skel__bone--line" :style="{ width: width(i) }" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.skel {
  width: 100%;
}
/* 只给读屏软件的一行字。visibility/display 都不行——那两个会让读屏软件也读不到。 */
.skel__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* 骨头本身。--fill-2 是「面上再进一层」的那一档，浅深两套主题里都是离 --surface
   一步、看得见但不抢眼的灰，所以这里不需要为深色再写一遍。
   坐在 --canvas 上的地方（侧栏那条 rail）另说：那里 --fill-2 几乎与底同色，调用点
   用 --skel-bone 换成再深一档的那个值。 */
.skel__bone {
  position: relative;
  overflow: hidden;
  background: var(--skel-bone, var(--fill-2));
  border-radius: var(--radius-sm);
}
/* 扫光。它是这个组件里唯一一处非用户触发的动效，理由是它就是那个「还在等」的
   信号本身——不动的灰条和「加载失败留下一片空白」看起来一模一样。
   一行比一行晚一点起步：同时闪是一片灰在眨眼，错开之后是一道光扫过整块区域，
   动的总量没变，但它有了方向。 */
.skel__bone::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, var(--fill), transparent);
  animation: skel-sweep 1.6s ease-in-out infinite;
  animation-delay: calc(var(--skel-i, 0) * 80ms);
}
@keyframes skel-sweep {
  to {
    transform: translateX(100%);
  }
}
/* 全局那条 prefers-reduced-motion 兜底只是把时长压到 0.001ms，扫光会变成一个
   停在末帧的静止渐变。这里干脆整个关掉：aria-busy 已经把「在加载」这件事说清楚
   了，不需要靠动来说。 */
@media (prefers-reduced-motion: reduce) {
  .skel__bone::after {
    animation: none;
    background: none;
  }
}

/* 一根骨头占掉的竖直空间 = 它模仿的那行字的行盒，不是骨头本身的高度。所以每根
   骨头都比它替代的文字矮，靠上下 margin 把行盒补齐——这样内容到达时行不会挪。
   各处的 main 都是 flex 列：块级流里相邻 margin 会合并，合并掉的那几像素正好是
   行与行之间的距离。 */
.skel__bone--line {
  height: 12px;
  margin: 5px 0 6px; /* .t-body / .im-text：14px × 1.62 ≈ 23px 行盒 */
}
.skel__bone--meta {
  height: 10px;
  margin: 4px 0 5px; /* .t-meta：12.5px × 1.5 ≈ 19px 行盒 */
}
.skel__bone--name {
  width: 84px;
  height: 12px;
  margin: 5px 0 4px; /* .im-name 13px + .im-meta 的 2px：≈ 21px */
}
.skel__bone--handle {
  width: 56px;
  height: 10px;
  margin: 4px 0 3px; /* .roster__handle 11.5px × 1.5 ≈ 17px */
}
.skel__bone--tag {
  width: 40px;
  height: 16px;
  flex: none;
}
/* 头像、状态点的尺寸和圆角照抄真东西：ChatPanel 的 .im-avatar 是 28px / 8px，
   TopicMembers 的 .roster__avatar 是 26px / 8px，看板的 .board-dot 是 10px 的圆。
   对不上的话内容到达那一刻整行会挪一下，而骨架存在的意义正是不让它挪。 */
.skel__bone--avatar {
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 8px;
}
.skel__bone--face {
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 8px;
}
.skel__bone--dot {
  width: 10px;
  height: 10px;
  flex: none;
  margin-top: 5px; /* 压到第一行的中线上，同 .board-dot */
  border-radius: 50%;
}
/* 同一颗点，但坐在一条 align-items: center 的行里（分组小标、卡片的状态行），
   那儿不需要自己往下压。 */
.skel__bone--dot-flat {
  margin-top: 0;
}

/* 行的间距同样照抄真东西。 */
/* ChatPanel 的 .im-row：padding 4px 16px、margin-top 8px、gap 10px，对侧留白 8%。 */
.skel__chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 16px;
  padding-right: calc(16px + 8%);
  margin-top: 8px;
}
.skel__chat--self {
  flex-direction: row-reverse;
  padding-right: 16px;
  padding-left: calc(16px + 8%);
}
/* 气泡：几何逐条抄自 .im-text —— padding 7/12、--radius-lg、1px --line-2 的描边，
   里面一行 .t-body 的行盒 23px。合计 7+23+7+2 = 39px，同一条单行消息。 */
.skel__bubble {
  height: 39px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  border-top-left-radius: var(--radius-sm);
  background: var(--skel-bone, var(--fill-2));
}
.skel__chat--self .skel__bubble {
  margin-left: auto;
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-sm);
}
.skel__chat--self .skel__bone--name {
  margin-left: auto;
}
.skel__chat-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
}
/* TopicMembers 的 .roster__item：padding 6px 14px、gap 9px；外面那层 ul 是 4px 0。 */
.skel__roster {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 14px;
}
.skel__roster:first-of-type {
  margin-top: 4px;
}
.skel__roster:last-of-type {
  margin-bottom: 4px;
}
.skel__roster-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
}
/* TaskProgress 的 .task-progress__group：padding 8px 12px 2px、gap 6px，一条
   .t-meta 行 —— 合计 28.8px。 */
.skel__ehead {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px 2px;
}
.skel__bone--group {
  width: 64px;
  height: 10px;
  margin: 4px 0 5px;
}
/* TaskProgress 的 .task-row：padding 6px 10px、gap 8px，外面那层 ul 左右各 8px。 */
.skel__entry {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 10px;
  margin-inline: 8px;
}
.skel__entry-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  gap: 2px; /* .task-row__text 的 gap */
}
/* 这一处的两行之间隔着上面那 2px，所以正文那根骨头少留 1px 下边距，合起来仍是
   .task-row 的 55px。 */
.skel__entry-main .skel__bone--line {
  margin-bottom: 5px;
}

/* RunningWorkView 的 .board-card：padding 10px、margin-bottom 8px、gap 4px、
   1px 边框 + --radius-md，坐在 --canvas 上。里面四样东西照着卡自己的顺序来：
   标题（可两行，这里画一行）/ 房间·队友·负责人 / 一条分隔线 / 状态 + 时间。
   合计 10+23+4+19+4+7+4+19+10+2 = 102px，真卡 101px。 */
.skel__card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--canvas);
}
/* 卡里的标题行盒 22.7px（.t-body 14px × 1.62），比正文默认那根少 1px —— 两张卡
   叠起来那 1px 就会看出来。 */
.skel__card > .skel__bone--line {
  margin-bottom: 5px;
}
.skel__card-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
/* .board-card__rule：1px 的线，margin 4px 0 2px。它是真线不是骨头——一条 1px
   的灰线扫光看不出来，反而会让这一处比真卡多一次闪。 */
.skel__card-rule {
  height: 1px;
  margin: 4px 0 2px;
  background: var(--line);
}
/* .board-card__avatar：18px 的圆。 */
.skel__bone--pill {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: var(--radius-pill);
}
/* 状态短语（「等你采纳」「在跑」）占的宽度远短于一行。 */
.skel__bone--phrase {
  width: 72px;
  flex: none;
}
/* 右端那个时间：.board-card__when / .site-act__time 都是 11px 的一小截。 */
.skel__bone--when {
  width: 40px;
  height: 10px;
  flex: none;
  margin-left: auto;
}

/* PanelSite 的 .site-log：padding 12px、gap 4px（列）。一条 .site-act 是一行：
   5px 圆点 + 4em 宽的动词 + 占满剩下宽度的参数。 */
.skel--site {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
}
.skel__site {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 1px 6px;
}
.skel__bone--sdot {
  width: 5px;
  height: 5px;
  flex: none;
  border-radius: 50%;
}
/* 动词那一列是固定宽度的四个汉字，参数从它右边同一个 x 开始。 */
.skel__bone--verb {
  width: 4em;
  flex: none;
  margin: 0;
}
.skel__bone--arg {
  margin: 0;
}

/* PanelCard 的头三段，尺寸逐条照抄：
   .panel-card__title  padding 6px 0 0、gap 8px、一行 .t-body     → 28.7px
   .panel-card__meta   padding 2px 0 8px 18px、一行 .t-meta       → 28.8px
   .panel-card__block  padding 8px 0，小标 20.8px + 若干行 22.7px */
.skel__btitle {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding-top: 6px;
}
.skel__btitle .skel__bone--dot {
  margin-top: 11px; /* .t-body 行盒的中线，同 PanelCard 里那颗 .board-dot */
}
.skel__bmeta {
  padding: 2px 0 8px 18px;
}
.skel__bmeta .skel__bone--meta {
  margin: 0;
  height: 10px;
  margin-block: 4px 5px;
}
.skel__bblock {
  padding: 8px 0;
}
.skel__bone--blockhead {
  width: 32px;
  margin-bottom: 7px; /* .panel-card__block-head 的 padding-bottom 2px + 行盒 */
}
.skel__bone--body {
  height: 12px;
  margin: 5px 0 6px; /* .card-markdown 的一行：14px × 1.62 ≈ 22.7px */
}

/* DocEditor 的 .doc-prose：16px / 1.8（行盒 28.8px），段落 margin-bottom 12px；
   h2 是 21.12px / 29.568px，上下 margin 24.29 / 7.39。骨头比它替代的那行字矮，
   差额平摊到上下 margin 上，所以每一根仍然占满原来那个行盒。
   flex 列不合并 margin，这里的数字就是加出来的，不用为合并留余量。 */
.skel__dsec {
  display: flex;
  flex-direction: column;
}
.skel__bone--h2 {
  height: 16px;
  margin: 31px 0 14px; /* 24.29 + 6.78 / 7.39 + 6.78 */
}
/* 文档的第一样东西顶格，同 .doc-prose > :first-child。 */
.skel__dsec:first-of-type .skel__bone--h2 {
  margin-top: 0;
}
.skel__bone--p {
  height: 12px;
  margin: 8px 0 9px; /* (28.8 − 12) / 2，取整后仍是 28.8 的行盒 */
}
/* 一段的最后一行还要补上段落自己的 12px 下边距。 */
.skel__dsec .skel__bone--p:last-child {
  margin-bottom: 21px;
}

/* FeedbackCard 的 .fb-card：padding 16px、gap 12px、1px 描边，外面那层 .fb-list
   是 8px 的列间距；底色是 --surface（v-card 的默认），不是 --canvas。
   圆角写 --radius-lg：真卡是 24px（VCard 的 rounded="xl" 默认值，FeedbackCard.vue
   里有那段说明），而 24px 不在 stylelint 的白名单里，骨架也不该为了观感去动全仓的
   v-card 默认值。圆角不参与布局，这一处对不上不会让内容挪位。 */
.skel__fb {
  display: flex;
  gap: 12px;
  padding: 16px;
  margin-bottom: 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.skel__fb-vote {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding-top: 2px;
}
/* 支持按钮：v-btn icon size=small —— 32px 的正圆（.v-btn--icon 是 border-radius 50%）。 */
.skel__bone--vote {
  width: 32px;
  height: 32px;
  border-radius: 50%;
}
/* 它下面那一行计数：12px 等宽字，行盒 18px。 */
.skel__bone--votecount {
  width: 20px;
  height: 10px;
  margin: 4px 0;
}
.skel__fb-main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
}
/* 标题那一行：15px × 1.4 = 21px 的行盒，右边跟一枚状态芯片（19px）。 */
.skel__fb-titlerow {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.skel__bone--fbtitle {
  width: 46%;
  height: 13px;
  margin: 4px 0;
}
/* 状态芯片是胶囊（.fb-chip），高 19px。 */
.skel__bone--fbchip {
  width: 56px;
  height: 19px;
  flex: none;
  border-radius: var(--radius-pill);
}
/* 摘要两行：13px × 1.6 = 20.8px 的行盒。第一行占满（真文字就是这样），第二行短一截。
   合计 20.8 × 2 + 卡片自己那 8px 下边距 = 49.6px。 */
.skel__bone--fbsum {
  height: 12px;
  margin: 4px 0 5px;
}
.skel__bone--fbsum-last {
  width: 48%;
  margin-bottom: 13px;
}
/* 元信息一行：作者 · 时间、评论数、来源或标签的芯片。 */
.skel__fb-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}
.skel__bone--fbauthor {
  width: 88px;
}
/* 中性 chip：--fill 底、6px 圆角、高 19px。 */
.skel__bone--fbtag {
  width: 48px;
  height: 19px;
  border-radius: var(--radius-sm);
}

/* 反馈详情页的一条评论。.fb-say 是 16px 的列间距；一条评论是「作者 13px（行盒
   19.5px）+ 4px + 正文若干行」。 */
.skel__cmt {
  display: flex;
  flex-direction: column;
  margin-bottom: 16px;
}
.skel__cmt:last-of-type {
  margin-bottom: 0;
}
.skel__cmt-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.skel__bone--cmtauthor {
  width: 72px;
  height: 11px;
  margin: 4px 0 5px;
}
.skel__bone--cmttime {
  width: 52px;
}
/* 评论的正文是 .t-body（14px × 1.62 ≈ 22.7px），比默认那根骨头多 1px 的行盒。 */
.skel__cmt .skel__bone--line {
  margin: 5px 0 6px;
}

/* 侧栏的 .topic-row：min-height 36px、margin-block 2px；标题从左边 40px 处起
   （v-list--nav 的 8px + 行的 8px + 16px 状态槽 + 8px 间隔），右边留 24px。
   状态槽里那个点只有「等你 / 在跑」的话题才有，多数行是空的，所以不画。 */
.skel__list {
  display: flex;
  align-items: center;
  min-height: 36px;
  padding-inline: 40px 24px;
  margin-block: 2px;
}
.skel__list .skel__bone--line {
  margin: 0;
}
.skel__text {
  display: flex;
  flex-direction: column;
}
</style>
