#!/usr/bin/env python
"""Taskiq worker entry point.

Run with:
    taskiq worker worker:broker
    taskiq scheduler worker:scheduler
"""
from app.core.taskiq_broker import broker, scheduler
import app.core.taskiq_tasks  # noqa: F401 - register tasks

__all__ = ["broker", "scheduler"]
