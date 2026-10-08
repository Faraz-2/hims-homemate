"""Command line tools:  flask --app run <command>"""
from pathlib import Path

import click

from .extensions import db
from .models import Role, User
from .seed import DEMO_PASSWORD, seed_defaults, seed_demo
from . import backup_vault
from .services import BackupError, assign_employee_code, create_backup, restore_backup, verify_backup
from .timeutil import today


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create tables and default data (roles, permissions, departments, settings, first admin)."""
        db.create_all()
        seed_defaults()
        click.echo("Database ready.")

    @app.cli.command("seed-demo")
    def seed_demo_cmd():
        """Load sample employees, tasks, leads, tickets and more (for demos and testing)."""
        if seed_demo():
            click.echo(f"Demo data loaded. Demo users sign in with their name, e.g. ayesha.khan@homemate.local / {DEMO_PASSWORD}")
        else:
            click.echo("Demo data is already loaded.")

    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(name, email, password):
        """Create an additional Super Admin account."""
        role = Role.query.filter_by(key="super_admin").first()
        if User.query.filter_by(email=email.lower()).first():
            raise click.ClickException("A user with this email already exists.")
        user = User(name=name, email=email.lower(), role_id=role.id, job_title="Super Admin", joined_on=today())
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        assign_employee_code(user)
        db.session.commit()
        click.echo(f"Super Admin {email} created.")

    @app.cli.command("reset-password")
    @click.argument("email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def reset_password(email, password):
        """Set a new password for a user (they must change it at next sign-in)."""
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            raise click.ClickException("No user with that email.")
        user.set_password(password)
        user.must_change_password = True
        user.failed_logins, user.locked_until = 0, None
        db.session.commit()
        click.echo(f"Password reset for {user.email}.")

    @app.cli.command("backup")
    def backup():
        """Create a backup zip (database + uploaded files) in instance/backups."""
        try:
            path = create_backup()
        except BackupError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Backup created: {path}")

    @app.cli.command("verify-backup")
    @click.argument("file", type=click.Path(exists=True))
    def verify(file):
        """Check that a backup can be opened and read (use this for your restore tests)."""
        try:
            info = verify_backup(file)
        except BackupError as exc:
            raise click.ClickException(str(exc))
        for key, value in info.items():
            click.echo(f"{key:>14}: {value}")

    @app.cli.command("restore")
    @click.argument("file", type=click.Path(exists=True))
    @click.confirmation_option(prompt="This replaces the live database with the backup. Is HIMS stopped?")
    def restore(file):
        """Restore a SQLite backup over the live database (stop HIMS first)."""
        try:
            info = restore_backup(Path(file))
        except BackupError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Restored {info['file']}: {info.get('users')} users, {info.get('tasks')} tasks. "
                   "The previous database was kept as hims.db.before-restore.")

    @app.cli.command("backup-complete")
    def backup_complete():
        """Full backup plus a zip per department and per person, filed by date (schedule this daily)."""
        manifest = backup_vault.create_complete_backup(trigger="command line")
        click.echo(f"Full backup: {manifest['full_backup']}")
        click.echo(f"{len(manifest['departments'])} department zips and {len(manifest['users'])} person zips created.")
        for problem in manifest["errors"]:
            click.echo(f"PROBLEM: {problem}")
