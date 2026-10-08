"""Each vendor's product list: a food menu, a stationery catalogue, a service price list.

Items can be added, edited, switched on/off ("available"), repriced and deleted at any time. Every price change is
remembered, so a price's history stays visible.
"""
import csv
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from ..constants import VENDOR_SECTIONS_HINT, VENDOR_UNITS
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Comment, Expense, StoredFile, Vendor, VendorProduct, VendorProductPrice
from ..security import access, has_perm
from ..services import csv_response, delete_upload, log_activity

bp = Blueprint("vendor_products", __name__, url_prefix="/vendors")

SAMPLE_ROWS = [
    ["name", "section", "unit", "price", "description", "available", "sku"],
    ["Chicken biryani", "Main course", "plate", "450", "Served with raita", "yes", "M-01"],
    ["Mineral water 500 ml", "Drinks", "each", "60", "", "yes", "D-01"],
    ["Gulab jamun", "Desserts", "portion", "120", "2 pieces", "no", "S-01"],
]


def _vendor(vid):
    return Vendor.query.get_or_404(vid)


def _product(vid, pid):
    return VendorProduct.query.filter_by(id=pid, vendor_id=vid).first_or_404()


def _form(price_note=False):
    fields = [
        Field("name", "Item name", required=True, placeholder="e.g. Chicken biryani, A4 paper ream, Deep cleaning (per visit)"),
        Field("section", "Section or group", placeholder=VENDOR_SECTIONS_HINT,
              help="Items with the same section are listed together, like the headings of a menu."),
        Field("price", "Price", "number", required=True, min=0),
        Field("unit", "Sold per", "select", options=strings(VENDOR_UNITS), value="each"),
        Field("sku", "Vendor's item code (optional)"),
        Field("available", "Available right now (untick when the vendor stops offering it)", "checkbox", value="1"),
        Field("description", "Description (optional)", half=False),
    ]
    if price_note:
        fields.append(Field("price_note", "Reason for a price change (optional)", half=False,
                            help="Saved in the price history if you change the price, for example 'new season rates'."))
    return Form(fields)


def _record_price(product, old, new, note=None):
    db.session.add(VendorProductPrice(product_id=product.id, old_price=old, new_price=new,
                                      changed_by_id=current_user.id, note=(note or "")[:200] or None))


@bp.route("/<int:vid>/products")
@access("vendors.view", "vendors")
def index(vid):
    vendor = _vendor(vid)
    text = request.args.get("q", "").strip()
    section = request.args.get("section", "")
    show = request.args.get("show", "all")
    q = VendorProduct.query.filter_by(vendor_id=vendor.id)
    if text:
        like = f"%{text}%"
        q = q.filter(or_(VendorProduct.name.ilike(like), VendorProduct.description.ilike(like), VendorProduct.sku.ilike(like)))
    if section == "__none__":
        q = q.filter(or_(VendorProduct.section.is_(None), VendorProduct.section == ""))
    elif section:
        q = q.filter(VendorProduct.section == section)
    if show == "available":
        q = q.filter(VendorProduct.available.is_(True))
    elif show == "unavailable":
        q = q.filter(VendorProduct.available.is_(False))
    items = q.order_by(VendorProduct.section, VendorProduct.name).all()
    groups = {}
    for p in items:
        groups.setdefault(p.section or "Not in a section", []).append(p)
    all_items = vendor.products
    sections = sorted({p.section for p in all_items if p.section})
    prices = [p.price for p in all_items]
    stats = {"total": len(all_items), "available": sum(1 for p in all_items if p.available),
             "low": min(prices) if prices else 0, "high": max(prices) if prices else 0,
             "average": (sum(prices) / len(prices)) if prices else 0}
    return render_template("vendors/products.html", vendor=vendor, groups=groups, shown=len(items), sections=sections,
                           stats=stats, q=text, section=section, show=show, can_edit=has_perm(current_user, "vendors.products"))


@bp.route("/<int:vid>/products/new", methods=["GET", "POST"])
@access("vendors.products", "vendors")
def new(vid):
    vendor = _vendor(vid)
    form = _form()
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        dup = VendorProduct.query.filter(VendorProduct.vendor_id == vendor.id, func.lower(VendorProduct.name) == d["name"].lower(),
                                         func.lower(func.coalesce(VendorProduct.section, "")) == (d["section"] or "").lower()).first()
        if dup:
            form.fields[0].error = "This vendor already has an item with that name in the same section. Edit that one instead."
        else:
            p = VendorProduct(vendor_id=vendor.id, name=d["name"], section=d["section"], sku=d["sku"], description=d["description"],
                              unit=d["unit"] or "each", price=d["price"], available=d["available"],
                              created_by_id=current_user.id, updated_by_id=current_user.id)
            db.session.add(p)
            db.session.flush()
            _record_price(p, None, p.price, "First price")
            log_activity("created", "vendor_product", p.id, f"Added '{p.name}' to {vendor.name}'s list")
            db.session.commit()
            flash(f"'{p.name}' added to {vendor.name}'s list.", "success")
            return redirect(url_for("vendor_products.index", vid=vendor.id))
    return render_template("form_page.html", form=form, title=f"Add an item to {vendor.name}",
                           subtitle="Menu dish, product or service with its price.", submit="Add item",
                           cancel=url_for("vendor_products.index", vid=vendor.id))


