// 平台模型角色的名字 → 词条。写成字面量表，不在模板里拼键：键要能被静态找到。
// 服务端多出一个这里没有的角色时，显示它的原名，而不是一个找不到的键。

const ROLE_KEYS: Record<string, string> = {
  agent_model: 'models.roles.role.agent_model',
  subagent_model: 'models.roles.role.subagent_model',
  assistant_model: 'models.roles.role.assistant_model',
  docs_assistant_model: 'models.roles.role.docs_assistant_model',
  topic_naming_model: 'models.roles.role.topic_naming_model',
  environment_diagnosis_model: 'models.roles.role.environment_diagnosis_model',
  voice_asr_model: 'models.roles.role.voice_asr_model',
  voice_polish_model: 'models.roles.role.voice_polish_model',
  memory_migration_model: 'models.roles.role.memory_migration_model',
}

/** 角色的词条键；不认识的角色给它的原名（`t` 找不到键时原样返回）。 */
export function roleLabelKey(role: string): string {
  return ROLE_KEYS[role] ?? role
}
