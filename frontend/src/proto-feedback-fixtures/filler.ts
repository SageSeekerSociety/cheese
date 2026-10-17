/**
 * proto-feedback-fixtures/filler.ts — 补白行：把队列和折线撑到能看出规则的长度。
 *
 * 造的是形状不是真事（id 从 FB-1029 往下排，没有线程也没有备注）。时间的铺法、为什么
 * 要故意留白几天、三种优先级为什么都得有，都写在下面那段注释里。
 */
import type { FeedbackKind, FeedbackPriority, FeedbackStatus } from '@/cx_types'

/** 补白行：让队列真的长过一屏。
 *
 *  **为什么要有**：`handwritten.ts` 那十三条在 620 高的视口里只溢出 24px，于是「表头常驻」
 *  「只有表格自己滚」「一屏十七行」这几条在预览里**一条都演不出来** —— 量出来是
 *  「滚动量足够了 = false」。四个栏位也各要有十几条，否则点进去三两条，看着像没做完。
 *
 *  **时间的铺法**（这一轮改的）：从今天铺到 **30 天前**，中间**故意留空几天**。
 *  两件事都是为了看板那张按天的折线：每一天都有数据的话，「没有数据的那天补 0」
 *  这条规则在预览里一次都验不到（量出来是「缺口 = 0 天」）。所以：
 *
 *  * 第 6 天故意整天空着（下面 `h: 143` 和 `h: 169` 之间跨过了这一整天）——
 *    「过去 7 天」这一档里就有一个 0，而 7 天是看板的默认档。
 *  * 11 天以外只落在 11 / 12 / 14 / 15 / 17 / 19 / 21 / 22 / 24 / 26 / 28 / 30 上，
 *    跳过的那些天（13、16、18、20、23、25、27、29…）在 30 天那一档里就是缺口。
 *
 *  **三种优先级都要有**（low / normal / high / urgent）：队列页的分诊靠它，
 *  少了 low 那一档，那一档的样式在预览里根本画不出来。
 *
 *  **不冒充真实工单**：正文比 `handwritten.ts` 那几条短、没有评论线程、没有内部备注；
 *  id 从 FB-1029 往下排。手写那十三条是照着真事写的，这下面是形状。 */
