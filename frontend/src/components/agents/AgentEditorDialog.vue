<script setup lang="ts">
// 新建 / 修改一个 AI 队友。
//
// 一个队友分两层，这个对话框也就分两段：上面是「它是谁」（名字、标识、用哪个
// 类型），跟着这个项目走，它攒下的记忆挂在这一层；下面是「这个类型怎么跑」
// （角色设定、运行方式、模型、思考深度、技能、外部工具），跟着类型走，是可以
// 被别的项目共用的出厂设置。把两段并排放在一起，是因为人来这里想的是「改这个
// 队友」，而不是「改一个类型」—— 但改下面那段会影响所有用同一个类型的队友，
// 所以下面那段自己说明了这一点，平台预设更是直接只读。
import type { AgentType, ProjectAgent } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import {
  createAgentType,
  createProjectAgent,
  isEndpointMissing,
  setProjectDefaultAgent,
  updateAgentType,
  updateProjectAgent,
} from '../../api'
import { displayNameError, findType, handleError } from '../../lib/projectAgents'

const props = defineProps<{
  modelValue: boolean
  projectId: string
  /** null = 新建 */
  agent: ProjectAgent | null
  types: AgentType[]
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  saved: []
}>()

const isNew = computed(() => props.agent === null)
// 项目还没配过队友时那条隐式的「芝士」。它是真的在干活、也真的有记忆，只是库里
// 还没有它的行 —— 所以能换类型（换的那一刻行就落下来了），还改不了名字。
const isImplicit = computed(() => !isNew.value && !props.agent?.id)

// 刚在这个对话框里复制出来的类型。目录是父组件加载的，等它重新拉一遍才出现，
// 而人点完「复制一份来改」下一秒就要在下面改内容 —— 所以复制出来的先记在这里，
// 和目录合并着用，父组件那边刷新到了就自然对上。
const freshTypes = ref<AgentType[]>([])
const allTypes = computed(() => [
  ...props.types.filter((t) => !freshTypes.value.some((f) => f.name === t.name)),
  ...freshTypes.value,
])

const displayName = ref('')
const handle = ref('')
const typeName = ref<string | null>(null)
const saving = ref(false)
const error = ref<string | null>(null)
// 提交过一次之后才把校验错误显示出来 —— 一进来就满屏红字，等于在人还没
// 开始填的时候就先说他填错了。
const submitted = ref(false)

// 类型这一段的草稿。选中的类型换了就整段重来，否则会把 A 类型的角色设定
// 存进 B 类型。
const typeBody = ref('')
const typeHarness = ref('')
const typeModel = ref('')
const typeEffort = ref<string | null>(null)
const typeSkills = ref<string[]>([])
const typeMcp = ref<string[]>([])

const selectedType = computed(() => findType(allTypes.value, typeName.value))
const typeIsBuiltin = computed(() => selectedType.value?.builtin === true)

const typeOptions = computed(() => [
  { title: '通用（不指定）', value: null },
  ...allTypes.value.map((t) => ({ title: t.title || t.name, value: t.name })),
])

const EFFORT_OPTIONS = [
  { title: '跟随平台', value: null },
  { title: '快', value: 'low' },
  { title: '标准', value: 'medium' },
  { title: '深', value: 'high' },
  { title: '很深', value: 'xhigh' },
  { title: '最深', value: 'max' },
]

function loadTypeDraft(t: AgentType | null) {
  typeBody.value = t?.body ?? ''
  typeHarness.value = t?.harness ?? ''
  typeModel.value = t?.model ?? ''
  typeEffort.value = t?.effort ?? null
  typeSkills.value = [...(t?.skills ?? [])]
  typeMcp.value = [...(t?.mcp_servers ?? [])]
}

watch(
  () => [props.modelValue, props.agent] as const,
  ([open]) => {
    if (!open) return
    error.value = null
    submitted.value = false
    freshTypes.value = []
    displayName.value = props.agent?.display_name ?? ''
    handle.value = props.agent?.handle ?? ''
    typeName.value = props.agent?.type_name ?? null
    loadTypeDraft(findType(allTypes.value, props.agent?.type_name))
  },
  { immediate: true }
)

