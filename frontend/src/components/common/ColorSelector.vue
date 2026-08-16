<template>
  <div>
    <label v-if="label" class="text-subtitle-2 mb-2 d-block">{{ label }}</label>

    <v-card flat class="pa-3 mb-2 color-selector-card">
      <div class="d-flex align-center">
        <!-- 颜色预览 -->
        <div class="d-flex align-center mr-4">
          <v-sheet class="color-display" :style="{ backgroundColor: modelValue }" rounded="lg" width="56" height="56">
          </v-sheet>
        </div>

        <!-- 右侧面板 -->
        <div class="flex-grow-1">
          <!-- 颜色代码输入框 -->
          <div class="d-flex align-center mb-3">
            <v-text-field
              :model-value="modelValue"
              label="十六进制颜色码"
              hide-details
              density="compact"
              variant="outlined"
              prepend-inner-icon="mdi-pound"
              :rules="[(v) => /^#[0-9A-Fa-f]{6}$/.test(v) || '请输入有效的颜色码']"
              @update:model-value="$emit('update:modelValue', $event)"
            >
              <template #append>
                <v-btn variant="text" size="small" color="primary" icon>
                  <v-icon>mdi-palette</v-icon>
                  <v-overlay
                    v-model="pickerOpen"
                    activator="parent"
                    location-strategy="connected"
                    scroll-strategy="close"
                    :scrim="false"
                    class="color-picker-overlay"
                    transition="slide-y-reverse-transition"
                  >
                    <v-card width="320" elevation="4" rounded="lg" class="color-picker-card">
                      <v-card-text class="px-2 pt-3 pb-2">
                        <v-color-picker
                          :model-value="modelValue"
                          elevation="0"
                          hide-inputs
                          mode="hex"
                          :swatches="colorSwatches"
                          @update:model-value="updateColor"
                        ></v-color-picker>
                      </v-card-text>
                    </v-card> </v-overlay
                ></v-btn>
              </template>
            </v-text-field>
          </div>

          <!-- 预设颜色选择 -->
          <div class="d-flex flex-wrap gap-2">
            <v-tooltip v-for="color in presetColors" :key="color" :text="color" location="top">
              <template #activator="{ props }">
                <div
                  v-bind="props"
                  class="color-swatch"
                  :class="{ 'color-swatch-selected': modelValue === color }"
                  :style="{ backgroundColor: color }"
                  @click="$emit('update:modelValue', color)"
                >
                  <!-- 这个勾的白色是有意保留的（§1.2 例外）：它压在**用户选中的那个颜色**上，
                       色块底色是数据不是主题色，两套主题下同一个值，所以勾也不该随主题变 -->
                  <v-icon v-if="modelValue === color" size="x-small" color="white"> mdi-check </v-icon>
                </div>
              </template>
            </v-tooltip>
          </div>
        </div>
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps({
  modelValue: {
    type: String,
    required: true,
  },
  label: {
    type: String,
    default: '颜色选择',
  },
  presetColors: {
    type: Array as () => string[],
    default: () => [
      '#6366f1', // 主题蓝
      '#818cf8', // 淡蓝色
      '#8b5cf6', // 淡紫色
      '#14b8a6', // 蓝绿色
      '#10b981', // 翠绿色
      '#60a5fa', // 天蓝色
      '#f59e0b', // 琥珀色
      '#f97316', // 橙色
      '#ef4444', // 红色
      '#ec4899', // 粉红色
    ],
  },
})

const emit = defineEmits(['update:modelValue'])

const pickerOpen = ref(false)

// 颜色选择器的预设色板
const colorSwatches = [
  ['#F44336', '#E91E63', '#9C27B0', '#673AB7', '#3F51B5', '#2196F3', '#03A9F4', '#00BCD4'],
  ['#009688', '#4CAF50', '#8BC34A', '#CDDC39', '#FFEB3B', '#FFC107', '#FF9800', '#FF5722'],
  ['#795548', '#607D8B', '#333333', '#555555', '#777777', '#999999', '#BBBBBB', '#DDDDDD'],
]

const updateColor = (color: string) => {
  emit('update:modelValue', color)
}
</script>

<style scoped lang="scss">
.color-selector-card {
  border: 1px solid var(--line);
  background-color: var(--fill);
}

/* 色块外面那一圈原本是纯白，作用是把色块和页面隔开。用 --surface 才能在两套主题下
   都保持"和页面同色的一道缝"，深色下不会变成一圈刺眼的白边。 */
.color-display {
  box-shadow: var(--shadow-1);
  border: 2px solid var(--surface);
}

.color-swatch {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;

  &:hover {
    transform: scale(1.1);
    box-shadow: var(--shadow-1);
    z-index: 1;
  }
}

/* 选中环：内圈是和页面同色的一道缝（--surface），外圈是最强的中性色（--ink）。
   浅色下 --ink 近黑、深色下近白，两边都能从各自的底色里跳出来。 */
.color-swatch-selected {
  transform: scale(1.05);
  box-shadow:
    0 0 0 2px var(--surface),
    0 0 0 4px var(--ink);
  z-index: 2;
}

.text-mono {
  font-family: monospace;
}

.color-picker-overlay {
  .v-overlay__content {
    border-radius: 8px;
    box-shadow: var(--shadow-2);
  }
}

.color-picker-card {
  max-width: 100%;
  border: none;
}

:deep(.v-color-picker) {
  width: 100%;
  max-width: 100%;

  .v-color-picker-swatches {
    width: 100%;
    padding: 8px;

    .v-color-picker-swatches__swatch {
      margin: 2px;
    }
  }
}
</style>
