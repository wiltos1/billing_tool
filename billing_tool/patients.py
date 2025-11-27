from datetime import datetime
import io

from flask import Blueprint, redirect, render_template, request, send_file, session, url_for

from .extensions import db
from .helpers import (
    _parse_datetime,
    get_active_shift_window,
    get_shift_doctor,
    optimize_billings,
    require_login,
)
from .models import Billing, Patient, ShiftSlot, Doctor
from .rules import build_optimized_billings

patients_bp = Blueprint("patients", __name__)


def _get_selected_patient(view: str):
    status_filter = "active" if view == "active" else "discharged"
    patient_id = request.args.get("selected_patient", type=int)
    session_key = f"last_patient_{status_filter}"
    if not patient_id:
        patient_id = session.get(session_key)
    if patient_id:
        patient = Patient.query.filter_by(id=patient_id, status=status_filter).first()
        if patient:
            session[session_key] = patient.id
            return patient

    patient = (
        Patient.query.filter_by(status=status_filter)
        .order_by(Patient.id)
        .first()
    )
    if patient:
        session[session_key] = patient.id
    return patient


@patients_bp.route("/", methods=["GET"])
def index():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    current_view = request.args.get("view", "active").lower()
    if current_view not in ("active", "archived"):
        current_view = "active"

    selected_patient = _get_selected_patient(current_view)
    now = datetime.now()
    current_shift_doctor = get_shift_doctor()

    active_patients = Patient.query.filter_by(status="active").order_by(Patient.id).all()
    archived_patients = (
        Patient.query.filter_by(status="discharged").order_by(Patient.id).all()
    )

    patient_billings = []
    optimized_billings = []
    patient_doctors = []
    shift_entries = []
    timeline_entries = []
    status_dt_default = now
    optimization_error = request.args.get("opt_error")
    if selected_patient:
        patient_billings = sorted(selected_patient.billings, key=lambda b: b.timestamp)
        optimized_billings = [b for b in patient_billings if b.optimized_included]
        doc_ids: set[int] = set()
        for billing in patient_billings:
            if billing.doctor_id not in doc_ids and billing.doctor:
                doc_ids.add(billing.doctor_id)
                patient_doctors.append(billing.doctor)
        status_dt_default = (
            selected_patient.care_admitted_at
            or selected_patient.care_delivered_at
            or now
        )
        shift_entries = (
            ShiftSlot.query.filter_by(patient_id=selected_patient.id)
            .order_by(ShiftSlot.start_time)
            .all()
        )

        def _delivery_label(raw_delivery: str | None) -> str:
            """Normalize delivery_by for display."""
            if not raw_delivery:
                return ""
            return "OB" if raw_delivery.lower() == "ob" else "Doctor"

        # Build timeline entries (status changes + shift actions).
        if selected_patient.start_datetime:
            timeline_entries.append(
                {
                    "time": selected_patient.start_datetime,
                    "doctor": None,
                    "doctor_name": "",
                    "action": "Triage",
                    "delivery_by": "",
                    "status_row": True,
                }
            )
        if selected_patient.care_admitted_at:
            timeline_entries.append(
                {
                    "time": selected_patient.care_admitted_at,
                    "doctor": None,
                    "doctor_name": "",
                    "action": "Admitted",
                    "delivery_by": "",
                    "status_row": True,
                }
            )
        if selected_patient.care_delivered_at:
            timeline_entries.append(
                {
                    "time": selected_patient.care_delivered_at,
                    "doctor": None,
                    "doctor_name": "",
                    "action": "Delivered",
                    "delivery_by": selected_patient.care_status,
                    "status_row": True,
                }
            )
        for slot in shift_entries:
            doctor_obj = slot.doctor or Doctor.query.get(slot.doctor_id)
            delivery_label = _delivery_label(slot.delivery_by)
            timeline_entries.append(
                {
                    "time": slot.start_time,
                    "doctor": doctor_obj,
                    "doctor_name": doctor_obj.name if doctor_obj else "",
                    "action": slot.action.title() if slot.action else "",
                    "delivery_by": delivery_label,
                    "status_row": False,
                }
            )
        timeline_entries.sort(key=lambda e: e["time"])

    return render_template(
        "main.html",
        session=session,
        current_view=current_view,
        current_shift_doctor=current_shift_doctor,
        selected_patient=selected_patient,
        active_patients=active_patients,
        archived_patients=archived_patients,
        patient_billings=patient_billings,
        optimized_billings=optimized_billings,
        patient_doctors=patient_doctors,
        shift_entries=shift_entries,
        timeline_entries=timeline_entries,
        optimization_error=optimization_error,
        care_status_options=["Triage", "Admitted", "Delivered"],
        status_default_date=status_dt_default.strftime("%Y-%m-%d"),
        status_default_time=status_dt_default.strftime("%H:%M"),
        default_start_date=now.strftime("%Y-%m-%d"),
        default_start_time=now.strftime("%H:%M"),
        default_billing_date=now.strftime("%Y-%m-%d"),
        default_billing_time=now.strftime("%H:%M"),
        default_discharge_date=now.strftime("%Y-%m-%d"),
        default_discharge_time=now.strftime("%H:%M"),
    )


