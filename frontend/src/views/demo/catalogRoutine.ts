/**
 * `components/routine/` 那一组在预览站里的条目。
 *
 * 单独一份是因为 `catalog.ts` 已经九百九十多行，塞进去会顶到 `frontend/src` 那一千行
 * 的上限 —— 和 `catalogRoom.ts`、`catalogPanels.ts` 同一个理由。夹具（`ROUTINE_WEEKLY`、
 * `DEMO_TOPIC`）从 `catalogPanelsFixtures.ts` / `demoPanels.ts` 里拿：两处画的是同一个
 * 产品里的同一条规则，各写一份会分叉。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ROUTINE_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry } from './catalog'

import { ROUTINE_WEEKLY } from './catalogPanelsFixtures'
import { DEMO_TOPIC } from './demoPanels'

import RoutineCompose from '@/components/routine/RoutineCompose.vue'

/** 建一条规则的那两页要 vuetify（一排输入框和按钮）和目录里的字。 */
const UI_T = ['vuetify', 'i18n'] as const

/** 项目里可选的频道。 */
const ROOMS = [
  { id: DEMO_TOPIC, title: '课程设计' },
  { id: 'room-2', title: '作业答疑' },
]

export const ROUTINE_ENTRIES: CatalogEntry[] = [
  {
    id: 'routine-compose',
    title: 'RoutineCompose',
    about:
      '建一条例行事务的那两页：左一页填一张表，另一页从模板里挑一条铺进去。项目总览页和房间右侧那一格画的是同一份，差别只有「频道」那一格。',
    file: 'src/components/routine/RoutineCompose.vue',
    component: RoutineCompose,
    needs: [...UI_T],
    states: [
      {
        name: '新建一条',
        note: '项目总览页上：频道要在项目的频道里挑一个，底下两颗勾分别是「时间」和「条件」，「存成模板」在新建时才画。',
        props: { modelValue: true, rooms: ROOMS, defaultRoom: DEMO_TOPIC },
        expect: '存成模板',
      },
      {
        name: '在这个房间里建',
        note: '房间里那一格：频道是定死的（写出来给人看一眼，不画选择框），消息里那句「本周进展」也从这里开始。',
        props: { modelValue: true, roomName: '周报房间' },
        expect: '周报房间',
      },
      {
        name: '改一条已有的',
        note: '改一条时只有那张表，标题那一行写的是改哪一条；「存成模板」不画 —— 那条规则已经在了。',
        props: { modelValue: true, routine: ROUTINE_WEEKLY, rooms: ROOMS, defaultRoom: ROUTINE_WEEKLY.topic_id },
        expect: '修改「每周一整理课程进展」',
      },
      {
        name: '存的时候没成',
        note: '存下去失败：那句话写在这一页里（不是弹一条提示就没了），人改了字段就地再存一次。',
        props: { modelValue: true, rooms: ROOMS, defaultRoom: DEMO_TOPIC, error: '没有保存成功' },
        expect: '没有保存成功',
      },
    ],
  },
]
