from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for

from .extensions import db
from .helpers import _split_window_into_days, get_active_shift_window, get_shift_doctor, require_login
from .models import Patient, ShiftSlot

shift_bp = Blueprint("shift", __name__)


@shift_bp.route("/shift_grid", methods=["GET", "POST"])
def shift_grid():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    shift_doctor = get_shift_doctor()
    shift_window = get_active_shift_window()
    if not shift_doctor or not shift_window:
        return redirect(url_for("doctors.manage_doctors"))

    if request.method == "POST":
        existing_slots = list(
            ShiftSlot.query.filter(
                ShiftSlot.doctor_id == shift_doctor.id,
                ShiftSlot.start_time >= shift_window.start_datetime,
                ShiftSlot.start_time < shift_window.end_datetime,
            ).all()
        )
        preserved_slots = {
            slot.start_time.replace(second=0, microsecond=0): slot
            for slot in existing_slots
            if slot.patient and slot.patient.care_status != "Admitted"
        }
        to_keep_ids = {
            slot.id
            for slot in existing_slots
            if slot.patient and slot.patient.care_status != "Admitted"
        }
        if to_keep_ids:
            ShiftSlot.query.filter(
                ShiftSlot.doctor_id == shift_doctor.id,
                ShiftSlot.start_time >= shift_window.start_datetime,
                ShiftSlot.start_time < shift_window.end_datetime,
                ShiftSlot.id.notin_(list(to_keep_ids)),
            ).delete(synchronize_session=False)
        else:
            ShiftSlot.query.filter(
                ShiftSlot.doctor_id == shift_doctor.id,
                ShiftSlot.start_time >= shift_window.start_datetime,
                ShiftSlot.start_time < shift_window.end_datetime,
            ).delete(synchronize_session=False)

        for key, val in request.form.items():
            if not key.startswith("slot_patient_"):
                continue
            if not val:
                continue
            try:
                ts = int(key.split("slot_patient_", 1)[1])
                slot_time = datetime.fromtimestamp(ts)
                patient_id = int(val)
            except (ValueError, IndexError):
                continue
            if not (shift_window.start_datetime <= slot_time < shift_window.end_datetime):
                continue
            patient = Patient.query.get(patient_id)
            if not patient:
                continue
            action_val = (
                request.form.get(f"slot_action_{ts}", "").strip().lower()
                or ""
            )
            delivery_by = None
            initial_status = patient.care_status
            preserved_slot = preserved_slots.get(slot_time.replace(second=0, microsecond=0))
            if action_val == "delivery":
                delivery_by = (
                    request.form.get(f"slot_delivery_{ts}", "").strip().lower() or None
                )
                # Update patient status/timestamp on delivery
                delivery_exact = request.form.get(f"slot_delivery_time_{ts}", "").strip()
                exact_dt = slot_time
                if delivery_exact:
                    try:
                        exact_dt = datetime.fromisoformat(delivery_exact)
                    except ValueError:
                        exact_dt = slot_time
                patient.care_status = "Delivered"
                patient.care_delivered_at = exact_dt
            if action_val:
                if preserved_slot:
                    preserved_slot.patient_id = patient.id
                    preserved_slot.action = action_val
                    preserved_slot.delivery_by = delivery_by
                elif initial_status == "Admitted":
                    db.session.add(
                        ShiftSlot(
                            doctor_id=shift_doctor.id,
                            patient_id=patient.id,
                            start_time=slot_time,
                            action=action_val,
                            delivery_by=delivery_by,
                        )
                    )
        db.session.commit()
        if request.headers.get("X-Requested-With") == "fetch":
            return ("", 204)
        return redirect(url_for("shift.shift_grid"))

    segments = _split_window_into_days(
        shift_window.start_datetime, shift_window.end_datetime
    )

    existing = {
        slot.start_time.replace(second=0, microsecond=0): slot
        for slot in ShiftSlot.query.filter(
            ShiftSlot.doctor_id == shift_doctor.id,
            ShiftSlot.start_time >= shift_window.start_datetime,
            ShiftSlot.start_time < shift_window.end_datetime,
        ).all()
    }

    admitted_patients = list(
        Patient.query.filter_by(status="active", care_status="Admitted")
        .order_by(Patient.id)
        .all()
    )
    # Include any patients already in this grid window so their rows don't disappear after discharge/delivery.
    existing_patient_ids = {slot.patient_id for slot in existing.values() if slot.patient_id}
    preserved_patients = []
    if existing_patient_ids:
        preserved_patients = (
            Patient.query.filter(Patient.id.in_(existing_patient_ids))
            .order_by(Patient.id)
            .all()
        )
    display_patients = []
    seen_ids = set()
    for p in admitted_patients + preserved_patients:
        if p.id in seen_ids:
            continue
        display_patients.append(p)
        seen_ids.add(p.id)

    return render_template(
        "shift.html",
        session=session,
        shift_doctor=shift_doctor,
        shift_window=shift_window,
        segments=segments,
        existing=existing,
        active_patients=display_patients,
    )
