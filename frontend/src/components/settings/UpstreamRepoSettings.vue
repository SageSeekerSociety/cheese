<script setup lang="ts">
// 上游仓库 (spec §6.3)：接一个已有的仓库、把它的历史拉进来。只画那一行输入框和
// 「保存」，保存与解绑在 `composables/useProjectSettings.ts`。拆自
// `views/ProjectSettingsView.vue`（#2143）。
//
// 只有还没接上仓库的 GitHub 项目看得到这一块 —— 那个条件（项目的仓库形态）是页面的
// 判断，所以由页面决定挂不挂它。
defineOptions({ name: 'UpstreamRepoSettings' })

defineProps<{
  /** 输入框里那段字（保存成功之后是后端回话的规范化地址）。 */
  url: string
  saving: boolean
}>()

const emit = defineEmits<{
  'update:url': [value: string]
  save: []
}>()
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-source-repository</v-icon>
      <span class="page-section-title">GitHub 仓库地址</span>
    </div>
    <div class="page-section-body">
      <div class="d-flex align-center" style="gap: 8px">
        <v-text-field
          :model-value="url"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          placeholder="https://github.com/组织或用户名/仓库名"
          style="flex: 1"
          @update:model-value="emit('update:url', $event)"
          @keydown.enter="emit('save')"
        />
        <v-btn size="small" color="primary" variant="tonal" :loading="saving" @click="emit('save')"> 保存 </v-btn>
      </div>
      <p class="t-body c-faint mt-2 settings-hint">
        填写要连接的 GitHub 仓库地址并保存，再点击下方“连接 GitHub 仓库”。连接后，代码与 PR 都保留在该仓库。
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
