"""Cron-Ausdrücke mit fünf Feldern – ohne zusätzliche Abhängigkeit.

Unterstützt werden ``*``, ``*/n``, ``a``, ``a-b``, ``a-b/n`` und Listen
(``a,b,c``) in den Feldern Minute, Stunde, Tag im Monat, Monat und Wochentag.
Der Wochentag zählt wie in Cron üblich ab Sonntag (0 = Sonntag, 7 ebenfalls).

Alle Rechnungen laufen in lokaler Zeit (naive ``datetime``-Werte); der
Container setzt ``TZ``, damit stimmen die Uhrzeiten mit der Oberfläche überein.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import i18n

FIELD_NAMES = (
    "label.cron_minute",
    "label.cron_hour",
    "label.cron_day",
    "label.cron_month",
    "label.cron_weekday",
)
FIELD_RANGES = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 6),
)
MAX_DAYS_AHEAD = 366 * 5


class CronError(ValueError):
    """Der Ausdruck ist kein gültiger Cron-Zeitplan."""


def _number(text: str, low: int, high: int, label: str) -> int:
    value = text.strip()
    if not value.isdigit():
        raise CronError(i18n.t("cron.invalid_value", field=label, value=text))
    number = int(value)
    if number < low or number > high:
        raise CronError(
            i18n.t("cron.out_of_range", field=label, low=low, high=high)
        )
    return number


def _parse_field(text: str, low: int, high: int, label: str) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            raise CronError(i18n.t("cron.invalid_value", field=label, value=text))

        step = 1
        if "/" in piece:
            base, _, raw_step = piece.partition("/")
            if not raw_step.strip().isdigit() or int(raw_step) < 1:
                raise CronError(
                    i18n.t("cron.invalid_step", field=label, value=raw_step)
                )
            step = int(raw_step)
            piece = base.strip() or "*"

        if piece == "*":
            start, end = low, high
        elif "-" in piece:
            first, _, last = piece.partition("-")
            start = _number(first, low, high, label)
            end = _number(last, low, high, label)
            if start > end:
                raise CronError(
                    i18n.t("cron.invalid_range", field=label, value=piece)
                )
        else:
            start = _number(piece, low, high, label)
            # "5/15" bedeutet wie in Cron: ab 5 bis zum Feldende im Schritt 15.
            end = high if step > 1 else start

        values.update(range(start, end + 1, step))

    if not values:
        raise CronError(i18n.t("cron.invalid_value", field=label, value=text))
    return frozenset(values)


@dataclass(frozen=True)
class Schedule:
    """Ein geprüfter Cron-Zeitplan."""

    expression: str
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool

    def matches_day(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        day_ok = day.day in self.days
        # Cron: Sonntag = 0; datetime.weekday(): Montag = 0.
        weekday_ok = (day.weekday() + 1) % 7 in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        if self.day_restricted:
            return day_ok
        if self.weekday_restricted:
            return weekday_ok
        return True

    def next_after(self, moment: datetime) -> datetime:
        """Nächster Zeitpunkt nach ``moment`` (eine Minute gilt als vergangen)."""
        start = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.date()
        for _ in range(MAX_DAYS_AHEAD):
            if self.matches_day(day):
                for hour in self.hours:
                    for minute in self.minutes:
                        candidate = datetime(
                            day.year, day.month, day.day, hour, minute
                        )
                        if candidate >= start:
                            return candidate
            day += timedelta(days=1)
        raise CronError(i18n.t("cron.no_match", expression=self.expression))


def parse(expression: str) -> Schedule:
    """Ausdruck prüfen und als :class:`Schedule` zurückgeben."""
    raw = (expression or "").strip()
    fields = raw.split()
    if len(fields) != 5:
        raise CronError(i18n.t("cron.field_count", expression=raw or "–"))

    parsed: list[frozenset[int]] = []
    for position, field in enumerate(fields):
        low, high = FIELD_RANGES[position]
        parsed.append(_parse_field(field, low, high, i18n.t(FIELD_NAMES[position])))

    schedule = Schedule(
        expression=" ".join(fields),
        minutes=tuple(sorted(parsed[0])),
        hours=tuple(sorted(parsed[1])),
        days=parsed[2],
        months=parsed[3],
        weekdays=parsed[4],
        day_restricted=fields[2].strip() != "*",
        weekday_restricted=fields[4].strip() != "*",
    )
    # Ausdrücke wie "0 0 30 2 *" sind formal gültig, treffen aber nie zu.
    if not _has_match(schedule):
        raise CronError(i18n.t("cron.no_match", expression=schedule.expression))
    return schedule


def _has_match(schedule: Schedule) -> bool:
    day = date(2000, 1, 1)
    for _ in range(MAX_DAYS_AHEAD):
        if schedule.matches_day(day):
            return True
        day += timedelta(days=1)
    return False


def validate(expression: str) -> str:
    """Ausdruck prüfen und normalisiert zurückgeben (wirft ``CronError``)."""
    return parse(expression).expression


def next_run(expression: str, after: datetime | None = None) -> datetime:
    """Nächster Laufzeitpunkt für einen Ausdruck."""
    return parse(expression).next_after(after or datetime.now())
