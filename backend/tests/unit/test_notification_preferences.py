"""The preference matrix, quiet hours and digest-vs-instant (设计稿 通知设置页).

Pure: no database, no app — just the rules that decide which channels one
notification takes, so a wrong default or an inverted switch is caught here
rather than in a mail somebody did not want.
"""

from dataclasses import replace
from datetime import UTC, datetime, time

import pytest

from app.domain.notification.models import NotificationType
from app.domain.notification.preferences import (
    CATEGORY_ORDER,
    CATEGORY_TYPES,
    ChannelChoice,
    DigestCadence,
    EmailMode,
    PreferenceCategory,
    default_preferences,
    in_quiet_hours,
    parse_hhmm,
    resolve,
    to_dict,
    with_channel,
)

#: 设计稿矩阵那一张表，一格一格抄下来：事件 → (站内, 推送, 邮件)。
MOCK_MATRIX: dict[PreferenceCategory, tuple[bool, bool, bool]] = {
    PreferenceCategory.MENTION: (True, True, True),
    PreferenceCategory.REPLY: (True, False, True),
    PreferenceCategory.REACTION: (True, False, False),
    PreferenceCategory.WAITS_ON_ME: (True, True, True),
    PreferenceCategory.DEVICE_IN_USE: (True, True, False),
    PreferenceCategory.INVITATION: (True, True, True),
    PreferenceCategory.ANNOUNCEMENT: (True, False, True),
    PreferenceCategory.BILLING: (True, False, True),
}

#: 有今天的生产者的那几行；`额度与计费` 还没有码落到它上面，`resolve` 试不到它。
PRODUCED = [c for c in CATEGORY_ORDER if CATEGORY_TYPES[c]]

NOON = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)  # 不在默认安静时段里
NIGHT = datetime(2025, 6, 1, 23, 0, tzinfo=UTC)  # 落在默认安静时段里


class TestMockDefaults:
    def test_the_eight_rows_match_the_mock(self):
        events = to_dict(default_preferences())["events"]
        assert set(events) == {category.value for category in CATEGORY_ORDER}
        for category, (in_app, push, email) in MOCK_MATRIX.items():
            assert events[category.value] == {
                "inApp": in_app,
                "push": push,
                "email": email,
            }, category

    def test_master_switches_match_the_mock(self):
        body = to_dict(default_preferences())
        assert body["inAppEnabled"] is True
        assert body["pushEnabled"] is True
        assert body["emailMode"] == "digest"  # 设计稿「邮件」那一格停在摘要
        assert body["quietHoursEnabled"] is True
        assert body["quietHoursStart"] == "22:00"
        assert body["quietHoursEnd"] == "08:00"
        assert body["digestCadence"] == "weekly"


class TestResolveDefaults:
    @pytest.mark.parametrize("category", PRODUCED)
    def test_a_row_keeps_its_in_app_and_push_columns(self, category):
        type_ = next(iter(CATEGORY_TYPES[category]))
        intent = resolve(default_preferences(), type_, now=NOON)
        in_app, push, _email = MOCK_MATRIX[category]
        assert intent.in_app is in_app
        assert intent.push is push

    @pytest.mark.parametrize("category", PRODUCED)
    def test_the_email_column_is_what_folds_into_the_digest(self, category):
        """默认邮件在 `摘要`：一封信里合，而不是每条立即发。"""
        type_ = next(iter(CATEGORY_TYPES[category]))
        intent = resolve(default_preferences(), type_, now=NOON)
        _in_app, _push, email = MOCK_MATRIX[category]
        assert intent.email is False
        assert intent.digest is email

    def test_a_row_without_a_producer_keeps_its_columns(self):
        """额度与计费还没有码落到它上面：矩阵那一行仍是设计稿的一格一格。"""
        choice = default_preferences().channel(PreferenceCategory.BILLING)
        in_app, push, email = MOCK_MATRIX[PreferenceCategory.BILLING]
        assert (choice.in_app, choice.push, choice.email) == (in_app, push, email)


