<script setup lang="ts">
// 这个房间里的东西 (#1085 结论四)。
//
// 一个房间常有好几样值得看的东西 —— 一份改好的 .docx、一张图、一个跑起来的应用
// —— 而上面那块预览只显示最后摆出来的那一样。这几行是全部，新的在前。
//
// 它们属于这个房间：用户看完拿走，事情就结束了。要成为项目的产物得有人按一下
// 「保存到项目」——按了才算，平台不猜、不自动升。按下去之后那份文件进项目那棵树
// （它就是源），清单上多一项或多一版。
//
// 只有一样东西时这一块照样出现：上面那块预览只是在看它，而「保存到项目」这个动作
// 只在这里有 —— 一个房间最常见的样子正是「就做了一份东西」，那一份也得存得进去。
import type { RoomOutput } from '@/api'

import { computed, ref, watch } from 'vue'

import { listRoomOutputs, saveRoomOutputToProject } from '@/api'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ topicId: string | null }>()

const outputs = ref<RoomOutput[]>([])
const saving = ref('')
const error = ref('')
const saved = ref<Record<string, string>>({})

/** 跑着的应用没有文件可存：它是一个进程，不是一份东西。 */
const files = computed(() => outputs.value.filter((o) => o.kind === 'file'))

async function load() {
  const topicId = props.topicId
  if (!topicId) return
  try {
    const listed = await listRoomOutputs(topicId)
    if (props.topicId !== topicId) return
    outputs.value = listed.data
  } catch {
    // 读不到这一块就不显示它：这一格的主体是上面那块预览。
    if (props.topicId === topicId) outputs.value = []
  }
}

async function save(output: RoomOutput) {
  const topicId = props.topicId
  if (!topicId) return
  saving.value = output.path
  error.value = ''
  try {
    const done = await saveRoomOutputToProject(topicId, output.path)
    saved.value = { ...saved.value, [output.path]: `已保存为《${done.artifact.name}》第 ${done.version} 版` }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未能保存到项目'
  } finally {
    saving.value = ''
  }
}

function name(path: string): string {
  return path.split('/').pop() || path
}

watch(
  () => props.topicId,
  () => {
    outputs.value = []
    saved.value = {}
    error.value = ''
    void load()
  },
  { immediate: true }
)

defineExpose({ reload: load })
</script>

<template>
  <section v-if="files.length" class="outs" data-testid="room-outputs">
    <h3 class="outs__title t-eyebrow c-muted">这个房间里的东西</h3>
    <p v-if="error" role="alert" class="outs__error t-meta">{{ error }}</p>
    <ul class="outs__list">
      <li v-for="output in files" :key="output.path" class="outs-row">
        <span class="outs-row__name t-body" :title="output.path">{{ name(output.path) }}</span>
        <span class="outs-row__when t-meta c-faint">{{ relTime(output.shown_at) }}</span>
        <span v-if="saved[output.path]" class="t-meta c-faint">{{ saved[output.path] }}</span>
        <v-btn
          v-else
          size="small"
          variant="text"
          color="on-surface-variant"
          :loading="saving === output.path"
          @click="save(output)"
        >
          保存到项目
        </v-btn>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.outs {
  padding: 8px 12px 12px;
  border-top: 1px solid var(--line);
}
.outs__title {
  margin: 0 0 6px;
}
/* 错误是给人读的一行字，所以用墨色那一档，不是记号色。 */
.outs__error {
  margin: 0 0 6px;
  color: var(--danger-ink);
}
.outs__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.outs-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.outs-row__name {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.outs-row__when {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
</style>
