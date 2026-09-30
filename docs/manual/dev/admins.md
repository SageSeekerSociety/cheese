---
title: 平台管理员
kind: 参考
summary: 后台管理的入口与名单。
covers:
  - backend/app/api/routes/admin_common.py
  - backend/app/domain/admin/
  - frontend/src/views/admin/
  - frontend/src/router/feedback.ts
---

# 平台管理员 {#admins}

后台管理的入口与名单。

> 讲：谁是管理员、后台有什么。不讲：空间或团队里的管理员角色，见[席位与权限判定](/dev/seats)。

## 名单从哪来 {#who}

平台管理员是两份名单的并集（`AdminService.admin_handles`）：

1. **根名单**：环境变量 `PLATFORM_ADMIN_HANDLES`，JSON 数组，例如 `["alice","bob"]`。它不能从界面上删，否则管理员可以把自己锁在门外。部署环境要求它不为空，否则启动失败；本地开发和测试可以为空。
2. **界面名单**：后台「成员管理」里添加的，存在 `platform_admins` 表，可以在界面上移除。

接口用 `require_platform_admin`（`backend/app/api/routes/admin_common.py`）拦截非管理员。

## 反馈管理员是另一份名单 {#feedback-admins}

反馈队列（`/admin/feedback`）、改状态、内部备注、读私密和安全反馈、删别人的反馈和评论，只归**反馈管理员**：环境变量 `FEEDBACK_TRIAGE_HANDLES`，JSON 数组（`settings.feedback_triage_handles`，判据在 `FeedbackService.is_admin`）。它和平台管理员互不包含：平台管理员要用后台做别的事，但那不等于能读每一条私密反馈。

- 只在部署配置里，界面上没有能往里加人的地方。测试环境的值写在 `.github/workflows/deploy-dev.yml` 里，每次部署整份写进那台机器的 env 文件，手改不会留下来；改名单就是改这个文件、走评审。
- 为空就是没有人管反馈：提交照常，管理员那一支全关。它不像平台管理员那样启动时强制要求非空，因为空着只是关掉一个队列，不是把整个后台锁死。其他部署在自己的 env 文件里设（`deploy/.env.prod.example`）。
- 名字不叫 `FEEDBACK_ADMIN_HANDLES`：那是平台管理员名单的旧名，后端仍把它当 `PLATFORM_ADMIN_HANDLES` 读。
- `/feedback/meta` 两个都答：`is_admin`（反馈管理员）和 `is_platform_admin`。后台外壳任一为真就能进，队列只给前者，其余各块只给后者。

## 后台有什么 {#console}

后台管理在 `/admin`（`frontend/src/router/feedback.ts`）：「反馈队列」（用户提交的反馈，只对反馈管理员）、「看板」、「模型管理」（平台可用的模型目录）、「空间申请」（审核新建的空间）、「成员管理」（界面名单里的管理员）。

## 开发文档的访问 {#dev-docs}

本栏「开发文档」只对平台管理员开放：服务端先核对管理员身份，再允许读取开发文档的页面和内容。
