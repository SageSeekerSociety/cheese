/* ---- 看板：一个分类一条接口 -------------------------------------------
 *
 * 服务端把看板拆成三块（`backend/app/api/routes/admin_stats.py`），**切到哪一类才拉
 * 哪一类**：合成一条的话，切到第二、第三类时读的是几十秒前的数，而这三块里有两块读
 * 的是全平台增长最快的表（`resource_usage` 每调一次 `/v1/messages` 长一行）。
 *
 * `days` 默认 7（就是页头上那句「过去 7 天」）。窗口是**页面**问的问题，所以由调用方
 * 给，不写死在这里。
 *
 * ⚠️ 这里以前是一条 `/admin/feedback/stats`。服务端把它拆成下面这三条之后，前端有
 * 一段时间还指着老路 —— 而老路上没有路由了，`stats` 就落进 `/admin/feedback/{id}`
 * 那条动态段，回来的是一句「不是合法 uuid」的 400，报错指向的地方和原因差很远。
 * 三块各自有名字之后，这种「路径悄悄指向另一个资源」不可能再发生。
 */

/** 反馈那一块：**全量口径**的总量 / 四栏 / 四级状态，加窗口内按天的三条曲线。
 *
 *  口径是这个类型的全部内容：看板数的是**整个板子**，不是公开那一臂。此前
 *  `counts` 走的是反馈中心那一行标签页的数（被 `PUBLIC_ONLY` 收窄，因为匿名读者
 *  不该从一个数字里得知私密反馈有多少），而旁边的 `series` 是全量 —— 卡片和曲线
 *  各答各的问题。现在两边都是全量，形状上也把三个切口分开写，不再挤在一个扁平的
 *  字典里让人猜哪个是哪个。
 *
 *  `columns` 的四个是**筛选，不是划分**（`agent` 是来源，和公开/私密重叠），所以
 *  它们**加起来不等于** `total.all` —— 和队列那四栏是同一批判据。
 */
export interface StatsFeedback {
  days: number
  /** 一句话答完的几个数。`open`/`closed` 用的是和别处同一个 `CLOSED_STATUSES`。 */
  total: {
    all: number
    open: number
    closed: number
    unassigned: number
    /** 压着没人管的急件（high/urgent 且未办完）—— 分诊台最该先动的一格。 */
    urgent_open: number
  }
  /** 队列那四栏，重拼成计数。**加起来不等于 `total.all`**，理由见上。 */
  columns: { public: number; private: number; agent: number; security: number }
  /** 梯子上的每一级，全量。这是四处里唯一并排展示四级状态的地方。 */
  status: { received: number; in_progress: number; resolved: number; deployed: number; declined: number }
  /** 这个管理员自己的未读数 —— 人各一份，和板子有多大无关。 */
  unread: number
  /** 长度恒等于 `days`、最早的一天在前。缺的那天是 0，不是一段缺口。 */
  series: { date: string; created: number; resolved: number; deployed: number }[]
  /** 上一等长窗口（`[since-days, since)`）的同口径合计 —— KPI 卡的环比差从这里出。
   *  可选：旧后端还没有它，前端按「键在才画 delta」接线。 */
  prev?: { created: number; resolved: number }
}

/** 平台那一块。`machines` 是四张台账的**存量**，不是在线数 —— 在线状态住在进程内存
 *  里，库里没有可以查的那一列。 */
