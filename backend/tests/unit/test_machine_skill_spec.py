"""agentskills.io 的四条必填规则。

机器侧那份在 `scan.py` 的 `record()` 里（那段程序不许 import 平台代码，所以只能各留
一份）；这一份是平台在**落盘前**拦一道的那份。两边必须对同一批边界给出同一句话，
所以这里把边界逐条钉死。
"""

from app.domain.machine_skill import spec


def test_a_compliant_skill_passes():
    assert spec.check("pdf", "Read PDFs.") is None
    # 大写是允许的：Trae 自己发的技能就叫 TRAE-xxx。
    assert spec.check("TRAE-code-review", "Review a change.") is None


def test_a_name_that_breaks_the_shape_is_refused():
    assert spec.check("", "Read PDFs.") == "skillWebSpec"
    assert spec.check("has space", "Read PDFs.") == "skillWebSpec"
    assert spec.check("leading-", "Read PDFs.") == "skillWebSpec"
    assert spec.check("-leading", "Read PDFs.") == "skillWebSpec"
    assert spec.check("double--dash", "Read PDFs.") == "skillWebSpec"
    assert spec.check("dot.name", "Read PDFs.") == "skillWebSpec"


def test_a_missing_or_overlong_description_is_refused():
    assert spec.check("pdf", "") == "skillWebSpec"
    assert spec.check("pdf", "   ") == "skillWebSpec"
    assert spec.check("pdf", "a" * (spec.MAX_DESCRIPTION + 1)) == "skillWebSpec"


def test_the_limits_are_the_spec_s_own():
    assert spec.check("a" * spec.MAX_NAME, "x") is None
    assert spec.check("a" * (spec.MAX_NAME + 1), "x") == "skillWebSpec"
    assert spec.check("pdf", "a" * spec.MAX_DESCRIPTION) is None
