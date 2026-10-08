"""Home Mate Integrated Management System (HIMS) - application factory."""
import os
import sqlite3
from datetime import timedelta

from flask import Flask, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, logout_user
from sqlalchemy import event
from sqlalchemy.engine import Engine

from config import INSTANCE_DIR, Config

from .extensions import csrf, db, login_manager
from .timeutil import now


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """Make SQLite safe for several people using HIMS at once."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=8000")
        cur.close()


def create_app(overrides=None):
    # Templates and static files live in the separate frontend folder (see config.py).
    app = Flask(__name__, instance_path=str(INSTANCE_DIR),
                template_folder=str(Config.TEMPLATE_DIR), static_folder=str(Config.STATIC_DIR))
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    if os.environ.get("HIMS_BEHIND_PROXY", "0") == "1":
        # Running behind Caddy / nginx / a tunnel: trust ONE proxy's X-Forwarded-* headers so the real visitor address,
        # host name and https scheme reach the activity log and the security checks. Leave off when not using a proxy.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["STARTED_AT"] = now()
    for folder in (app.config["UPLOAD_DIR"], app.config["BACKUP_DIR"], app.config["ORGANIZED_BACKUP_DIR"]):
        folder.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    from . import models  # noqa: F401  (register tables)
    from .blueprints import register_blueprints
    from .cli import register_cli
    from .navigation import register_context
    from . import ui

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    register_blueprints(app)
    register_cli(app)
    register_context(app)

    # ---- template helpers
    app.jinja_env.globals.update(icon=ui.icon, badge=ui.badge, label=ui.label)
    for name in ("datefmt", "dtfmt", "timefmt", "timeago", "number", "filesize"):
        app.jinja_env.filters[name] = getattr(ui, name)

    @app.template_filter("money")
    def money(value):
        from .services import get_setting
        return f"{get_setting('currency', 'PKR')} {ui.number(value)}"

    @app.template_global()
    def page_url(page):
        args = request.args.to_dict()
        args["page"] = page
        return url_for(request.endpoint, **{**(request.view_args or {}), **args})

    # ---- request hooks
    @app.before_request
    def guard():
        if request.endpoint in (None, "static"):
            return None
        if not current_user.is_authenticated:
            return None
        if not current_user.active:
            logout_user()
            flash("This account is no longer active. Please contact IT.", "error")
            return redirect(url_for("auth.login"))
        if current_user.must_change_password and request.endpoint not in ("auth.change_password", "auth.logout"):
            return redirect(url_for("auth.change_password"))
        last = current_user.last_seen_at
        if last is None or now() - last > timedelta(seconds=60):
            current_user.last_seen_at = now()
            db.session.commit()
        return None

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; "
            "frame-ancestors 'self'")
        if request.endpoint not in (None, "static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- error pages
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/error.html", code=403, title="You don't have access to this page",
                               text="Your role or department doesn't include this area. If you need it, ask your department head or IT."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/error.html", code=404, title="We couldn't find that page",
                               text="The link may be old, or the record may have been removed."), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("errors/error.html", code=413, title="That file is too large",
                               text=f"Uploads can be up to {app.config['MAX_UPLOAD_MB']} MB. Compress the file or split it and try again."), 413

    @app.errorhandler(500)
    def server_error(_e):
        db.session.rollback()
        return render_template("errors/error.html", code=500, title="Something went wrong on our side",
                               text="The problem has been logged. Try again, and tell IT if it keeps happening."), 500

    if app.config["AUTO_INIT"]:
        with app.app_context():
            db.create_all()
            from .db_upgrade import upgrade_schema
            upgrade_schema()
            from .seed import seed_defaults
            seed_defaults()
    return app
