import re
from math import ceil
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from ..extensions import db
from ..models import User
from ..services import log_activity
from ..timeutil import now
from datetime import timedelta

bp = Blueprint("auth", __name__)


def _safe_next(target):
    if not target:
        return None
    parts = urlparse(target)
    if parts.netloc or parts.scheme or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def password_problem(pw: str):
    minimum = current_app.config["MIN_PASSWORD_LENGTH"]
    if len(pw) < minimum:
        return f"Use at least {minimum} characters."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Include at least one letter and one number."
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(func.lower(User.email) == email).first()
        cfg = current_app.config
        if user and user.locked_until and user.locked_until > now():
            minutes = ceil((user.locked_until - now()).total_seconds() / 60)
            error = f"Too many failed attempts. Try again in {minutes} minute(s), or ask IT to unlock the account."
        elif user and user.active and user.check_password(password):
            user.failed_logins, user.locked_until, user.last_login_at = 0, None, now()
            session.clear()
            login_user(user)
            session.permanent = True
            log_activity("login", "user", user.id, f"{user.name} signed in", user=user)
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.index"))
        else:
            if user:
                user.failed_logins += 1
                log_activity("login_failed", "user", user.id, f"Failed sign-in for {user.email}", user=user)
                if user.failed_logins >= cfg["MAX_FAILED_LOGINS"]:
                    user.locked_until = now() + timedelta(minutes=cfg["LOCKOUT_MINUTES"])
                    user.failed_logins = 0
                db.session.commit()
            error = "The email or password is incorrect."
    return render_template("auth/login.html", error=error)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_activity("logout", "user", current_user.id, f"{current_user.name} signed out")
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    forced = current_user.must_change_password
    error = None
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current_pw):
            error = "Your current password is not correct."
        elif new_pw != confirm:
            error = "The new passwords do not match."
        elif new_pw == current_pw:
            error = "Choose a password you haven't used here before."
        else:
            error = password_problem(new_pw)
        if not error:
            current_user.set_password(new_pw)
            current_user.must_change_password = False
            log_activity("password_change", "user", current_user.id, "Changed own password")
            db.session.commit()
            flash("Your password has been updated.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/change_password.html", error=error, forced=forced)
