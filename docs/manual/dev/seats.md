---
title: 席位与权限判定
kind: 参考
summary: 谁能在哪里做什么。
covers:
  - backend/app/domain/authz/policy.py
  - backend/app/auth/
---

# 席位与权限判定 {#seats}

谁能在哪里做什么。

> 讲：判定规则和它在代码里的位置。不讲：界面上怎么邀请成员，见使用文档[团队](/teams#teams)。

## 人和 AI 队友用同一套判定 {#same}

`backend/app/domain/authz/policy.py` 的判定不区分参与者是人还是 AI 队友：

- 没有凭证、或名单为空，一律不授予。
- 共享话题：房间成员或项目成员可以访问。
- 私有话题：必须是这个房间的成员。
- 凭证的作用范围（例如会话令牌只属于某个项目和话题）在请求入口由 `ActorResolver` 先检查，再进入上面的判定。

## 管理项目 {#manage}

管理一个项目，包括外部成员和项目设置，要求是项目的所有者，或者项目所属团队的所有者或管理员。

## 空间 {#spaces}

空间有自己的角色：创建者、管理员（课程里就是教师）和成员。只有创建者能设置或撤销管理员（`backend/app/auth/space_access.py`）。

## AI 队友 {#agents}

AI 队友以席位的身份参与房间，和人走同一套判定。平台管理类的操作要求平台管理员身份，AI 队友不在管理员名单里，所以做不了，见[平台管理员](/dev/admins)。
