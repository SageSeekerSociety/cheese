"""出题人能选的每一种运行环境，评测主机都有镜像能跑（方案 §运行环境镜像）。"""

from app.domain.judge.runtimes import RUNTIMES, image_of
from app.domain.task.test_suite import RUNTIME_KEYS


def test_every_runtime_a_setter_can_pick_has_an_image():
    assert set(RUNTIMES) == set(RUNTIME_KEYS)
    for key in RUNTIME_KEYS:
        assert image_of(key)


def test_a_runtime_nobody_can_pick_has_no_image():
    assert image_of("cobol85") is None
