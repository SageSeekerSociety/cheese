<script setup lang="ts">
// 「退出项目」的确认框，从成员页打开。「点了之后发生什么」只有这一份：确认、
// DELETE /projects/{id}/membership、刷新名册和项目列表、离开这个项目。
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { leaveProject } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const open = defineModel<boolean>({ required: true })

const router = useRouter()
const store = useWorkspaceStore()

const leaving = ref(false)
const error = ref<string | null>(null)
watch(open, (v) => {
  if (v) error.value = null
})

async function confirmLeave() {
  leaving.value = true
  error.value = null
  try {
    await leaveProject(props.projectId)
  } catch (e) {
    // 只有退出本身失败才算是失败。拒绝的理由（需要先转让、访问来自小队、还是某个
    // 话题唯一的 owner）就是用户要的全部内容，原样留在弹窗里 —— 弹窗不关：人还没
    // 退成，「取消」仍然有意义，而那句话正是他要的下一步。
    error.value = e instanceof Error ? e.message : '退出失败'
    leaving.value = false
    return
  }
  open.value = false
  // 退出的那一刻，这条请求已经成功了：**接下来做什么都不能再把它变成失败**。
  // 两份刷新是为了让别的页面不拿着旧数据把我送回这个项目（名册里没有我了，项目
  // 列表里也没有这个项目了），但它们是锦上添花 —— 刷新接口抖一下，用 allSettled
  // 让失败就地咽掉，人照样是退出成功的，照样该离开。用 Promise.all 的话一次刷新
  // 失败会走到 catch 里，挂出「退出失败」，而人其实已经退掉了 —— 他再点一次只会
  // 拿到 409。
  await Promise.allSettled([store.refreshMembers(), store.refreshProjects()])
  void router.push({ name: 'HomeSpaces' })
  leaving.value = false
}
</script>

<template>
  <v-dialog v-model="open" max-width="420">
    <v-card>
      <v-card-title class="t-dialog-title pt-4">退出项目？</v-card-title>
      <v-card-text class="t-body c-muted">
        你将无法查看这个项目。已有的消息和记录会保留，再次受邀后可以回来
        <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
          {{ error }}
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="open = false">取消</v-btn>
        <v-btn color="error" variant="flat" :loading="leaving" @click="confirmLeave">退出</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
