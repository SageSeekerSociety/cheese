from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("where.py")
spec = importlib.util.spec_from_file_location("where", SCRIPT)
where = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = where
spec.loader.exec_module(where)

REPO = SCRIPT.resolve().parents[2]

FILES = {
    "app/lib.py": "def widget():\n    return 1\n",
    "app/caller.py": "from app.lib import widget\n\n\ndef run():\n    return widget()\n",
    "app/attr.py": "from app import lib\n\n\ndef run():\n    return lib.widget()\n",
    "app/commented.py": "# widget is mentioned here only\nNOTE = 'call widget later'\n",
    "app/other.py": "def widget():\n    return 2\n",
    "app/rel.py": "import app.other\nfrom app.other import widget as w\nfrom .lib import widget\n\n\ndef run():\n    return widget()\n",
    "app/user.py": "from app.other import widget\n\n\ndef run():\n    return widget()\n",
    "scripts/zz_tool.py": "from app.lib import widget\n\nwidget()\n",
    "tests/unit/test_lib.py": "from app.lib import widget\n\n\ndef test_it():\n    assert widget() == 1\n",
}


def build(root: Path) -> None:
    (root / ".github/scripts").mkdir(parents=True)
    real = REPO / ".github/scripts/ci_fast_backend.py"
    (root / ".github/scripts/ci_fast_backend.py").write_text(real.read_text())
    for rel, body in FILES.items():
        path = root / "backend" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (root / "backend/scripts").mkdir(exist_ok=True)


def grep_calibrated(root: Path, name: str) -> set[str]:
    """The files `grep -w` finds, minus those where every hit is a comment or a string."""
    out = set()
    for path in (root / "backend").rglob("*.py"):
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            code = re.sub(r"'[^']*'|\"[^\"]*\"", "", code)
            if re.search(rf"\b{name}\b", code):
                out.add(str(path.relative_to(root)))
    return out


class WhereTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        build(self.root)

    def test_symbol_uses_match_calibrated_grep(self):
        found = set(where.find(self.root, "widget"))
        self.assertEqual(found, grep_calibrated(self.root, "widget"))
        self.assertNotIn("backend/app/commented.py", found)

    def test_scoped_symbol_excludes_the_namesake(self):
        found = where.find(self.root, "app.lib.widget")
        self.assertNotIn("backend/app/other.py", found)
        self.assertEqual(found["backend/app/lib.py"]["tags"], {"defines"})
        self.assertEqual(found["backend/app/caller.py"]["lines"][-1], (5, "return widget()"))

    def test_module_importers_include_tests(self):
        found = where.find(self.root, "backend/app/lib.py")
        self.assertEqual(set(found), {"backend/app/caller.py", "backend/app/attr.py", "backend/app/rel.py",
                                      "backend/scripts/zz_tool.py", "backend/tests/unit/test_lib.py"})
        # only the statement that reaches app.lib is listed, not `import app.other` beside it
        self.assertEqual(found["backend/app/rel.py"]["lines"], [(3, "from .lib import widget")])
        self.assertEqual(found["backend/app/attr.py"]["lines"], [(1, "from app import lib")])

    def test_tags_follow_the_module_scope(self):
        found = where.find(self.root, "widget")  # no module scope: nothing can be told apart
        self.assertEqual(found["backend/app/caller.py"]["tags"], {"uses"})
        found = where.find(self.root, "app.lib.widget")
        self.assertEqual(found["backend/app/caller.py"]["tags"], {"imports"})
        self.assertNotIn("backend/app/user.py", found)  # namesake from app.other, outside the scope

    def test_comment_filter_is_what_keeps_the_set_equal(self):
        def text_match(tree, name):
            return set(range(1, 3))  # a text scan counts the comment lines too

        with patch.object(where, "uses_of", text_match):
            found = set(where.find(self.root, "widget"))
        with self.assertRaises(AssertionError):
            self.assertEqual(found, grep_calibrated(self.root, "widget"))

    def test_cli_prints_files_with_their_rows(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), "widget"],
                             capture_output=True, text=True, check=True).stdout
        self.assertLess(out.index("app/caller.py"), out.index("tests/unit/test_lib.py"))


if __name__ == "__main__":
    unittest.main()
