/**
 * 市场那两件（`NodeBoard`、`MarketViewView`）在预览站里吃的数据。
 *
 * 形状照 `cx_types` 的 `MarketNodes` / `MarketPools` 写（`GET /api/market/nodes`、
 * `GET /api/market/pools` 的回包），少一个键多一个键都在 `vue-tsc` 那里当场红。自有设备
 * 的名字取自 `COMPUTE_DEVICES`（选环境那张表单用的那两台），模型名取自模型管理那一组
 * 的夹具，免得同一台机器、同一个模型在预览站的两处叫两个名字。
 *
 * 条目本身在 `catalogMarket.ts`。
 */
import type { MarketNodes, MarketPools } from '@/cx_types'

import { COMPUTE_DEVICES } from './catalogComputeFixtures'

const [LAB, HOME] = COMPUTE_DEVICES

/** 三种规格：平台的标准沙箱（没选时用它）、虚拟机沙箱、成员自己的设备。 */
export const MARKET_NODES: MarketNodes = {
  nodes: [
    {
      id: 'standard',
      label: '标准沙箱',
      kind: 'standard',
      online: true,
      current: true,
      detail: '平台的机器 · 12 个会话在跑',
      description: '每位 AI 队友一个沙箱，在平台的机器上，只看得到自己的工作目录。',
    },
    {
      id: 'vm',
      label: '虚拟机沙箱',
      kind: 'vm',
      online: true,
      current: false,
      detail: '平台的机器',
      description: '一整台虚拟机给一位 AI 队友：能跑 Docker、KVM、装内核模块。',
    },
    {
      id: 'devices_of',
      label: '自己的设备',
      kind: 'devices_of',
      online: LAB.online || HOME.online,
      current: false,
      detail: `${LAB.name} 在线`,
      description: '沙箱放在项目成员自己接入的电脑上。',
    },
  ],
  active_turns_total: 7,
  current_provider: 'standard',
}

/** 一台节点都没有：部署还没配任何算力。 */
export const MARKET_NODES_EMPTY: MarketNodes = { nodes: [], active_turns_total: 0, current_provider: '' }

/** 目录：两个 AI 模型（一个默认、一个暂未开通）和三种沙箱规格。 */
export const MARKET_POOLS: MarketPools = {
  ai: [
    {
      kind: 'ai',
      id: 'glm-4.7',
      label: 'GLM 4.7',
      tier: 'default',
      price: '平台补贴',
      description: '每个项目默认用它，长上下文、写代码和改文档都够用。',
      available: true,
      default: true,
    },
    {
      kind: 'ai',
      id: 'gpt-5-mini',
      label: 'GPT-5 mini',
      tier: 'premium',
      price: '按用量计费',
      description: '更快的回答，适合大量短对话。',
      available: false,
      default: false,
    },
  ],
  compute: [
    {
      kind: 'compute',
      id: 'standard',
      label: '标准沙箱',
      tier: 'included',
      price: '按时长计费',
      description: '每位 AI 队友一个沙箱，在平台的机器上。',
      available: true,
      default: true,
    },
    {
      kind: 'compute',
      id: 'vm',
      label: '虚拟机沙箱',
      tier: 'included',
      price: '按规格和时长计费',
      description: '一整台虚拟机给一位 AI 队友：能跑 Docker、KVM、装内核模块。',
      available: false,
      default: false,
    },
    {
      kind: 'compute',
      id: 'devices_of',
      label: '自己的设备',
      tier: 'byo',
      price: '不收费',
      description: '沙箱放在项目成员自己接入的电脑上。',
      available: true,
      default: false,
    },
  ],
}

/** 目录读回来了，但两组都是空的。 */
export const MARKET_POOLS_EMPTY: MarketPools = { ai: [], compute: [] }
