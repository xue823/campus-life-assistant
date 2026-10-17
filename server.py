# -*- coding: utf-8 -*-
"""
校园生活 AI 助手 —— 服务端（课表 / 干饭推荐 / 记账分析 / 校园问答）

技术栈：Python 3 标准库（http.server + sqlite3），零第三方依赖，离线可用。
启动：python server.py --port 8866

设计要点：
  1. 冷启动自带演示数据：一份完整课表 + 一周消费记录，打开就有东西看；
  2. 推荐与问答都是本地算法（规则 + 权重打分 + 关键词检索），断网也能演示；
  3. 支持接入大模型（设置环境变量 LLM_API_KEY 后启用），不设置则使用本地知识库。
"""
import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from data_seed import (CAMPUS_LINKS, CANTEENS, DISHES, FAQ, MEAL_SLOTS,
                       PERIOD_TIME, QUICK_QUESTIONS, SCHEDULE, SCHOOL, STUDENT,
                       WEEKDAYS)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
DB_PATH = os.path.join(BASE_DIR, "data", "campus.db")

SESSION_TTL = 30 * 24 * 3600
PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

BUDGET_MONTH = 1500.0     # 月度预算（元）
BUDGET_DAY = 45.0         # 每日建议上限（元）


# =============================================================== 数据库
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sid      TEXT UNIQUE NOT NULL,
    name     TEXT NOT NULL,
    major    TEXT NOT NULL,
    pwd_hash TEXT NOT NULL,
    salt     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    college TEXT,
    day     TEXT NOT NULL,
    start_p INTEGER NOT NULL,
    end_p   INTEGER NOT NULL,
    place   TEXT,
    teacher TEXT,
    kind    TEXT,
    credit  REAL DEFAULT 0,
    weeks   TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS expenses (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount  REAL NOT NULL,
    category TEXT NOT NULL,
    place   TEXT,
    note    TEXT,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role    TEXT NOT NULL,
    text    TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token    TEXT PRIMARY KEY,
    user_id  INTEGER NOT NULL,
    created  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
    user_id      INTEGER PRIMARY KEY,
    taste        TEXT DEFAULT '',
    budget_meal  REAL DEFAULT 15,
    avoid_wait   INTEGER DEFAULT 15,
    canteen      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS students (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    sid     TEXT DEFAULT '',
    contact TEXT DEFAULT '',
    dept    TEXT DEFAULT '',
    role    TEXT DEFAULT '同学',
    note    TEXT DEFAULT '',
    ts      TEXT NOT NULL
);
"""


def hash_pwd(pwd, salt):
    return hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 120_000).hex()


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def seed_version():
    """种子数据指纹：数据改了以后重启服务会自动重灌课表。"""
    raw = repr(SCHEDULE) + repr(STUDENT)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def migrate(conn):
    """轻量迁移：老版本数据库缺少的列自动补上，避免升级后打不开。"""
    def cols(table):
        return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}

    if "weeks" not in cols("courses"):
        conn.execute("ALTER TABLE courses ADD COLUMN weeks TEXT")
    conn.commit()


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    migrate(conn)
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        salt = secrets.token_hex(8)
        conn.execute(
            "INSERT INTO users (sid, name, major, pwd_hash, salt) VALUES (?,?,?,?,?)",
            (STUDENT["sid"],
             STUDENT["name"],
             "%s（%s）" % (STUDENT["major"], STUDENT["class_name"]),
             hash_pwd("123456", salt), salt))
        uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        seed_courses(conn, uid)
        seed_students(conn, uid)
        conn.execute("INSERT INTO prefs (user_id, taste, budget_meal, avoid_wait)"
                     " VALUES (?,?,?,?)", (uid, "咸香,辣", 16.0, 12))
        seed_expenses(conn, uid)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed', ?)",
                     (seed_version(),))
    else:
        # 种子课表更新后自动重灌（保留记账与问答历史）
        row = conn.execute("SELECT value FROM meta WHERE key='seed'").fetchone()
        if not row or row["value"] != seed_version():
            uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
            conn.execute("DELETE FROM courses WHERE user_id=?", (uid,))
            seed_courses(conn, uid)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed', ?)",
                         (seed_version(),))
    conn.commit()
    conn.close()


def seed_courses(conn, uid):
    for c in SCHEDULE:
        name, teacher, day, sp, ep, weeks, place, kind, credit = c
        conn.execute(
            "INSERT INTO courses (user_id,name,college,day,start_p,end_p,place,"
            "teacher,kind,credit,weeks) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, name, STUDENT["college"], day, sp, ep, place, teacher, kind,
             credit, weeks))


def seed_students(conn, uid):
    """同学名单：给一份示例名单，方便演示增删改查。"""
    rows = [
        ("张伟", "20250101", "138****2201", "计算机与人工智能学院", "班长", "负责通知发布"),
        ("李娜", "20250102", "139****3312", "计算机与人工智能学院", "学习委员", ""),
        ("王强", "20250103", "137****5566", "计算机与人工智能学院", "同学", "同宿舍"),
        ("刘洋", "20250104", "135****7788", "计算机与人工智能学院", "同学", ""),
        ("陈静", "20250105", "136****9900", "外国语学院", "同学", "英语角搭档"),
    ]
    for name, sid, contact, dept, role, note in rows:
        conn.execute(
            "INSERT INTO students (user_id,name,sid,contact,dept,role,note,ts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (uid, name, sid, contact, dept, role, note, now_str()))


def register_user(conn, sid, name, major, pwd):
    """注册新账号：给示例课表 + 示例同学名单，记账从零开始。"""
    salt = secrets.token_hex(8)
    cur = conn.execute(
        "INSERT INTO users (sid, name, major, pwd_hash, salt) VALUES (?,?,?,?,?)",
        (sid, name, major, hash_pwd(pwd, salt), salt))
    uid = cur.lastrowid
    conn.execute("INSERT INTO prefs (user_id, taste, budget_meal, avoid_wait)"
                 " VALUES (?,?,?,?)", (uid, "", 15.0, 15))
    seed_courses(conn, uid)
    seed_students(conn, uid)
    conn.commit()
    return uid


def seed_expenses(conn, uid):
    """造 14 天消费记录：早餐 5 元档、午餐 13 元档、晚餐 12 元档 + 少量其他消费。"""
    today = datetime.now()
    random_like = [13.0, 11.0, 12.0, 18.0, 10.0, 14.0, 16.0, 12.0]
    for d in range(13, -1, -1):
        day = today - timedelta(days=d)
        ds = day.strftime("%Y-%m-%d")
        if day.weekday() >= 5 and d % 3 == 1:      # 周末偶尔没记录
            continue
        recs = [
            (5.0 + (d % 3), "早餐", "第一食堂", "豆浆油条", ds + " 07:35:00"),
            (random_like[d % len(random_like)], "午餐",
             ["第一食堂", "第二食堂", "第三食堂"][d % 3], "午饭", ds + " 12:10:00"),
            (10.0 + (d % 5), "晚餐",
             ["第三食堂", "西区小吃街"][d % 2], "晚饭", ds + " 17:50:00"),
        ]
        if d % 4 == 0:
            recs.append((9.0, "饮品", "图书馆咖啡角", "咖啡",
                         ds + " 15:20:00"))
        if d % 5 == 0:
            recs.append((23.0, "购物", "校园超市", "日用品",
                         ds + " 20:05:00"))
        for amount, cat, place, note, ts in recs:
            conn.execute("INSERT INTO expenses (user_id, amount, category, place,"
                         " note, ts) VALUES (?,?,?,?,?,?)",
                         (uid, amount, cat, place, note, ts))


# =============================================================== 业务算法
def period_range(start_p, end_p):
    return PERIOD_TIME.get(start_p, ("08:00", "08:45"))[0], \
           PERIOD_TIME.get(end_p, ("08:45", "09:30"))[1]


def parse_weeks(text):
    """把「1-16 周」「单周 1-16 周」「3·7·11·14 周」解析成周次集合；空串表示每周。"""
    t = (text or "").strip()
    if not t:
        return None
    parity = "单" if "单" in t else ("双" if "双" in t else None)
    nums = set()
    for m in re.finditer(r"(\d+)\s*[-—~]\s*(\d+)", t):
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        nums.update(range(a, min(b, 30) + 1))
    for m in re.finditer(r"\d+", re.sub(r"\d+\s*[-—~]\s*\d+", " ", t)):
        n = int(m.group())
        if 1 <= n <= 30:
            nums.add(n)
    if not nums:
        return None
    if parity == "单":
        nums = {n for n in nums if n % 2 == 1}
    elif parity == "双":
        nums = {n for n in nums if n % 2 == 0}
    return nums


def current_week(now=None):
    """第几教学周：以 9 月 1 日为第 1 周起点（演示用规则）。"""
    now = now or datetime.now()
    start = datetime(now.year, 9, 1)
    if now < start:
        start = datetime(now.year - 1, 9, 1)
    return max(1, (now - start).days // 7 + 1)


def exam_sort_key(offset, course):
    return (offset, course["start_p"])


def course_to_dict(row):
    begin, end = period_range(row["start_p"], row["end_p"])
    try:
        weeks = row["weeks"]
    except (IndexError, KeyError):
        weeks = ""
    return {
        "id": row["id"], "name": row["name"], "college": row["college"],
        "day": row["day"], "start_p": row["start_p"], "end_p": row["end_p"],
        "place": row["place"], "teacher": row["teacher"], "kind": row["kind"],
        "credit": row["credit"], "begin": begin, "end": end,
        "weeks": weeks or "",
        "periods": "%d-%d 节" % (row["start_p"], row["end_p"]),
    }


def schedule_payload(conn, uid, now=None, week_no=None):
    now = now or datetime.now()
    week_no = week_no or current_week(now)
    rows = conn.execute("SELECT * FROM courses WHERE user_id=? ORDER BY id",
                        (uid,)).fetchall()
    all_courses = [course_to_dict(r) for r in rows]
    for c in all_courses:
        w = parse_weeks(c["weeks"])
        c["week_ok"] = True if w is None else week_no in w

    courses = [c for c in all_courses if c["week_ok"]]
    today = WEEKDAYS[now.weekday()]
    now_hm = now.strftime("%H:%M")
    today_courses = sorted([c for c in courses if c["day"] == today],
                           key=lambda c: c["start_p"])

    next_course, status = None, "今天没有考核安排"
    for c in today_courses:
        if c["end"] >= now_hm:
            next_course = c
            if c["begin"] <= now_hm <= c["end"]:
                status = "正在考试"
            else:
                status = "距离下一场还有 %d 分钟" % minutes_between(now_hm, c["begin"])
            break
    else:
        if today_courses:
            status = "今天的安排已全部结束"

    # 下一场考核（跨天）
    upcoming = None
    for offset in range(0, 8):
        day = WEEKDAYS[(now.weekday() + offset) % 7]
        if offset == 0:
            cand = [c for c in today_courses if c["end"] >= now_hm]
        else:
            cand = sorted([c for c in courses if c["day"] == day],
                          key=lambda c: c["start_p"])
        if cand:
            upcoming = dict(cand[0])
            upcoming["in_days"] = offset
            break

    seen, uniq = set(), []
    for c in all_courses:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        uniq.append(c)
    total_credit = sum(c["credit"] or 0 for c in uniq)
    course_count = len(uniq)

    return {
        "today": today, "week_no": week_no, "student": STUDENT,
        "courses": courses, "all_courses": all_courses,
        "today_courses": today_courses, "next": next_course,
        "upcoming": upcoming, "status": status,
        "total_credit": round(total_credit, 1), "course_count": course_count,
        "week_map": {d: [c for c in courses if c["day"] == d] for d in WEEKDAYS},
        "period_time": {str(k): v for k, v in PERIOD_TIME.items()},
    }


def minutes_between(hm1, hm2):
    def to_min(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    return max(0, to_min(hm2) - to_min(hm1))


def current_meal_slot(hm=None):
    hm = hm or datetime.now().strftime("%H:%M")
    for name, begin, end in MEAL_SLOTS:
        if begin <= hm <= end:
            return name, begin, end
    return "夜宵", "21:00", "23:30"


def recommend_meals(hm=None, taste="", budget=None, canteen="", avoid_wait=None,
                    limit=3):
    """本地推荐算法：时间匹配 + 口味匹配 + 预算惩罚 + 排队惩罚 + 评分加权。"""
    slot, _, _ = current_meal_slot(hm)
    slot_map = {"早餐": ["早餐"], "午餐": ["正餐", "盖饭", "面食", "快餐", "套餐"],
                "下午茶": ["饮品", "甜品", "小吃"],
                "晚餐": ["正餐", "盖饭", "粉面", "汤面", "轻食", "面食"],
                "夜宵": ["夜宵", "小吃", "粉面"]}
    wanted = slot_map.get(slot, [])
    tastes = [t for t in re.split(r"[,，、\s]+", taste or "") if t]

    scored = []
    for name, canteen_name, window, price, kind, tags, wait, rating in DISHES:
        score = rating * 12.0
        if kind in wanted:
            score += 22
        elif kind in ("早餐", "夜宵"):
            score -= 6
        score += sum(8 for t in tastes if t in tags)
        if canteen and canteen_name == canteen:
            score += 30
        elif canteen and canteen_name != canteen:
            score -= 45
        if budget:
            if price <= budget:
                score += 10 - abs(budget - price) * 0.4
            else:
                score -= (price - budget) * 3.0
        if avoid_wait is not None:
            score -= max(0, wait - avoid_wait) * 2.2
        if slot == "早餐" and kind != "早餐":
            score -= 30
        if slot == "夜宵" and kind not in ("夜宵", "小吃", "粉面"):
            score -= 25
        scored.append({
            "name": name, "canteen": canteen_name, "window": window,
            "price": price, "kind": kind, "tags": tags, "wait": wait,
            "rating": rating, "score": round(score, 1),
        })
    scored.sort(key=lambda x: -x["score"])
    top = scored[:limit]
    for i, item in enumerate(top, 1):
        item["rank"] = i
        item["reason"] = meal_reason(item, slot, tastes, budget)
    return {"slot": slot, "list": top, "all_count": len(scored),
            "canteens": sorted({d[1] for d in DISHES})}


def meal_reason(item, slot, tastes, budget):
    bits = ["现在适合吃%s" % slot]
    hit = [t for t in tastes if t in item["tags"]]
    if hit:
        bits.append("口味符合你偏好的「%s」" % "/".join(hit))
    if budget and item["price"] <= budget:
        bits.append("价格 %.0f 元在预算内" % item["price"])
    if item["wait"] <= 6:
        bits.append("出餐快（约 %d 分钟）" % item["wait"])
    bits.append("评分 %.1f" % item["rating"])
    return "；".join(bits)


def answer_question(question, uid=None, conn=None):
    """本地校园问答：关键词打分检索 + 大模型可选增强。"""
    q = (question or "").strip()
    if not q:
        return {"answer": "你想问点什么？比如“校园网连不上怎么办”。", "source": "本地知识库"}

    best, best_score = None, 0
    for keywords, title, answer, cat in FAQ:
        score = 0
        for kw in keywords:
            if kw.lower() in q.lower():
                score += 3 if len(kw) > 1 else 1
        if title and any(part in q for part in re.split(r"[（(]", title)[:1]):
            score += 2
        if score > best_score:
            best, best_score = (title, answer, cat, keywords), score

    llm = try_llm(q)
    if llm:
        return {"answer": llm, "source": "大模型", "title": best[0] if best else ""}
    if best and best_score >= 3:
        return {"answer": best[1], "title": best[0], "category": best[2],
                "source": "本地知识库", "matched": [k for k in best[3]
                                                    if k.lower() in q.lower()]}
    if best and best_score > 0:
        return {"answer": best[1], "title": best[0], "category": best[2],
                "source": "本地知识库（相近问题）",
                "tip": "没有完全匹配的问题，先给你最接近的一条。"}
    return {
        "answer": "这个问题本地知识库暂时没有收录。我已知的领域是：图书馆、校园卡、"
                  "校园网、教务成绩、快递、请假、校医院、奖学金、考研自习室、社团。",
        "source": "本地知识库",
        "tip": "换个说法再问，或点下方的常见问题试试。",
    }


def try_llm(question):
    """可选：配置 LLM_API_KEY 后调用大模型；失败时静默降级到本地知识库。"""
    key = os.environ.get("LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not key:
        return None
    try:
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "你是某高校的校园生活助手，回答简洁、"
                                              "给出可执行步骤，不超过 150 字。"},
                {"role": "user", "content": question},
            ],
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def expense_payload(conn, uid, days=30):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM expenses WHERE user_id=? AND date(ts) >= date(?)"
        " ORDER BY ts DESC", (uid, since)).fetchall()
    items = [dict(r) for r in rows]
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")
    today_total = round(sum(i["amount"] for i in items if i["ts"][:10] == today), 2)
    month_total = round(sum(i["amount"] for i in items if i["ts"][:7] == month), 2)

    by_cat, by_day, by_place = {}, {}, {}
    for i in items:
        by_cat[i["category"]] = round(by_cat.get(i["category"], 0) + i["amount"], 2)
        by_day[i["ts"][:10]] = round(by_day.get(i["ts"][:10], 0) + i["amount"], 2)
        if i["place"]:
            by_place[i["place"]] = round(by_place.get(i["place"], 0) + i["amount"], 2)

    day_list = sorted(by_day.items())
    recent7 = day_list[-7:]
    avg7 = round(sum(v for _, v in recent7) / len(recent7), 2) if recent7 else 0
    left_month = round(BUDGET_MONTH - month_total, 2)
    days_left = max(1, 30 - int(datetime.now().strftime("%d")))
    daily_allow = round(left_month / days_left, 2) if left_month > 0 else 0

    advice = []
    if today_total > BUDGET_DAY:
        advice.append("今天已花 %.0f 元，超过建议上限 %.0f 元，晚饭可以考虑食堂套餐。"
                      % (today_total, BUDGET_DAY))
    if left_month <= 0:
        advice.append("本月预算已经用完（%d 天），接下来建议只保留三餐刚需支出。"
                      % days_left)
    elif daily_allow < 45:
        advice.append("本月剩余 %.0f 元、还有 %d 天，日均建议控制在 %.0f 元以内。"
                      % (left_month, days_left, daily_allow))
    if by_cat:
        top_cat = max(by_cat.items(), key=lambda x: x[1])
        advice.append("花得最多的是「%s」，共 %.0f 元，占 %.0f%%。"
                      % (top_cat[0], top_cat[1],
                         top_cat[1] * 100 / sum(by_cat.values())))
    if by_place:
        top_place = max(by_place.items(), key=lambda x: x[1])
        advice.append("最常消费的地点是「%s」（%.0f 元）。" % top_place)
    if avg7 and avg7 > BUDGET_DAY * 0.8 and today_total <= BUDGET_DAY:
        advice.append("近 7 天日均 %.0f 元，偏高，建议自己带一两次早餐。" % avg7)

    return {
        "items": items[:80], "today_total": today_total, "month_total": month_total,
        "budget_month": BUDGET_MONTH, "budget_day": BUDGET_DAY,
        "left_month": left_month, "daily_allow": daily_allow, "avg7": avg7,
        "by_cat": sorted([{"name": k, "value": v} for k, v in by_cat.items()],
                         key=lambda x: -x["value"]),
        "by_day": [{"date": k, "value": v} for k, v in day_list],
        "by_place": sorted([{"name": k, "value": v} for k, v in by_place.items()],
                           key=lambda x: -x["value"])[:5],
        "advice": advice,
    }


# =============================================================== HTTP
def render(name, **ctx):
    with open(os.path.join(TEMPLATE_DIR, name), "r", encoding="utf-8") as fh:
        html = fh.read()
    for k, v in ctx.items():
        html = html.replace("{{%s}}" % k.upper(), str(v))
    return html


class Handler(BaseHTTPRequestHandler):
    server_version = "CampusLife/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("APP_VERBOSE"):
            sys.stderr.write("[%s] %s\n" % (now_str(), fmt % args))

    # ---------------------------------------------------------- 基础输出
    def send_text(self, text, code=200, ctype="text/plain; charset=utf-8", extra=None):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200, extra=None):
        self.send_text(json.dumps(obj, ensure_ascii=False), code,
                       "application/json; charset=utf-8", extra)

    def send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def query(self):
        if "?" not in self.path:
            return {}
        return {k: v[0] for k, v in
                urllib.parse.parse_qs(self.path.split("?", 1)[1]).items()}

    def session_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        m = cookie.get("campus_session")
        if not m:
            return None
        conn = get_db()
        row = conn.execute("SELECT u.id, u.sid, u.name, u.major, s.created"
                           " FROM sessions s JOIN users u ON u.id = s.user_id"
                           " WHERE s.token=?", (m.value,)).fetchone()
        conn.close()
        if not row or time.time() - row["created"] > SESSION_TTL:
            return None
        return {"id": row["id"], "sid": row["sid"], "name": row["name"],
                "major": row["major"]}

    def need_user(self):
        u = self.session_user()
        if not u:
            self.send_json({"ok": False, "error": "未登录"}, 401)
        return u

    # ---------------------------------------------------------- 路由
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/login"):
                self.send_bytes(render("login.html").encode("utf-8"),
                                "text/html; charset=utf-8")
            elif path == "/app":
                self.send_bytes(render("app.html").encode("utf-8"),
                                "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                self.serve_static(path[len("/static/"):])
            elif path.startswith("/api/"):
                self.api_get(path)
            elif path == "/favicon.ico":
                self.send_bytes(PIXEL_GIF, "image/gif")
            else:
                self.send_text("404 Not Found", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self.send_text("500 %s" % exc, 500)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            routes = {
                "/api/login": self.api_login,
                "/api/register": self.api_register,
                "/api/logout": self.api_logout,
                "/api/expense": self.api_add_expense,
                "/api/expense/update": self.api_update_expense,
                "/api/expense/delete": self.api_delete_expense,
                "/api/course": self.api_add_course,
                "/api/course/update": self.api_update_course,
                "/api/course/delete": self.api_delete_course,
                "/api/course/clear": self.api_clear_courses,
                "/api/student": self.api_add_student,
                "/api/student/update": self.api_update_student,
                "/api/student/delete": self.api_delete_student,
                "/api/chat/clear": self.api_clear_chat,
                "/api/chat": self.api_chat,
                "/api/prefs": self.api_prefs,
                "/api/reset": self.api_reset,
            }
            fn = routes.get(path)
            if fn:
                fn()
            else:
                self.send_json({"ok": False, "error": "未知接口"}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def serve_static(self, rel):
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self.send_text("404 Not Found", 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self.send_bytes(fh.read(), ctype)

    # ---------------------------------------------------------- 登录 / 注册
    def api_login(self):
        data = self.body_json()
        sid = (data.get("sid") or "").strip()
        pwd = data.get("password") or ""
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE sid=?", (sid,)).fetchone()
        if not row or not hmac.compare_digest(row["pwd_hash"], hash_pwd(pwd, row["salt"])):
            conn.close()
            self.send_json({"ok": False, "error": "学号或口令错误"}, 401)
            return
        token = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO sessions (token, user_id, created) VALUES (?,?,?)",
                     (token, row["id"], int(time.time())))
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "name": row["name"]}, 200, {
            "Set-Cookie": "campus_session=%s; Path=/; HttpOnly; SameSite=Lax" % token})

    def api_register(self):
        d = self.body_json()
        sid = (d.get("sid") or "").strip()
        name = (d.get("name") or "").strip()
        major = (d.get("major") or "计算机科学与技术").strip()
        pwd = d.get("password") or ""
        pwd2 = d.get("password2", pwd)
        errors = []
        if not re.match(r"^\d{6,12}$", sid):
            errors.append("学号应为 6—12 位数字")
        if not (2 <= len(name) <= 12):
            errors.append("姓名长度应为 2—12 个字符")
        if len(pwd) < 6:
            errors.append("口令至少 6 位")
        if pwd != pwd2:
            errors.append("两次输入的口令不一致")
        conn = get_db()
        if conn.execute("SELECT id FROM users WHERE sid=?", (sid,)).fetchone():
            errors.append("该学号已注册，请直接登录")
        if errors:
            conn.close()
            self.send_json({"ok": False, "error": "；".join(errors)}, 400)
            return
        uid = register_user(conn, sid, name, major, pwd)
        conn.close()
        self.send_json({"ok": True, "message": "注册成功，请登录",
                        "sid": sid, "uid": uid})

    def api_logout(self):
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        m = cookie.get("campus_session")
        if m:
            conn = get_db()
            conn.execute("DELETE FROM sessions WHERE token=?", (m.value,))
            conn.commit()
            conn.close()
        self.send_json({"ok": True}, 200,
                       {"Set-Cookie": "campus_session=; Path=/; Max-Age=0; HttpOnly"})

    # ---------------------------------------------------------- 查询接口
    def api_get(self, path):
        user = self.session_user()
        if path == "/api/me":
            self.send_json({"ok": bool(user), "user": user})
            return
        if path == "/api/quick":
            self.send_json({"ok": True, "questions": QUICK_QUESTIONS,
                            "slots": [{"name": n, "begin": b, "end": e}
                                      for n, b, e in MEAL_SLOTS]})
            return
        if path == "/api/campus":
            self.send_json({
                "ok": True,
                "school": SCHOOL,
                "canteens": CANTEENS,
                "links": [{"name": n, "url": u, "desc": d, "tag": t}
                          for n, u, d, t in CAMPUS_LINKS],
                "faq_categories": sorted({f[3] for f in FAQ}),
            })
            return
        if not user:
            self.send_json({"ok": False, "error": "未登录"}, 401)
            return
        conn = get_db()
        if path == "/api/schedule":
            self.send_json({"ok": True, **schedule_payload(conn, user["id"])})
        elif path == "/api/meals":
            q = self.query()
            prefs = conn.execute("SELECT * FROM prefs WHERE user_id=?",
                                 (user["id"],)).fetchone()
            taste = q.get("taste", prefs["taste"] if prefs else "")
            budget = float(q["budget"]) if q.get("budget") else \
                (prefs["budget_meal"] if prefs else 15.0)
            canteen = q.get("canteen", "")
            avoid = int(prefs["avoid_wait"]) if prefs else 15
            data = recommend_meals(hm=q.get("hm"), taste=taste, budget=budget,
                                   canteen=canteen, avoid_wait=avoid, limit=3)
            data["ok"] = True
            data["taste"] = taste
            data["budget"] = budget
            self.send_json(data)
        elif path == "/api/expenses":
            days = int(self.query().get("days", "30"))
            self.send_json({"ok": True, **expense_payload(conn, user["id"], days)})
        elif path == "/api/students":
            rows = conn.execute("SELECT * FROM students WHERE user_id=?"
                                " ORDER BY id", (user["id"],)).fetchall()
            self.send_json({"ok": True, "students": [dict(r) for r in rows]})
        elif path == "/api/chat":
            rows = conn.execute("SELECT role, text, ts FROM chat WHERE user_id=?"
                                " ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
            self.send_json({"ok": True,
                            "history": [dict(r) for r in reversed(rows)]})
        elif path == "/api/export":
            data = expense_payload(conn, user["id"], 30)
            lines = ["日期,分类,地点,备注,金额"]
            for i in data["items"]:
                lines.append("%s,%s,%s,%s,%.2f" % (i["ts"], i["category"],
                                                   i["place"] or "", i["note"] or "",
                                                   i["amount"]))
            lines.append("合计,,,,%.2f" % sum(i["amount"] for i in data["items"]))
            self.send_text("\ufeff" + "\n".join(lines), 200,
                           "text/csv; charset=utf-8",
                           {"Content-Disposition":
                            "attachment; filename=expenses_%s.csv"
                            % datetime.now().strftime("%Y%m%d")})
        else:
            self.send_json({"ok": False, "error": "未知接口"}, 404)
        conn.close()

    # ---------------------------------------------------------- 写入接口
    def api_add_expense(self):
        user = self.need_user()
        if not user:
            return
        data = self.body_json()
        try:
            amount = float(data.get("amount"))
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": "金额格式不正确"}, 400)
            return
        if amount <= 0 or amount > 10000:
            self.send_json({"ok": False, "error": "金额需在 0—10000 元之间"}, 400)
            return
        category = (data.get("category") or "其他").strip()[:20]
        place = (data.get("place") or "").strip()[:30]
        note = (data.get("note") or "").strip()[:50]
        conn = get_db()
        conn.execute("INSERT INTO expenses (user_id, amount, category, place, note, ts)"
                     " VALUES (?,?,?,?,?,?)",
                     (user["id"], amount, category, place, note, now_str()))
        conn.commit()
        payload = expense_payload(conn, user["id"])
        conn.close()
        self.send_json({"ok": True, "message": "已记账 %.2f 元" % amount, **payload})

    # ---------------------------------------------------------- 记账：改 / 删
    def api_update_expense(self):
        user = self.need_user()
        if not user:
            return
        d = self.body_json()
        eid = int(d.get("id") or 0)
        conn = get_db()
        row = conn.execute("SELECT * FROM expenses WHERE id=? AND user_id=?",
                           (eid, user["id"])).fetchone()
        if not row:
            conn.close()
            self.send_json({"ok": False, "error": "记录不存在或无权修改"}, 404)
            return
        try:
            amount = float(d.get("amount", row["amount"]))
        except (TypeError, ValueError):
            conn.close()
            self.send_json({"ok": False, "error": "金额格式不正确"}, 400)
            return
        if amount <= 0 or amount > 10000:
            conn.close()
            self.send_json({"ok": False, "error": "金额需在 0—10000 元之间"}, 400)
            return
        conn.execute("UPDATE expenses SET amount=?, category=?, place=?, note=?"
                     " WHERE id=?", (amount,
                                     (d.get("category") or row["category"]).strip()[:20],
                                     (d.get("place") if d.get("place") is not None
                                      else row["place"]) or "",
                                     (d.get("note") if d.get("note") is not None
                                      else row["note"]) or "", eid))
        conn.commit()
        payload = expense_payload(conn, user["id"])
        conn.close()
        self.send_json({"ok": True, "message": "已修改这笔记录", **payload})

    def api_delete_expense(self):
        user = self.need_user()
        if not user:
            return
        eid = int(self.body_json().get("id") or 0)
        conn = get_db()
        cur = conn.execute("DELETE FROM expenses WHERE id=? AND user_id=?",
                           (eid, user["id"]))
        conn.commit()
        payload = expense_payload(conn, user["id"])
        conn.close()
        ok = cur.rowcount > 0
        self.send_json({"ok": ok, "message": "已删除这笔记录" if ok else "记录不存在",
                        **payload})

    # ---------------------------------------------------------- 课程：改 / 删 / 清空
    def api_update_course(self):
        user = self.need_user()
        if not user:
            return
        d = self.body_json()
        cid = int(d.get("id") or 0)
        conn = get_db()
        row = conn.execute("SELECT * FROM courses WHERE id=? AND user_id=?",
                           (cid, user["id"])).fetchone()
        if not row:
            conn.close()
            self.send_json({"ok": False, "error": "课程不存在或无权修改"}, 404)
            return
        day = d.get("day", row["day"])
        if day not in WEEKDAYS:
            day = row["day"]
        try:
            sp = int(d.get("start_p", row["start_p"]))
            ep = int(d.get("end_p", row["end_p"]))
        except (TypeError, ValueError):
            conn.close()
            self.send_json({"ok": False, "error": "节次不正确"}, 400)
            return
        if not (1 <= sp <= ep <= 10):
            conn.close()
            self.send_json({"ok": False, "error": "节次需在 1—10 之间"}, 400)
            return
        conn.execute("UPDATE courses SET name=?, teacher=?, place=?, day=?,"
                     " start_p=?, end_p=?, weeks=?, credit=? WHERE id=?",
                     ((d.get("name") or row["name"]).strip()[:60],
                      (d.get("teacher") if d.get("teacher") is not None
                       else row["teacher"]) or "",
                      (d.get("place") if d.get("place") is not None
                       else row["place"]) or "",
                      day, sp, ep,
                      (d.get("weeks") if d.get("weeks") is not None
                       else row["weeks"]) or "",
                      float(d.get("credit") or row["credit"] or 0), cid))
        conn.commit()
        payload = schedule_payload(conn, user["id"])
        conn.close()
        self.send_json({"ok": True, "message": "已修改这条安排", **payload})

    def api_delete_course(self):
        user = self.need_user()
        if not user:
            return
        cid = int(self.body_json().get("id") or 0)
        conn = get_db()
        cur = conn.execute("DELETE FROM courses WHERE id=? AND user_id=?",
                           (cid, user["id"]))
        conn.commit()
        payload = schedule_payload(conn, user["id"])
        conn.close()
        ok = cur.rowcount > 0
        self.send_json({"ok": ok, "message": "已删除这条安排" if ok else "课程不存在",
                        **payload})

    def api_clear_courses(self):
        user = self.need_user()
        if not user:
            return
        conn = get_db()
        conn.execute("DELETE FROM courses WHERE user_id=?", (user["id"],))
        conn.commit()
        payload = schedule_payload(conn, user["id"])
        conn.close()
        self.send_json({"ok": True, "message": "已清空课程表", **payload})

    # ---------------------------------------------------------- 同学名单：增 / 改 / 删
    def api_add_student(self):
        user = self.need_user()
        if not user:
            return
        d = self.body_json()
        name = (d.get("name") or "").strip()
        if not (1 <= len(name) <= 12):
            self.send_json({"ok": False, "error": "姓名长度应为 1—12 个字符"}, 400)
            return
        conn = get_db()
        conn.execute("INSERT INTO students (user_id,name,sid,contact,dept,role,note,ts)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     (user["id"], name, (d.get("sid") or "").strip()[:20],
                      (d.get("contact") or "").strip()[:30],
                      (d.get("dept") or "").strip()[:30],
                      (d.get("role") or "同学").strip()[:10],
                      (d.get("note") or "").strip()[:50], now_str()))
        conn.commit()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM students WHERE user_id=? ORDER BY id", (user["id"],))]
        conn.close()
        self.send_json({"ok": True, "message": "已添加同学", "students": rows})

    def api_update_student(self):
        user = self.need_user()
        if not user:
            return
        d = self.body_json()
        try:
            tid = int(d.get("id") or 0)
        except (TypeError, ValueError):
            tid = 0
        name = (d.get("name") or "").strip()
        conn = get_db()
        row = conn.execute("SELECT * FROM students WHERE id=? AND user_id=?",
                           (tid, user["id"])).fetchone()
        if not row:
            conn.close()
            self.send_json({"ok": False, "error": "同学不存在或无权修改"}, 404)
            return
        if not (1 <= len(name) <= 12):
            conn.close()
            self.send_json({"ok": False, "error": "姓名长度应为 1—12 个字符"}, 400)
            return
        conn.execute("UPDATE students SET name=?, sid=?, contact=?, dept=?, role=?,"
                     " note=? WHERE id=?",
                     (name, (d.get("sid") or "").strip()[:20],
                      (d.get("contact") or "").strip()[:30],
                      (d.get("dept") or "").strip()[:30],
                      (d.get("role") or "同学").strip()[:10],
                      (d.get("note") or "").strip()[:50], tid))
        conn.commit()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM students WHERE user_id=? ORDER BY id", (user["id"],))]
        conn.close()
        self.send_json({"ok": True, "message": "已修改同学信息", "students": rows})

    def api_delete_student(self):
        user = self.need_user()
        if not user:
            return
        try:
            tid = int(self.body_json().get("id") or 0)
        except (TypeError, ValueError):
            tid = 0
        conn = get_db()
        cur = conn.execute("DELETE FROM students WHERE id=? AND user_id=?",
                           (tid, user["id"]))
        conn.commit()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM students WHERE user_id=? ORDER BY id", (user["id"],))]
        conn.close()
        ok = cur.rowcount > 0
        self.send_json({"ok": ok, "message": "已删除该同学" if ok else "同学不存在",
                        "students": rows})

    def api_clear_chat(self):
        user = self.need_user()
        if not user:
            return
        conn = get_db()
        conn.execute("DELETE FROM chat WHERE user_id=?", (user["id"],))
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "message": "已清空问答记录"})

    def api_prefs(self):
        user = self.need_user()
        if not user:
            return
        data = self.body_json()
        conn = get_db()
        conn.execute("INSERT INTO prefs (user_id, taste, budget_meal, avoid_wait,"
                     " canteen) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
                     " taste=excluded.taste, budget_meal=excluded.budget_meal,"
                     " avoid_wait=excluded.avoid_wait, canteen=excluded.canteen",
                     (user["id"], data.get("taste", ""),
                      float(data.get("budget_meal") or 15),
                      int(data.get("avoid_wait") or 15), data.get("canteen", "")))
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "message": "偏好已保存"})

    def api_add_course(self):
        user = self.need_user()
        if not user:
            return
        d = self.body_json()
        name = (d.get("name") or "").strip()
        day = d.get("day") if d.get("day") in WEEKDAYS else None
        try:
            sp, ep = int(d.get("start_p")), int(d.get("end_p"))
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": "节次不正确"}, 400)
            return
        if not name or not day or not (1 <= sp <= ep <= 9):
            self.send_json({"ok": False, "error": "请填写课程名、星期与节次"}, 400)
            return
        conn = get_db()
        conn.execute("INSERT INTO courses (user_id,name,college,day,start_p,end_p,"
                     "place,teacher,kind,credit,weeks) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (user["id"], name, d.get("college", ""), day, sp, ep,
                      d.get("place", ""), d.get("teacher", ""),
                      d.get("kind", "自主添加"), float(d.get("credit") or 0),
                      d.get("weeks", "")))
        conn.commit()
        payload = schedule_payload(conn, user["id"])
        conn.close()
        self.send_json({"ok": True, "message": "已添加课程", **payload})

    def api_chat(self):
        user = self.need_user()
        if not user:
            return
        text = (self.body_json().get("text") or "").strip()[:200]
        if not text:
            self.send_json({"ok": False, "error": "请输入问题"}, 400)
            return
        conn = get_db()
        conn.execute("INSERT INTO chat (user_id, role, text, ts) VALUES (?,?,?,?)",
                     (user["id"], "user", text, now_str()))
        result = answer_question(text, user["id"], conn)
        conn.execute("INSERT INTO chat (user_id, role, text, ts) VALUES (?,?,?,?)",
                     (user["id"], "bot", result["answer"], now_str()))
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "question": text, **result})

    def api_reset(self):
        user = self.need_user()
        if not user:
            return
        conn = get_db()
        conn.execute("DELETE FROM expenses WHERE user_id=?", (user["id"],))
        seed_expenses(conn, user["id"])
        conn.execute("DELETE FROM chat WHERE user_id=?", (user["id"],))
        conn.commit()
        payload = expense_payload(conn, user["id"])
        conn.close()
        self.send_json({"ok": True, "message": "演示数据已重置", **payload})


def main():
    ap = argparse.ArgumentParser(description="校园生活 AI 助手")
    ap.add_argument("--port", type=int, default=8866)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    init_db()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 60)
    print("  校园生活 AI 助手  |  演示账号：20230101 / 123456")
    print("  打开： http://%s:%d/" % (args.host, args.port))
    print("  离线可用；配置 LLM_API_KEY 可切换到大模型问答")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
