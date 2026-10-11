/**
 * 项目设置那五件（`Project*Settings.vue`，不含 TopicComputePicker）在预览站里吃的数据。
 *
 * 形状不是编的：就是那五件自己声明的 props，少一个键多一个键都在 `vue-tsc` 那里当场红
 * （下面每个 helper 的返回类型引的是组件 props 用的那些接口）。值照抄
 * `views/ProjectSettingsView.vue` 那一层从 composable 递下去的读口 —— `state` 是
 * `composables/useProject*Settings.ts` 取回来的那一份，`saving` / `saved` / `error` 是
 * 那些 composable 里的 `useSaveState`（就地回执那一条）与各自的 error ref，`diagnosis`
 * 是「让芝士看看」的回答。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 和 `catalog.ts` 都八百多行，五件好几格的
 * 数据塞进去会顶到 `frontend/src` 那一千行的上限；和 `catalogSettingsFixtures.ts`
 * 同一个理由。条目本身在 `catalogSettingsData.ts`，那边的 `CatalogEntry` 是 type-only
 * 引用。
 *
 * 为什么这五件能在预览站里单独画：它们都是「只吃 props、只往上发事件」的那种组件
 * （`frontend_grade.py` 的 A 级），取数、保存、状态轮询、MCP 授权回跳全在
 * `composables/useProject*Settings.ts` 里，由设置页接线。
 */
import type { McpServer, McpServerList, ProjectDefaultModel, TaskNaming } from '@/api'
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '@/cx_types'
import type { AgentFieldChoice } from '@/lib/modelChoices'
import type { ProjectComputeConfigs } from '@/types/compute'
import type { EnvironmentDiagnosis } from '@/types/environment'

import { COMPUTE_DEVICES } from './catalogFixtures'

/** 工作电脑现状：默认云端，两台自有设备，分布里各有几段对话在跑。 */
export function computeState(over: Partial<ProjectComputeConfigs> = {}): ProjectComputeConfigs {
  return {
    default: { name: null, profile: 'cloud', device_id: null },
    can_manage: true,
    devices: COMPUTE_DEVICES,
    cloud_available: true,
    cloud_vm_available: false,
    distribution: {
      cloud: 3,
      cloud_vm: 0,
      devices: [{ device_id: 'lab', name: '实验室工作站', agents: 2, machine_access: true }],
    },
    ...over,
  }
}

/** 任务命名那两档的现状。默认智能命名、能改、部署也支持。 */
export function taskNamingState(over: Partial<TaskNaming> = {}): TaskNaming {
  return { mode: 'auto', available: true, can_manage: true, ...over }
}

/** 一档模型可选值。默认允许、没有方案门槛、不带思考强度。 */
export function modelChoice(
  over: Partial<AgentFieldChoice> & Pick<AgentFieldChoice, 'id' | 'label'>
): AgentFieldChoice {
  return { description: '', default: false, allowed: true, requires_plan: null, ...over }
}

/** 项目默认模型那两档的现状。默认回落部署给的 sonnet，可选的只有两档。 */
export function defaultModelState(over: Partial<ProjectDefaultModel> = {}): ProjectDefaultModel {
  return {
    model: null,
    subagent_model: null,
    deployment_default: 'sonnet',
    can_manage: true,
    choices: [
      modelChoice({ id: 'sonnet', label: 'Claude Sonnet 5' }),
      modelChoice({ id: 'deepseek-flash', label: 'DeepSeek V4.1 Flash' }),
    ],
    ...over,
  }
}

/** 读到的环境配置。两个频道，其中「综合助理」上次准备失败过。 */
export function envInfo(over: Partial<ProjectEnvironmentInfo> = {}): ProjectEnvironmentInfo {
  return {
    config: {
      setup_script: 'apt-get install -y python3 nodejs',
      startup_script: 'pnpm install',
      variables: { NODE_ENV: 'development', CUSTOM: 'literal\n$(no expansion)' },
      revision: 'rev-2026-10-11-a1b2c3d4e5f6',
    },
    can_edit: true,
    rooms: [
      { id: 'r-general', title: '综合助理', revision: 'rev-2026-10-11-a1b2c3d4e5f6', failure: ENV_FAILURE },
      { id: 'r-frontend', title: '网页端', revision: null },
    ],
    ...over,
  }
}

/** 一个频道最近一次还没人重试的准备失败。 */
export const ENV_FAILURE = {
  attempt: 'a-1',
  at: '2026-10-11T09:12:00Z',
  stage: 'startup' as const,
  exit_code: 1,
  log: 'pnpm install\n ERR_PNPM_NO_MATCHING_VERSION  No matching version found for left-pad@99\n',
  waiting: 2,
}

/** 一个频道准备现状：默认「准备失败」，卡在启动脚本那一步。 */
export function envStatus(over: Partial<EnvironmentStatus> = {}): EnvironmentStatus {
  return {
    state: 'failed',
    stage: 'startup',
    pinned_revision: 'rev-2026-10-11-a1b2c3d4e5f6',
    exit_code: 1,
    log: 'pnpm install\n ERR_PNPM_NO_MATCHING_VERSION  No matching version found for left-pad@99\n',
    started_at: '2026-10-11T09:11:40Z',
    finished_at: '2026-10-11T09:12:00Z',
    ...over,
  }
}

/** 芝士看了那次失败给出的回答：原因、改法，和改完的启动脚本。 */
export function envDiagnosis(over: Partial<EnvironmentDiagnosis> = {}): EnvironmentDiagnosis {
  return {
    reason: '启动脚本里写死了一个不存在的 left-pad 版本',
    change: '把 left-pad 换成项目当前依赖表里的版本',
    setup_script: null,
    startup_script: 'pnpm install\n# left-pad 版本已在 package.json 里固定',
    sure: true,
    ran: { setup_script: 'apt-get install -y python3 nodejs', startup_script: 'pnpm install' },
    ...over,
  }
}

/** 一台 MCP 服务器。默认是项目 `.mcp.json` 里的 oauth 服务器、还没连。 */
export function mcpServer(over: Partial<McpServer> = {}): McpServer {
  return {
    name: 'tracker',
    transport: 'http',
    host: 'mcp.example.test',
    auth: 'oauth',
    status: 'disconnected',
    declared_by: null,
    authorized_by: null,
    authorized_at: null,
    variables: [],
    ...over,
  }
}

/** MCP 那一条读回来的清单。默认两台：一台待授权、一台缺密钥。 */
export function mcpList(over: Partial<McpServerList> = {}): McpServerList {
  return {
    servers: [
      mcpServer(),
      mcpServer({
        name: 'search',
        host: 'search.example.test',
        auth: 'headers',
        status: 'missing_values',
        variables: [{ name: 'SEARCH_KEY', set: false, updated_by: null, updated_at: null }],
      }),
    ],
    problem: null,
    ...over,
  }
}