watch(typeName, (name) => loadTypeDraft(findType(allTypes.value, name)))

const nameProblem = computed(() => displayNameError(displayName.value))
const handleProblem = computed(() => (isNew.value ? handleError(handle.value.trim()) : null))
const nameMessage = computed(() => (submitted.value && nameProblem.value ? [nameProblem.value] : []))
const handleMessage = computed(() => (submitted.value && handleProblem.value ? [handleProblem.value] : []))

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i])
}

// 类型那一段有没有被动过。没动过就不发请求 —— 只是改了个名字却顺手重写了
// 一个共用类型，是这一页最容易造成的连带伤害。
const typeDirty = computed(() => {
  const t = selectedType.value
  if (!t || t.builtin) return false
  return (
    typeBody.value !== t.body ||
    typeHarness.value !== (t.harness ?? '') ||
    typeModel.value !== (t.model ?? '') ||
    typeEffort.value !== (t.effort ?? null) ||
    !sameList(typeSkills.value, t.skills) ||
    !sameList(typeMcp.value, t.mcp_servers)
  )
})

// 平台预设是只读的，所以「改一个预设」的真实动作是把它复制成自己的类型再改。
// 名字取一个还没被占用的后缀 —— 让人自己起名会在这里插一个表单，而这里的意图
// 明明白白就是「照这个来，我要动几笔」。
function copyName(base: string): string {
  const taken = new Set(allTypes.value.map((t) => t.name))
  if (!taken.has(`${base}-custom`)) return `${base}-custom`
  for (let i = 2; ; i += 1) {
    if (!taken.has(`${base}-custom-${i}`)) return `${base}-custom-${i}`
  }
}

const copying = ref(false)