@bp.route("/<int:vid>/products/<int:pid>/edit", methods=["GET", "POST"])
@access("vendors.products", "vendors")
def edit(vid, pid):
    vendor, p = _vendor(vid), _product(vid, pid)
    form = _form(price_note=True).fill(p)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        clash = VendorProduct.query.filter(VendorProduct.vendor_id == vendor.id, VendorProduct.id != p.id,
                                           func.lower(VendorProduct.name) == d["name"].lower(),
                                           func.lower(func.coalesce(VendorProduct.section, "")) == (d["section"] or "").lower()).first()
        if clash:
            form.fields[0].error = "Another item with that name already exists in the same section."
        else:
            old_price = p.price
            for key in ("name", "section", "sku", "description"):
                setattr(p, key, d[key])
            p.unit, p.price, p.available = d["unit"] or "each", d["price"], d["available"]
            p.updated_by_id = current_user.id
            if abs((old_price or 0) - p.price) > 1e-9:
                _record_price(p, old_price, p.price, d.get("price_note"))
            log_activity("updated", "vendor_product", p.id, f"Updated '{p.name}' on {vendor.name}'s list"
                         + (f" (price {old_price:g} to {p.price:g})" if old_price != p.price else ""))
            db.session.commit()
            flash("Item saved.", "success")
            return redirect(url_for("vendor_products.index", vid=vendor.id))
    return render_template("form_page.html", form=form, title=f"Edit {p.name}", subtitle=vendor.name, submit="Save changes",
                           cancel=url_for("vendor_products.index", vid=vendor.id))


@bp.route("/<int:vid>/products/<int:pid>/toggle", methods=["POST"])
@access("vendors.products", "vendors")
def toggle(vid, pid):
    p = _product(vid, pid)
    p.available = not p.available
    p.updated_by_id = current_user.id
    log_activity("updated", "vendor_product", p.id, f"'{p.name}' marked {'available' if p.available else 'unavailable'}")
    db.session.commit()
    return redirect(url_for("vendor_products.index", vid=vid) + f"#item-{p.id}")


@bp.route("/<int:vid>/products/<int:pid>/delete", methods=["POST"])
@access("vendors.products", "vendors")
def delete(vid, pid):
    vendor, p = _vendor(vid), _product(vid, pid)
    name = p.name
    log_activity("deleted", "vendor_product", p.id, f"Deleted '{name}' from {vendor.name}'s list")
    db.session.delete(p)
    db.session.commit()
    flash(f"'{name}' was deleted from {vendor.name}'s list.", "success")
    return redirect(url_for("vendor_products.index", vid=vid))


@bp.route("/<int:vid>/products/<int:pid>/history")
@access("vendors.view", "vendors")
def history(vid, pid):
    vendor, p = _vendor(vid), _product(vid, pid)
    return render_template("vendors/product_history.html", vendor=vendor, p=p, rows=p.price_history,
                           can_edit=has_perm(current_user, "vendors.products"))


@bp.route("/<int:vid>/products/bulk-price", methods=["POST"])
@access("vendors.products", "vendors")
def bulk_price(vid):
    """Raise or lower every price (or just one section's prices) by a percentage."""
    vendor = _vendor(vid)
    try:
        percent = float(request.form.get("percent", "").strip())
    except ValueError:
        flash("Enter the percentage as a number, for example 10 to raise prices by 10%, or -5 to lower them by 5%.", "error")
        return redirect(url_for("vendor_products.index", vid=vid))
    if not -90 <= percent <= 500 or percent == 0:
        flash("Choose a percentage other than zero, between -90 and 500.", "error")
        return redirect(url_for("vendor_products.index", vid=vid))
    section = request.form.get("section", "")
    q = VendorProduct.query.filter_by(vendor_id=vendor.id)
    if section == "__none__":
        q = q.filter(or_(VendorProduct.section.is_(None), VendorProduct.section == ""))
    elif section:
        q = q.filter(VendorProduct.section == section)
    changed = 0
    for p in q.all():
        new_price = round(p.price * (1 + percent / 100), 2)
        if new_price != p.price:
            _record_price(p, p.price, new_price, f"Bulk change {percent:+g}%")
            p.price, p.updated_by_id = new_price, current_user.id
            changed += 1
    log_activity("updated", "vendor_product", None, f"Changed {changed} price(s) on {vendor.name}'s list by {percent:+g}%")
    db.session.commit()
    flash(f"{changed} price(s) changed by {percent:+g}%.", "success")
    return redirect(url_for("vendor_products.index", vid=vid))


