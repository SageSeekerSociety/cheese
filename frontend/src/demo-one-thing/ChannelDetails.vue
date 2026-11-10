<script setup lang="ts">
// 频道详情: what the channel is for and who is in it, as a popover card on the
// channel title (also 频道详情 in ⋯). It is not a panel: the right panel holds
// only the one 一件事 that is open.
import { computed, ref } from 'vue'

import { CHANNELS, NAMES } from './data'
import { S, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

const channel = computed(() => CHANNELS.find((c) => c.id === S.chan)!)

const about = ref<Record<string, string>>({})
const editing = ref(false)
const draft = ref('')
function edit() {
  draft.value = about.value[S.chan] ?? channel.value.about
  editing.value = true
}
function save() {
  about.value = { ...about.value, [S.chan]: draft.value.trim() }
  editing.value = false
  toast('频道说明已保存')
}
</script>

<template>
  <div class="ot-cp" data-zone="details">
    <div class="ot-cp__bar">
      <h2 class="ot-cp__title">频道详情</h2>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        title="关闭"
        aria-label="关闭"
        @click="S.chanDetails = false"
      />
    </div>
    <div class="ot-cp__body">
      <section class="ot-cp__sec">
        <div class="ot-cp__head">
          <h3 class="t-eyebrow-read">频道说明</h3>
          <button v-if="!editing" type="button" class="ot-cp__link" @click="edit">编辑</button>
        </div>
        <template v-if="editing">
          <v-textarea
            v-model="draft"
            autocomplete="off"
            density="compact"
            variant="outlined"
            rows="2"
            auto-grow
            hide-details
          />
          <div class="ot-cp__actions">
            <BaseButton kind="ghost" size="sm" @click="editing = false">取消</BaseButton>
            <BaseButton kind="primary" size="sm" @click="save">保存</BaseButton>
          </div>
        </template>
        <p v-else class="ot-cp__about">{{ about[S.chan] ?? channel.about }}</p>
      </section>

      <section class="ot-cp__sec">
        <div class="ot-cp__head">
          <h3 class="t-eyebrow-read">成员 · {{ channel.members.length + 1 }}</h3>
        </div>
        <div v-for="h in [...channel.members, 'cheese']" :key="h" class="ot-cp__member">
          <CheeseAvatar v-if="h === 'cheese'" :size="24" name="芝士" handle="cheese" />
          <UserAvatar v-else :name="NAMES[h]" :seed="h" :size="24" />
          <span>{{ NAMES[h] }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ot-cp {
  display: flex;
  flex-direction: column;
  width: 320px;
  max-height: 480px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.ot-cp__bar {
  display: flex;
  flex: none;
  align-items: center;
  height: 40px;
  padding: 0 4px 0 16px;
}

.ot-cp__title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.ot-cp__body {
  flex: 1;
  min-height: 0;
  padding: 0 16px 12px;
  overflow: auto;
}

.ot-cp__sec {
  padding: 8px 0;
}

.ot-cp__sec + .ot-cp__sec {
  border-top: 1px solid var(--line);
}

.ot-cp__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.ot-cp__head h3 {
  margin: 0;
}

.ot-cp__link {
  padding: 0;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-cp__link:hover {
  color: var(--ink);
}

.ot-cp__about {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-cp__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 8px;
}

.ot-cp__member {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 0;
  font-size: 13px;
  color: var(--text);
}
</style>
