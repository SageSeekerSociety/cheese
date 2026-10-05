<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAdminDashboard } from '@/composables/useAdminDashboard'

import { queue } from '@/lib/adminStats'
import AdminDashboardPageView from '@/views/admin/AdminDashboardPageView.vue'

// 管理后台的看板（§4.2）。**它读的是整个平台，不只是反馈。**
//
// 分类是这一页的骨架（需求方原话：「看板我觉得不是专门为反馈打造的？其它的也应该算
// 进去，比如 token 消费之类的，然后表也不要太多……不然人的注意力比较稀疏」）。各类
// 各占一屏，切到哪一类才拉哪一类 —— 服务端一分类一条接口（`/admin/stats/{…}`），而
// 其中用量那块读的是全平台增长最快的表（`resource_usage` 每调一次 `/v1/messages` 长
// 一行），没有理由让「看一眼反馈」把用量也读一遍。
//
// 一页里并列八张图是**没人读**的页面：每一块都有自己的尺度和自己的读者，摆在一起只会
// 让注意力平均分配，而那恰好等于没有重点。所以「表不要太多」不是删表，是**分屏**。
//
// 数字口径有两处是刻意的：
//
//   * **存量与窗口分开**。`counts` 是全量（现在一共多少条），`series` 是窗口内的
//     （这七天怎么变的）—— 页面上「现在是多少」和「这七天怎么走的」是两个问题，
//     合成一个数就会有一个是错的。窗口（7/30/90 天）由 store 的 `statsDays` 记，
//     缓存键就是窗口：切窗口把已加载的窗口类全部重拉（`setStatsDays`），不留多窗口
//     副本 —— 否则导轨短值会混窗口（用量卡显示 30 天的、反馈卡显示 7 天的）。
//   * **未定价的 token 单独算**。模型没有单价时，行上的 `cost_usd = 0.0` 意思是
//     「没有单价」而不是「免费」；把它们加进总额，会在几百万 token 上印一个
//     `$0.0000`，读起来像「这个月没花钱」。
//
// 墙上每个数字要么写得出点下去去哪，要么说得出**为什么不能点**（§6.3 那张准入表）。
// 机器那四个数是例外，而且是**说得出理由的例外**：平台里没有一页列设备，它们点不进
// 去，所以那一块只报数、不装成链接（口径是存量，见 `machines`）。「机器连败」那一栏
// 同理 —— 它曾经是跳回 `/admin` 的自链，假 affordance 比不点更糟，所以去掉了。
//
// 口径注脚（「这个数是怎么算的」）一律收进 `AdminNoteTip`（标题旁的 info 图标 +
// tooltip），不再以 12.5px 灰字散行叠在块底 —— 一屏七八条注脚等于没有重点。防误读级
// 的常驻注每屏至多一条（平台的 machines.note、性能的 perf.note）。
//
// 上面这一段是原来那个 2880 行页面的注释，一句没改 —— 它说的是这一页**是什么**；
// 现在这一只改的只是**它由谁画**。它是一只薄容器，和 `PanelChanges` / `PanelPreview`
// / `PanelDoc` 同一个形状，三件事各归各位：
//   - 取数（分类、窗口、轮询、时效戳、重试）→ `composables/useAdminDashboard.ts`；
//   - 画（页头、分类导轨、每屏的数、拆分、曲线、表）→ 同目录 `AdminDashboardPageView.vue`
//     只吃 props、只往上发事件，接住各屏的事件转出来（八屏能单独摆进预览站，
//     见 `views/demo/catalogDashboard.ts`）；
//   - 接线（路由那一条）→ 这里。
// 加取数动作在组合式函数里加，加画法在视图里加，这一只基本不再长。

defineOptions({ name: 'AdminDashboardPage' })

const router = useRouter()

const {
  kinds,
  tabs,
  titles,
  pulse,
  kind,
  days,
  windowed,
  loading,
  error,
  failed,
  stampText,
  pending,
  listLoading,
  pipeline,
  product,
  feedback,
  usage,
  platform,
  performance,
  integrations,
  selectKind,
  setDays,
  retry,
} = useAdminDashboard()

/** 图上点某一天 → 队列看那一天收进来的。**路由留在页面**：各屏只发「点了哪一天」，
 *  由这里决定它去哪（只有反馈那一屏发这个事件 —— 用量和平台没有对应的筛选参数）。 */
function onSelectDay(date: string | null) {
  if (!date) return
  void router.push(queue({ since: date.slice(0, 10) }))
}
</script>

<template>
  <AdminDashboardPageView
    :kinds="kinds"
    :tabs="tabs"
    :titles="titles"
    :pulse="pulse"
    :kind="kind"
    :days="days"
    :windowed="windowed"
    :loading="loading"
    :error="error"
    :failed="failed"
    :stamp="stampText"
    :pending="pending"
    :list-loading="listLoading"
    :pipeline="pipeline"
    :product="product"
    :feedback="feedback"
    :usage="usage"
    :platform="platform"
    :performance="performance"
    :integrations="integrations"
    @set-days="setDays"
    @select="selectKind"
    @retry="retry"
    @select-day="onSelectDay"
  />
</template>
