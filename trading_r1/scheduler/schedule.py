# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""When a job fires: a five-field cron expression in a time zone, or one time.

Instants are kept in UTC; the zone only decides what "08:00" means. Daylight-saving shifts follow
the usual cron rules (a time that does not exist that day runs when the clock jumps over it; a
time that happens twice runs once). A one-time run given as a local time in a zone runs at
that clock time; a time the clock skips runs at the offset in force before the jump (so just
after it), and a time that happens twice runs at its first occurrence.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

from trading_r1.state.runtime_config import runtime_config

EVERY = ("day", "weekdays", "week")  # the presets a person picks instead of writing cron
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_LOCAL_AT = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
_PROBE = 6  # occurrences examined when checking the spacing


class ScheduleError(ValueError):
    """A schedule that cannot be used."""


@dataclass(frozen=True)
class Schedule:
    kind: str  # "cron" or "once"
    cron: str = ""
    tz: str = "UTC"
    run_at: str = ""  # UTC ISO instant, for "once"

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ScheduleError(f"unknown time zone {self.tz!r}") from err

    @classmethod
    def preset(cls, every: str, time: str, weekday: str | None, tz: str) -> Schedule:
        """``every`` day, weekdays or week (on ``weekday``) at ``time`` ("HH:MM", 24 hours)."""
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
            raise ScheduleError(f"not a time of day (use HH:MM): {time!r}")
        hour, minute = int(time[:2]), int(time[3:])
        if every == "day":
            days = "*"
        elif every == "weekdays":
            days = "1-5"
        elif every == "week" and weekday in WEEKDAYS:
            days = str((WEEKDAYS.index(weekday) + 1) % 7)  # cron: 0 is Sunday
        else:
            raise ScheduleError(f"every must be one of {', '.join(EVERY)} (a weekday for week)")
        return cls("cron", cron=f"{minute} {hour} * * {days}", tz=tz)

    @classmethod
    def once(cls, at: str, tz: str) -> Schedule:
        """One time. ``at`` is an ISO instant with a UTC offset, or a local "YYYY-MM-DD HH:MM"
        read in ``tz``; the run time is kept in UTC."""
        if not _LOCAL_AT.fullmatch(at):
            return cls("once", tz=tz, run_at=at)
        zone = cls("once", tz=tz).zone()
        try:
            local = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=zone)
        except ValueError as err:
            raise ScheduleError(f"not a date and time: {at!r}") from err
        return cls("once", tz=tz, run_at=local.astimezone(UTC).isoformat())

    def after(self, instant: datetime) -> Iterator[datetime]:
        """The fire times after ``instant`` (UTC), in order."""
        if self.kind == "once":
            at = datetime.fromisoformat(self.run_at)
            if at > instant:
                yield at
            return
        zone = self.zone()
        for local in CronSim(self.cron, instant.astimezone(zone)):
            fire = local.astimezone(UTC)
            if fire > instant:  # inside a repeated hour the iterator can step back
                yield fire

    def validate(self, now: datetime) -> None:
        """Raise ScheduleError unless this can be scheduled: a valid expression and zone, fires
        at least fifteen minutes apart, a one-time run in the future."""
        if self.kind == "once":
            try:
                at = datetime.fromisoformat(self.run_at)
            except ValueError as err:
                raise ScheduleError(f"not a time: {self.run_at!r}") from err
            if at.tzinfo is None:
                raise ScheduleError(
                    "a one-time run needs a UTC offset, or a date and time as YYYY-MM-DD HH:MM"
                )
            if at <= now:
                raise ScheduleError("that time has already passed; pick a time in the future")
            self.zone()
            return
        self.zone()
        try:
            fires = []
            for fire in self.after(now):
                fires.append(fire)
                if len(fires) == _PROBE:
                    break
        except CronSimError as err:
            raise ScheduleError(f"not a cron expression: {self.cron!r} ({err})") from err
        if not fires:
            raise ScheduleError("this expression never fires")
        gaps = [b - a for a, b in zip(fires, fires[1:], strict=False)]
        closest = runtime_config().jobs.min_interval_s
        if gaps and min(gaps) < timedelta(seconds=closest):
            raise ScheduleError(f"fires closer than {closest / 60:g} minutes apart")

    def describe(self) -> str:
        """Plain words for what a preset makes; any other expression is shown as written."""
        if self.kind == "once":
            local = datetime.fromisoformat(self.run_at).astimezone(self.zone())
            return f"once at {local:%Y-%m-%d %H:%M} ({self.tz})"
        fields = self.cron.split()
        if (
            len(fields) == 5
            and fields[0].isdigit()
            and fields[1].isdigit()
            and fields[2:4] == ["*", "*"]
        ):
            at = f"{int(fields[1]):02d}:{int(fields[0]):02d}"
            days = fields[4]
            if days == "*":
                return f"every day at {at} ({self.tz})"
            if days == "1-5":
                return f"weekdays at {at} ({self.tz})"
            if days.isdigit() and int(days) < 7:
                return f"every {WEEKDAYS[(int(days) - 1) % 7]} at {at} ({self.tz})"
        return f"{self.cron} ({self.tz})"
