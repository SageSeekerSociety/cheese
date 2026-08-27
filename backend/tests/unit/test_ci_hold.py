"""把 CI 压在红灯上的占位测试 —— 本 PR 全部工作落地前不要删。

采纳即合并 (#296) 会在 CI 全绿的那一刻自动合并 PR。这一批工作要全部落在
同一个 PR 上，所以在最后一步之前必须有一个必然失败的检查把合并闸门压住。

删除本文件是这批工作的最后一个动作。
"""


def test_ci_hold_until_every_task_lands() -> None:
    """故意失败：它红着，就说明这个 PR 还没做完。"""
    raise AssertionError(
        "占位闸门：全部任务完成后删除 backend/tests/unit/test_ci_hold.py"
    )
