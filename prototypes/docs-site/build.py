#!/usr/bin/env python3
"""Inline the one screenshot into src.html so the preview is a single self-contained file."""
import base64
from pathlib import Path

HERE = Path(__file__).resolve().parent
img = HERE.parents[1] / "docs/manual/public/images/teams-invite.png"
data = "data:image/png;base64," + base64.b64encode(img.read_bytes()).decode()
(HERE / "index.html").write_text((HERE / "src.html").read_text().replace("__INVITE_IMG__", data))
