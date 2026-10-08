"""Internal chat: direct messages between two people, plus one team chat per department."""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from ..extensions import db
from ..models import Conversation, ConversationMember, Department, Message, User
from ..security import access
from ..services import notify, save_upload, upload_path
from ..timeutil import now
from ..ui import dtfmt

bp = Blueprint("messages", __name__, url_prefix="/messages")

MAX_LEN = 4000
PAGE = 200      # most recent messages shown in a thread


# ---------------------------------------------------------------- helpers
def unread_total(user):
    """Number of unread messages across every conversation this person belongs to."""
    return (db.session.query(func.count(Message.id))
            .join(ConversationMember, ConversationMember.conversation_id == Message.conversation_id)
            .filter(ConversationMember.user_id == user.id, Message.sender_id != user.id,
                    or_(ConversationMember.last_read_at.is_(None), Message.created_at > ConversationMember.last_read_at))
            .scalar() or 0)


def _member(conv, user, fresh_is_read=False):
    """This user's membership row (created on first use). New department-chat members start 'caught up'."""
    row = ConversationMember.query.filter_by(conversation_id=conv.id, user_id=user.id).first()
    if row is None:
        row = ConversationMember(conversation_id=conv.id, user_id=user.id, last_read_at=now() if fresh_is_read else None)
        db.session.add(row)
        db.session.flush()
    return row


def _dept_chat(dept):
    conv = Conversation.query.filter_by(kind="department", department_id=dept.id).first()
    if conv is None:
        conv = Conversation(kind="department", department_id=dept.id)
        db.session.add(conv)
        db.session.flush()
    return conv


def _direct(a, b):
    """The one-to-one conversation between two people, created if needed."""
    mine = [m.conversation_id for m in ConversationMember.query.filter_by(user_id=a.id).all()]
    if mine:
        theirs = (ConversationMember.query.join(Conversation, Conversation.id == ConversationMember.conversation_id)
                  .filter(ConversationMember.user_id == b.id, ConversationMember.conversation_id.in_(mine), Conversation.kind == "direct").first())
        if theirs:
            return theirs.conversation
    conv = Conversation(kind="direct")
    db.session.add(conv)
    db.session.flush()
    db.session.add_all([ConversationMember(conversation_id=conv.id, user_id=a.id), ConversationMember(conversation_id=conv.id, user_id=b.id)])
    db.session.flush()
    return conv


def _can_open(conv, user):
    if conv.kind == "department":
        return user.is_admin_level or user.department_id == conv.department_id
    return any(m.user_id == user.id for m in conv.members)


def _get(cid):
    conv = db.session.get(Conversation, cid)
    if conv is None or not _can_open(conv, current_user):
        abort(404)          # 404, not 403: don't reveal that someone else's conversation exists
    return conv


def _title(conv, user):
    if conv.kind == "department":
        return f"{conv.department.name} team chat"
    other = next((m.user for m in conv.members if m.user_id != user.id), None)
    return other.name if other else "Direct message"


def _sidebar(user):
    """Conversation list for the left column: team chats first, then direct messages."""
    depts = (Department.query.filter_by(active=True).order_by(Department.name).all() if user.is_admin_level
             else ([user.department] if user.department else []))
    team = []
    for d in depts:
        conv = _dept_chat(d)
        _member(conv, user, fresh_is_read=True)
    db.session.commit()
    unread = {}
    for cid, n in (db.session.query(Message.conversation_id, func.count(Message.id))
                   .join(ConversationMember, ConversationMember.conversation_id == Message.conversation_id)
                   .filter(ConversationMember.user_id == user.id, Message.sender_id != user.id,
                           or_(ConversationMember.last_read_at.is_(None), Message.created_at > ConversationMember.last_read_at))
                   .group_by(Message.conversation_id).all()):
        unread[cid] = n

    def row(conv):
        last = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id.desc()).first()
        preview = ""
        if last:
            who = "You" if last.sender_id == user.id else last.sender.name.split()[0]
            preview = f"{who}: {last.body[:60] or 'Sent a file'}"
        return {"conv": conv, "title": _title(conv, user), "preview": preview, "unread": unread.get(conv.id, 0),
                "when": last.created_at if last else conv.created_at}

    for d in depts:
        team.append(row(_dept_chat(d)))
    direct_ids = [m.conversation_id for m in ConversationMember.query.filter_by(user_id=user.id).all()]
    direct = [row(c) for c in Conversation.query.filter(Conversation.id.in_(direct_ids or [0]), Conversation.kind == "direct")
              .order_by(Conversation.updated_at.desc()).all()]
    return team, direct