export const FILLER: {
  kind: FeedbackKind
  title: string
  summary: string
  status: FeedbackStatus
  /** 优先级。不给就是 `normal`（`row()` 的默认值）。 */
  p?: FeedbackPriority
  author: string
  supports: number
  /** 多少小时前提的。 */
  h: number
  vis?: 'private'
  sec?: boolean
  agent?: boolean
  who?: string
}[] = [
  {
    kind: 'bug',
    title: '消息列表滚到底之后不再加载',
    summary: '翻到最后一页就不动了，得刷新整页才继续。',
    status: 'in_progress',
    p: 'normal',
    author: 'ligan',
    supports: 3,
    h: 30,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '深色模式下引用块和正文一个颜色',
    summary: '引用看不出来是引用，得对着格式工具栏猜。',
    status: 'received',
    p: 'low',
    author: 'chiruotong',
    supports: 1,
    h: 34,
  },
  {
    kind: 'suggestion',
    title: '话题列表想按最近活动排序',
    summary: '现在只能按创建时间，聊得最热的那几个沉在底下。',
    status: 'received',
    p: 'normal',
    author: 'maxiaoyu',
    supports: 4,
    h: 40,
    who: 'pengwenbo',
  },
  {
    kind: 'bug',
    title: '换头像之后要刷新才变',
    summary: '换完还是旧的那张，刷新一次才对。',
    status: 'resolved',
    p: 'low',
    author: 'caisongyang',
    supports: 2,
    h: 52,
    who: 'andylizf',
  },
  {
    kind: 'bug',
    title: '手机上弹起键盘后输入框跑到屏幕外',
    summary: '键盘一出来整页上移，光标看不见了。',
    status: 'in_progress',
    p: 'high',
    author: 'n1ctheboy',
    supports: 6,
    h: 58,
    who: 'andy',
  },
  {
    kind: 'other',
    title: '想一次导出话题里的全部文档',
    summary: '打包成 markdown 下载，现在只能一篇篇复制。',
    status: 'received',
    p: 'normal',
    author: 'pengwenbo',
    supports: 5,
    h: 66,
  },
  {
    kind: 'bug',
    title: '任务卡里的代码块没有复制按钮',
    summary: '每次都要手动选中一大段。',
    status: 'deployed',
    p: 'low',
    author: 'ligan',
    supports: 1,
    h: 72,
    who: 'andylizf',
  },
  {
    kind: 'suggestion',
    title: '侧栏的项目想能拖拽排序',
    summary: '项目一多就要从头找。',
    status: 'received',
    p: 'normal',
    author: 'chiruotong',
    supports: 3,
    h: 80,
  },
  {
    kind: 'bug',
    title: '点通知进去有时候是空页面',
    summary: '通知里带的那条链接偶尔指向已经不在的东西。',
    status: 'received',
    p: 'normal',
    author: 'maxiaoyu',
    supports: 2,
    h: 90,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '长文档写到大半会卡',
    summary: '三千字以上输入有明显延迟，一段字要等一下才出。',
    status: 'in_progress',
    p: 'high',
    author: 'caisongyang',
    supports: 7,
    h: 96,
    who: 'n1ctheboy',
  },
  {
    kind: 'suggestion',
    title: '想订阅某一条反馈',
    summary: '有动静的时候推给我，不想天天回来看。',
    status: 'received',
    p: 'normal',
    author: 'ligan',
    supports: 8,
    h: 104,
  },
  {
    kind: 'bug',
    title: '输入法组字时把拼音当查询发出去了',
    summary: '中文打字的时候列表一直闪，组完才稳。',
    status: 'resolved',
    p: 'low',
    author: 'chiruotong',
    supports: 4,
    h: 112,
    who: 'andy',
  },
  // ---- 缺口：`h: 112` 之后直接跳到 `h: 169` ----
  //
  // 上一档（`h: 112`）和这一档之间，**`[120h, 168h]` 这一段一条都没有**。一条反馈落在
  // 「6 天前那个 UTC 日」里的充要条件是它的 `h` 落在 `[120h, 168h]` —— 那个日子的两端
  // 是「今天已经过了 d 小时」的函数：它从 `120 + 24d` 小时前排到 `144 + 24d` 小时前，
  // d 在 `[0, 24)` 里取遍，并集正好是这一段。所以这一段空着 = **不管页面在一天里的
  // 哪个小时打开，7 天窗口里最早的那天都是 0**，折线上那个缺口是真的补出来的 0。
  //
  // 只按「h 差了多少」看会看错：`h: 143` 和 `h: 169` 差 26 小时，看着跨过了一整天，
  // 但页面在下午打开时它们是**同一天**里的两条。原来这里就是这么写的，缺口时有时无。
  {
    kind: 'other',
    title: '想看到每个项目用了多少额度',
    summary: '现在只有一个总数，不知道是哪个项目在烧。',
    status: 'received',
    p: 'normal',
    author: 'pengwenbo',
    supports: 6,
    h: 169,
  },
  {
    kind: 'bug',
    title: '表格里的数字没对齐',
    summary: '千分位之后列宽一直在跳。',
    status: 'deployed',
    p: 'low',
    author: 'maxiaoyu',
    supports: 1,
    h: 178,
    who: 'caisongyang',
  },
  {
    kind: 'suggestion',
    title: '评论里想能 @ 人',
    summary: '现在只能手打 handle，还得记住拼写。',
    status: 'received',
    p: 'normal',
    author: 'n1ctheboy',
    supports: 9,
    h: 180,
  },
  {
    kind: 'bug',
    title: '定时巡检连着两轮没跑',
    summary: '日志里干脆没有那一轮，只能等下一轮。',
    status: 'resolved',
    p: 'high',
    author: 'andy',
    supports: 5,
    h: 185,
    who: 'ligan',
  },
  {
    kind: 'bug',
    title: '从详情页复制代码会带上行号',
    summary: '粘到编辑器里还得一行行删。',
    status: 'received',
    p: 'low',
    author: 'caisongyang',
    supports: 2,
    h: 190,
    who: 'andylizf',
  },
  {
    kind: 'bug',
    title: '深色模式下透明图片是一块白',
    summary: '截图贴进来之后背景没跟着变。',
    status: 'in_progress',
    p: 'normal',
    author: 'pengwenbo',
    supports: 3,
    h: 195,
    who: 'andy',
  },
  {
    kind: 'suggestion',
    title: '机器列表想能按团队筛',
    summary: '几十台机器混在一起，找人借一台要翻半天。',
    status: 'received',
    p: 'normal',
    author: 'ligan',
    supports: 4,
    h: 200,
  },
  {
    kind: 'bug',
    title: '登出之后还能看见上一个账号的草稿',
    summary: '共享电脑上换人登录，抽屉里还是上一个人的字。',
    status: 'received',
    p: 'urgent',
    author: 'chiruotong',
    supports: 10,
    h: 205,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: 'agent 提的反馈在列表里看不出是 agent',
    summary: '得点进去看来源那一栏。',
    status: 'received',
    p: 'normal',
    author: 'cheese-c82aeb40',
    agent: true,
    supports: 2,
    h: 210,
  },
  {
    kind: 'suggestion',
    title: 'agent 想能自己认领一条反馈',
    summary: '现在只能在聊天里说一声让人去改。',
    status: 'received',
    p: 'normal',
    author: 'cheese-9f31d7c2',
    agent: true,
    supports: 3,
    h: 215,
  },
  {
    kind: 'bug',
    title: '私密反馈在管理端要一眼看得出来',
    summary: '和公开的挤在同一栏里长得一样。',
    status: 'received',
    p: 'normal',
    author: 'maxiaoyu',
    vis: 'private',
    supports: 1,
    h: 220,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '有一条写接口可以被匿名调用',
    summary: '没登录也能把它触发一遍。',
    status: 'in_progress',
    p: 'urgent',
    author: 'chiruotong',
    vis: 'private',
    sec: true,
    supports: 2,
    h: 230,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '日志里出现了会话凭证',
    summary: '排查的时候在前置机的日志里看到了。',
    status: 'received',
    p: 'high',
    author: 'n1ctheboy',
    vis: 'private',
    sec: true,
    supports: 4,
    h: 240,
  },
  // ---- 11~30 天：只有这一段才把窗口铺到 30 天前，跳过的天就是折线上的缺口 ----
  {
    kind: 'bug',
    title: '话题里的代码块打印时被截掉右边',
    summary: '长于一行的代码在导出 PDF 时右边被切掉。',
    status: 'resolved',
    p: 'high',
    author: 'pengwenbo',
    supports: 2,
    h: 264,
    who: 'andylizf',
  },
  {
    kind: 'suggestion',
    title: '机器列表想标出哪几台是平台托管的',
    summary: '自托管和托管混在一张表里，分不出来。',
    status: 'received',
    p: 'normal',
    author: 'chiruotong',
    supports: 4,
    h: 290,
  },
  {
    kind: 'bug',
    title: '任务卡上的验收按钮在窄屏上换行',
    summary: '390px 下按钮掉到第二行，和旁边那个挤在一起。',
    status: 'deployed',
    p: 'normal',
    author: 'caisongyang',
    supports: 1,
    h: 336,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '通知里的话题名被截成两行',
    summary: '一行放得下的名字被折成两行，列表跟着跳。',
    status: 'in_progress',
    p: 'low',
    author: 'maxiaoyu',
    supports: 3,
    h: 362,
    who: 'pengwenbo',
  },
  {
    kind: 'suggestion',
    title: '想给话题加一个「只看结论」的开关',
    summary: '结论散在对话里，只想把结论挑出来读。',
    status: 'received',
    p: 'normal',
    author: 'ligan',
    supports: 6,
    h: 410,
  },
  {
    kind: 'bug',
    title: '搜索结果里的话题点进去是空的',
    summary: '搜到的话题打得开，里面一条消息都没有。',
    status: 'resolved',
    p: 'high',
    author: 'n1ctheboy',
    supports: 2,
    h: 458,
    who: 'andy',
  },
  {
    kind: 'other',
    title: '想按周收到一份用量邮件',
    summary: '不想天天进来看，一周一封就够。',
    status: 'received',
    p: 'low',
    author: 'pengwenbo',
    supports: 5,
    h: 505,
  },
  {
    kind: 'bug',
    title: 'agent 的会话在侧栏里没有区分',
    summary: '我开的和 agent 开的排在一起，长得一样。',
    status: 'received',
    p: 'normal',
    author: 'cheese-4a1d90f3',
    agent: true,
    supports: 1,
    h: 530,
  },
  {
    kind: 'bug',
    title: '浅色模式下选中行的底色和 hover 分不开',
    summary: '指到哪儿和选中哪儿，看着是同一块颜色。',
    status: 'deployed',
    p: 'low',
    author: 'chiruotong',
    supports: 2,
    h: 578,
    who: 'andylizf',
  },
  {
    kind: 'bug',
    title: '导出 markdown 时表格丢了表头',
    summary: '导出来的表格第一行是数据，表头整行没了。',
    status: 'resolved',
    p: 'normal',
    author: 'caisongyang',
    vis: 'private',
    supports: 4,
    h: 626,
    who: 'andy',
  },
  {
    kind: 'suggestion',
    title: '想能一次把几条反馈标成已解决',
    summary: '分诊完一批要一条条点，手都点酸了。',
    status: 'in_progress',
    p: 'normal',
    author: 'ligan',
    supports: 3,
    h: 672,
    who: 'andy',
  },
  {
    kind: 'bug',
    title: '登录之后跳回上一条话题而不是首页',
    summary: '在别人机器上登录，落地的是别人上次看的那条话题。',
    status: 'deployed',
    p: 'high',
    author: 'maxiaoyu',
    supports: 7,
    h: 720,
    who: 'andylizf',
  },
]
