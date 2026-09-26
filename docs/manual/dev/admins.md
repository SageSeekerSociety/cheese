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

## 后台有什么 {#console}

后台管理在 `/admin`（`frontend/src/router/feedback.ts`）：「反馈队列」（用户提交的反馈）、「看板」、「模型管理」（平台可用的模型目录）、「空间申请」（审核新建的空间）、「成员管理」（界面名单里的管理员）。

## 开发文档的访问 {#dev-docs}

本栏「开发文档」只对平台管理员开放：服务端先核对管理员身份，再允许读取开发文档的页面和内容。