async function copyPreset() {
  const source = selectedType.value
  if (!source) return
  copying.value = true
  error.value = null
  try {
    const created = await createAgentType({
      name: copyName(source.name),
      title: `${source.title || source.name}（自定义）`,
      description: source.description,
      body: source.body,
      skills: source.skills,
      mcp_servers: source.mcp_servers,
      model: source.model,
      effort: source.effort,
      harness: source.harness,
    })
    freshTypes.value = [...freshTypes.value, created]
    typeName.value = created.name
  } catch (e) {
    error.value = isEndpointMissing(e)
      ? '这个环境还没上线类型管理，无法复制'
      : e instanceof Error
        ? e.message
        : '复制失败'
  } finally {
    copying.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}

async function save() {
  submitted.value = true
  if (nameProblem.value || handleProblem.value) return
  saving.value = true
  error.value = null
  try {
    if (typeDirty.value && selectedType.value) {
      await updateAgentType(selectedType.value.name, {
        body: typeBody.value,
        harness: typeHarness.value.trim() || null,
        model: typeModel.value.trim() || null,
        effort: typeEffort.value,
        skills: typeSkills.value,
        mcp_servers: typeMcp.value,
      })
    }
    if (props.agent?.id) {
      await updateProjectAgent(props.projectId, props.agent.id, {
        display_name: displayName.value.trim(),
        type_name: typeName.value,
      })
    } else if (isNew.value) {
      await createProjectAgent(props.projectId, {
        display_name: displayName.value.trim(),
        handle: handle.value.trim() || undefined,
        type_name: typeName.value,
      })
    } else {
      // 项目还没配过队友时那条隐式的「芝士」：库里没有它的行，所以改不了名字，
      // 但换类型是通的 —— 按类型设置默认队友，后端会把这一行落下来，它一直在
      // 攒的那份记忆原样跟过去。这是新项目在这一页上最该做的一件事，不能因为
      // 「没有 id」就变成死路。
      await setProjectDefaultAgent(props.projectId, { type_name: typeName.value })
    }
    emit('saved')
    close()
  } catch (e) {
    error.value = isEndpointMissing(e)
      ? '这个环境还没上线队友的修改功能，改动没有保存'
      : e instanceof Error
        ? e.message
        : '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="720"
    scrollable
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="d-flex align-center pa-5 pb-3">
        <span class="t-title">{{ isNew ? '新建 AI 队友' : '修改 AI 队友' }}</span>
        <v-spacer />
        <v-btn variant="text" icon="mdi-close" size="small" aria-label="关闭" @click="close" />
      </v-card-title>

      <v-card-text class="pa-5 pt-0">
        <v-alert v-if="error" type="error" density="comfortable" class="mb-4">{{ error }}</v-alert>

        <div class="t-eyebrow mb-2">它是谁</div>
        <v-text-field
          v-model="displayName"
          label="名字"
          density="comfortable"
          variant="outlined"
          :readonly="isImplicit"
          :error-messages="nameMessage"
          :hint="isImplicit ? '这是项目自带的队友，名字还改不了，但可以给它换个类型' : undefined"
          :persistent-hint="isImplicit"
          class="mb-1"
        />
        <v-text-field
          v-if="isNew"
          v-model="handle"
          label="标识（英文，可留空自动生成）"
          density="comfortable"
          variant="outlined"
          :error-messages="handleMessage"
          hint="建好之后不能再改，它的记忆按这个标识归档"
          persistent-hint
          class="mb-4"
        />
        <div v-else class="t-meta c-muted mb-4">标识 · {{ agent?.handle }}</div>

        <v-select
          v-model="typeName"
          :items="typeOptions"
          label="类型"
          density="comfortable"
          variant="outlined"
          class="mb-1"
        />
        <div v-if="selectedType?.description" class="t-meta c-muted mb-4">{{ selectedType.description }}</div>
        <div v-else class="mb-4" />

        <v-divider class="mb-4" />

        <div class="d-flex align-center mb-2">
          <span class="t-eyebrow">这个类型怎么跑</span>
          <v-spacer />
          <v-chip v-if="typeIsBuiltin" size="x-small" variant="tonal">平台预设</v-chip>
        </div>

        <div v-if="!selectedType" class="t-body c-muted mb-2">
          没有指定类型时，队友按平台默认的方式工作。要自定义角色设定和工具，先选一个类型
        </div>

        <template v-else>
          <div v-if="typeIsBuiltin" class="d-flex align-center ga-3 mb-3">
            <span class="t-meta c-muted">平台预设不能改</span>
            <v-btn size="small" variant="tonal" :loading="copying" @click="copyPreset">复制一份来改</v-btn>
          </div>
          <div v-else class="t-meta c-muted mb-3">
            改动会影响所有用「{{ selectedType.title || selectedType.name }}」的队友，包括别的项目
          </div>

          <v-textarea
            v-model="typeBody"
            label="角色设定"
            rows="6"
            density="comfortable"
            variant="outlined"
            :readonly="typeIsBuiltin"
            class="mb-1"
          />
          <div class="d-flex ga-3 mb-1 flex-wrap">
            <v-text-field
              v-model="typeModel"
              label="模型"
              placeholder="跟随平台"
              density="comfortable"
              variant="outlined"
              :readonly="typeIsBuiltin"
              style="min-width: 180px; flex: 1 1 180px"
            />
            <v-select
              v-model="typeEffort"
              :items="EFFORT_OPTIONS"
              label="思考深度"
              density="comfortable"
              variant="outlined"
              :readonly="typeIsBuiltin"
              style="min-width: 180px; flex: 1 1 180px"
            />
            <v-text-field
              v-model="typeHarness"
              label="运行方式"
              placeholder="跟随平台"
              density="comfortable"
              variant="outlined"
              :readonly="typeIsBuiltin"
              style="min-width: 180px; flex: 1 1 180px"
            />
          </div>
          <v-combobox
            v-model="typeSkills"
            label="技能"
            multiple
            chips
            closable-chips
            density="comfortable"
            variant="outlined"
            :readonly="typeIsBuiltin"
            class="mb-1"
          />
          <v-combobox
            v-model="typeMcp"
            label="外部工具"
            multiple
            chips
            closable-chips
            density="comfortable"
            variant="outlined"
            :readonly="typeIsBuiltin"
          />
        </template>
      </v-card-text>

      <v-card-actions class="pa-5 pt-0">
        <v-spacer />
        <v-btn variant="text" @click="close">取消</v-btn>
        <v-btn color="primary" variant="flat" :loading="saving" @click="save">保存</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