# ---------------------------------------------------------------- export / import
@bp.route("/<int:vid>/products/export.csv")
@access("vendors.view", "vendors")
def export(vid):
    vendor = _vendor(vid)
    rows = [[p.name, p.section or "", p.unit or "", f"{p.price:g}", p.description or "", "yes" if p.available else "no", p.sku or ""]
            for p in vendor.products]
    return csv_response(f"{vendor.name.replace(' ', '_')}_product_list.csv", SAMPLE_ROWS[0], rows)


@bp.route("/products/sample.csv")
@access("vendors.view", "vendors")
def sample():
    return csv_response("vendor_product_list_sample.csv", SAMPLE_ROWS[0], SAMPLE_ROWS[1:])


@bp.route("/<int:vid>/products/import", methods=["GET", "POST"])
@access("vendors.products", "vendors")
def import_csv(vid):
    vendor = _vendor(vid)
    result = None
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file first.", "error")
        else:
            try:
                text = upload.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
                if not {"name", "price"} <= headers:
                    raise ValueError("The file needs at least the columns 'name' and 'price' in its first row.")
            except (UnicodeDecodeError, ValueError, csv.Error) as exc:
                flash(str(exc) if isinstance(exc, ValueError) else "That file could not be read. Save it as CSV (UTF-8) and try again.", "error")
            else:
                result = {"added": 0, "updated": 0, "skipped": []}
                existing = {((p.name or "").lower(), (p.section or "").lower()): p for p in vendor.products}
                for n, raw in enumerate(reader, start=2):
                    row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
                    name = row.get("name", "")
                    if not name:
                        continue
                    try:
                        price = float(row.get("price", "").replace(",", ""))
                        if price < 0:
                            raise ValueError
                    except ValueError:
                        result["skipped"].append(f"Row {n} ({name}): the price is missing or not a number.")
                        continue
                    section = row.get("section") or None
                    available = row.get("available", "yes").lower() not in ("no", "n", "0", "false", "unavailable")
                    unit = row.get("unit") or "each"
                    key = (name.lower(), (section or "").lower())
                    p = existing.get(key)
                    if p is None:
                        p = VendorProduct(vendor_id=vendor.id, name=name, section=section, unit=unit, price=price,
                                          description=row.get("description") or None, sku=row.get("sku") or None,
                                          available=available, created_by_id=current_user.id, updated_by_id=current_user.id)
                        db.session.add(p)
                        db.session.flush()
                        _record_price(p, None, price, "Imported")
                        existing[key] = p
                        result["added"] += 1
                    else:
                        if abs(p.price - price) > 1e-9:
                            _record_price(p, p.price, price, "Imported")
                        p.price, p.unit, p.available = price, unit, available
                        p.description = row.get("description") or p.description
                        p.sku = row.get("sku") or p.sku
                        p.updated_by_id = current_user.id
                        result["updated"] += 1
                log_activity("imported", "vendor_product", None, f"Imported {result['added']} new and {result['updated']} existing items for {vendor.name}")
                db.session.commit()
                flash(f"Import finished: {result['added']} added, {result['updated']} updated, {len(result['skipped'])} skipped.", "success")
    return render_template("vendors/products_import.html", vendor=vendor, result=result)


# ---------------------------------------------------------------- delete a whole vendor
@bp.route("/<int:vid>/delete", methods=["POST"])
@access("vendors.delete", "vendors")
def vendor_delete(vid):
    vendor = _vendor(vid)
    if Expense.query.filter_by(vendor_id=vendor.id).count():
        flash("This vendor has expenses linked to it, so it cannot be deleted without losing finance history. "
              "Set its status to 'On hold' or 'Blacklisted' instead.", "error")
        return redirect(url_for("vendors.detail", vid=vendor.id))
    for f in StoredFile.query.filter_by(entity_type="vendor", entity_id=vendor.id).all():
        delete_upload(f.file_name)
        db.session.delete(f)
    Comment.query.filter_by(entity_type="vendor", entity_id=vendor.id).delete()
    name = vendor.name
    log_activity("deleted", "vendor", vendor.id, f"Deleted vendor {name} and its {len(vendor.products)} product(s)")
    db.session.delete(vendor)
    db.session.commit()
    flash(f"Vendor {name} and its product list were deleted.", "success")
    return redirect(url_for("vendors.index"))
