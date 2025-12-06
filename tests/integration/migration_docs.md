# Kotlin to Python Test Migration Documentation

## Overview

This document tracks the migration of integration tests from `cheese-backend-nt` (Kotlin) to `cheese-backend-py` (Python).

## Critical Issues Summary

### Assertion Coverage Gap

| Test File | Kotlin Assertions | Python Assertions | Coverage |
|-----------|-------------------|-------------------|----------|
| TeamTest | 134 | 89 | 66% |
| TaskTest | 243 | 127 | 52% |
| TaskSubmissionTest | 144 | 103 | 72% |
| RankTest | 17 | 49 | 288% ✓ |
| TeamApplicationTest | 84 | 65 | 77% |

### Missing Field Validations

| Test File | Missing Fields |
|-----------|----------------|
| TeamTest | `owner.id`, `admins.total`, `members.total` |
| TaskTest | `eligibility` checks (12 in Kotlin, 0 in Python) |

### xfail Test Count: 46 total

| Test File | xfail Count | Main Reasons |
|-----------|-------------|--------------|
| TeamTest | 5 | 'joined' field missing in response |
| TaskTest | 9 | Permission checks, archived categories |
| TaskSubmissionTest | 19 | Eligibility, permissions, team features |
| RankTest | 6 | Rank progression not fully implemented |
| TeamApplicationTest | 7 | Response structure differences |

## Test Count Summary

| Test File | Kotlin | Python | Status |
|-----------|--------|--------|--------|
| DiscussionTest | 3 | 5 | Python +2 |
| KnowledgeTest | 6 | 8 | Python +2 |
| NotificationTest | 12 | 14 | Python +2 |
| ProjectTest | 2 | 4 | Python +2 |
| RankTest | 15 | 17 | Python +2 |
| SpaceTest | 21 | 25 | Python +4 |
| TaskSubmissionReviewTest | 21 | 23 | Python +2 |
| TaskSubmissionTest | 32 | 34 | Python +2 |
| TaskTest | 59 | 61 | Python +2 |
| TaskTopicTest | 4 | 6 | Python +2 |
| TeamApplicationTest | 28 | 30 | Python +2 |
| TeamTest | 28 | 30 | Python +2 |
| **Total** | **231** | **257** | **Python +26** |

## Issues Found

### 1. Weak Assertions in Python Tests

Python tests often have fewer assertions than their Kotlin counterparts. Example:

**Kotlin `Team - Create team`:**
```kotlin
assertEquals(teamName, team.name)
assertEquals(teamIntro, team.intro)
assertEquals(teamDescription, team.description)
assertEquals(teamAvatarId, team.avatarId)
assertEquals(creator.userId, team.owner.id)
assertEquals(0, team.admins.total)
assertEquals(0, team.members.total)
assertEquals(true, team.joined)
assertEquals(TeamMemberRoleTypeDTO.OWNER, team.role)
```

**Python `test_create_team`:**
```python
assert data["data"]["team"]["name"] == team_setup["team_name"]
assert data["data"]["team"]["intro"] == team_setup["team_intro"]
assert data["data"]["team"]["id"] > 0
# Missing: description, avatarId, owner.id, admins, members, joined, role
```

### 2. Missing Kotlin Tests in Python

#### TeamTest
| Kotlin Test | Python Equivalent | Status |
|-------------|-------------------|--------|
| `Membership - Member user 'member' is already added` | None | ❌ Missing |
| `Membership - Change role ADMIN to MEMBER again` | None | ❌ Missing |

#### TaskTest
| Kotlin Test | Python Equivalent | Status |
|-------------|-------------------|--------|
| `Task - Registration start time gates participation` | None | ❌ Missing |
| `Task - Try updating Task category to archived` | None | ❌ Missing |

#### RankTest
| Kotlin Test | Python Equivalent | Status |
|-------------|-------------------|--------|
| `test get space with myRank is 2 after passing rank 2 task` | `test_rank2_review_upgrades_to_rank2` (xfail) | ⚠️ Partial |

