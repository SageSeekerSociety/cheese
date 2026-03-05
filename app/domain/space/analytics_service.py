from collections import Counter
from dataclasses import dataclass

from app.domain.task.repositories import TaskRepository, TaskMembershipRepository
from app.domain.user.repositories import UserRepository, UserProfileRepository


@dataclass
class AnalyticsFilters:
    category_id: int | None = None
    publisher_id: int | None = None
    task_status: str | None = None


class SpaceAnalyticsService:
    _PAGE_SIZE = 1000

    def __init__(
        self,
        task_repo: TaskRepository,
        membership_repo: TaskMembershipRepository,
        user_repo: UserRepository | None = None,
        profile_repo: UserProfileRepository | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._membership_repo = membership_repo
        self._user_repo = user_repo
        self._profile_repo = profile_repo

    async def _fetch_all_tasks(
        self,
        *,
        space_id: int,
        category_id: int | None = None,
        approved: int | None = None,
        owner_id: int | None = None,
    ) -> list:
        """Paginate through list_tasks until all results are fetched."""
        all_tasks: list = []
        offset = 0
        while True:
            batch = await self._task_repo.list_tasks(
                space_id=space_id,
                category_id=category_id,
                approved=approved,
                owner_id=owner_id,
                keywords=None,
                topics=None,
                joined=None,
                current_user_id=None,
                limit=self._PAGE_SIZE,
                offset=offset,
                sort_by="updatedAt",
                sort_order="desc",
            )
            all_tasks.extend(batch)
            if len(batch) < self._PAGE_SIZE:
                break
            offset += self._PAGE_SIZE
        return all_tasks

    async def get_task_analytics(
        self,
        *,
        space_id: int,
        category_id: int | None,
        task_status: str | None,
        publisher_id: int | None,
    ) -> dict:
        tasks = await self._fetch_all_tasks(
            space_id=space_id,
            category_id=category_id,
            approved=self._map_task_status(task_status),
            owner_id=publisher_id,
        )
        status_counter = Counter(self._status_label(task.approved) for task in tasks)
        category_counter = Counter(
            getattr(task, "category_id", None) or "Uncategorized" for task in tasks
        )

        memberships = await self._membership_repo.list_memberships_for_space(space_id)
        participant_counter = Counter(self._participant_label(m.approved) for m in memberships)

        return {
            "taskCategoryDistribution": self._build_distribution("taskCategory", category_counter),
            "taskStatusDistribution": self._build_distribution("taskStatus", status_counter),
            "participantStatusDistribution": self._build_distribution(
                "participantStatus", participant_counter
            ),
            "rankDistribution": self._empty_distribution("rank"),
            "successStudentStatistics": self._empty_student_stats(),
            "unsuccessStudentStatistics": self._empty_student_stats(),
        }

    async def get_publishers_participation(self, *, space_id: int) -> list[dict]:
        tasks = await self._fetch_all_tasks(space_id=space_id)
        publisher_counter = Counter(task.creator_id for task in tasks)
        memberships = await self._membership_repo.list_memberships_for_space(space_id)
        participants_by_task: dict[int, list[int]] = {}
        completed_users: dict[int, int] = {}
        for membership in memberships:
            participants_by_task.setdefault(membership.task_id, []).append(membership.member_id)
            if getattr(membership, "completion_status", "NOT_SUBMITTED") == "COMPLETED":
                completed_users[membership.task_id] = completed_users.get(membership.task_id, 0) + 1

        # Resolve publisher names from profiles (nickname) with fallback to username
        all_publisher_ids = list(publisher_counter.keys())
        profiles: dict = {}
        users: dict = {}
        if self._profile_repo and all_publisher_ids:
            profiles = await self._profile_repo.get_profiles_by_user_ids(all_publisher_ids)
        if self._user_repo and all_publisher_ids:
            users = await self._user_repo.get_by_ids(all_publisher_ids)

        data: list[dict] = []
        for publisher_id, count in publisher_counter.items():
            task_ids = [task.id for task in tasks if task.creator_id == publisher_id]
            participant_ids = {
                member for task_id in task_ids for member in participants_by_task.get(task_id, [])
            }
            completed_total = sum(completed_users.get(task_id, 0) for task_id in task_ids)

            profile = profiles.get(publisher_id)
            user = users.get(publisher_id)
            if profile and profile.nickname:
                name = profile.nickname
            elif user:
                name = user.username
            else:
                name = f"User {publisher_id}"

            data.append(
                {
                    "publisherId": publisher_id,
                    "publisherName": name,
                    "participants": len(participant_ids),
                    "completedUsers": completed_total,
                    "taskCount": count,
                }
            )
        return data

    async def export_participants(self, *, space_id: int) -> str:
        tasks = await self._fetch_all_tasks(space_id=space_id)
        memberships = await self._membership_repo.list_memberships_for_space(space_id)
        membership_map: dict[int, list] = {}
        for membership in memberships:
            membership_map.setdefault(membership.task_id, []).append(membership)

        rows = ["taskId,taskName,participantId,status"]
        for task in tasks:
            members = membership_map.get(task.id, [])
            name = self._csv_escape(task.name)
            if not members:
                rows.append(f"{task.id},{name},,0")
            for member in members:
                rows.append(
                    f"{task.id},{name},{member.member_id},{self._participant_label(member.approved)}"
                )
        return "\n".join(rows)

    @staticmethod
    def _csv_escape(value: str) -> str:
        if any(c in value for c in (",", '"', "\n", "\r")):
            return '"' + value.replace('"', '""') + '"'
        return value

    _APPROVED_MAP: dict[str, int] = {
        "APPROVED": 0,
        "DISAPPROVED": 1,
        "NONE": 2,
    }
    _APPROVED_REV: dict[int, str] = {v: k for k, v in _APPROVED_MAP.items()}

    def _map_task_status(self, value: str | None) -> int | None:
        if value is None:
            return None
        result = self._APPROVED_MAP.get(value.upper())
        if result is None:
            from app.core.errors import BadRequestError

            raise BadRequestError(
                f"Invalid taskStatus: {value}. Must be APPROVED, DISAPPROVED, or NONE"
            )
        return result

    def _status_label(self, approved_value: int | None) -> str:
        return self._APPROVED_REV.get(approved_value, "UNKNOWN")

    def _participant_label(self, approved_value: int) -> str:
        return self._APPROVED_REV.get(approved_value, "UNKNOWN")

    def _build_distribution(self, name: str, counter: Counter) -> dict:
        total = sum(counter.values()) or 1
        items = [
            {
                "label": str(label),
                "count": count,
                "percentage": round(count / total, 2),
            }
            for label, count in counter.items()
        ]
        return {"name": name, "type": "DISCRETE", "items": items}

    def _empty_distribution(self, name: str) -> dict:
        return {"name": name, "type": "DISCRETE", "items": []}

    def _empty_student_stats(self) -> dict:
        return {
            "totalStudents": 0,
            "totalStudentsWithRealName": 0,
            "gradeDistribution": self._empty_distribution("grade"),
            "majorDistribution": self._empty_distribution("major"),
            "classNameDistribution": self._empty_distribution("class"),
        }
