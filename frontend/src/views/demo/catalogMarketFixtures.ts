/**
 * 市场那两件（`NodeBoard`、`MarketViewView`）在预览站里吃的数据。
 *
 * 形状照 `cx_types` 的 `MarketNodes` / `MarketPools` 写（`GET /api/market/nodes`、
 * `GET /api/market/pools` 的回包），少一个键多一个键都在 `vue-tsc` 那里当场红。自有设备
 * 的名字取自 `COMPUTE_DEVICES`（选工作电脑那张表单用的那两台），模型名取自模型管理那一组
 * 的夹具，免得同一台机器、同一个模型在预览站的两处叫两个名字。
 *
 * 条目本身在 `catalogMarket.ts`。
 */
import type { MarketNodes, MarketPools } from '@/cx_types'

import { COMPUTE_DEVICES } from './catalogFixtures'

const [LAB, HOME] = COMPUTE_DEVICES

/** 三台节点：平台的云端（没选时落在这里）、在线的实验室工作站、离线的家里那台。 */
export const MARKET_NODES: MarketNodes = {
  nodes: [
    {
      id: 'cloud',
      label: '云端环境',
      kind: 'cloud',
      online: true,
      current: true,
      detail: 'sandbox · 12 个会话在跑',
      description: '平台提供的沙箱，每个会话一台，开箱即用。',
    },
    {
      id: `device:${LAB.device_id}`,
      label: LAB.name,
      kind: 'device',
      online: LAB.online,
      current: false,
      detail: 'linux/amd64 · 上次心跳 8 秒前',
      description: '项目授权的自有设备，跑要 GPU 或要读本地数据的任务。',
    },
    {
      id: `device:${HOME.device_id}`,
      label: HOME.name,
      kind: 'device',
      online: HOME.online,
      current: false,
      detail: 'darwin/arm64 · 上次心跳 3 小时前',
      description: '连不上时不接新任务，回来之后自动重新上线。',
    },
  ],
  active_turns_total: 7,
  current_provider: 'cloud',
}

/** 一台节点都没有：部署还没配任何算力。 */
export const MARKET_NODES_EMPTY: MarketNodes = { nodes: [], active_turns_total: 0, current_provider: '' }

/** 目录：两个 AI 模型（一个默认、一个暂未开通）和三种工作电脑。 */
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
      id: 'cloud',
      label: '云端环境',
      tier: 'included',
      price: '平台补贴',
      description: '每个会话一个沙箱；要 Docker 时可换成整台云虚拟机。',
      available: true,
      default: true,
    },
    {
      kind: 'compute',
      id: 'device',
      label: '自有设备',
      tier: 'byo',
      price: '免费',
      description: '接入你自己的电脑，任务在它上面跑。',
      available: true,
      default: false,
    },
    {
      kind: 'compute',
      id: 'gpu',
      label: 'GPU 云主机',
      tier: 'testing',
      price: '内测中',
      description: '带显卡的整台机器，跑训练和推理。',
      available: false,
      default: false,
    },
  ],
}

/** 目录读回来了，但两组都是空的。 */
export const MARKET_POOLS_EMPTY: MarketPools = { ai: [], compute: [] }
