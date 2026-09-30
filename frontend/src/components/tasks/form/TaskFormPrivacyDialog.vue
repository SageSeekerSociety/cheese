<script setup lang="ts">
// 「实名信息隐私保护」那段说明：出题人第一次要求实名信息、要交卷时弹出来，读完点
// 「了解并接受」才真的交上去。
//
// 它只画这一段说明。要不要弹、弹完之后那份提交怎么办，全在 `useTaskForm.ts` 里
// （第一道闸门）—— 这里只把「人点的是哪一个」报出去（`confirm` / `cancel`）。
//
// `open` 是 `v-model:open`：关掉的时候（人按 Esc 也不行，它是 `persistent` 的）由
// 容器把状态收回去，所以这一件自己没有状态。
const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()
</script>

<template>
  <v-dialog v-model="open" max-width="600" persistent scrollable>
    <v-card rounded="lg">
      <v-card-title class="d-flex align-center px-4 pt-4 pb-2">
        <v-icon color="primary" class="mr-3" size="28">mdi-shield-check</v-icon>
        <span class="text-h5 font-weight-medium">实名信息隐私保护</span>
      </v-card-title>

      <v-card-text class="px-4 pb-2">
        <p class="text-subtitle-2 font-weight-medium mb-4">
          为保护参与者隐私，我们对需要实名信息的题目采取了多重保护措施：
        </p>

        <!-- 信息保护卡片 -->
        <v-card class="mb-5 privacy-protection-card" variant="flat" rounded="lg">
          <v-card-text class="pa-0">
            <v-row>
              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-eye-off" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">匿名参与</div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      平台上的日常活动保持匿名，其他用户无法看到参与者的真实身份信息
                    </p>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-file-document-outline" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">用途限制</div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      实名信息仅用于身份验证、学分认定和评优评奖等必要场景
                    </p>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-shield-lock" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">加密存储</div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      采用端到端加密技术存储和传输实名信息，防止未授权访问
                    </p>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-history" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">访问记录</div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      所有对实名信息的访问都被记录，参与者可随时查看访问记录
                    </p>
                  </div>
                </div>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>

        <!-- 使用场景 -->
        <div class="mb-4">
          <div class="text-subtitle-2 font-weight-medium mb-3">信息使用场景</div>
          <v-row dense>
            <v-col cols="12" md="4">
              <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                <v-card-text class="pa-3">
                  <div class="d-flex align-start h-100">
                    <v-avatar size="36" class="primary-soft mr-3 mt-1">
                      <v-icon icon="mdi-account-check" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">身份验证</div>
                      <p class="text-body-2 text-medium-emphasis mb-0">验证参与者身份，帮助出题人了解成员背景</p>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" md="4">
              <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                <v-card-text class="pa-3">
                  <div class="d-flex align-start h-100">
                    <v-avatar size="36" class="primary-soft mr-3 mt-1">
                      <v-icon icon="mdi-certificate-outline" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">项目认证</div>
                      <p class="text-body-2 text-medium-emphasis mb-0">用于题目结题后的证书发放和学分认定</p>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" md="4">
              <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                <v-card-text class="pa-3">
                  <div class="d-flex align-start h-100">
                    <v-avatar size="36" class="primary-soft mr-3 mt-1">
                      <v-icon icon="mdi-trophy" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">评奖评优</div>
                      <p class="text-body-2 text-medium-emphasis mb-0">用于题目结题后的奖项评定与项目评选</p>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>
        </div>

        <!-- 合规承诺 -->
        <v-alert type="info" variant="tonal" class="privacy-rights-alert mb-3" border="start" density="comfortable">
          <div class="text-subtitle-2 font-weight-medium mb-1">合规承诺</div>
          <p class="text-body-2 mb-0">
            作为题目发布者，您应当严格遵守隐私保护规范，只有在必要的场景下才能查看参与者的实名信息。
            平台会记录每次查看行为，并对滥用行为采取相应处罚。
          </p>
        </v-alert>
      </v-card-text>

      <v-card-actions class="pa-4 pt-2">
        <v-spacer></v-spacer>
        <v-btn color="secondary" variant="text" @click="emit('cancel')">取消</v-btn>
        <v-btn color="primary" variant="flat" @click="emit('confirm')">了解并接受</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.privacy-protection-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
}

.privacy-usage-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: var(--surface);
  transition: all 0.2s ease;
}

.privacy-usage-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.15);
  background-color: rgba(var(--v-theme-primary), 0.01);
}

.privacy-rights-alert {
  background-color: rgba(var(--v-theme-info), 0.05);
  border-color: rgba(var(--v-theme-info), 0.3);
}
</style>

<style scoped src="./task-form.css"></style>
