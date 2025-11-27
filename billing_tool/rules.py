from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, List, Optional

from .models import Doctor, Patient, ShiftSlot, ShiftWindow


def _floor_to_quarter(dt: datetime) -> datetime:
    """Floor datetime to the nearest 15-minute boundary."""
    minute_block = (dt.minute // 15) * 15
    return dt.replace(minute=minute_block, second=0, microsecond=0)


def _time_modifier(dt: datetime) -> tuple[str | None, int]:
    """Return modifier and a weight for priority ordering."""
    weekday = dt.weekday()
    hour = dt.hour
    minute = dt.minute
    hm = hour + minute / 60

    if hm < 7:
        return "NTAM", 3
    if hm >= 22:
        return "NTPM", 3
    if weekday >= 5:
        if 7 <= hm < 22:
            return "WK", 2
    else:
        if 17 <= hm < 22:
            return "EV", 2
    return None, 1


def _generate_slots(start: datetime, end: datetime) -> Iterable[datetime]:
    """Yield 15-minute aligned slots from start (inclusive) to end (exclusive)."""
    cursor = start.replace(second=0, microsecond=0)
    while cursor < end:
        yield cursor
        cursor += timedelta(minutes=15)


def build_optimized_billings(
    patient: Patient,
    patient_slots: List[ShiftSlot],
    active_shift_window: Optional[ShiftWindow],
) -> list[dict]:
    """
    Build an optimized billing plan for the patient based on shift activity and rules.

    Returns a list of dictionaries with keys: time, code, modifier.
    """
    admitted = patient.care_admitted_at
    delivered = patient.care_delivered_at
    if not admitted or not delivered or admitted >= delivered:
        return []

    # Only consider patient slots within the admitted-to-delivered window.
    scoped_slots = [
        s
        for s in patient_slots
        if admitted <= s.start_time <= delivered
    ]

    delivery_slot = next(
        (
            s
            for s in sorted(scoped_slots, key=lambda s: s.start_time)
            if (s.action or "").lower() == "delivery"
        ),
        None,
    )
    delivered_time = delivery_slot.start_time if delivery_slot else None
    delivered_by_ob = bool(
        delivery_slot
        and (delivery_slot.delivery_by or "").lower() == "ob"
    )

    if delivered_by_ob:
        # 03.03AR for each attended 15-minute block (excluding delivery slot).
        attended = sorted(
            [
                s
                for s in scoped_slots
                if s.doctor_id and (not delivered_time or s.start_time != delivered_time)
            ],
            key=lambda s: s.start_time,
        )

        # Find first pair of consecutive attended slots (15-minute apart) to mark COINPT.
        coinpt_times: set[datetime] = set()
        for first, second in zip(attended, attended[1:]):
            gap = second.start_time - first.start_time
            if timedelta(minutes=14) <= gap <= timedelta(minutes=16):
                if active_shift_window:
                    in_shift = (
                        active_shift_window.start_datetime
                        <= first.start_time
                        < active_shift_window.end_datetime
                    ) and (
                        active_shift_window.start_datetime
                        <= second.start_time
                        < active_shift_window.end_datetime
                    )
                    if not in_shift:
                        continue
                coinpt_times = {first.start_time, second.start_time}
                break

        shift_doctor = (
            Doctor.query.get(active_shift_window.doctor_id)
            if active_shift_window
            else None
        )
        billings = [
            {
                "time": slot.start_time,
                "code": "03.03AR",
                "modifier": "COINPT" if slot.start_time in coinpt_times else "",
                "doctor": slot.doctor or Doctor.query.get(slot.doctor_id) or shift_doctor,
            }
            for slot in attended
        ]
        if delivered_time:
            billings.append(
                {
                    "time": delivered_time,
                    "code": "87.98B",
                    "modifier": "",
                    "doctor": delivery_slot.doctor
                    or Doctor.query.get(delivery_slot.doctor_id)
                    or shift_doctor,
                }
            )
        return sorted(billings, key=lambda b: b["time"])

    # Delivered by attending doctor: select top 12 paying slots where an on-call doctor was available.
    if not active_shift_window:
        return []

    shift_doctor = Doctor.query.get(active_shift_window.doctor_id)
    window_start = max(admitted, active_shift_window.start_datetime)
    window_end = min(delivered, active_shift_window.end_datetime)
    if window_start >= window_end:
        return []

    # Align start to 15-minute boundary but do not go before the shift window start.
    aligned_start = _floor_to_quarter(window_start)
    if aligned_start < active_shift_window.start_datetime:
        aligned_start = active_shift_window.start_datetime.replace(second=0, microsecond=0)

    slots = []
    for slot_time in _generate_slots(aligned_start, window_end):
        if delivered_time and slot_time == delivered_time:
            continue
        modifier, weight = _time_modifier(slot_time)
        slots.append((slot_time, modifier or "", weight))

    if not slots:
        return []

    # Pick up to 12 highest-paying slots; if ties, keep earliest first.
    top_slots = sorted(slots, key=lambda s: (-s[2], s[0]))[:12]
    top_slots.sort(key=lambda s: s[0])

    billings = [
        {
            "time": slot_time,
            "code": "13.99JA",
            "modifier": modifier,
            "doctor": shift_doctor,
        }
        for slot_time, modifier, _ in top_slots
    ]
    if delivered_time and window_start <= delivered_time <= window_end:
        billings.append(
            {
                "time": delivered_time,
                "code": "87.98A",
                "modifier": "",
                "doctor": shift_doctor,
            }
        )

    return sorted(billings, key=lambda b: b["time"])