### 3. Test Mapping

#### TeamTest (Kotlin 28 → Python 30)

| Kotlin Test | Python Test |
|-------------|-------------|
| Team - Create team | test_create_team |
| Team - Get team details as owner | test_get_team_details_as_owner |
| Team - Enumerate teams filtered by name | test_enumerate_teams |
| Team - Enumerate teams filtered by ID | test_enumerate_teams_by_id |
| Team - Enumerate teams filtered by name with pagination | test_enumerate_teams_with_pagination |
| Team - Enumerate teams default (no filter) | test_enumerate_teams_no_filter |
| Team - Get my teams | test_get_my_teams |
| Membership - Invite and accept ADMIN | test_invite_admin |
| Membership - Try invite ADMIN again | ❌ Missing |
| Membership - Try invite MEMBER using ADMIN | test_admin_can_invite_member |
| Membership - Accept ADMIN invitation | test_invite_and_accept_member |
| Membership - Member user already added | ❌ Missing |
| Membership - Try invite using MEMBER token fails | test_member_cannot_invite |
| Team - Update fails for anonymous user | test_update_team_fails_for_anonymous |
| Team - Update fails for member | test_update_team_forbidden_for_member |
| Team - Update success for admin | test_update_team_success_for_admin |
| Team - Update success for owner | test_update_team |
| Membership - Get team members list | test_get_team_members |
| Membership - Change role ADMIN to MEMBER | test_change_role_admin_to_member |
| Membership - Change role MEMBER to ADMIN | test_change_role_member_to_admin |
| Membership - Change role ADMIN to MEMBER again | ❌ Missing |
| Team - Get team as member | test_get_team_as_member |
| Team - Get team as non-member | test_get_team_as_non_member |
| Membership - Remove self as member | test_remove_self_as_member |
| Membership - Add member back via invitation | test_add_member_back_via_invitation |
| Membership - Remove member using owner token | test_remove_member_using_owner_token |
| Membership - Remove admin using owner token | test_remove_admin_using_owner_token |
| Team - Delete team success by owner | test_delete_team |

**Python-only tests:**
- test_get_team
- test_update_team_forbidden_for_non_member
- test_change_member_role
- test_remove_member
- test_join_request_workflow

#### TaskSubmissionTest (Kotlin 32 → Python 34)

| Kotlin Test | Python Test |
|-------------|-------------|
| test create tasks | test_update_task_properties |
| test update task details | test_update_task_name_and_intro |
| test add participant user 1 | (covered in setup) |
| test add participant user 2 | (covered in setup) |
| test add participant team | (covered in team fixture) |
| test get task 1 eligibility before approval | test_get_task_eligibility_before_joining |
| test get task 2 eligibility for team before approval | test_get_task_eligibility_for_team_before_approval |
| test submit task user fails before approval | test_submit_fails_before_participant_approval |
| test approve participant 1 using bulk endpoint | test_approve_participant_via_bulk_endpoint |
| test get task 1 eligibility after approval | test_get_task_eligibility_after_joining |
| test approve participant 2 using bulk endpoint | (combined) |
| test approve participant team using bulk endpoint | test_approve_team_participant_via_bulk_endpoint |
| test get task 2 eligibility for team after approval | test_get_task_eligibility_for_team_after_approval |
| test submit task user 1 first time | test_submit_task_first_time |
| test submit task user 2 first time | test_submit_task_user2_first_time |
| test submit task team first time | test_submit_task_team_first_time |
| test submit again fails when not resubmittable | test_resubmit_task_fails_when_not_resubmittable |
| test update task to resubmittable fails for non-owner | test_update_task_fails_for_non_owner |
| test update task to resubmittable succeeds for owner | (covered in resubmit test) |
| test resubmit task user 1 succeeds after update | test_resubmit_task_when_resubmittable |
| test update submission fails when not editable | test_edit_submission_fails_when_not_editable |
| test update task to editable succeeds for owner | (covered in edit test) |
| test update submission succeeds after task made editable | test_edit_submission_when_editable |
| test get submissions fails for irrelevant user | test_get_submissions_fails_for_irrelevant_user |
| test get submissions fails for different participant | test_get_submissions_fails_for_other_participant |
| test get submissions default (latest) for owner | test_get_submissions_default_returns_latest |
| test get submissions with all versions | test_get_submissions_all_versions |
| test get submissions fails via member query param | test_get_submissions_fails_via_member_query_param |
| test get submissions succeeds for self via member query param | test_get_submissions_succeeds_via_member_query_param |
| test get submissions succeeds for owner via participantId path | test_get_submissions_succeeds_for_owner_via_participant_path |
| test delete task fails for non-owner | test_delete_task_fails_for_non_owner |
| test delete task succeeds for owner | test_delete_task_as_owner |

