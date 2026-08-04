-- 教職員（帳號、名冊）
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_no TEXT UNIQUE,
    name TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    is_admin INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 簽收單（管理者從教育局公告挑選出來需要簽收的項目）
CREATE TABLE IF NOT EXISTS bulletins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    target_note TEXT NOT NULL DEFAULT '',
    deadline TEXT,
    created_by INTEGER NOT NULL REFERENCES staff(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 應簽對象（建立簽收單時選定的教職員清單）
CREATE TABLE IF NOT EXISTS bulletin_targets (
    bulletin_id INTEGER NOT NULL REFERENCES bulletins(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    PRIMARY KEY (bulletin_id, staff_id)
);

-- 簽收紀錄
CREATE TABLE IF NOT EXISTS signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bulletin_id INTEGER NOT NULL REFERENCES bulletins(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    signed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    ip_address TEXT NOT NULL DEFAULT '',
    UNIQUE (bulletin_id, staff_id)
);
