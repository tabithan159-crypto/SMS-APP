"""
Church Members & Messaging App
------------------------------
- Admin login
- Record members (name, phone, fellowship)
- Import / export members as CSV
- Send SMS to everyone or to one fellowship
- Message history

Run:  pip install -r requirements.txt
      flask --app app create-admin
      flask --app app run
"""
import csv
import io
import os
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

import click
import requests
from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
# Set SECRET_KEY in your environment so logins survive restarts.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///church.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("HTTPS", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

db = SQLAlchemy(app)

DEFAULT_FELLOWSHIPS = ["Men", "Women", "Youth", "Sunday School", "Choir"]


# ---------------------------------------------------------------- models
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Fellowship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    chairperson = db.Column(db.String(120), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)
    members = db.relationship("Member", backref="fellowship", lazy=True)


class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)  # +2547XXXXXXXX
    fellowship_id = db.Column(db.Integer, db.ForeignKey("fellowship.id"))
    date_joined = db.Column(db.Date, default=date.today)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    target = db.Column(db.String(120), nullable=False)
    recipients = db.Column(db.Integer, default=0)
    status = db.Column(db.String(40), default="sent")
    sent_by = db.Column(db.String(80))
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------- helpers
def normalize_phone(raw):
    """Turn 0712345678 / 712345678 / 254712345678 / +254712345678 into +254712345678."""
    p = re.sub(r"[^\d+]", "", raw or "")
    if p.startswith("+254") and len(p) == 13:
        return p
    if p.startswith("254") and len(p) == 12:
        return "+" + p
    if p.startswith("0") and len(p) == 10:
        return "+254" + p[1:]
    if len(p) == 9 and p[0] in "17":
        return "+254" + p
    return None


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Invalid or missing form token. Go back and try again.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def fellowships_sorted():
    return Fellowship.query.order_by(Fellowship.name).all()


# ------------------------------------------------------------------- SMS
def send_sms(phones, text):
    provider = os.environ.get("SMS_PROVIDER", "console").lower()

    if provider == "africastalking":
        username = os.environ["AT_USERNAME"]
        api_key = os.environ["AT_API_KEY"]
        sender = os.environ.get("AT_SENDER_ID")
        base = ("https://api.sandbox.africastalking.com"
                if username == "sandbox" else "https://api.africastalking.com")
        accepted = 0
        for i in range(0, len(phones), 100):
            chunk = phones[i:i + 100]
            data = {"username": username, "to": ",".join(chunk), "message": text}
            if sender:
                data["from"] = sender
            resp = requests.post(
                f"{base}/version1/messaging", data=data,
                headers={"apiKey": api_key, "Accept": "application/json"}, timeout=30)
            resp.raise_for_status()
            recipients = resp.json().get("SMSMessageData", {}).get("Recipients", [])
            accepted += sum(1 for r in recipients if r.get("statusCode") in (100, 101, 102))
        return accepted

    # default: console / dry run
    for ph in phones:
        print(f"[SMS dry-run] to {ph}: {text}")
    return len(phones)