#### RankTest (Kotlin 15 → Python 17)

| Kotlin Test | Python Test |
|-------------|-------------|
| test get space with myRank without rank enabled | test_get_space_with_rank_disabled |
| test enumerate spaces with myRank without rank enabled | test_enumerate_spaces_with_rank_disabled |
| test enable rank for space | test_enable_rank_for_space |
| test get space with myRank after enabling rank | test_get_space_with_rank_enabled |
| test enumerate spaces with myRank after enabling rank | test_enumerate_spaces_with_rank_enabled |
| test join rank 2 task fails when rank is 0 | test_join_rank2_task_fails_with_rank0 |
| test create failing review for rank 1 task submission | test_failing_review_does_not_upgrade_rank |
| test get space with myRank remains 0 after failing review | test_rank_remains_zero_after_failing_review |
| test update review for rank 1 task to accepted | test_update_review_to_accepted_upgrades_rank |
| test join rank 2 task succeeds when rank is 1 | test_join_rank2_task_succeeds_after_rank1_achieved |
| test get space with myRank is 1 after passing rank 1 task | test_rank_visible_in_space_response |
| test create accepted review for rank 2 task submission | test_rank2_review_upgrades_to_rank2 (xfail) |
| test get space with myRank is 2 after passing rank 2 task | (covered in above, xfail) |
| test create accepted review for another rank 1 does not upgrade | test_another_rank1_task_does_not_upgrade_further |
| test get space with myRank remains 2 after another rank 1 | (covered in above) |

**Python-only tests:**
- test_review_submission_upgrades_rank
- test_rank_disabled_returns_zero_or_null
- test_task_has_rank_field
- test_update_space_disable_rank

## Action Items

### Priority 1: Strengthen Assertions

Files needing assertion improvements:
1. `test_team.py` - Add owner, admins, members, joined, role checks
2. `test_task.py` - Add category, approval status, participant count checks
3. `test_task_submission.py` - Add eligibility, version, submitter checks
4. `test_rank.py` - Add myRank value checks after operations

### Priority 2: Add Missing Tests

1. **TeamTest:**
   - `test_duplicate_member_addition_fails`
   - `test_change_role_chain` (ADMIN→MEMBER→ADMIN→MEMBER)

2. **TaskTest:**
   - `test_registration_start_time_gates_participation`
   - `test_update_task_category_to_archived_fails`

3. **RankTest:**
   - Remove xfail from `test_rank2_review_upgrades_to_rank2` and implement properly

### Priority 3: Fix xfail Tests

Many tests are marked `@pytest.mark.xfail` due to Python backend differences. These need investigation:
- Team tests with 'joined' field
- Task eligibility tests
- Bulk approval tests

## File Locations

- Kotlin tests: `/home/andyl/cheese/cheese-backend-nt/src/test/kotlin/org/rucca/cheese/api/`
- Python tests: `/home/andyl/cheese/cheese-backend-py/tests/integration/`

## Notes

- Python tests are independent (each test sets up its own state)
- Kotlin tests use `@Order` annotation for sequential execution
- Some Kotlin tests share state via class variables, Python tests use fixtures
