from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "S0meV3ryL0ngRandomString_!@#$%^&*()_+2025"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///billing.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


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
    status = db.Column(db.String(20), default="active")
    optimized_total = db.Column(db.Float, default=0.0)
    billings = db.relationship(
        "Billing", backref="patient", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def start_display(self):
        return (
            self.start_datetime.strftime("%b %d, %Y %I:%M %p")
            if self.start_datetime
            else None
        )

    @property
    def discharge_display(self):
        return (
            self.discharge_datetime.strftime("%b %d, %Y %I:%M %p")
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


def ensure_default_data():
    db.create_all()

    if not User.query.filter_by(username="doctor").first():
        db.session.add(User(username="doctor", password_hash=generate_password_hash("test123")))

    if Doctor.query.count() == 0:
        defaults = ["A. Smith", "B. Johnson", "C. Patel"]
        for name in defaults:
            db.session.add(Doctor(name=name))

    db.session.commit()


def get_shift_doctor():
    return Doctor.query.filter_by(is_on_shift=True).first()


def _parse_datetime(date_str: str, time_str: str) -> datetime:
    """Parse user-provided date/time or fall back to now."""
    now = datetime.now()
    if date_str:
        if not time_str:
            time_str = now.strftime("%H:%M")
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return now
    return now


def require_login():
    """Redirect if not logged in."""
    if "user" not in session:
        return redirect(url_for("login"))
    return None


def optimize_billings(patient):
    """Simple MVP optimization: keep highest-dollar entry for each code"""
    grouped = {}
    for billing in patient.billings:
        if (
            billing.code not in grouped
            or billing.amount > grouped[billing.code].amount
        ):
            grouped[billing.code] = billing

    for billing in patient.billings:
        billing.optimized_included = grouped.get(billing.code) is billing

    patient.optimized_total = sum(b.amount for b in grouped.values())
    patient.status = "discharged"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pw = request.form.get("password", "").strip()

        user_record = User.query.filter_by(username=user).first()
        if user_record and check_password_hash(user_record.password_hash, pw):
            session["user"] = user_record.username
            return redirect(url_for("index", view="active"))

        return render_template_string(LOGIN_HTML, error="Invalid username or password")

    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
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

    active_patients = (
        Patient.query.filter_by(status="active").order_by(Patient.id).all()
    )
    archived_patients = (
        Patient.query.filter_by(status="discharged").order_by(Patient.id).all()
    )

    patient_billings = []
    optimized_billings = []
    patient_doctors = []
    if selected_patient:
        patient_billings = sorted(
            selected_patient.billings, key=lambda b: b.timestamp
        )
        optimized_billings = [
            b for b in patient_billings if b.optimized_included
        ]
        doc_ids = set()
        for billing in patient_billings:
            if billing.doctor_id not in doc_ids and billing.doctor:
                doc_ids.add(billing.doctor_id)
                patient_doctors.append(billing.doctor)

    return render_template_string(
        MAIN_HTML,
        session=session,
        current_view=current_view,
        current_shift_doctor=current_shift_doctor,
        selected_patient=selected_patient,
        active_patients=active_patients,
        archived_patients=archived_patients,
        patient_billings=patient_billings,
        optimized_billings=optimized_billings,
        patient_doctors=patient_doctors,
        default_start_date=now.strftime("%Y-%m-%d"),
        default_start_time=now.strftime("%H:%M"),
        default_billing_date=now.strftime("%Y-%m-%d"),
        default_billing_time=now.strftime("%H:%M"),
        default_discharge_date=now.strftime("%Y-%m-%d"),
        default_discharge_time=now.strftime("%H:%M"),
    )


def _get_selected_patient(view):
    status_filter = "active" if view == "active" else "discharged"
    p_id = request.args.get("selected_patient", type=int)
    if p_id:
        patient = Patient.query.filter_by(id=p_id, status=status_filter).first()
        if patient:
            return patient

    return (
        Patient.query.filter_by(status=status_filter)
        .order_by(Patient.id)
        .first()
    )


@app.route("/patients", methods=["POST"])
def create_patient():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    initials = request.form.get("patient_initials", "").strip()
    identifier = request.form.get("patient_identifier", "").strip()
    start_date = request.form.get("start_date", "").strip()
    start_time = request.form.get("start_time", "").strip()

    if not initials or not identifier:
        return redirect(url_for("index", view="active"))

    start_dt = _parse_datetime(start_date, start_time)

    patient = Patient(
        initials=initials,
        identifier=identifier,
        start_datetime=start_dt,
        status="active",
    )
    db.session.add(patient)
    db.session.commit()

    return redirect(url_for("index", view="active", selected_patient=patient.id))


@app.route("/doctors", methods=["POST"])
def create_doctor():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    name = request.form.get("doctor_name", "").strip()
    if not name:
        return redirect(url_for("index", view="active"))

    doctor = Doctor(name=name)
    db.session.add(doctor)
    db.session.commit()

    return redirect(url_for("manage_doctors"))


@app.route("/doctors/manage", methods=["GET"])
def manage_doctors():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    shift_doctor = get_shift_doctor()
    return render_template_string(
        DOCTORS_HTML,
        session=session,
        doctors=Doctor.query.order_by(Doctor.name).all(),
        shift_doctor=shift_doctor,
    )


@app.route("/doctors/on_shift", methods=["POST"])
def set_on_shift():
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    doctor_id = request.form.get("doctor_id")
    try:
        doctor_id = int(doctor_id)
    except (TypeError, ValueError):
        return redirect(url_for("manage_doctors"))

    doctor = Doctor.query.get(doctor_id)
    if doctor:
        Doctor.query.update({Doctor.is_on_shift: False}, synchronize_session=False)
        doctor.is_on_shift = True
        db.session.commit()

    return redirect(url_for("manage_doctors"))


@app.route("/patients/<int:pid>/billings", methods=["POST"])
def add_billing(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    patient = Patient.query.get(pid)
    if not patient or patient.status != "active":
        return redirect(url_for("index", view="active"))

    shift_doctor = get_shift_doctor()
    if not shift_doctor:
        return redirect(url_for("manage_doctors"))

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
    return redirect(url_for("index", view="active", selected_patient=pid))


@app.route("/patients/<int:pid>/discharge", methods=["POST"])
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
        optimize_billings(patient)
        db.session.commit()
    return redirect(url_for("index", view="active"))


@app.route("/patients/<int:pid>/restore", methods=["POST"])
def restore_patient(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    patient = Patient.query.get(pid)
    if patient and patient.status == "discharged":
        patient.status = "active"
        patient.discharge_datetime = None
        for billing in patient.billings:
            billing.optimized_included = False
        patient.optimized_total = 0
        db.session.commit()
    return redirect(url_for("index", view="active", selected_patient=pid))


@app.route("/patients/<int:pid>/delete", methods=["POST"])
def delete_patient(pid):
    redirect_check = require_login()
    if redirect_check:
        return redirect_check

    view = request.form.get("view", "active")
    patient = Patient.query.get(pid)
    if patient:
        db.session.delete(patient)
        db.session.commit()
    return redirect(url_for("index", view=view))


# ===================
# HTML TEMPLATES BELOW
# ===================

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>AHS Billing Login</title>
    <style>
        body {
            background: #e1eff7;
            display:flex; justify-content:center; align-items:center;
            height:100vh; margin:0; font-family:Arial;
        }
        .box {
            background:white; padding:30px; border-radius:10px;
            width:300px; text-align:center;
            box-shadow:0 2px 10px rgba(0,0,0,0.15);
        }
        img { max-width:120px; margin-bottom: 12px; }
        input {
            width:100%; padding:8px; margin:4px 0; border-radius:4px;
            border:1px solid #ccc;
        }
        button {
            width:100%; padding:10px; background:#1976d2;
            color:white; border:none; border-radius:4px; margin-top:8px;
        }
        .error { background:#ffcdd2; padding:6px; border-radius:4px; }
    </style>
</head>
<body>
    <div class="box">
        <img src="/static/ahs_logo.png" alt="AHS Logo">
        <h3>AHS Billing Login</h3>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="post">
            <input name="username" placeholder="Username" required>
            <input name="password" placeholder="Password" type="password" required>
            <button>Login</button>
        </form>
    </div>
</body>
</html>
"""


MAIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Patient Billing Optimizer</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 1100px;
            margin: 0 auto;
            padding: 20px;
            background: #f3f7fb;
        }
        .topbar { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }
        .card {
            background:#fff; padding:15px 20px; margin:15px 0;
            border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.08);
        }
        .toggle-buttons { display:flex; gap:8px; align-items:center; }
        .toggle-buttons a {
            padding:6px 14px; border-radius:999px; border:1px solid #1976d2;
            color:#1976d2; text-decoration:none; font-size:0.9em;
        }
        .toggle-buttons a.active-view {
            background:#1976d2; color:#fff;
        }
        .topbar-actions { display:flex; align-items:center; gap:12px; }
        .topbar-actions a.action-link { color:#1976d2; text-decoration:none; font-weight:bold; }
        label { display:block; margin-top:8px; }
        input, select {
            width:100%; padding:6px; margin-top:2px; border:1px solid #ccc; border-radius:4px;
        }
        button {
            margin-top:10px; padding:6px 12px; border:none; border-radius:4px;
            background:#1976d2; color:#fff; cursor:pointer;
        }
        .delete-btn {
            background:#c62828; margin-left:8px;
        }
        .delete-btn:hover { opacity:0.9; }
        .card-actions { margin-top:8px; }
        table {
            width:100%; border-collapse:collapse; font-size:0.9em; margin-top:8px;
        }
        th, td {
            border:1px solid #ddd; padding:6px;
        }
        th { background:#e8eef4; }
        .badge {
            padding:2px 6px; border-radius:4px; font-size:0.75em; margin-left:6px;
        }
        .active { background:#c8e6c9; }
        .discharged { background:#ffcdd2; }
        .muted { opacity:0.6; font-style:italic; }
        img { max-height:60px; vertical-align:middle; margin-right:10px; }
    </style>
</head>
<body>

<div class="topbar">
    <div>
        <img src="/static/ahs_logo.png" alt="AHS logo">
        <strong>Patient Billing Optimizer</strong>
    </div>
    <div class="toggle-buttons">
        <a href="{{ url_for('index', view='active') }}"
           class="{% if current_view == 'active' %}active-view{% endif %}">Active Patients</a>
        <a href="{{ url_for('index', view='archived') }}"
           class="{% if current_view == 'archived' %}active-view{% endif %}">Archived Patients</a>
    </div>
    <div class="topbar-actions">
        <a class="action-link" href="{{ url_for('manage_doctors') }}">Manage Doctors</a>
        <span>Logged in as {{ session["user"] }} &mdash; <a href="{{ url_for('logout') }}">Logout</a></span>
    </div>
</div>

{% if current_view == "active" %}
<div class="card">
    <h2>Create New Patient</h2>
    <form action="{{ url_for('create_patient') }}" method="post">
        <label>Initials</label>
        <input type="text" name="patient_initials" maxlength="4" required>

        <label>Patient Identifier</label>
        <input type="text" name="patient_identifier" required>

        <label>Activation Date</label>
        <input type="date" name="start_date" value="{{ default_start_date }}" required>

        <label>Activation Time</label>
        <input type="time" name="start_time" value="{{ default_start_time }}" step="60">

        <button>Create Patient</button>
    </form>
</div>
{% endif %}

{% if current_view == "active" %}
<div class="card">
    <h3>Active Patients</h3>
{% if active_patients %}
    <form method="get">
        <input type="hidden" name="view" value="active">
        <select name="selected_patient" onchange="this.form.submit()">
            {% for p in active_patients %}
            <option value="{{ p.id }}"
                {% if selected_patient and selected_patient.id == p.id %}selected{% endif %}>
                #{{ p.id }} — {{ p.initials }} — {{ p.identifier }}
            </option>
            {% endfor %}
        </select>
    </form>
    {% else %}
    <p class="muted">None</p>
    {% endif %}
</div>
{% endif %}

{% if current_view == "archived" %}
<div class="card">
    <h3>Archived Patients</h3>
    {% if archived_patients %}
    <form method="get">
        <input type="hidden" name="view" value="archived">
        <select name="selected_patient" onchange="this.form.submit()">
            {% for p in archived_patients %}
            <option value="{{ p.id }}"
                {% if selected_patient and selected_patient.id == p.id %}selected{% endif %}>
                #{{ p.id }} — {{ p.initials }} — {{ p.identifier }}
            </option>
            {% endfor %}
        </select>
    </form>
    {% else %}
    <p class="muted">None</p>
    {% endif %}
</div>
{% endif %}

{% if selected_patient %}
<div class="card">
    <h2>
        Patient #{{ selected_patient.id }} — {{ selected_patient.initials }} — {{ selected_patient.identifier }}
        {% if selected_patient.status == "active" %}
            <span class="badge active">active</span>
        {% else %}
            <span class="badge discharged">archived</span>
        {% endif %}
    </h2>
    {% if selected_patient.start_display %}
    <p class="muted">Activated {{ selected_patient.start_display }}</p>
    {% endif %}
    {% if selected_patient.discharge_display %}
    <p class="muted">Discharged {{ selected_patient.discharge_display }}</p>
    {% endif %}
    <div class="card-actions">
        <form action="{{ url_for('delete_patient', pid=selected_patient.id) }}" method="post" onsubmit="return confirm('Delete this patient?');">
            <input type="hidden" name="view" value="{{ current_view }}">
            <button type="submit" class="delete-btn">🗑️ Delete Patient</button>
        </form>
    </div>

{% if selected_patient.status == "active" %}

{% if current_shift_doctor %}
<p class="muted">Current on-shift doctor: Dr. {{ current_shift_doctor.name }} (<a href="{{ url_for('manage_doctors') }}">change</a>)</p>
{% else %}
<p class="muted">No doctor is currently on shift. <a href="{{ url_for('manage_doctors') }}">Set one on the doctors page.</a></p>
{% endif %}

{% if patient_doctors %}
<h3>Doctors Involved</h3>
<table>
<tr><th>ID</th><th>Doctor</th></tr>
{% for d in patient_doctors %}
<tr><td>{{ d.id }}</td><td>Dr. {{ d.name }}</td></tr>
{% endfor %}
</table>
{% endif %}

<h3>Add Billing Entry</h3>
{% if not current_shift_doctor %}
<p><strong>Set an on-shift doctor before adding billing entries.</strong></p>
{% else %}
<form action="{{ url_for('add_billing', pid=selected_patient.id) }}" method="post">
    <label>Code</label><input name="code" required>
    <label>Description</label><input name="description">
    <label>Amount</label><input name="amount" type="number" step="0.01" required>
    <label>Billing Date</label><input type="date" name="billing_date" value="{{ default_billing_date }}" required>
    <label>Billing Time</label><input type="time" name="billing_time" value="{{ default_billing_time }}" step="60">
    <button>Add Billing</button>
</form>
{% endif %}

{% if patient_billings %}
<h3>Billing Entries</h3>
<table>
<tr><th>Doctor</th><th>Code</th><th>Description</th><th>Amount</th><th>Date/Time</th></tr>
{% for b in patient_billings %}
<tr>
<td>{% if b.doctor %}Dr. {{ b.doctor.name }}{% else %}Unknown{% endif %}</td>
<td>{{ b.code }}</td><td>{{ b.description }}</td><td>{{ "%.2f"|format(b.amount) }}</td>
<td>{{ b.timestamp.strftime("%b %d, %Y %I:%M %p") }}</td>
</tr>
{% endfor %}
</table>
{% endif %}

<form action="{{ url_for('discharge_patient', pid=selected_patient.id) }}" method="post">
    <label>Discharge Date</label>
    <input type="date" name="discharge_date" value="{{ default_discharge_date }}" required>
    <label>Discharge Time</label>
    <input type="time" name="discharge_time" value="{{ default_discharge_time }}" step="60">
    <button>Discharge Patient & Optimize</button>
</form>

{% else %}

<h3>Final Optimized Billing</h3>
{% if optimized_billings %}
<table>
<tr><th>Doctor</th><th>Code</th><th>Description</th><th>Amount</th><th>Date/Time</th></tr>
{% for b in optimized_billings %}
<tr>
<td>{% if b.doctor %}Dr. {{ b.doctor.name }}{% else %}Unknown{% endif %}</td>
<td>{{ b.code }}</td><td>{{ b.description }}</td><td>{{ "%.2f"|format(b.amount) }}</td>
<td>{{ b.timestamp.strftime("%b %d, %Y %I:%M %p") }}</td>
</tr>
{% endfor %}
</table>
<h3>Total: ${{ "%.2f"|format(selected_patient.optimized_total) }}</h3>
{% else %}
<p class="muted">No billing data.</p>
{% endif %}

<p class="muted">Archived patients cannot be modified.</p>

<form action="{{ url_for('restore_patient', pid=selected_patient.id) }}" method="post">
    <button>Restore to Active</button>
</form>

{% endif %}
</div>
{% endif %}

</body>
</html>
"""


DOCTORS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Manage Doctors</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #f3f7fb;
        }
        .card {
            background:#fff; padding:20px; margin:15px 0;
            border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.08);
        }
        h1 { margin-top:0; }
        label { display:block; margin-top:8px; }
        input, select {
            width:100%; padding:6px; margin-top:2px; border:1px solid #ccc; border-radius:4px;
        }
        button {
            margin-top:10px; padding:6px 12px; border:none; border-radius:4px;
            background:#1976d2; color:#fff; cursor:pointer;
        }
        ul { padding-left:20px; }
        .muted { opacity:0.7; font-style:italic; }
        a { color:#1976d2; }
        .top-link { display:inline-block; margin-bottom:12px; }
    </style>
</head>
<body>
    <a class="top-link" href="{{ url_for('index', view='active') }}">&#8592; Back to Patients</a>
    <div class="card">
        <h1>Doctors On Call</h1>
        <p class="muted">Logged in as {{ session["user"] }} &mdash; <a href="{{ url_for('logout') }}">Logout</a></p>

        {% if doctors %}
        <h3>Current Roster</h3>
        <ul>
            {% for doc in doctors %}
            <li>Dr. {{ doc.name }}{% if shift_doctor and shift_doctor.id == doc.id %} (on shift){% endif %}</li>
            {% endfor %}
        </ul>
        <h3>Set On-Shift Doctor</h3>
        <form method="post" action="{{ url_for('set_on_shift') }}">
            <label>Doctor</label>
            <select name="doctor_id">
                {% for doc in doctors %}
                <option value="{{ doc.id }}" {% if shift_doctor and shift_doctor.id == doc.id %}selected{% endif %}>
                    Dr. {{ doc.name }}
                </option>
                {% endfor %}
            </select>
            <button>Set On Shift</button>
        </form>
        {% else %}
        <p class="muted">No doctors available yet. Add one below.</p>
        {% endif %}

        <h3>Add Doctor</h3>
        <form action="{{ url_for('create_doctor') }}" method="post">
            <label>Name</label>
            <input type="text" name="doctor_name" required>
            <button>Add Doctor</button>
        </form>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    with app.app_context():
        ensure_default_data()
    app.run(debug=True)