# ------------------------------------------------------------ auth routes
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        admin = Admin.query.filter_by(username=request.form.get("username", "").strip()).first()
        if admin and check_password_hash(admin.password_hash, request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["admin_id"] = admin.id
            session["admin_name"] = admin.username
            return redirect(url_for("members"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------- member routes
@app.route("/")
@login_required
def members():
    q = request.args.get("q", "").strip()
    fid = request.args.get("fellowship", type=int)
    query = Member.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Member.full_name.ilike(like), Member.phone.ilike(like)))
    if fid:
        query = query.filter_by(fellowship_id=fid)
    rows = query.order_by(Member.full_name).all()
    return render_template("members.html", members=rows, fellowships=fellowships_sorted(),
                           q=q, fid=fid, total=Member.query.count())


def _read_member_form(member_id=0):
    name = request.form.get("full_name", "").strip()
    phone = normalize_phone(request.form.get("phone"))
    fid = request.form.get("fellowship_id", type=int)
    if not name:
        return None, "Enter the member's full name."
    if not phone:
        return None, "Enter a valid Kenyan phone number, e.g. 0712 345 678."
    dup = Member.query.filter(Member.phone == phone, Member.id != member_id).first()
    if dup:
        return None, f"That number already belongs to {dup.full_name}."
    if fid and not db.session.get(Fellowship, fid):
        fid = None
    return {"full_name": name, "phone": phone, "fellowship_id": fid}, None


@app.route("/members/new", methods=["GET", "POST"])
@login_required
def member_new():
    if request.method == "POST":
        data, error = _read_member_form()
        if error:
            flash(error, "error")
        else:
            db.session.add(Member(**data))
            db.session.commit()
            flash(f"{data['full_name']} added.", "ok")
            return redirect(url_for("members"))
    return render_template("member_form.html", member=None, fellowships=fellowships_sorted())


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
def member_edit(member_id):
    member = db.get_or_404(Member, member_id)
    if request.method == "POST":
        data, error = _read_member_form(member.id)
        if error:
            flash(error, "error")
        else:
            for key, value in data.items():
                setattr(member, key, value)
            db.session.commit()
            flash("Changes saved.", "ok")
            return redirect(url_for("members"))
    return render_template("member_form.html", member=member, fellowships=fellowships_sorted())


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@login_required
def member_delete(member_id):
    member = db.get_or_404(Member, member_id)
    db.session.delete(member)
    db.session.commit()
    flash(f"{member.full_name} deleted.", "ok")
    return redirect(url_for("members"))


# ------------------------------------------------------ fellowship routes
@app.route("/fellowships", methods=["GET", "POST"])
@login_required
def fellowships():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        chairperson = request.form.get("chairperson", "").strip()
        phone_raw = request.form.get("phone_number", "").strip()
        phone_number = normalize_phone(phone_raw) if phone_raw else None

        if not name:
            flash("Enter a fellowship name.", "error")
        elif Fellowship.query.filter(db.func.lower(Fellowship.name) == name.lower()).first():
            flash("That fellowship already exists (ignoring letter case).", "error")
        else:
            db.session.add(Fellowship(name=name, chairperson=chairperson, phone_number=phone_number))
            db.session.commit()
            flash(f"{name} added successfully.", "ok")
        return redirect(url_for("fellowships"))
    return render_template("fellowships.html", fellowships=fellowships_sorted())


@app.route("/fellowships/<int:fid>/edit", methods=["GET", "POST"])
@login_required
def fellowship_edit(fid):
    f = db.get_or_404(Fellowship, fid)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        chairperson = request.form.get("chairperson", "").strip()
        phone_raw = request.form.get("phone_number", "").strip()
        phone_number = normalize_phone(phone_raw) if phone_raw else None

        if not name:
            flash("Enter a fellowship name.", "error")
        else:
            existing = Fellowship.query.filter(db.func.lower(Fellowship.name) == name.lower(), Fellowship.id != fid).first()
            if existing:
                flash("Another fellowship with that name already exists.", "error")
            else:
                f.name = name
                f.chairperson = chairperson
                f.phone_number = phone_number
                db.session.commit()
                flash(f"{name} updated successfully.", "ok")
                return redirect(url_for("fellowships"))
    return render_template("fellowship_edit.html", fellowship=f)


@app.route("/fellowships/<int:fid>/delete", methods=["POST"])
@login_required
def fellowship_delete(fid):
    f = db.get_or_404(Fellowship, fid)
    if f.members:
        flash(f"{f.name} still has {len(f.members)} members. Move them first before deleting.", "error")
    else:
        db.session.delete(f)
        db.session.commit()
        flash(f"{f.name} deleted.", "ok")
    return redirect(url_for("fellowships"))


# ----------------------------------------------------- import / export
@app.route("/import", methods=["GET", "POST"])
@login_required
def import_members():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file first.", "error")
            return redirect(url_for("import_members"))
        text = upload.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        existing = {p for (p,) in db.session.query(Member.phone)}
        groups = {f.name.lower(): f for f in Fellowship.query.all()}
        added = duplicates = invalid = 0
        for raw in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            name = row.get("name") or row.get("full name") or row.get("full_name")
            phone = normalize_phone(row.get("phone") or row.get("telephone"))
            if not name or not phone:
                invalid += 1
                continue
            if phone in existing:
                duplicates += 1
                continue
            group = None
            gname = row.get("fellowship")
            if gname:
                group = groups.get(gname.lower())
                if not group:
                    group = Fellowship(name=gname)
                    db.session.add(group)
                    db.session.flush()
                    groups[gname.lower()] = group
            db.session.add(Member(full_name=name, phone=phone,
                                  fellowship_id=group.id if group else None))
            existing.add(phone)
            added += 1
        db.session.commit()
        flash(f"Imported {added} members. Skipped {duplicates} duplicates and {invalid} rows with a missing name or invalid phone.", "ok")
        return redirect(url_for("members"))
    return render_template("import.html")


@app.route("/export")
@login_required
def export_members():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Name", "Phone", "Fellowship", "Date joined"])
    for m in Member.query.order_by(Member.full_name).all():
        writer.writerow([m.full_name, m.phone, m.fellowship.name if m.fellowship else "",
                         m.date_joined or ""])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=members.csv"})


# ------------------------------------------------------------- messaging
@app.route("/send", methods=["GET", "POST"])
@login_required
def send():
    groups = fellowships_sorted()
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        target = request.form.get("target", "all")
        if not text:
            flash("Write a message first.", "error")
            return redirect(url_for("send"))
        if target == "all":
            recipients, label = Member.query.all(), "Everyone"
        else:
            try:
                group = db.get_or_404(Fellowship, int(target))
            except ValueError:
                abort(400)
            recipients, label = group.members, group.name
        phones = [m.phone for m in recipients]
        if not phones:
            flash("No members in that group yet.", "error")
            return redirect(url_for("send"))
        try:
            accepted = send_sms(phones, text)
            status = "sent" if accepted == len(phones) else f"partial ({accepted} of {len(phones)})"
        except Exception as exc:
            app.logger.exception("SMS sending failed: %s", exc)
            accepted, status = 0, "failed"
        db.session.add(Message(text=text, target=label, recipients=accepted, status=status,
                               sent_by=session.get("admin_name")))
        db.session.commit()
        flash(f"Message to {label}: {status}.", "error" if status == "failed" else "ok")
        return redirect(url_for("history"))
    counts = {g.id: len(g.members) for g in groups}
    return render_template("send.html", fellowships=groups, counts=counts,
                           total=Member.query.count(),
                           provider=os.environ.get("SMS_PROVIDER", "console"))


@app.route("/history")
@login_required
def history():
    rows = Message.query.order_by(Message.sent_at.desc()).limit(200).all()
    return render_template("history.html", messages=rows)


# ----------------------------------------------------------- setup / CLI
def init_db():
    db.create_all()
    if Fellowship.query.count() == 0:
        db.session.add_all(Fellowship(name=n) for n in DEFAULT_FELLOWSHIPS)
        db.session.commit()


@app.cli.command("create-admin")
@click.option("--username", prompt=True)
@click.password_option()
def create_admin(username, password):
    """Create an admin login (or reset its password)."""
    init_db()
    admin = Admin.query.filter_by(username=username).first()
    if admin:
        admin.password_hash = generate_password_hash(password)
        click.echo("Password updated.")
    else:
        db.session.add(Admin(username=username, password_hash=generate_password_hash(password)))
        click.echo("Admin created.")
    db.session.commit()


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")