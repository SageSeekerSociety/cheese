/**
 * proto-admin-fixtures/spaces.ts — 空间申请审批那页的夹具。
 *
 * 五条覆盖四种状态（待审、已通过、已驳回各要看得见、待审的多给几条好看出列表滚动），
 * 其中一条的**名字和简介都故意写得很长** —— 申请列表要能在长内容下不被撑坏。
 */
import type { SpaceApplication } from '@/types/apiSpaces'

import { ago } from './support'

function application(
  id: number,
  name: string,
  owner: string,
  intro: string,
  status: SpaceApplication['reviewStatus'],
  hoursAgo: number
): SpaceApplication {
  return {
    id,
    avatarId: null,
    name,
    intro,
    reviewStatus: status,
    description: intro,
    owner,
    reviewReason: status === 'REJECTED' ? '简介里没说明题目来源，补充后可重新提交。' : null,
    reviewedBy: status === 'PENDING' ? null : 'andy',
    reviewedAt: status === 'PENDING' ? null : ago(hoursAgo * 3600_000 - 3600_000),
    createdAt: ago(hoursAgo * 3600_000),
  }
}

export const APPLICATIONS: SpaceApplication[] = [
  application(41, '算法竞赛周练', 'maxiaoyu', '每周一套题，赛后讲评。面向校队新人。', 'PENDING', 3),
  application(
    42,
    '计算机系统基础 2026 秋季学期实验题目板（含 4 个大作业与每周小测的全部题目，名字很长的样例）',
    'caisongyang',
    '本学期约 100 名学生使用。题目按周发布，每道题附评分脚本；助教 4 人负责批改。申请公开是为了让往届学生也能看到讲评。这段简介故意写得很长，用来看列表在长内容下会不会被撑坏。',
    'PENDING',
    20
  ),
  application(43, '数据库课程设计', 'chiruotong', '期末大作业选题与中期检查。', 'PENDING', 30),
  application(38, '前端组件练习', 'pengwenbo', '组件拆分与测试的练习题。', 'APPROVED', 72),
  application(36, '随便建建', 'n1ctheboy', '测试一下。', 'REJECTED', 96),
]
