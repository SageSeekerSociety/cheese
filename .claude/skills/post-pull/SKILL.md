---
name: post-pull
description: >
  git pull 之后必须执行的检查(迁移、依赖、健康检查)。
  TRIGGER when: 用户刚 git pull / git fetch+merge / 切换分支后拉取了别人的改动,
  或问"拉完代码要做什么""怎么同步环境"。
  SKIP: 初次 clone 后的完整 setup(见 README)、纯前端拉取。
---

# Post-Pull Checks

每次 `git pull` 后执行:

```bash
bash .claude/scripts/post-pull.sh
# 或
task deps:sync && task db:migrate
```

该脚本自动完成:迁移检查、alembic 升级、依赖变更检测、健康检查。

## 注意事项

- 迁移失败时不要回滚:优先前滚修复(forward fix),避免数据丢失。
- 如有大量数据操作(如加索引、NOT NULL 约束),先在 staging 环境验证耗时。