@patients_bp.route("/patients", methods=["POST"])
def create_patient():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    initials = request.form.get("patient_initials", "").strip()
    identifier = request.form.get("patient_identifier", "").strip()
    start_date = request.form.get("start_date", "").strip()
    start_time = request.form.get("start_time", "").strip()

    if not initials or not identifier:
        return redirect(url_for("patients.index", view="active"))

    start_dt = _parse_datetime(start_date, start_time)

    patient = Patient(
        initials=initials,
        identifier=identifier,
        start_datetime=start_dt,
        status="active",
        care_status="Triage",
    )
    db.session.add(patient)
    db.session.commit()

    return redirect(url_for("patients.index", view="active", selected_patient=patient.id))


@patients_bp.route("/patients/<int:pid>/billings", methods=["POST"])
def add_billing(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    patient = Patient.query.get(pid)
    if not patient or patient.status != "active":
        return redirect(url_for("patients.index", view="active"))

    shift_doctor = get_shift_doctor()
    if not shift_doctor:
        return redirect(url_for("doctors.manage_doctors"))

    billing_date = request.form.get("billing_date", "").strip()
    billing_time = request.form.get("billing_time", "").strip()

    billing_dt = _parse_datetime(billing_date, billing_time)

    billing = Billing(
        patient_id=patient.id,
        doctor_id=shift_doctor.id,
        code=request.form.get("code", "").strip(),
        description=request.form.get("description", "").strip(),
        amount=float(request.form.get("amount", 0)),
        timestamp=billing_dt,
    )
    db.session.add(billing)
    db.session.commit()
    return redirect(url_for("patients.index", view="active", selected_patient=pid))


@patients_bp.route("/patients/<int:pid>/discharge", methods=["POST"])
def discharge_patient(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    discharge_date = request.form.get("discharge_date", "").strip()
    discharge_time = request.form.get("discharge_time", "").strip()

    patient = Patient.query.get(pid)
    if patient and patient.status == "active":
        discharge_dt = _parse_datetime(discharge_date, discharge_time)
        patient.discharge_datetime = discharge_dt
        patient.care_status = "Delivered"
        if not patient.care_delivered_at:
            patient.care_delivered_at = discharge_dt
        optimize_billings(patient)
        db.session.commit()
    return redirect(url_for("patients.index", view="active"))


@patients_bp.route("/patients/<int:pid>/restore", methods=["POST"])
def restore_patient(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    patient = Patient.query.get(pid)
    if patient and patient.status == "discharged":
        patient.status = "active"
        patient.discharge_datetime = None
        patient.care_status = "Triage"
        patient.care_admitted_at = None
        patient.care_delivered_at = None
        for billing in patient.billings:
            billing.optimized_included = False
        patient.optimized_total = 0
        db.session.commit()
    return redirect(url_for("patients.index", view="active", selected_patient=pid))


@patients_bp.route("/patients/<int:pid>/delete", methods=["POST"])
def delete_patient(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    view = request.form.get("view", "active")
    patient = Patient.query.get(pid)
    if patient:
        db.session.delete(patient)
        db.session.commit()
    return redirect(url_for("patients.index", view=view))


@patients_bp.route("/patients/<int:pid>/optimized_pdf", methods=["GET"])
def download_optimized_pdf(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    patient = Patient.query.get(pid)
    if not patient:
        return redirect(url_for("patients.index", view="active"))

    admitted = patient.care_admitted_at
    delivered = patient.care_delivered_at
    if not admitted or not delivered:
        return redirect(
            url_for(
                "patients.index",
                view="active",
                selected_patient=pid,
                opt_error="Set both Admitted and Delivered times to generate billing.",
            )
        )
    if admitted >= delivered:
        return redirect(
            url_for(
                "patients.index",
                view="active",
                selected_patient=pid,
                opt_error="Delivered time must be after Admitted time.",
            )
        )

    patient_slots = (
        ShiftSlot.query.filter_by(patient_id=patient.id)
        .filter(ShiftSlot.start_time >= admitted)
        .filter(ShiftSlot.start_time <= delivered)
        .order_by(ShiftSlot.start_time)
        .all()
    )
    recommendations = build_optimized_billings(
        patient, patient_slots, get_active_shift_window()
    )
    if not recommendations:
        return redirect(
            url_for(
                "patients.index",
                view="active",
                selected_patient=pid,
                opt_error="No eligible billing slots found within the Admitted-to-Delivered window.",
            )
        )

    # Build PDF
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        return redirect(
            url_for(
                "patients.index",
                view="active",
                selected_patient=pid,
                opt_error="PDF generation library is missing on the server.",
            )
        )

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 40

    def line(text, dy=16, bold=False):
        nonlocal y
        if y < 60:
            c.showPage()
            y = height - 40
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11)
        c.drawString(40, y, text)
        y -= dy

    line("Patient Billing Summary", bold=True)
    line(f"Patient ID: {patient.id}")
    line(f"Initials / Identifier: {patient.initials} / {patient.identifier}")
    line(f"Care Status: {patient.care_status}")
    if patient.start_datetime:
        line(f"Activated: {patient.start_datetime.strftime('%b %d, %Y %I:%M %p')}")
    line(f"Admitted: {admitted.strftime('%b %d, %Y %I:%M %p')}")
    line(f"Delivered: {delivered.strftime('%b %d, %Y %I:%M %p')}")
    if patient.discharge_datetime:
        line(f"Discharged: {patient.discharge_datetime.strftime('%b %d, %Y %I:%M %p')}")

    y -= 8
    line("Optimized Billing", bold=True)
    # Table header
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Time")
    c.drawString(180, y, "Code")
    c.drawString(240, y, "Modifier")
    c.drawString(320, y, "Doctor")
    y -= 14
    c.setFont("Helvetica", 10)
    for rec in recommendations:
        if y < 50:
            c.showPage()
            y = height - 40
            c.setFont("Helvetica-Bold", 11)
            c.drawString(40, y, "Time")
            c.drawString(180, y, "Code")
            c.drawString(240, y, "Modifier")
            c.drawString(320, y, "Doctor")
            y -= 14
            c.setFont("Helvetica", 10)
        c.drawString(40, y, rec["time"].strftime("%b %d, %Y %I:%M %p"))
        c.drawString(180, y, rec["code"])
        c.drawString(240, y, rec["modifier"] or "-")
        doctor_name = "-"
        doc = rec.get("doctor")
        if doc:
            doctor_name = f"Dr. {doc.name}"
        c.drawString(320, y, doctor_name)
        y -= 14

    c.save()
    buffer.seek(0)
    filename = f"patient_{patient.id}_billing.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")
@patients_bp.route("/patients/<int:pid>/status", methods=["POST"])
def update_status(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    patient = Patient.query.get(pid)
    if not patient or patient.status != "active":
        return redirect(url_for("patients.index", view="active"))

    new_status = request.form.get("care_status", "").title()
    if new_status not in {"Triage", "Admitted", "Delivered"}:
        return redirect(url_for("patients.index", view="active", selected_patient=pid))

    status_dt = _parse_datetime(
        request.form.get("care_date", "").strip(),
        request.form.get("care_time", "").strip(),
    )

    patient.care_status = new_status
    if new_status == "Triage":
        patient.care_admitted_at = None
        patient.care_delivered_at = None
    elif new_status == "Admitted":
        patient.care_admitted_at = status_dt
        patient.care_delivered_at = None
    elif new_status == "Delivered":
        if not patient.care_admitted_at:
            patient.care_admitted_at = status_dt
        patient.care_delivered_at = status_dt

    db.session.commit()
    return redirect(url_for("patients.index", view="active", selected_patient=pid))
