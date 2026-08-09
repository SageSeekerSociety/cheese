## 目标

修复消息"补写"（离线/断线重连后补偿写入）路径里 eid 漏传导致同一条消息被重复 insert 的问题，补上幂等去重，并补充回归测试。

## 约束

- 遵循 Route → Service → Repository → Model 分层，修复应落在 Service/Repository 层（幂等判断、去重逻辑），不应把业务逻辑塞进 Route。
- 幂等保证优先用数据库唯一约束（unique constraint/index），退化方案是写入前查重；若加唯一约束需要新增 alembic migration。
- 新增测试放在 backend/tests/unit 或 backend/tests/integration（视是否需要 DB），风格参照现有测试（pytest.mark.anyio + SimpleNamespace/AsyncMock）。
- 完成后必须过 `task check`（ruff + pyright + pytest 全绿）。

## 当前状态

- 已派出 Explore 子代理定位：eid 在项目中的确切含义、补写调用链（入口到写库）、具体漏传位置、model 是否已有唯一约束、可参考的现有测试。结果待回收。

## 下一步

1. 收到定位结果后，确认 bug 根因（漏传参数 vs 缺失幂等判断 vs 唯一约束失效）。
2. 实施修复：补传 eid + 加/修正唯一约束或写入前查重。
3. 补充回归测试：模拟同一 eid 补写两次，断言只落一条记录。
4. 跑 `task check`，全绿后递验收。

## 验收标准

补写路径正确传递 eid；有唯一约束或等价幂等保证防止重复写入；新增测试覆盖重复补写场景；task check 全绿。
