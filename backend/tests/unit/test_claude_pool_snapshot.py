"""看板能看见账号池的哪一部分 —— 读快照文件这一层。

代理和平台是两个发布链，所以这里最要紧的一条不是「读得对」，而是**读不到时说什么**：
开发环境不跑订阅版代理是常态，用量那块看板不能因为这一小块就红掉，也不能把「没有
快照」画成「没有账号」。第二条是过期：行的 `until` 是绝对时刻，池子不变时文件本来
就不该变，所以旧文件照样给行，只是要说明它有多旧。
"""

import json
from pathlib import Path

from app.domain.platform_stats.claude_pool import (
    STALE_AFTER_S,
    read_claude_pool,
    snapshot_path,
)

NOW = 1_700_000_000.0


def _row(name, state, until=None, failures=0):
    return {"name": name, "state": state, "until": until, "failures": failures}


def _write(tmp_path, accounts=None, *, written_at=NOW, **extra):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            {
                "written_at": written_at,
                "retry_after": None,
                "accounts": accounts or [],
                **extra,
            }
        )
    )
    return path


def test_no_configured_log_means_nowhere_to_look():
    """`SUBSCRIPTION_USAGE_LOG` 为空是开发环境的常态：没有路径，也就没有文件。"""
    assert snapshot_path("") is None
    assert snapshot_path("   ") is None
    body = read_claude_pool(None, now=NOW)
    assert body["accounts"] == []
    assert "订阅版计量代理" in body["reason"]


def test_the_snapshot_is_looked_for_beside_the_ledger():
    assert snapshot_path("/var/log/cheese-proxy/usage.jsonl") == Path(
        "/var/log/cheese-proxy/accounts.json"
    )


def test_the_pool_reads_back_as_the_proxy_wrote_it(tmp_path):
    path = _write(
        tmp_path,
        [
            _row("primary", "available"),
            _row("second", "cooling", until=NOW + 600, failures=2),
        ],
    )
    body = read_claude_pool(path, now=NOW)
    assert body["reason"] is None
    assert body["stale"] is False
    assert body["written_at"] == NOW
    assert body["age_seconds"] == 0
    assert body["accounts"] == [
        _row("primary", "available"),
        _row("second", "cooling", until=NOW + 600, failures=2),
    ]
    assert body["retry_after"] == 600


def test_a_missing_file_is_a_reason_not_an_error():
    body = read_claude_pool(Path("/nonexistent/accounts.json"), now=NOW)
    assert body["accounts"] == []
    assert "还没有写下" in body["reason"]


def test_an_unparseable_file_is_a_reason_not_an_error(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text("{ this is not json")
    body = read_claude_pool(path, now=NOW)
    assert body["accounts"] == []
    assert "JSON" in body["reason"]


def test_a_snapshot_without_a_write_time_is_not_trusted(tmp_path):
    """没有 `written_at` 就判不出它有多新，宁可说不知道。"""
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": [_row("primary", "available")]}))
    body = read_claude_pool(path, now=NOW)
    assert body["accounts"] == []
    assert "写入时刻" in body["reason"]


def test_rows_that_are_not_rows_are_dropped(tmp_path):
    path = _write(tmp_path, [_row("primary", "available"), {"nope": 1}, "junk", 7])
    assert [row["name"] for row in read_claude_pool(path, now=NOW)["accounts"]] == [
        "primary"
    ]


def test_an_expired_cooldown_reads_as_available(tmp_path):
    """解冻时刻已经过去 —— 代理只会在真有请求时去探测它，界面没有理由把一个能用的
    账号画成「冷却到某时刻」，而那个时刻已经过了。"""
    path = _write(tmp_path, [_row("primary", "cooling", until=NOW - 1, failures=3)])
    body = read_claude_pool(path, now=NOW)
    assert body["accounts"][0]["state"] == "available"
    assert body["accounts"][0]["until"] is None
    assert body["retry_after"] is None


def test_a_spent_allowance_is_not_a_deadline(tmp_path):
    """`disabled` 是「等不来」：它不进 `retry_after`，否则界面会承诺一个不会发生的恢复。

    代理写 `disabled` 时不会带 `until`，所以这里特意塞一个 —— 判断要按状态，不能靠
    上游没写那个字段。
    """
    path = _write(tmp_path, [_row("primary", "disabled", until=NOW + 60, failures=4)])
    body = read_claude_pool(path, now=NOW)
    assert body["accounts"][0]["state"] == "disabled"
    assert body["retry_after"] is None


def test_the_earliest_deadline_wins(tmp_path):
    path = _write(
        tmp_path,
        [
            _row("primary", "cooling", until=NOW + 3600),
            _row("second", "cooling", until=NOW + 91),
            _row("third", "disabled", failures=1),
        ],
    )
    assert read_claude_pool(path, now=NOW)["retry_after"] == 91


def test_a_label_this_backend_does_not_know_is_not_available(tmp_path):
    """代理有自己的发布链，可能先写下这一版还不认识的状态 —— 那时既不能画成可用，
    也不该按「需人工重置」替它解释。"""
    path = _write(tmp_path, [_row("primary", "throttled")])
    assert read_claude_pool(path, now=NOW)["accounts"][0]["state"] == "unknown"


def test_an_old_snapshot_still_gives_its_rows(tmp_path):
    path = _write(
        tmp_path,
        [_row("primary", "available")],
        written_at=NOW - STALE_AFTER_S - 1,
    )
    body = read_claude_pool(path, now=NOW)
    assert body["stale"] is True
    assert body["age_seconds"] == STALE_AFTER_S + 1
    assert [row["name"] for row in body["accounts"]] == ["primary"]
    assert body["reason"] is None


def test_the_files_own_countdown_is_not_taken(tmp_path):
    """文件里的 `retry_after` 写下的那一刻就开始变旧；行上的时刻是绝对的，现算才对。"""
    path = _write(
        tmp_path,
        [_row("primary", "cooling", until=NOW + 120)],
        retry_after=99999,
    )
    assert read_claude_pool(path, now=NOW)["retry_after"] == 120
