from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from .extensions import db
from .helpers import _parse_datetime, get_active_shift_window, get_shift_doctor, require_login
from .models import Doctor, ShiftWindow

doctors_bp = Blueprint("doctors", __name__)


@doctors_bp.route("/doctors", methods=["POST"])
def create_doctor():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    name = request.form.get("doctor_name", "").strip()
    if not name:
        return redirect(url_for("patients.index", view="active"))

    doctor = Doctor(name=name)
    db.session.add(doctor)
    db.session.commit()

    return redirect(url_for("doctors.manage_doctors"))


@doctors_bp.route("/doctors/manage", methods=["GET"])
def manage_doctors():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    shift_doctor = get_shift_doctor()
    shift_window = get_active_shift_window()

    if shift_window:
        default_start_date = shift_window.start_datetime.strftime("%Y-%m-%d")
        default_start_time = shift_window.start_datetime.strftime("%H:%M")
        default_end_date = shift_window.end_datetime.strftime("%Y-%m-%d")
        default_end_time = shift_window.end_datetime.strftime("%H:%M")
    else:
        default_start_date = datetime.now().strftime("%Y-%m-%d")
        default_start_time = "08:00"
        default_end_date = datetime.now().strftime("%Y-%m-%d")
        default_end_time = "20:00"

    return render_template(
        "doctors.html",
        session=session,
        doctors=Doctor.query.order_by(Doctor.name).all(),
        shift_doctor=shift_doctor,
        shift_window=shift_window,
        default_start_date=default_start_date,
        default_start_time=default_start_time,
        default_end_date=default_end_date,
        default_end_time=default_end_time,
    )


@doctors_bp.route("/doctors/on_shift", methods=["POST"])
def set_on_shift():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    doctor_id = request.form.get("doctor_id")
    start_date = request.form.get("shift_start_date", "").strip()
    start_time = request.form.get("shift_start_time", "").strip()
    end_date = request.form.get("shift_end_date", "").strip()
    end_time = request.form.get("shift_end_time", "").strip()

    try:
        doctor_id = int(doctor_id)
    except (TypeError, ValueError):
        return redirect(url_for("doctors.manage_doctors"))

    doctor = Doctor.query.get(doctor_id)
    if doctor:
        start_dt = _parse_datetime(start_date, start_time)
        end_dt = _parse_datetime(
            end_date, end_time, fallback_date=start_date or start_dt.strftime("%Y-%m-%d")
        )
        if not end_date and end_dt <= start_dt:
            end_dt += timedelta(days=1)

        Doctor.query.update({Doctor.is_on_shift: False}, synchronize_session=False)
        ShiftWindow.query.update({ShiftWindow.is_active: False}, synchronize_session=False)

        doctor.is_on_shift = True
        db.session.add(
            ShiftWindow(
                doctor_id=doctor.id,
                start_datetime=start_dt,
                end_datetime=end_dt,
                is_active=True,
            )
        )
        db.session.commit()

    return redirect(url_for("doctors.manage_doctors"))
