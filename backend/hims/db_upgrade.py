"""Tiny schema upgrader.

`db.create_all()` creates *new tables* but never adds columns to tables that already exist. When a release adds a
column to an existing table, list it here: on start-up HIMS adds it if it is missing, so an existing database
(with real data in it) keeps working without any manual SQL. Safe to run every start.
"""
from sqlalchemy import inspect, text

from .extensions import db

# (table, column, column definition). Definitions must be valid for both SQLite and PostgreSQL.
NEW_COLUMNS = [
    ("users", "employment_type", "VARCHAR(20) NOT NULL DEFAULT 'full_time'"),
    ("users", "reports_to_id", "INTEGER REFERENCES users(id)"),
    ("tasks", "project_id", "INTEGER REFERENCES projects(id)"),
    ("comments", "attachment_file", "VARCHAR(255)"),
    ("comments", "attachment_name", "VARCHAR(255)"),
    ("comments", "version", "INTEGER"),
    ("users", "display_title", "VARCHAR(80)"),
    ("tasks", "campaign_id", "INTEGER REFERENCES campaigns(id)"),
    ("leads", "campaign_id", "INTEGER REFERENCES campaigns(id)"),
    ("users", "task_assignable", "BOOLEAN NOT NULL DEFAULT TRUE"),          # 1.5
]


def upgrade_schema():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    added = []
    for table, column, ddl in NEW_COLUMNS:
        if table not in tables:
            continue
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        added.append(f"{table}.{column}")
    return added
