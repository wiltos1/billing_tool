from datetime import datetime

from .extensions import db


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    is_on_shift = db.Column(db.Boolean, default=False)
    billings = db.relationship("Billing", backref="doctor", lazy=True)


class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    initials = db.Column(db.String(4), nullable=False)
    identifier = db.Column(db.String(120), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    discharge_datetime = db.Column(db.DateTime)
    care_status = db.Column(db.String(20), default="Triage")  # Triage, Admitted, Delivered
    care_admitted_at = db.Column(db.DateTime)
    care_delivered_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default="active")
    optimized_total = db.Column(db.Float, default=0.0)
    billings = db.relationship(
        "Billing", backref="patient", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def start_display(self):
        return (
            self.start_datetime.strftime("%b %d, %Y %H:%M")
            if self.start_datetime
            else None
        )

    @property
    def discharge_display(self):
        return (
            self.discharge_datetime.strftime("%b %d, %Y %H:%M")
            if self.discharge_datetime
            else None
        )


class Billing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    code = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(255))
    amount = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    optimized_included = db.Column(db.Boolean, default=False)


class ShiftSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"))
    start_time = db.Column(db.DateTime, nullable=False, index=True)
    action = db.Column(db.String(20), default="attended")
    delivery_by = db.Column(db.String(20))
    doctor = db.relationship("Doctor")
    patient = db.relationship("Patient")


class ShiftWindow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
