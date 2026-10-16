/**
 * 看板页那几张卡：一个格式化好的数字串、一排柱子、一张数字表、一列待办。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：抄自看板页真正的调用方
 * （`AdminDashboardPage`），行的形状、口径句、时间戳的写法都一样。
 * 条目在 `../catalogDashboard.ts`。
 */
import type { RouteLocationRaw } from 'vue-router'
import type { FeedbackStatus } from '@/cx_types'

// 抄自看板页真正的调用方：一个数字串（格式化好的）、一个去处、一行环比。

export const ADMIN_QUEUE: RouteLocationRaw = { path: '/admin/feedback' }

export const KPI_STATES = {
  linked: {
    label: '待分诊',
    value: '12,048',
    unit: '条',
    to: ADMIN_QUEUE,
    delta: '+12.3%',
    deltaTitle: '上一周期（再前 7 天）：10,727',
    spark: [3, 5, 4, 8, 6, null, 9],
  },
  plain: {
    label: '本周新增反馈',
    value: '316',
    unit: '条',
    note: '按反馈创建时间算，上一周期是 281 条。',
    spark: [12, 19, 14, 22, 31, 28, 26],
  },
  empty: { label: '使用中的设备', value: '', unit: '台', note: '读不到时不画 0：0 是「确实是零」。' },
}

export const BAR_ROWS = [
  { id: 'spaces', label: '空间协作', value: 412 },
  { id: 'questions', label: '题库与判题', value: 268 },
  { id: 'docs', label: '文档站', value: 155 },
  { id: 'infra', label: '平台自身', value: 93 },
]

export const BAR_ROWS_LONG = [
  { id: 'very-long', label: '一个名字长到会走省略号的项目（空间协作与题库与判题与文档站）', value: 412 },
  ...BAR_ROWS.slice(1),
]

export const NUMBER_ROWS: { id: string; no: number; title: string; status: FeedbackStatus; updatedAt: string }[] = [
  {
    id: 'fb-1024',
    no: 1024,
    title: '导出一个月的数据要等四十秒',
    status: 'received',
    updatedAt: '2026-09-28T09:12:00Z',
  },
  {
    id: 'fb-1019',
    no: 1019,
    title: '判题结果偶尔少一个测试点',
    status: 'in_progress',
    updatedAt: '2026-09-27T18:40:00Z',
  },
  { id: 'fb-1011', no: 1011, title: '手机上上传课件会掉一半', status: 'resolved', updatedAt: '2026-09-26T11:05:00Z' },
]

export const ACTION_ROWS = [
  {
    id: 'task:2100',
    title: '合并 #2100（前端边界闸）',
    subtitle: '任务 · 施工中',
    tone: 'warn' as const,
    statusLabel: '待评审',
    age: '3 小时前',
    to: { path: '/topics/t-1' } as RouteLocationRaw,
  },
  {
    id: 'task:2098',
    title: '机器 cm-2b81 连不上',
    subtitle: '设备',
    tone: 'danger' as const,
    statusLabel: '已离线',
    age: '1 天前',
  },
  {
    id: 'topic:77',
    title: '第 3 题：为什么天空是蓝的',
    subtitle: '话题',
    statusLabel: '等你验收',
    age: '2 小时前',
    to: { path: '/topics/t-77' } as RouteLocationRaw,
  },
]
