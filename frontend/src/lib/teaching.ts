// 「AI 队友辅导」在表单上那几格与接口那一份之间的翻译。
//
// 服务端存的是**整份**（`SpaceTeaching`，见 `backend/app/domain/task/protocol.py`）：
// 方式、补充要求、出题人私有指导、学习目标、资料、文档、技能。空间和题目两层同时生效，
// 所以题目那一层留空就是只用空间的；写了什么就在空间的基础上加什么。
//
// 空间设置页和发题页、改题页对同一份东西用同一套规矩，放在这里免得几处各写一遍。
import type { SpaceTeaching, TeachingMode } from '@/types'

/** 方式的四种，按表单上的顺序。 */
export const TEACHING_MODES: readonly TeachingMode[] = ['guide', 'example', 'spec', 'free']

/** 表单上那几格：`learningGoals` 存的是人敲的原文，解析交给 `buildTeaching`。 */
export type TeachingDraft = {
  mode: TeachingMode | null
  systemPrompt: string
  /** 出题人的私有指导（#944 第二种可见性）：只有出题人/板管理员看得到。 */
  privatePrompt: string
  /** 本节学习目标，逗号分隔；写上它这份指导就按教育性走。 */
  learningGoals: string
  materialIds: number[]
  /** 表单上不改，原样带着：老配置里写过的团队文档不因为换了表单就丢掉。 */
  documentIds: string[]
  /** 项目里每个 AI 队友都带上的技能名。 */
  skills: string[]
}

export function emptyTeachingDraft(): TeachingDraft {
  return {
    mode: null,
    systemPrompt: '',
    privatePrompt: '',
    learningGoals: '',
    materialIds: [],
    documentIds: [],
    skills: [],
  }
}

/** 把逗号 / 顿号分隔的一行拆成几项，去掉空白与空项。 */
export function splitList(value: string): string[] {
  return value
    .split(/[,，、]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

/** 接口那一份 → 表单那几格。 */
export function draftFromConfig(config?: SpaceTeaching | null): TeachingDraft {
  return {
    mode: config?.mode ?? null,
    systemPrompt: config?.systemPrompt ?? '',
    privatePrompt: config?.privatePrompt ?? '',
    learningGoals: (config?.learningGoals ?? []).join(', '),
    materialIds: [...(config?.materialIds ?? [])],
    documentIds: [...(config?.documentIds ?? [])],
    skills: [...(config?.skills ?? [])],
  }
}

/** 表单那几格 → 交给接口的那一份。空的落成 `null` / `[]`，与 `is_empty` 一个意思。 */
export function buildTeaching(draft: TeachingDraft): SpaceTeaching {
  return {
    mode: draft.mode,
    systemPrompt: draft.systemPrompt.trim() || null,
    privatePrompt: draft.privatePrompt.trim() || null,
    learningGoals: splitList(draft.learningGoals),
    materialIds: [...draft.materialIds],
    documentIds: [...draft.documentIds],
    skills: [...draft.skills],
  }
}

/**
 * 「默认队友」这一格 → 提交给接口的值（#944）。
 *
 * 与指导那一份不同，它没有「整份」的概念，只有「这一格写没写」：
 * - 选中空串（继承这一层不说）：
 *   - 原始值本来就 `null`（这一层没写过）→ `undefined`，**不带这个键**，沿用上层。
 *   - 原始值非 `null`（这一层写过）→ `''`，明确清掉这一层的覆盖。
 * - 选中预设名 → 带上它。
 *
 * `undefined` 就是「不带这个键」，由调用方据此决定放不放进 payload。
 */
export function defaultRolePayload(selected: string, raw: string | null | undefined): string | undefined {
  if (selected === '') return raw == null ? undefined : ''
  return selected
}

/** 全空 = 这一层什么都没说。题目那一层据此决定「本题有没有自己的辅导设置」。 */
export function isTeachingBlank(teaching?: SpaceTeaching | null): boolean {
  if (!teaching) return true
  return (
    !teaching.mode &&
    !teaching.systemPrompt?.trim() &&
    !teaching.privatePrompt?.trim() &&
    (teaching.learningGoals?.length ?? 0) === 0 &&
    (teaching.materialIds?.length ?? 0) === 0 &&
    (teaching.documentIds?.length ?? 0) === 0 &&
    (teaching.skills?.length ?? 0) === 0
  )
}
