import sys

# The macOS sandbox tests need `sandbox-exec`, which only a Mac has. CI runs on
# Linux and admits no skipped case (`scripts/assert_suite_ran.py`), so off a
# Mac the module is not collected at all.
collect_ignore = [] if sys.platform == "darwin" else ["test_device_sandbox_macos.py"]
