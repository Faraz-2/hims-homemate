"""A tiny form layer: declare fields, validate, re-render with values and errors.

CSRF protection comes from Flask-WTF's CSRFProtect (every POST needs the hidden token).
"""
from datetime import datetime


class Field:
    def __init__(self, name, label, type="text", required=False, options=None, value=None, help=None,
                 half=True, placeholder="", rows=4, min=None, min_length=None, coerce=None, accept=None,
                 blank_label="Select…"):
        self.name, self.label, self.type = name, label, type
        self.required, self.options, self.help = required, options or [], help
        self.half, self.placeholder, self.rows = half, placeholder, rows
        self.min, self.min_length, self.coerce, self.accept = min, min_length, coerce, accept
        self.blank_label = blank_label
        self.value = "" if value is None else value
        self.error = None

    def is_selected(self, option_value):
        return str(self.value) == str(option_value)


class Form:
    def __init__(self, fields):
        self.fields = fields
        self.data = {}

    @property
    def has_file(self):
        return any(f.type == "file" for f in self.fields)

    @property
    def errors(self):
        return [f"{f.label}: {f.error}" for f in self.fields if f.error]

    def fill(self, obj):
        """Pre-fill from a model instance (attribute names must match field names)."""
        for f in self.fields:
            v = getattr(obj, f.name, None)
            if v is None:
                continue
            if hasattr(v, "isoformat") and not isinstance(v, str):
                v = v.isoformat()[:10]
            f.value = "1" if v is True else ("" if v is False else v)
        return self

    def validate(self, formdata):
        ok = True
        self.data = {}
        for f in self.fields:
            if f.type == "file":
                continue
            if f.type == "checkbox":
                self.data[f.name] = f.name in formdata
                f.value = "1" if self.data[f.name] else ""
                continue
            raw = (formdata.get(f.name) or "").strip()
            f.value = raw
            if not raw:
                self.data[f.name] = None
                if f.required:
                    f.error, ok = "This field is required.", False
                continue
            try:
                self.data[f.name] = self._convert(f, raw)
            except ValueError as exc:
                f.error, ok = str(exc), False
                self.data[f.name] = None
        return ok

    @staticmethod
    def _convert(f, raw):
        if f.type == "date":
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Enter a valid date.")
        if f.type == "number":
            try:
                val = float(raw)
            except ValueError:
                raise ValueError("Enter a number.")
            if f.min is not None and val < f.min:
                raise ValueError(f"Must be at least {f.min}.")
            return val
        if f.type == "email":
            if "@" not in raw or "." not in raw.split("@")[-1]:
                raise ValueError("Enter a valid email address.")
            return raw.lower()
        if f.type == "select":
            if raw not in {str(v) for v, _ in f.options}:
                raise ValueError("Choose one of the listed options.")
            return f.coerce(raw) if f.coerce else raw
        if f.min_length and len(raw) < f.min_length:
            raise ValueError(f"Use at least {f.min_length} characters.")
        return raw


def opts(pairs, blank=False):
    """Helper: turn (value, label) pairs into option list, optionally starting with an empty choice."""
    return list(pairs)


def strings(items):
    """['A','B'] -> [('A','A'),('B','B')]"""
    return [(i, i) for i in items]
