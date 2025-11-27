from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pw = request.form.get("password", "").strip()

        user_record = User.query.filter_by(username=user).first()
        if user_record and check_password_hash(user_record.password_hash, pw):
            session["user"] = user_record.username
            return redirect(url_for("patients.index", view="active"))

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html", error=None)


@auth_bp.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("auth.login"))
