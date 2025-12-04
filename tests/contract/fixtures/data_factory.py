"""
Test data factory for generating consistent test data across contract tests.

Provides methods to generate test users, teams, tasks, notifications, etc.
with realistic but predictable data for both Kotlin and Python backends.
"""

from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


def random_string(length: int = 8) -> str:
    """Generate a random alphanumeric string."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def random_email() -> str:
    """Generate a random email address."""
    return f"test_{random_string()}@example.com"


@dataclass
class TestUser:
    """Test user data structure."""

    id: int
    username: str
    nickname: str
    email: str
    password: str = "TestPassword123!"

    def to_register_payload(self) -> dict[str, Any]:
        """Convert to registration request payload."""
        return {
            "username": self.username,
            "nickname": self.nickname,
            "email": self.email,
            "emailCode": "123456",
            "password": self.password,
        }

    def to_login_payload(self) -> dict[str, Any]:
        """Convert to login request payload."""
        return {
            "username": self.username,
            "password": self.password,
        }


@dataclass
class TestTeam:
    """Test team data structure."""

    id: int | None = None
    name: str = ""
    intro: str = ""
    avatar_id: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"TestTeam_{random_string()}"
        if not self.intro:
            self.intro = f"This is a test team created at {datetime.now().isoformat()}"

    def to_create_payload(self) -> dict[str, Any]:
        """Convert to team creation request payload."""
        payload: dict[str, Any] = {
            "name": self.name,
            "intro": self.intro,
        }
        if self.avatar_id:
            payload["avatarId"] = self.avatar_id
        return payload


@dataclass
class TestTask:
    """Test task data structure."""

    id: int | None = None
    name: str = ""
    intro: str = ""
    description: str = ""
    space_id: int | None = None
    category_id: int | None = None
    deadline: datetime | None = None
    participant_limit: int = 100
    submission_type: str = "INDIVIDUAL"

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"TestTask_{random_string()}"
        if not self.intro:
            self.intro = "A test task for contract testing"
        if not self.description:
            self.description = "Detailed description of the test task"
        if not self.deadline:
            self.deadline = datetime.now() + timedelta(days=7)

    def to_create_payload(self) -> dict[str, Any]:
        """Convert to task creation request payload."""
        payload: dict[str, Any] = {
            "name": self.name,
            "intro": self.intro,
            "description": self.description,
            "participantLimit": self.participant_limit,
            "submissionType": self.submission_type,
        }
        if self.space_id:
            payload["spaceId"] = self.space_id
        if self.category_id:
            payload["categoryId"] = self.category_id
        if self.deadline:
            payload["deadline"] = self.deadline.isoformat()
        return payload


@dataclass
class TestNotification:
    """Test notification data structure."""

    id: int | None = None
    type: str = "SYSTEM"
    title: str = ""
    content: str = ""
    receiver_id: int = 1
    read: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title:
            self.title = f"Test Notification {random_string()}"
        if not self.content:
            self.content = "This is a test notification content"


class TestDataFactory:
    """
    Factory for creating test data with optional ID tracking.

    Usage:
        factory = TestDataFactory()
        user = factory.create_user()
        team = factory.create_team()
        task = factory.create_task(space_id=1)
    """

    def __init__(self, seed: int | None = None):
        """
        Initialize the factory.

        Args:
            seed: Optional random seed for reproducible test data
        """
        if seed is not None:
            random.seed(seed)
        self._user_counter = 0
        self._team_counter = 0
        self._task_counter = 0
        self._notification_counter = 0
        self._created_users: list[TestUser] = []
        self._created_teams: list[TestTeam] = []
        self._created_tasks: list[TestTask] = []

    def create_user(
        self,
        user_id: int | None = None,
        username: str | None = None,
        nickname: str | None = None,
        email: str | None = None,
    ) -> TestUser:
        """Create a test user."""
        self._user_counter += 1
        user = TestUser(
            id=user_id or self._user_counter,
            username=username or f"testuser_{random_string()}",
            nickname=nickname or f"Test User {self._user_counter}",
            email=email or random_email(),
        )
        self._created_users.append(user)
        return user

    def create_team(
        self,
        team_id: int | None = None,
        name: str | None = None,
        intro: str | None = None,
    ) -> TestTeam:
        """Create a test team."""
        self._team_counter += 1
        team = TestTeam(
            id=team_id,
            name=name or f"TestTeam_{random_string()}",
            intro=intro or f"Test team {self._team_counter} intro",
        )
        self._created_teams.append(team)
        return team

    def create_task(
        self,
        task_id: int | None = None,
        name: str | None = None,
        space_id: int | None = None,
        category_id: int | None = None,
        deadline_days: int = 7,
    ) -> TestTask:
        """Create a test task."""
        self._task_counter += 1
        task = TestTask(
            id=task_id,
            name=name or f"TestTask_{random_string()}",
            space_id=space_id,
            category_id=category_id,
            deadline=datetime.now() + timedelta(days=deadline_days),
        )
        self._created_tasks.append(task)
        return task

    def create_notification(
        self,
        notification_id: int | None = None,
        notification_type: str = "SYSTEM",
        receiver_id: int = 1,
        read: bool = False,
    ) -> TestNotification:
        """Create a test notification."""
        self._notification_counter += 1
        return TestNotification(
            id=notification_id,
            type=notification_type,
            receiver_id=receiver_id,
            read=read,
        )

    @property
    def users(self) -> list[TestUser]:
        """Get all created users."""
        return self._created_users.copy()

    @property
    def teams(self) -> list[TestTeam]:
        """Get all created teams."""
        return self._created_teams.copy()

    @property
    def tasks(self) -> list[TestTask]:
        """Get all created tasks."""
        return self._created_tasks.copy()

    def reset(self) -> None:
        """Reset all counters and clear created entities."""
        self._user_counter = 0
        self._team_counter = 0
        self._task_counter = 0
        self._notification_counter = 0
        self._created_users.clear()
        self._created_teams.clear()
        self._created_tasks.clear()


# Singleton instance for convenience
_default_factory: TestDataFactory | None = None


def get_factory(seed: int | None = None) -> TestDataFactory:
    """Get the default test data factory instance."""
    global _default_factory
    if _default_factory is None:
        _default_factory = TestDataFactory(seed=seed)
    return _default_factory
