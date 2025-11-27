from flask import Flask

from .auth import auth_bp
from .doctors import doctors_bp
from .extensions import db
from .helpers import ensure_default_data
from .patients import patients_bp
from .shift import shift_bp


def create_app():
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.secret_key = "S0meV3ryL0ngRandomString_!@#$%^&*()_+2025"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///billing.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(patients_bp)
    app.register_blueprint(doctors_bp)
    app.register_blueprint(shift_bp)

    with app.app_context():
        ensure_default_data()

    return app