export interface StatsPlatform {
  days: number
  people: {
    total: number
    new: number
    /** 上一等长窗口新增的账号数（「{d} 日新增」那张卡的环比）。可选，理由同上。 */
    prev_new?: number
    admins: number
    /** 真人 / agent 的拆分。判据是 `agent_bindings`，和后端 `IdentityService.is_agent` 同一份。
     *  `total`/`new`/`series[].created` 仍是和，拆分是附加列。 */
    humans: number
    agents: number
    new_humans: number
    new_agents: number
    series: { date: string; created: number; human_created: number; agent_created: number }[]
  }
  machines: { devices: number; hosted_devices: number; warm_machines: number; cloud_hosts: number }
  /** **这一刻**的健康度（和上面两组的「存量 / 窗口」不是一回事）。判据与 `/health/detailed` 同源。 */
  health: {
    overall: 'healthy' | 'degraded' | 'unknown'
    checks: Record<string, { status: string; detail?: string | number | null }>
  }
  /** 三样缺口。各自的口径写在 `gaps.py` —— 磁盘只覆盖后端这一台，预览活在进程内存，
   *  机器普查数的是台账行不是容器。 */
  extras: {
    disk: {
      available: boolean
      free_gb?: number
      total_gb?: number
      used_pct?: number
      tier?: string
      warn_pct?: number
      critical_pct?: number
      note_key: string
    }
    preview: { available: boolean; attached: number | null; note_key: string }
    machines: {
      devices: number
      hosted_devices: number
      warm_total: number
      warm_by_state: Record<string, number>
      warm_error: number
      host_total: number
      host_by_status: Record<string, number>
      host_active: number
      host_enroll_error: number
      host_slots_used: number
      host_slots_total: number
      note_key: string
    }
  }
}

/** 一个网络平面的速率读数。见 `core/net_io.py` 的模块 docstring。 */
export interface NetIoBlock {
  available: boolean
  iface: string | null
  scope: 'host' | 'container' | 'process' | null
  /** 字节/秒。读不到是 `null`，**绝不为 0**。 */
  rx_bps: number | null
  tx_bps: number | null
  samples: { rx_bps: number | null; tx_bps: number | null }[]
  note_key: string
}

/** 接口耗时那一块。**和上面三块有一条根本区别：它读进程内存，不读库。**
 *
 *  所以它**没有 `days`**（没有窗口）、重启即清零，而且只覆盖这一个进程 —— 生产上
 *  业务 API 就一个 backend 进程，dev 栈里那个 device-connection 是另一份。口径写在
 *  响应里（`routes_total` 与 `routes_shown`），页面照读。
 *
 *  `p50/p95/p99` 单位是**毫秒**，没有样本的路由是 `null` 不是 0：0 是一个读数
 *  （「真的很快」），null 是「没有数据」，两者画成同一个数会骗人。 */
export interface StatsPerformance {
  /** 这个 app 注册的全部路由（**每一条端点都在 `routes` 里有一行**，没样本的也在）。 */
  routes_registered?: number | null
  /** 有样本的路由数。和 `routes_registered` 一起读才答得了「是不是太少了」。 */
  routes_with_samples: number
  /** 线上护栏截断掉的条数。**非 0 就必须在页面上说出来** —— 静默截断读起来像「就这些」。 */
  routes_omitted?: number
  /** 溢出桶丢掉的样本（`core/route_metrics.py` 的 `MAX_ROUTE_SERIES`）。 */
  dropped_series?: number
  routes: {
    method: string
    /** 路由**模板**（`/feedback/{feedback_id}`），不是带 uuid 的原始路径。 */
    route: string
    count: number
    error_count: number
    status: { '2xx': number; '3xx': number; '4xx': number; '5xx': number }
    /** 毫秒。**最近 256 个样本窗口上的精确分位**，不是全生命期；没有样本是 `null` 不是 0。 */
    p50: number | null
    p95: number | null
    p99: number | null
    /** 每分钟平均耗时，最多 24 点；空槽是 `null` 不是 0。 */
    spark: (number | null)[]
  }[]
  /** 平台网络：两面都给，各自有口径（见 `core/net_io.py`）。
   *  `uplink` 是**这台机器的网卡**（含计量代理到 LLM 的出向流量），`api` 是本进程的
   *  HTTP 载荷。**读不到是 `null` 不是 0** —— 0 说「网是闲的」，null 说「看不见」。 */
  network?: {
    uplink: NetIoBlock
    api: NetIoBlock
  }
  /** 这一刻正在处理的请求数。**探针（`/health`、`/metrics`）不算**，否则读它的那一次
   *  自己就在里面、这个数恒 ≥1。 */
  active_requests: number
  /** 这个进程起来了多久。 */
  uptime_seconds: number
  /** 事件循环的滞后：接口慢而 p95 不高时，答案常常在这里。 */
  loop_lag: { recent_ms: number; worst_ms: number }
  /** 投递账本积压。 */
  reliability: {
    delivery_unsent: number
    delivery_dead_letters: number
  }
}