def _render(active=None):
    team, direct = _sidebar(current_user)
    people = User.query.filter(User.active.is_(True), User.id != current_user.id).order_by(User.name).all()
    msgs, last_id, first_id, has_older = [], 0, 0, False
    if active is not None:
        msgs = list(reversed(Message.query.filter_by(conversation_id=active.id).order_by(Message.id.desc()).limit(PAGE).all()))
        last_id = msgs[-1].id if msgs else 0
        first_id = msgs[0].id if msgs else 0
        has_older = bool(msgs) and Message.query.filter(Message.conversation_id == active.id, Message.id < first_id).first() is not None
        _member(active, current_user, fresh_is_read=True).last_read_at = now()
        db.session.commit()
    return render_template("messages/index.html", team=team, direct=direct, people=people, active=active, msgs=msgs, last_id=last_id,
                           first_id=first_id, has_older=has_older, active_title=_title(active, current_user) if active else "", max_len=MAX_LEN)


# ---------------------------------------------------------------- routes
@bp.route("/")
@access("messages.use")
def index():
    return _render()


@bp.route("/<int:cid>")
@access("messages.use")
def thread(cid):
    return _render(_get(cid))


@bp.route("/direct", methods=["POST"])
@access("messages.use")
def direct():
    other = db.session.get(User, request.form.get("user_id", type=int) or 0)
    if other is None or not other.active or other.id == current_user.id:
        flash("Choose a person to message.", "error")
        return redirect(url_for("messages.index"))
    conv = _direct(current_user, other)
    db.session.commit()
    return redirect(url_for("messages.thread", cid=conv.id))


@bp.route("/<int:cid>/send", methods=["POST"])
@access("messages.use")
def send(cid):
    conv = _get(cid)
    body = request.form.get("body", "").strip()
    upload = request.files.get("attachment")
    has_file = bool(upload and upload.filename)
    if not body and not has_file:
        return redirect(url_for("messages.thread", cid=conv.id))
    if len(body) > MAX_LEN:
        flash(f"Messages can be up to {MAX_LEN} characters. Yours has {len(body)}.", "error")
        return redirect(url_for("messages.thread", cid=conv.id))
    stored = original = None
    if has_file:
        try:
            stored, original, _ = save_upload(upload, "messages")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("messages.thread", cid=conv.id))
    msg = Message(conversation_id=conv.id, sender_id=current_user.id, body=body, attachment_file=stored, attachment_name=original)
    db.session.add(msg)
    conv.updated_at = now()
    _member(conv, current_user, fresh_is_read=True).last_read_at = now()
    if conv.kind == "direct":
        others = [m.user for m in conv.members if m.user_id != current_user.id]
        notify(others, f"New message from {current_user.name}", (body or f"Sent {original}")[:120], url_for("messages.thread", cid=conv.id), kind="message")
    db.session.commit()
    return redirect(url_for("messages.thread", cid=conv.id))


@bp.route("/<int:cid>/poll")
@access("messages.use")
def poll(cid):
    """Small JSON feed the open chat page asks every few seconds for anything new."""
    conv = _get(cid)
    after = request.args.get("after", 0, type=int)
    fresh = Message.query.filter(Message.conversation_id == conv.id, Message.id > after).order_by(Message.id).limit(100).all()
    if fresh:
        _member(conv, current_user, fresh_is_read=True).last_read_at = now()
        db.session.commit()
    return jsonify({"messages": [_payload(m) for m in fresh]})


def _payload(m):
    return {
        "id": m.id, "mine": m.sender_id == current_user.id, "initials": m.sender.initials, "sender": m.sender.name,
        "body": m.body, "time": dtfmt(m.created_at),
        "file": ({"name": m.attachment_name, "url": url_for("messages.file", mid=m.id)} if m.attachment_file else None),
    }


@bp.route("/<int:cid>/older")
@access("messages.use")
def older(cid):
    """The 50 messages before the oldest one on screen, for the 'Load earlier messages' button."""
    conv = _get(cid)
    before = request.args.get("before", 0, type=int)
    rows = Message.query.filter(Message.conversation_id == conv.id, Message.id < before).order_by(Message.id.desc()).limit(50).all()
    more = bool(rows) and Message.query.filter(Message.conversation_id == conv.id, Message.id < rows[-1].id).first() is not None
    return jsonify({"messages": [_payload(m) for m in reversed(rows)], "more": more})


@bp.route("/file/<int:mid>")
@access("messages.use")
def file(mid):
    msg = db.session.get(Message, mid)
    if msg is None or not msg.attachment_file or not _can_open(msg.conversation, current_user):
        abort(404)
    try:
        path = upload_path(msg.attachment_file)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=msg.attachment_name or path.name)
