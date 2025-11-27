from datetime import date, datetime, timedelta

from flask import redirect, session, url_for
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import Billing, Doctor, Patient, ShiftWindow, User


def ensure_default_data():
    """Create tables and seed a default user and doctor list."""
    db.create_all()

    if not User.query.filter_by(username="doctor").first():
        db.session.add(
            User(username="doctor", password_hash=generate_password_hash("test123"))
        )

    if Doctor.query.count() == 0:
        for name in ["A. Smith", "B. Johnson", "C. Patel"]:
            db.session.add(Doctor(name=name))

    db.session.commit()


def get_shift_doctor():
    return Doctor.query.filter_by(is_on_shift=True).first()


def _split_window_into_days(start_dt: datetime, end_dt: datetime):
    segments = []
    current = start_dt
    while current < end_dt:
        next_boundary = datetime.combine(
            current.date() + timedelta(days=1), datetime.min.time()
        )
        segment_end = min(end_dt, next_boundary)
        slots = []
        cursor = current
        while cursor < segment_end:
            slots.append(cursor)
            cursor += timedelta(minutes=15)
        segments.append({"date": current.date(), "slots": slots})
        current = segment_end
    return segments


def get_active_shift_window():
    return (
        ShiftWindow.query.filter_by(is_active=True)
        .order_by(ShiftWindow.start_datetime.desc())
        .first()
    )


def _parse_datetime(date_str: str, time_str: str, fallback_date: str | None = None) -> datetime:
    """Parse user-provided date/time or fall back to now."""
    now = datetime.now()
    date_part = date_str or fallback_date
    if date_part:
        if not time_str:
            time_str = now.strftime("%H:%M")
        try:
            return datetime.strptime(f"{date_part} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return now
    return now


def require_login():
    """Redirect to login if not logged in."""
    if "user" not in session:
        return redirect(url_for("auth.login"))
    return None


def optimize_billings(patient: Patient):
    """Simple MVP optimization: keep highest-dollar entry for each code."""
    grouped: dict[str, Billing] = {}
    for billing in patient.billings:
        if billing.code not in grouped or billing.amount > grouped[billing.code].amount:
            grouped[billing.code] = billing

    for billing in patient.billings:
        billing.optimized_included = grouped.get(billing.code) is billing

    patient.optimized_total = sum(b.amount for b in grouped.values())
    patient.status = "discharged"
