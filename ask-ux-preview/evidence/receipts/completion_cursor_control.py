"""Suppress durable completion settlement, retaining journal and assertions."""

import sys

import pytest

from app.domain.agent.chat import ChatService


async def ignore_completion(self, completion):
    pass


ChatService.confirm_work_completion = ignore_completion
sys.exit(pytest.main([
    "-q", "tests/integration/test_native_completion_commit_cursor.py",
    "-k", "older-than-retention",
]))
