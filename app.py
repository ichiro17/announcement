import csv
import io
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import (
    Flask, g, redirect, render_template, request, session, url_for, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "instance"))
DB_PATH = os.path.join(DATA_DIR, "bulletin_signoff.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
PRODUCTION = os.environ.get("PRODUCTION", "0") == "1"

app = Flask(__name__)

_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    if PRODUCTION:
        sys.exit("正式環境必須設定 SECRET_KEY 環境變數，請在部署平台上設定後再啟動。")
    _secret_key = "dev-secret-change-me"
app.secret_key = _secret_key

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCTION,
)

if PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

TAIPEI_TZ = timezone(timedelta(hours=8))


def now_taipei_str():
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def bulletin_source_link(bulletin):
    """Returns (display_text, url) for the source announcement, or None."""
    if bulletin["bulletin_no"]:
        return (
            bulletin["bulletin_no"],
            f"https://bulletin.tn.edu.tw/ViewDetail.aspx?bid={bulletin['bulletin_no']}",
        )
    if bulletin["source_url"]:
        return ("查看原公告", bulletin["source_url"])
    return None


# ---------- 資料庫 ----------

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _migrate(db):
    existing_cols = {row[1] for row in db.execute("PRAGMA table_info(bulletins)")}
    if "bulletin_no" not in existing_cols:
        db.execute("ALTER TABLE bulletins ADD COLUMN bulletin_no TEXT NOT NULL DEFAULT ''")
    db.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        db.executescript(f.read())
    _migrate(db)
    row = db.execute("SELECT COUNT(*) AS c FROM staff").fetchone()
    if row[0] == 0:
        try:
            db.execute(
                """INSERT INTO staff
                   (employee_no, name, username, password_hash, department, title,
                    email, is_admin, must_change_password, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1)""",
                (
                    "0000",
                    "系統管理者",
                    DEFAULT_ADMIN_USERNAME,
                    generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                    "資訊組",
                    "管理者",
                    "",
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            pass  # 另一個 worker 行程已經建立過預設管理者帳號
    db.close()


init_db()


# ---------- 登入 / 權限 ----------

def current_user():
    if "staff_id" not in session:
        return None
    if "_user_cache" not in g:
        g._user_cache = get_db().execute(
            "SELECT * FROM staff WHERE id = ? AND is_active = 1", (session["staff_id"],)
        ).fetchone()
    return g._user_cache


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        if user["must_change_password"] and request.endpoint not in (
            "change_password", "logout",
        ):
            return redirect(url_for("change_password"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        if not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


DEPARTMENT_COLORS = {
    "教務處": "#2563eb",
    "學務處": "#16a34a",
    "總務處": "#d97706",
    "輔導室": "#9333ea",
    "人事室": "#dc2626",
    "會計室": "#0891b2",
    "幼兒園": "#db2777",
}
DEFAULT_DEPARTMENT_COLOR = "#64748b"


def dept_color(department):
    return DEPARTMENT_COLORS.get(department, DEFAULT_DEPARTMENT_COLOR)


@app.context_processor
def inject_user():
    return {
        "current_user": current_user(),
        "today": date.today().isoformat(),
        "dept_color": dept_color,
        "bulletin_source_link": bulletin_source_link,
    }


def _post_login_redirect(user):
    if user["must_change_password"]:
        return redirect(url_for("change_password"))
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("staff_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return _post_login_redirect(current_user())
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM staff WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("帳號或密碼錯誤", "error")
            return render_template("login.html")
        session.clear()
        session["staff_id"] = user["id"]
        return _post_login_redirect(user)
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not check_password_hash(user["password_hash"], current_pw):
            flash("目前密碼不正確", "error")
        elif len(new_pw) < 6:
            flash("新密碼至少需要 6 碼", "error")
        elif new_pw != confirm_pw:
            flash("兩次輸入的新密碼不一致", "error")
        else:
            db = get_db()
            db.execute(
                "UPDATE staff SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (generate_password_hash(new_pw), user["id"]),
            )
            db.commit()
            flash("密碼已更新", "success")
            return redirect(url_for("admin_dashboard" if user["is_admin"] else "staff_dashboard"))
    return render_template("change_password.html", force=bool(user["must_change_password"]))


def _target_summary(db, bulletin_id):
    rows = db.execute(
        """SELECT DISTINCT st.department, st.title
           FROM bulletin_targets t JOIN staff st ON st.id = t.staff_id
           WHERE t.bulletin_id = ?
           ORDER BY st.department, st.title""",
        (bulletin_id,),
    ).fetchall()
    return [(r["department"], r["title"]) for r in rows]


@app.route("/")
def index():
    db = get_db()
    dept_filter = request.args.get("dept", "").strip()
    if dept_filter:
        bulletins = db.execute(
            """SELECT DISTINCT b.* FROM bulletins b
               JOIN bulletin_targets t ON t.bulletin_id = b.id
               JOIN staff st ON st.id = t.staff_id
               WHERE st.department = ?
               ORDER BY b.created_at DESC""",
            (dept_filter,),
        ).fetchall()
    else:
        bulletins = db.execute(
            "SELECT * FROM bulletins ORDER BY created_at DESC"
        ).fetchall()
    today_str = date.today().isoformat()
    target_summaries = {b["id"]: _target_summary(db, b["id"]) for b in bulletins}
    return render_template(
        "index.html",
        bulletins=bulletins,
        today_str=today_str,
        target_summaries=target_summaries,
        dept_tabs=list(DEPARTMENT_COLORS.keys()),
        dept_filter=dept_filter,
    )


# ---------- 一般教職員 ----------

@app.route("/dashboard")
@login_required
def staff_dashboard():
    user = current_user()
    db = get_db()
    pending = db.execute(
        """SELECT b.* FROM bulletins b
           JOIN bulletin_targets t ON t.bulletin_id = b.id
           WHERE t.staff_id = ?
             AND NOT EXISTS (
                 SELECT 1 FROM signatures s
                 WHERE s.bulletin_id = b.id AND s.staff_id = ?
             )
           ORDER BY (b.deadline IS NULL), b.deadline ASC, b.created_at DESC""",
        (user["id"], user["id"]),
    ).fetchall()
    return render_template("staff/dashboard.html", pending=pending)


@app.route("/history")
@login_required
def staff_history():
    user = current_user()
    db = get_db()
    signed = db.execute(
        """SELECT b.*, s.signed_at FROM bulletins b
           JOIN signatures s ON s.bulletin_id = b.id
           WHERE s.staff_id = ?
           ORDER BY s.signed_at DESC""",
        (user["id"],),
    ).fetchall()
    return render_template("staff/history.html", signed=signed)


@app.route("/bulletins/<int:bulletin_id>/sign", methods=["POST"])
@login_required
def sign_bulletin(bulletin_id):
    user = current_user()
    db = get_db()
    is_target = db.execute(
        "SELECT 1 FROM bulletin_targets WHERE bulletin_id = ? AND staff_id = ?",
        (bulletin_id, user["id"]),
    ).fetchone()
    if not is_target:
        abort(403)
    already = db.execute(
        "SELECT 1 FROM signatures WHERE bulletin_id = ? AND staff_id = ?",
        (bulletin_id, user["id"]),
    ).fetchone()
    if not already:
        db.execute(
            "INSERT INTO signatures (bulletin_id, staff_id, ip_address, signed_at) VALUES (?, ?, ?, ?)",
            (bulletin_id, user["id"], request.remote_addr or "", now_taipei_str()),
        )
        db.commit()
        flash("已完成簽收", "success")
    return redirect(url_for("staff_dashboard"))


@app.route("/bulletins/<int:bulletin_id>/forward", methods=["GET", "POST"])
@login_required
def forward_bulletin(bulletin_id):
    user = current_user()
    if user["title"] != "主任":
        abort(403)
    db = get_db()
    bulletin = db.execute("SELECT * FROM bulletins WHERE id = ?", (bulletin_id,)).fetchone()
    if bulletin is None:
        abort(404)
    signed = db.execute(
        "SELECT 1 FROM signatures WHERE bulletin_id = ? AND staff_id = ?",
        (bulletin_id, user["id"]),
    ).fetchone()
    if not signed:
        abort(403)

    if request.method == "POST":
        selected_ids = request.form.getlist("target_ids")
        if not selected_ids:
            flash("請至少選擇一位組長", "error")
        else:
            db.executemany(
                "INSERT OR IGNORE INTO bulletin_targets (bulletin_id, staff_id) VALUES (?, ?)",
                [(bulletin_id, sid) for sid in selected_ids],
            )
            db.commit()
            flash("已轉發給選定的組長，他們會在自己的待簽收清單看到這則公告", "success")
            return redirect(url_for("staff_history"))

    colleagues = db.execute(
        """SELECT * FROM staff
           WHERE is_active = 1 AND department = ? AND id != ? AND title != '主任'
           ORDER BY title, name""",
        (user["department"], user["id"]),
    ).fetchall()
    current_ids = {
        r["staff_id"] for r in db.execute(
            "SELECT staff_id FROM bulletin_targets WHERE bulletin_id = ?", (bulletin_id,)
        ).fetchall()
    }
    return render_template(
        "staff/forward.html", bulletin=bulletin, colleagues=colleagues, current_ids=current_ids
    )


# ---------- 管理者：簽收單 ----------

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    bulletins = db.execute(
        """SELECT b.*,
                  (SELECT COUNT(*) FROM bulletin_targets t WHERE t.bulletin_id = b.id) AS total_targets,
                  (SELECT COUNT(*) FROM signatures s WHERE s.bulletin_id = b.id) AS signed_count
           FROM bulletins b
           ORDER BY b.created_at DESC"""
    ).fetchall()
    today_str = date.today().isoformat()
    return render_template("admin/dashboard.html", bulletins=bulletins, today_str=today_str)


def _staff_groups(db):
    departments = [r["department"] for r in db.execute(
        "SELECT DISTINCT department FROM staff WHERE is_active = 1 AND department != '' ORDER BY department"
    ).fetchall()]
    titles = [r["title"] for r in db.execute(
        "SELECT DISTINCT title FROM staff WHERE is_active = 1 AND title != '' ORDER BY title"
    ).fetchall()]
    return departments, titles


@app.route("/admin/bulletins/new", methods=["GET", "POST"])
@admin_required
def admin_bulletin_new():
    db = get_db()
    all_staff = db.execute(
        "SELECT * FROM staff WHERE is_active = 1 ORDER BY department, title, name"
    ).fetchall()
    departments, titles = _staff_groups(db)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        bulletin_no = request.form.get("bulletin_no", "").strip()
        deadline = request.form.get("deadline", "").strip() or None
        target_ids = request.form.getlist("target_ids")

        if not bulletin_no:
            flash("請填寫公告編號", "error")
        elif not title:
            flash("請填寫標題", "error")
        elif not target_ids:
            flash("請至少選擇一位應簽對象", "error")
        else:
            user = current_user()
            cur = db.execute(
                """INSERT INTO bulletins (title, bulletin_no, deadline, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (title, bulletin_no, deadline, user["id"], now_taipei_str()),
            )
            bulletin_id = cur.lastrowid
            db.executemany(
                "INSERT INTO bulletin_targets (bulletin_id, staff_id) VALUES (?, ?)",
                [(bulletin_id, sid) for sid in target_ids],
            )
            db.commit()
            flash("簽收單已建立", "success")
            return redirect(url_for("admin_bulletin_detail", bulletin_id=bulletin_id))

    return render_template(
        "admin/bulletin_new.html",
        all_staff=all_staff,
        departments=departments,
        titles=titles,
    )


@app.route("/admin/bulletins/<int:bulletin_id>")
@admin_required
def admin_bulletin_detail(bulletin_id):
    db = get_db()
    bulletin = db.execute("SELECT * FROM bulletins WHERE id = ?", (bulletin_id,)).fetchone()
    if bulletin is None:
        abort(404)
    targets = db.execute(
        """SELECT st.id, st.name, st.department, st.title, sig.signed_at
           FROM bulletin_targets t
           JOIN staff st ON st.id = t.staff_id
           LEFT JOIN signatures sig ON sig.bulletin_id = t.bulletin_id AND sig.staff_id = t.staff_id
           WHERE t.bulletin_id = ?
           ORDER BY (sig.signed_at IS NULL) DESC, st.department, st.name""",
        (bulletin_id,),
    ).fetchall()
    today_str = date.today().isoformat()
    target_summary = _target_summary(db, bulletin_id)
    return render_template(
        "admin/bulletin_detail.html",
        bulletin=bulletin,
        targets=targets,
        today_str=today_str,
        target_summary=target_summary,
    )


@app.route("/admin/bulletins/<int:bulletin_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_bulletin_edit(bulletin_id):
    db = get_db()
    bulletin = db.execute("SELECT * FROM bulletins WHERE id = ?", (bulletin_id,)).fetchone()
    if bulletin is None:
        abort(404)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        bulletin_no = request.form.get("bulletin_no", "").strip()
        deadline = request.form.get("deadline", "").strip() or None

        if not title:
            flash("請填寫標題", "error")
            return render_template("admin/bulletin_edit.html", bulletin=bulletin)

        db.execute(
            """UPDATE bulletins SET title = ?, bulletin_no = ?, deadline = ?
               WHERE id = ?""",
            (title, bulletin_no, deadline, bulletin_id),
        )
        db.commit()
        flash("已更新公告內容", "success")
        return redirect(url_for("admin_bulletin_detail", bulletin_id=bulletin_id))

    return render_template("admin/bulletin_edit.html", bulletin=bulletin)


@app.route("/admin/bulletins/<int:bulletin_id>/delete", methods=["POST"])
@admin_required
def admin_bulletin_delete(bulletin_id):
    db = get_db()
    bulletin = db.execute("SELECT * FROM bulletins WHERE id = ?", (bulletin_id,)).fetchone()
    if bulletin is None:
        abort(404)
    db.execute("DELETE FROM signatures WHERE bulletin_id = ?", (bulletin_id,))
    db.execute("DELETE FROM bulletin_targets WHERE bulletin_id = ?", (bulletin_id,))
    db.execute("DELETE FROM bulletins WHERE id = ?", (bulletin_id,))
    db.commit()
    flash(f"已刪除簽收單「{bulletin['title']}」", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/bulletins/<int:bulletin_id>/targets", methods=["GET", "POST"])
@admin_required
def admin_bulletin_targets(bulletin_id):
    db = get_db()
    bulletin = db.execute("SELECT * FROM bulletins WHERE id = ?", (bulletin_id,)).fetchone()
    if bulletin is None:
        abort(404)

    if request.method == "POST":
        target_ids = set(int(x) for x in request.form.getlist("target_ids"))
        if not target_ids:
            flash("請至少選擇一位應簽對象", "error")
        else:
            current_ids = {
                r["staff_id"] for r in db.execute(
                    "SELECT staff_id FROM bulletin_targets WHERE bulletin_id = ?", (bulletin_id,)
                ).fetchall()
            }
            to_add = target_ids - current_ids
            to_remove = current_ids - target_ids
            if to_add:
                db.executemany(
                    "INSERT INTO bulletin_targets (bulletin_id, staff_id) VALUES (?, ?)",
                    [(bulletin_id, sid) for sid in to_add],
                )
            if to_remove:
                db.executemany(
                    "DELETE FROM bulletin_targets WHERE bulletin_id = ? AND staff_id = ?",
                    [(bulletin_id, sid) for sid in to_remove],
                )
                db.executemany(
                    "DELETE FROM signatures WHERE bulletin_id = ? AND staff_id = ?",
                    [(bulletin_id, sid) for sid in to_remove],
                )
            db.commit()
            flash("已更新應簽對象", "success")
            return redirect(url_for("admin_bulletin_detail", bulletin_id=bulletin_id))

    all_staff = db.execute(
        "SELECT * FROM staff WHERE is_active = 1 ORDER BY department, title, name"
    ).fetchall()
    current_ids = {
        r["staff_id"] for r in db.execute(
            "SELECT staff_id FROM bulletin_targets WHERE bulletin_id = ?", (bulletin_id,)
        ).fetchall()
    }
    departments, titles = _staff_groups(db)
    return render_template(
        "admin/bulletin_targets.html",
        bulletin=bulletin,
        all_staff=all_staff,
        current_ids=current_ids,
        departments=departments,
        titles=titles,
    )


# ---------- 管理者：教職員名冊 ----------

@app.route("/admin/staff")
@admin_required
def admin_staff_list():
    db = get_db()
    staff = db.execute("SELECT * FROM staff ORDER BY is_active DESC, department, name").fetchall()
    return render_template("admin/staff_list.html", staff=staff)


@app.route("/admin/staff/new", methods=["GET", "POST"])
@admin_required
def admin_staff_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        employee_no = request.form.get("employee_no", "").strip() or None
        department = request.form.get("department", "").strip()
        title = request.form.get("title", "").strip()
        email = request.form.get("email", "").strip()
        is_admin = 1 if request.form.get("is_admin") == "on" else 0

        if not name or not username:
            flash("姓名與帳號為必填", "error")
            return render_template("admin/staff_form.html", staff=None)

        db = get_db()
        exists = db.execute("SELECT 1 FROM staff WHERE username = ?", (username,)).fetchone()
        if exists:
            flash("此帳號已存在", "error")
            return render_template("admin/staff_form.html", staff=None)

        if employee_no:
            dup_no = db.execute(
                "SELECT 1 FROM staff WHERE employee_no = ?", (employee_no,)
            ).fetchone()
            if dup_no:
                flash("此員工編號已被其他教職員使用", "error")
                return render_template("admin/staff_form.html", staff=None)

        default_password = employee_no if employee_no else "changeme123"
        db.execute(
            """INSERT INTO staff
               (employee_no, name, username, password_hash, department, title, email,
                is_admin, must_change_password, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            (
                employee_no, name, username, generate_password_hash(default_password),
                department, title, email, is_admin,
            ),
        )
        db.commit()
        flash(f"已新增教職員，預設密碼為「{default_password}」，請告知本人並提醒首次登入需修改密碼", "success")
        return redirect(url_for("admin_staff_list"))

    return render_template("admin/staff_form.html", staff=None)


@app.route("/admin/staff/<int:staff_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_staff_edit(staff_id):
    db = get_db()
    staff = db.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        employee_no = request.form.get("employee_no", "").strip() or None
        department = request.form.get("department", "").strip()
        title = request.form.get("title", "").strip()
        email = request.form.get("email", "").strip()
        is_admin = 1 if request.form.get("is_admin") == "on" else 0
        is_active = 1 if request.form.get("is_active") == "on" else 0

        if not name or not username:
            flash("姓名與帳號為必填", "error")
            return render_template("admin/staff_form.html", staff=staff)

        dup = db.execute(
            "SELECT 1 FROM staff WHERE username = ? AND id != ?", (username, staff_id)
        ).fetchone()
        if dup:
            flash("此帳號已被其他教職員使用", "error")
            return render_template("admin/staff_form.html", staff=staff)

        if employee_no:
            dup_no = db.execute(
                "SELECT 1 FROM staff WHERE employee_no = ? AND id != ?", (employee_no, staff_id)
            ).fetchone()
            if dup_no:
                flash("此員工編號已被其他教職員使用", "error")
                return render_template("admin/staff_form.html", staff=staff)

        db.execute(
            """UPDATE staff SET name = ?, username = ?, employee_no = ?, department = ?,
               title = ?, email = ?, is_admin = ?, is_active = ? WHERE id = ?""",
            (name, username, employee_no, department, title, email, is_admin, is_active, staff_id),
        )
        db.commit()
        flash("已更新教職員資料", "success")
        return redirect(url_for("admin_staff_list"))

    return render_template("admin/staff_form.html", staff=staff)


@app.route("/admin/staff/<int:staff_id>/reset-password", methods=["POST"])
@admin_required
def admin_staff_reset_password(staff_id):
    db = get_db()
    staff = db.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff is None:
        abort(404)
    default_password = staff["employee_no"] if staff["employee_no"] else "changeme123"
    db.execute(
        "UPDATE staff SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (generate_password_hash(default_password), staff_id),
    )
    db.commit()
    flash(f"已重設密碼為「{default_password}」，該教職員下次登入需重新設定密碼", "success")
    return redirect(url_for("admin_staff_list"))


@app.route("/admin/staff/<int:staff_id>/delete", methods=["POST"])
@admin_required
def admin_staff_delete(staff_id):
    db = get_db()
    staff = db.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if staff is None:
        abort(404)

    if staff_id == current_user()["id"]:
        flash("無法刪除自己目前登入中的帳號", "error")
        return redirect(url_for("admin_staff_list"))

    in_use = db.execute(
        """SELECT
             (SELECT COUNT(*) FROM bulletin_targets WHERE staff_id = ?) +
             (SELECT COUNT(*) FROM signatures WHERE staff_id = ?) +
             (SELECT COUNT(*) FROM bulletins WHERE created_by = ?) AS cnt""",
        (staff_id, staff_id, staff_id),
    ).fetchone()["cnt"]
    if in_use:
        flash("此教職員已有簽收單或簽收紀錄相關資料，無法刪除，請改用「停用」帳號", "error")
        return redirect(url_for("admin_staff_list"))

    db.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
    db.commit()
    flash(f"已刪除教職員「{staff['name']}」（{staff['username']}）", "success")
    return redirect(url_for("admin_staff_list"))


@app.route("/admin/staff/import", methods=["GET", "POST"])
@admin_required
def admin_staff_import():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("請選擇要匯入的 CSV 檔案", "error")
            return render_template("admin/staff_import.html")

        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        required_cols = {"員工編號", "姓名", "帳號", "單位", "職稱"}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            flash(
                f"CSV 欄位需包含：{'、'.join(required_cols)}（Email 欄位選填）",
                "error",
            )
            return render_template("admin/staff_import.html")

        db = get_db()
        created, updated = 0, 0
        for row in reader:
            employee_no = (row.get("員工編號") or "").strip() or None
            name = (row.get("姓名") or "").strip()
            username = (row.get("帳號") or "").strip()
            department = (row.get("單位") or "").strip()
            title = (row.get("職稱") or "").strip()
            email = (row.get("Email") or "").strip()
            if not name or not username:
                continue

            existing = db.execute(
                "SELECT id FROM staff WHERE username = ?", (username,)
            ).fetchone()
            if existing:
                db.execute(
                    """UPDATE staff SET employee_no = ?, name = ?, department = ?,
                       title = ?, email = ?, is_active = 1 WHERE id = ?""",
                    (employee_no, name, department, title, email, existing["id"]),
                )
                updated += 1
            else:
                default_password = employee_no if employee_no else "changeme123"
                db.execute(
                    """INSERT INTO staff
                       (employee_no, name, username, password_hash, department, title,
                        email, is_admin, must_change_password, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 1)""",
                    (
                        employee_no, name, username,
                        generate_password_hash(default_password),
                        department, title, email,
                    ),
                )
                created += 1
        db.commit()
        flash(f"匯入完成：新增 {created} 筆、更新 {updated} 筆", "success")
        return redirect(url_for("admin_staff_list"))

    return render_template("admin/staff_import.html")


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", message="沒有權限執行此操作"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="找不到這個頁面"), 404


if __name__ == "__main__":
    app.run(debug=not PRODUCTION, host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