class TestEmailModes:
    def test_instant_sends_now_and_does_not_fold(self):
        pref = replace(default_preferences(), email_mode=EmailMode.instant)
        intent = resolve(pref, NotificationType.MENTION, now=NOON)
        assert intent.email is True
        assert intent.digest is False

    def test_digest_folds_and_never_sends_now(self):
        pref = replace(default_preferences(), email_mode=EmailMode.digest)
        intent = resolve(pref, NotificationType.MENTION, now=NOON)
        assert intent.email is False
        assert intent.digest is True

    def test_off_sends_nothing(self):
        pref = replace(default_preferences(), email_mode=EmailMode.off)
        intent = resolve(pref, NotificationType.MENTION, now=NOON)
        assert intent.email is False
        assert intent.digest is False

    def test_an_instant_email_in_quiet_hours_becomes_a_digest_line(self):
        pref = replace(default_preferences(), email_mode=EmailMode.instant)
        intent = resolve(pref, NotificationType.MENTION, now=NIGHT)
        assert intent.email is False
        assert intent.digest is True

    def test_cadence_off_leaves_no_digest_to_fold_into(self):
        pref = replace(
            default_preferences(),
            email_mode=EmailMode.digest,
            digest_cadence=DigestCadence.off,
        )
        intent = resolve(pref, NotificationType.MENTION, now=NOON)
        assert intent.email is False
        assert intent.digest is False


class TestQuietHours:
    def test_default_window_wraps_midnight(self):
        pref = default_preferences()
        assert in_quiet_hours(pref, NIGHT) is True
        assert in_quiet_hours(pref, NOON) is False

    def test_the_edges_are_half_open(self):
        pref = default_preferences()
        assert in_quiet_hours(pref, datetime(2025, 6, 1, 22, 0, tzinfo=UTC)) is True
        assert in_quiet_hours(pref, datetime(2025, 6, 1, 8, 0, tzinfo=UTC)) is False

    def test_switched_off_means_never(self):
        pref = replace(default_preferences(), quiet_hours_enabled=False)
        assert in_quiet_hours(pref, NIGHT) is False

    def test_start_equal_end_is_not_a_window(self):
        pref = replace(
            default_preferences(),
            quiet_hours_start=time(9, 0),
            quiet_hours_end=time(9, 0),
        )
        assert in_quiet_hours(pref, datetime(2025, 6, 1, 9, 0, tzinfo=UTC)) is False

    def test_quiet_hours_suppress_push_but_not_the_digest(self):
        intent = resolve(default_preferences(), NotificationType.MENTION, now=NIGHT)
        assert intent.push is False
        assert intent.in_app is True
        assert intent.digest is True


class TestMasterSwitches:
    def test_in_app_off_keeps_everything_out_of_the_inbox(self):
        pref = replace(default_preferences(), in_app_enabled=False)
        intent = resolve(pref, NotificationType.MENTION, now=NOON)
        assert intent.in_app is False

    def test_push_off_silences_every_type(self):
        pref = replace(default_preferences(), push_enabled=False)
        intent = resolve(pref, NotificationType.MENTION, now=NOON)
        assert intent.push is False

    def test_a_row_switch_overrides_the_matrix_default(self):
        pref = with_channel(
            default_preferences(),
            PreferenceCategory.REACTION,
            ChannelChoice(in_app=True, push=True, email=True),
        )
        intent = resolve(pref, NotificationType.REACTION, now=NOON)
        assert intent.push is True
        assert intent.digest is True


class TestTypesOutsideTheMatrix:
    def test_a_pushable_legacy_type_still_pushes(self):
        # 房间提示不在矩阵里：推送仍按旧的 `PUSHABLE`，邮件仍不落在 `MAILBOX_ONLY`。
        intent = resolve(default_preferences(), NotificationType.ROOM_NOTICE, now=NOON)
        assert intent.push is True
        assert intent.in_app is True
        assert intent.digest is True

    def test_a_non_pushable_legacy_type_does_not_push(self):
        intent = resolve(
            default_preferences(), NotificationType.DEADLINE_REMIND, now=NOON
        )
        assert intent.push is False


class TestHhmm:
    def test_round_trip(self):
        assert parse_hhmm("22:00") == time(22, 0)
        assert parse_hhmm("08:05") == time(8, 5)

    @pytest.mark.parametrize("bad", ["", "24:00", "08:60", "0800", "aa:bb"])
    def test_bad_input_raises(self, bad):
        with pytest.raises(ValueError):
            parse_hhmm(bad)
