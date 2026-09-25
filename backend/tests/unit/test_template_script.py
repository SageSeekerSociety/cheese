"""template.py：读模板的结构，按占位填出一份新文件，模板本身一个字节不动。

用仓库里随平台发的标准模板做样本 —— 它们就是用户在房间里「从模板新建」拿到的那几份。
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "sandbox" / "skills" / "documents" / "scripts" / "template.py"
TEMPLATES = ROOT / "app" / "domain" / "documents" / "templates"


def run(*args) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True
    )


def _values(tmp_path: Path, values: dict) -> Path:
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return path


def test_fill_replaces_placeholders_and_keeps_every_other_part(tmp_path):
    template = TEMPLATES / "report.docx"
    before = template.read_bytes()
    out = tmp_path / "报告.docx"
    done = run(
        "fill",
        template,
        "-o",
        out,
        "--values",
        _values(tmp_path, {"【报告标题】": "季度报告 <草案> & 附录"}),
        "--allow-leftover",
    )
    assert done.returncode == 0, done.stderr
    assert template.read_bytes() == before
    with zipfile.ZipFile(out) as new, zipfile.ZipFile(template) as old:
        body = new.read("word/document.xml").decode()
        assert "季度报告 &lt;草案&gt; &amp; 附录" in body
        assert "【报告标题】" not in body
        for name in old.namelist():
            if name != "word/document.xml":
                assert new.read(name) == old.read(name), name


def test_fill_refuses_to_call_a_half_filled_document_done(tmp_path):
    done = run(
        "fill",
        TEMPLATES / "weekly.docx",
        "-o",
        tmp_path / "周报.docx",
        "--values",
        _values(tmp_path, {"【项目名称】": "知是"}),
    )
    assert done.returncode == 1
    assert "还留着没填的占位" in done.stdout


def test_fill_says_which_values_found_no_placeholder(tmp_path):
    done = run(
        "fill",
        TEMPLATES / "project-deck.pptx",
        "-o",
        tmp_path / "汇报.pptx",
        "--values",
        _values(tmp_path, {"【不存在的占位】": "x"}),
        "--allow-leftover",
    )
    assert done.returncode == 1
    assert "【不存在的占位】" in done.stdout


def test_fill_keeps_the_template_format(tmp_path):
    done = run(
        "fill",
        TEMPLATES / "analysis.xlsx",
        "-o",
        tmp_path / "分析.docx",
        "--values",
        _values(tmp_path, {}),
    )
    assert done.returncode == 2
    assert "同一种格式" in done.stderr


def test_inspect_describes_each_format(tmp_path):
    word = run("inspect", TEMPLATES / "report.docx").stdout
    assert "heading 1" in word.lower() and "表格" in word and "【报告标题】" in word
    slides = run("inspect", TEMPLATES / "research-deck.pptx").stdout
    assert "第 6 页" in slides
    book = run("inspect", TEMPLATES / "analysis.xlsx").stdout
    assert "[汇总]" in book and "图表：1 个" in book


def test_inspect_names_what_cannot_be_carried_over(tmp_path):
    source = TEMPLATES / "analysis.xlsx"
    with_pivot = tmp_path / "透视.xlsx"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(with_pivot, "w") as new:
        for item in old.infolist():
            new.writestr(item, old.read(item.filename))
        new.writestr("xl/pivotTables/pivotTable1.xml", "<pivotTableDefinition/>")
    assert "数据透视表" in run("inspect", with_pivot).stdout
