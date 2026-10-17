"""会话主机的内存读数：`/proc/meminfo` 里哪两行、单位是什么、读不到时是什么。

看板要回答的是「新会话还能不能起」—— 门槛是 `MemAvailable` 够不够
`agent_session_memory_max_mb`（默认 3072 MiB）。所以这里钉的是两个数本身：
`MemTotal` 和 `MemAvailable`，都从 kB 换成 MiB。

读不到时返回 `None` 而不是 0：0 是一个读数（「这台机器一点内存都没有了」），
`None` 是「没人读到」，看板上这两者必须长得不一样。
"""

from app.api.routes.admin_stats import meminfo_mib


def test_meminfo_mib_reads_total_and_available_out_of_the_kernel_text():
    """内核文本里还有很多别的行，只取要的那两行，并且除以 1024 换成 MiB。"""
    text = "\n".join(
        [
            "MemTotal:       16252928 kB",
            "MemFree:         1048576 kB",
            "MemAvailable:    6291456 kB",
            "Buffers:           65536 kB",
            "SwapTotal:             0 kB",
        ]
    )

    assert meminfo_mib(text) == (15872, 6144)


def test_a_missing_or_unparsable_line_is_unknown_not_zero():
    """没有这两行、或者值不是一个数时就是「读不到」，不是「主机没有内存」。"""
    assert meminfo_mib("MemFree: 1024 kB\n") == (None, None)
    assert meminfo_mib("MemTotal: n/a kB\nMemAvailable: 4096 kB\n") == (None, 4)
