"""组长审核与样本库：审核结果写回，供 Jev 意图少样本校正。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from .party import clean_sender
from .wecom_intent import INTENTS, intent_label, is_skip_intent, normalize_intent, suggested_intent_labels

DB_PATH = DATA_DIR / "data" / "agent_review.sqlite"
SCORE_MIN = 70
SKIP_INTENTS = ("confirm", "chat")

FIELDS = [
    {"id": "filter_ok", "label": "是否有效消息", "note": "filter_note", "yesMeans": "有效"},
    {"id": "intent_ok", "label": "Jev 意图是否合理", "note": "intent_note", "yesMeans": "合理"},
    {"id": "score_ok", "label": "评分是否合理", "note": "score_note", "yesMeans": "合理"},
    {"id": "process_ok", "label": "是否进入后续处理", "note": "process_note", "yesMeans": "进入"},
    {"id": "passed", "label": "本条是否通过审核", "note": "passed_note", "yesMeans": "通过"},
]


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT,
                session_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender TEXT,
                time_text TEXT,
                text TEXT,
                filename TEXT,
                party TEXT,
                gemini_useful INTEGER,
                gemini_reason TEXT,
                intent TEXT,
                intent_label TEXT,
                need TEXT,
                action TEXT,
                score INTEGER,
                shown INTEGER,
                error TEXT,
                seq INTEGER,
                updated_at TEXT,
                UNIQUE(session_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ar_hits_session ON pipeline_hits(session_id, shown, score);

            CREATE TABLE IF NOT EXISTS intent_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT,
                session_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender TEXT,
                time_text TEXT,
                filter_ok INTEGER,
                filter_note TEXT,
                intent_ok INTEGER,
                intent_note TEXT,
                score_ok INTEGER,
                score_note TEXT,
                process_ok INTEGER,
                process_note TEXT,
                passed INTEGER,
                passed_note TEXT,
                correct_useful INTEGER,
                correct_intent TEXT,
                correct_score INTEGER,
                reviewer TEXT,
                updated_at TEXT,
                UNIQUE(session_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS prompt_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                text TEXT,
                filename TEXT,
                label TEXT,
                score INTEGER,
                reviewer TEXT,
                session_id TEXT,
                message_id TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ar_ex_kind ON prompt_examples(kind, id);

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                session_id TEXT PRIMARY KEY,
                source_id TEXT,
                name TEXT,
                last_time TEXT,
                last_message_id TEXT,
                msg_count INTEGER,
                full_done INTEGER,
                updated_at TEXT
            );
            """
        )
        cols = {r[1] for r in con.execute("PRAGMA table_info(pipeline_hits)").fetchall()}
        if "candidates" not in cols:
            con.execute("ALTER TABLE pipeline_hits ADD COLUMN candidates TEXT")
        if "skip_jev" not in cols:
            con.execute("ALTER TABLE pipeline_hits ADD COLUMN skip_jev INTEGER")
        if "seq" not in cols:
            con.execute("ALTER TABLE pipeline_hits ADD COLUMN seq INTEGER")
        rcols = {r[1] for r in con.execute("PRAGMA table_info(intent_reviews)").fetchall()}
        if "selected_intent" not in rcols:
            con.execute("ALTER TABLE intent_reviews ADD COLUMN selected_intent TEXT")
        if "selected_score" not in rcols:
            con.execute("ALTER TABLE intent_reviews ADD COLUMN selected_score INTEGER")
        if "selected_label" not in rcols:
            con.execute("ALTER TABLE intent_reviews ADD COLUMN selected_label TEXT")
        if "selected_intents" not in rcols:
            con.execute("ALTER TABLE intent_reviews ADD COLUMN selected_intents TEXT")
        if "should_filter" not in rcols:
            con.execute("ALTER TABLE intent_reviews ADD COLUMN should_filter INTEGER")
        run_cols = {r[1] for r in con.execute("PRAGMA table_info(pipeline_runs)").fetchall()}
        if "progress_done" not in run_cols:
            con.execute("ALTER TABLE pipeline_runs ADD COLUMN progress_done INTEGER")
        if "progress_total" not in run_cols:
            con.execute("ALTER TABLE pipeline_runs ADD COLUMN progress_total INTEGER")
        ex_cols = {r[1] for r in con.execute("PRAGMA table_info(prompt_examples)").fetchall()}
        if "enabled" not in ex_cols:
            con.execute("ALTER TABLE prompt_examples ADD COLUMN enabled INTEGER DEFAULT 1")
        if "pinned" not in ex_cols:
            con.execute("ALTER TABLE prompt_examples ADD COLUMN pinned INTEGER DEFAULT 0")


def _bool(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if str(value) in ("1", "true", "True", "是", "yes"):
        return 1
    if str(value) in ("0", "false", "False", "否", "no"):
        return 0
    return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hit_shown(row: dict) -> bool:
    if row.get("skip_jev"):
        return False
    if str(row.get("party") or "") == "乙方":
        return False
    if not row.get("gemini_useful"):
        return False
    if is_skip_intent(row.get("intent"), row.get("intent_label") or row.get("intentLabel")):
        return False
    try:
        return int(row.get("score") or 0) >= SCORE_MIN
    except (TypeError, ValueError):
        return False


def hit_reviewed(review: dict | None) -> bool:
    if not isinstance(review, dict) or not review:
        return False
    if review.get("id") not in (None, "", 0):
        return True
    if review.get("should_filter"):
        return True
    if str(review.get("selected_intent") or "").strip():
        return True
    items = review.get("selected_intents")
    if isinstance(items, str) and items.strip():
        return True
    return bool(isinstance(items, list) and items)


def save_hits(session_id: str, source_id: str, items: list[dict]) -> int:
    init_db()
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    now = _now()
    n = 0
    with connect() as con:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            mid = str(it.get("message_id") or it.get("messageId") or "").strip()
            if not mid:
                continue
            useful = 1 if it.get("gemini_useful") else 0
            skip_jev = 1 if it.get("skip_jev") else 0
            score = int(it.get("score") or 0)
            shown = 1 if hit_shown({**it, "gemini_useful": useful, "score": score, "skip_jev": skip_jev}) else 0
            raw_cands = it.get("candidates") if isinstance(it.get("candidates"), list) else []
            cands = [c for c in raw_cands if isinstance(c, dict) and not c.get("fallback")]
            seq = it.get("seq")
            try:
                seq = None if seq in (None, "") else int(seq)
            except (TypeError, ValueError):
                seq = None
            row = (
                str(source_id or it.get("source_id") or "*"),
                sid,
                mid,
                (clean_sender(it.get("sender")) or "未知")[:80],
                str(it.get("time_text") or it.get("time") or "")[:32],
                str(it.get("text") or "")[:2000],
                str(it.get("filename") or "")[:240],
                str(it.get("party") or "")[:8],
                useful,
                str(it.get("gemini_reason") or "")[:200],
                str(it.get("intent") or "")[:40],
                str(it.get("intent_label") or "")[:40],
                str(it.get("need") or "")[:80],
                str(it.get("action") or "")[:80],
                score,
                shown,
                str(it.get("error") or "")[:160],
                now,
                json.dumps(cands[:5], ensure_ascii=False),
                skip_jev,
                seq,
            )
            con.execute(
                """
                INSERT INTO pipeline_hits (
                    source_id, session_id, message_id, sender, time_text, text, filename, party,
                    gemini_useful, gemini_reason, intent, intent_label, need, action, score, shown, error, updated_at, candidates, skip_jev, seq
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id, message_id) DO UPDATE SET
                    source_id=excluded.source_id,
                    sender=excluded.sender,
                    time_text=excluded.time_text,
                    text=excluded.text,
                    filename=excluded.filename,
                    party=excluded.party,
                    gemini_useful=excluded.gemini_useful,
                    gemini_reason=excluded.gemini_reason,
                    intent=excluded.intent,
                    intent_label=excluded.intent_label,
                    need=excluded.need,
                    action=excluded.action,
                    score=excluded.score,
                    shown=excluded.shown,
                    error=excluded.error,
                    updated_at=excluded.updated_at,
                    candidates=excluded.candidates,
                    skip_jev=excluded.skip_jev,
                    seq=COALESCE(excluded.seq, pipeline_hits.seq)
                """,
                row,
            )
            n += 1
        con.commit()
    return n


def _id_rank(message_id) -> tuple:
    s = str(message_id or "")
    try:
        return (int(s), s)
    except ValueError:
        return (0, s)


def sort_hits(rows: list[dict]) -> list[dict]:
    """有 seq 时按会话原顺序；否则按时间 + message_id，避免乙方先入库排到前面。"""
    if not rows:
        return rows
    if all(r.get("seq") not in (None, "") for r in rows):
        rows.sort(key=lambda r: (int(r.get("seq") or 0), str(r.get("time_text") or ""), _id_rank(r.get("message_id"))))
    else:
        rows.sort(key=lambda r: (str(r.get("time_text") or ""), _id_rank(r.get("message_id"))))
    return rows


def skip_jev_count(session_id: str) -> int:
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM pipeline_hits WHERE session_id=? AND IFNULL(skip_jev,0)=1",
            (session_id,),
        ).fetchone()
    return int(row["n"] or 0) if row else 0


def max_seq(session_id: str) -> int:
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT MAX(seq) AS n FROM pipeline_hits WHERE session_id=?",
            (session_id,),
        ).fetchone()
    try:
        return int(row["n"])
    except (TypeError, ValueError):
        return -1


def update_hit_seqs(session_id: str, items: list[tuple[str, int]]) -> int:
    init_db()
    if not items:
        return 0
    n = 0
    with connect() as con:
        for mid, seq in items:
            if not mid:
                continue
            con.execute(
                "UPDATE pipeline_hits SET seq=? WHERE session_id=? AND message_id=?",
                (int(seq), session_id, str(mid)),
            )
            n += 1
        con.commit()
    return n


def clear_hits(session_id: str) -> None:
    init_db()
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (session_id,))
        con.execute("DELETE FROM pipeline_runs WHERE session_id=?", (session_id,))
        con.commit()


def get_run(session_id: str) -> dict:
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM pipeline_runs WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row:
            data = dict(row)
            data["full_done"] = bool(int(data.get("full_done") or 0))
            data["progress_done"] = int(data.get("progress_done") or 0)
            data["progress_total"] = int(data.get("progress_total") or 0)
            data["incomplete"] = not data["full_done"]
            return data
        hit = con.execute(
            """
            SELECT time_text, message_id FROM pipeline_hits
            WHERE session_id=? AND time_text IS NOT NULL AND time_text != ''
            ORDER BY time_text DESC, message_id DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        n = con.execute(
            "SELECT COUNT(*) AS n FROM pipeline_hits WHERE session_id=?",
            (session_id,),
        ).fetchone()
    count = int(n["n"] or 0) if n else 0
    if hit and count:
        return {
            "session_id": session_id,
            "last_time": str(hit["time_text"] or ""),
            "last_message_id": str(hit["message_id"] or ""),
            "msg_count": count,
            "full_done": 0,
            "progress_done": count,
            "incomplete": True,
        }
    return {}


def list_runs() -> dict[str, dict]:
    init_db()
    with connect() as con:
        runs = {str(r["session_id"]): dict(r) for r in con.execute("SELECT * FROM pipeline_runs").fetchall()}
        counts = con.execute(
            """
            SELECT h.session_id,
                   COUNT(*) AS hits,
                   SUM(CASE WHEN h.shown=1 AND r.id IS NULL THEN 1 ELSE 0 END) AS queue,
                   SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) AS reviewed
            FROM pipeline_hits h
            LEFT JOIN intent_reviews r
              ON r.session_id=h.session_id AND CAST(r.message_id AS TEXT)=CAST(h.message_id AS TEXT)
            GROUP BY h.session_id
            """
        ).fetchall()
    for c in counts:
        sid = str(c["session_id"])
        row = runs.get(sid) or {"session_id": sid, "full_done": 0}
        row["hits"] = int(c["hits"] or 0)
        row["queue"] = int(c["queue"] or 0)
        row["reviewed"] = int(c["reviewed"] or 0)
        runs[sid] = row
    for row in runs.values():
        row["hits"] = int(row.get("hits") or 0)
        row["queue"] = int(row.get("queue") or 0)
        row["reviewed"] = int(row.get("reviewed") or 0)
        row["full_done"] = bool(int(row.get("full_done") or 0))
        row["incomplete"] = bool(row["hits"] and not row["full_done"])
        row["analyzed"] = bool(row["full_done"] or row["hits"])
        row["progress_done"] = int(row.get("progress_done") or 0)
        row["progress_total"] = int(row.get("progress_total") or 0)
    return runs


def save_run(
    session_id: str,
    *,
    source_id: str = "",
    name: str = "",
    last_time: str = "",
    last_message_id: str = "",
    msg_count: int = 0,
    full_done: bool = True,
    progress_done: int = 0,
    progress_total: int = 0,
) -> None:
    init_db()
    now = _now()
    with connect() as con:
        con.execute(
            """
            INSERT INTO pipeline_runs (
                session_id, source_id, name, last_time, last_message_id, msg_count, full_done, updated_at,
                progress_done, progress_total
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                source_id=excluded.source_id,
                name=excluded.name,
                last_time=excluded.last_time,
                last_message_id=excluded.last_message_id,
                msg_count=excluded.msg_count,
                full_done=excluded.full_done,
                updated_at=excluded.updated_at,
                progress_done=excluded.progress_done,
                progress_total=excluded.progress_total
            """,
            (
                session_id,
                source_id,
                name[:120],
                str(last_time or "")[:32],
                str(last_message_id or "")[:80],
                int(msg_count or 0),
                1 if full_done else 0,
                now,
                int(progress_done or 0),
                int(progress_total or 0),
            ),
        )
        con.commit()


def clear_run(session_id: str) -> None:
    init_db()
    with connect() as con:
        con.execute("DELETE FROM pipeline_runs WHERE session_id=?", (session_id,))
        con.commit()


def analyzed_ids(session_id: str) -> set[str]:
    init_db()
    with connect() as con:
        rows = con.execute(
            "SELECT message_id FROM pipeline_hits WHERE session_id=?",
            (session_id,),
        ).fetchall()
    return {str(r["message_id"]) for r in rows}


def list_hits(session_id: str, view: str = "all", sender: str = "", party: str = "", offset: int = 0, limit: int = 40, tail: bool = False) -> dict:
    init_db()
    sql = "SELECT * FROM pipeline_hits WHERE session_id=?"
    args: list = [session_id]
    view = str(view or "all").strip().lower()
    if view == "dropped":
        sql += " AND IFNULL(skip_jev,0)=0 AND (gemini_useful=0 OR intent IN ('confirm','chat'))"
    elif view == "low":
        sql += " AND IFNULL(skip_jev,0)=0 AND IFNULL(intent,'') NOT IN ('confirm','chat') AND (shown=0 OR score < ?)"
        args.append(SCORE_MIN)
    elif view == "queue":
        sql += """ AND shown=1 AND CAST(message_id AS TEXT) NOT IN (
            SELECT CAST(message_id AS TEXT) FROM intent_reviews WHERE session_id=?
        )"""
        args.append(session_id)
    if sender:
        sql += " AND sender=?"
        args.append(sender)
    if party:
        sql += " AND party=?"
        args.append(party)
    with connect() as con:
        rows = [dict(r) for r in con.execute(sql, args).fetchall()]
        stats = con.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN h.party='甲方' THEN 1 ELSE 0 END) AS jia,
              SUM(CASE WHEN h.party='乙方' OR IFNULL(h.skip_jev,0)=1 THEN 1 ELSE 0 END) AS yi,
              SUM(CASE WHEN IFNULL(h.skip_jev,0)=0 AND (h.gemini_useful=0 OR h.intent IN ('confirm','chat')) THEN 1 ELSE 0 END) AS dropped,
              SUM(CASE WHEN h.gemini_useful=1 AND IFNULL(h.skip_jev,0)=0 THEN 1 ELSE 0 END) AS kept,
              SUM(CASE WHEN h.shown=1 AND r.id IS NULL THEN 1 ELSE 0 END) AS queue
            FROM pipeline_hits h
            LEFT JOIN intent_reviews r
              ON r.session_id=h.session_id AND CAST(r.message_id AS TEXT)=CAST(h.message_id AS TEXT)
            WHERE h.session_id=?
            """,
            (session_id,),
        ).fetchone()
    sort_hits(rows)
    reviews = review_map(session_id)
    for row in rows:
        _dump_hit(row, reviews.get(str(row.get("message_id") or "")) or {})
    off = max(0, int(offset or 0))
    try:
        lim_in = int(limit or 0)
    except (TypeError, ValueError):
        lim_in = 40
    n = len(rows)
    has_earlier = False
    from_end = 0
    if lim_in <= 0:
        data = rows if tail else rows[off:]
        lim = len(data)
        from_end = 0
        has_earlier = False
        off = 0 if tail else off
    elif tail:
        lim = max(1, min(lim_in, 500))
        fe = off
        end = n - fe
        if end < 0:
            end = 0
        start = max(0, end - lim)
        data = rows[start:end]
        has_earlier = start > 0
        from_end = n - end
        off = start
    else:
        lim = max(1, min(lim_in, 500))
        data = rows[off: off + lim]
        has_earlier = off + lim < n
        from_end = max(0, n - (off + len(data)))
    st = dict(stats) if stats else {}
    return {
        "total": n,
        "offset": off,
        "limit": lim,
        "fromEnd": from_end,
        "hasEarlier": has_earlier,
        "data": data,
        "stats": {
            "total": int(st.get("total") or 0),
            "jia": int(st.get("jia") or 0),
            "yi": int(st.get("yi") or 0),
            "dropped": int(st.get("dropped") or 0),
            "kept": int(st.get("kept") or 0),
            "queue": int(st.get("queue") or 0),
            "reviewed": len(reviews),
            "scoreMin": SCORE_MIN,
        },
    }


def get_hit(session_id: str, message_id: str) -> dict:
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM pipeline_hits WHERE session_id=? AND message_id=?",
            (session_id, str(message_id)),
        ).fetchone()
    if not row:
        return {}
    data = dict(row)
    _dump_hit(data, get_review(str(data.get("source_id") or "*"), session_id, str(message_id)))
    return data


def fallback_candidates() -> list[dict]:
    """不再把起步词表整表塞进审核弹窗。Jev 没结果时由组长手填。"""
    return []


def list_known_intents() -> list[dict]:
    """起步词表 + 建议细分 + 样本/历史里出现过的意图，供筛选和编辑，不充当审核唯一选项。"""
    seen: dict[str, str] = {}
    out: list[dict] = []

    def add(code, name="") -> None:
        code, lab = normalize_intent(code, name)
        if not code or code in seen:
            return
        seen[code] = lab
        out.append({"id": code, "name": lab})

    for k, v in INTENTS.items():
        add(k, v)
    for k, v in suggested_intent_labels().items():
        add(k, v)
    init_db()
    with connect() as con:
        for r in con.execute(
            "SELECT DISTINCT label FROM prompt_examples WHERE kind='intent' AND IFNULL(label,'')!=''"
        ):
            add(r["label"], r["label"])
        for r in con.execute(
            "SELECT DISTINCT intent, intent_label FROM pipeline_hits WHERE IFNULL(intent,'')!=''"
        ):
            add(r["intent"], r["intent_label"] or "")
    return out


def candidates_of(hit: dict) -> list[dict]:
    raw = (hit or {}).get("candidates")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    out = []
    if isinstance(raw, list):
        for item in raw[:5]:
            if not isinstance(item, dict):
                continue
            code = str(item.get("intent") or "").strip()
            if not code:
                continue
            out.append({
                "intent": code[:40],
                "intentLabel": str(item.get("intentLabel") or intent_label(code))[:40],
                "score": int(item.get("score") or 0),
                "need": str(item.get("need") or "")[:80],
                "action": str(item.get("action") or "")[:80],
            })
    if out:
        return out
    code = str((hit or {}).get("intent") or "").strip()
    if not code:
        return []
    return [{
        "intent": code[:40],
        "intentLabel": str((hit or {}).get("intent_label") or intent_label(code))[:40],
        "score": int((hit or {}).get("score") or 0),
        "need": str((hit or {}).get("need") or "")[:80],
        "action": str((hit or {}).get("action") or "")[:80],
    }]


def _dump_hit(row: dict, review: dict | None = None) -> dict:
    row["sender"] = clean_sender(row.get("sender")) or "未知"
    row["gemini_useful"] = bool(row.get("gemini_useful"))
    row["shown"] = bool(row.get("shown"))
    row["skip_jev"] = bool(row.get("skip_jev"))
    row["candidates"] = candidates_of(row)
    rev = review or {}
    row["review"] = rev
    row["reviewed"] = hit_reviewed(rev)
    return row


def _selected_items(payload: dict) -> list[dict]:
    """审核可多选意图。兼容旧字段 selected_intent。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(code, label="", score=None, note="") -> None:
        intent = str(code or "").strip()[:40]
        if not intent or intent in seen:
            return
        seen.add(intent)
        sc = None
        try:
            if score not in (None, ""):
                sc = int(score)
        except (TypeError, ValueError):
            sc = None
        note_text = str(note or "").strip()[:80]
        lab = str(label or "").strip() or intent_label(intent)
        if note_text and intent == "other" and "：" not in lab and ":" not in lab:
            lab = "其他：" + note_text
        item = {
            "intent": intent,
            "label": lab[:40],
            "score": sc,
        }
        if note_text:
            item["note"] = note_text
        out.append(item)

    raw = payload.get("selected_intents")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                add(
                    item.get("intent") or item.get("id"),
                    item.get("label") or item.get("intentLabel"),
                    item.get("score"),
                    item.get("note") or item.get("intent_note"),
                )
    if not out:
        blob = str(payload.get("selected_intent") or payload.get("correct_intent") or "").strip()
        labels = [x.strip() for x in str(payload.get("selected_label") or "").split("、") if x.strip()]
        codes = [c.strip() for c in blob.replace("，", ",").split(",") if c.strip()]
        score = payload.get("selected_score")
        if score in (None, ""):
            score = payload.get("correct_score")
        for i, code in enumerate(codes):
            add(code, labels[i] if i < len(labels) else "", score if i == 0 else None)
    return out


def upsert_review(payload: dict) -> dict:
    init_db()
    session_id = str(payload.get("session_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    if not (session_id and message_id):
        raise ValueError("缺少 session_id / message_id")
    source_id = str(payload.get("source_id") or "*")
    now = _now()
    should_filter = _bool(payload.get("should_filter"))
    if should_filter is None:
        should_filter = 0
    items = [] if should_filter else _selected_items(payload)
    if not should_filter:
        cleaned = []
        for item in items:
            code, lab = normalize_intent(item.get("intent"), item.get("label"))
            if not code:
                continue
            if code == "other" and not str(item.get("note") or "").strip() and lab in ("", "其他"):
                raise ValueError("选「其他」时请填写具体意图")
            note = str(item.get("note") or "").strip()[:80]
            if code == "other" and note:
                code, lab = normalize_intent(note, note)
            item = {**item, "intent": code, "label": lab or item.get("label") or code}
            cleaned.append(item)
        items = cleaned
    if not should_filter and not items:
        raise ValueError("请选择意图，或标记应过滤")
    selected_intent = ",".join(x["intent"] for x in items)
    selected_label = "应过滤" if should_filter else "、".join(x["label"] for x in items)
    scores = [x["score"] for x in items if x.get("score") is not None]
    selected_score = max(scores) if scores else None
    chatter = bool(should_filter) or (bool(items) and all(is_skip_intent(x.get("intent"), x.get("label")) for x in items))
    hit = get_hit(session_id, message_id)
    was_useful = payload.get("gemini_useful")
    if was_useful is None:
        was_useful = hit.get("gemini_useful")
    was_useful = bool(was_useful)
    correct_useful = _bool(payload.get("correct_useful"))
    if correct_useful is None:
        correct_useful = 0 if chatter else 1
    if should_filter:
        correct_useful = 0
    filter_ok = _bool(payload.get("filter_ok"))
    if filter_ok is None:
        filter_ok = 1 if was_useful == bool(correct_useful) else 0
    process_ok = _bool(payload.get("process_ok"))
    if process_ok is None:
        if chatter or should_filter:
            process_ok = 0
        elif not was_useful:
            process_ok = 1
        else:
            process_ok = 1 if (selected_score is None or selected_score >= SCORE_MIN) else 0
    if not payload.get("sender") and hit.get("sender"):
        payload = {**payload, "sender": hit.get("sender"), "time_text": hit.get("time_text"),
                   "text": hit.get("text"), "filename": hit.get("filename")}
    row = {
        "source_id": source_id,
        "session_id": session_id,
        "message_id": message_id,
        "sender": (clean_sender(payload.get("sender") or hit.get("sender")) or "未知")[:80],
        "time_text": str(payload.get("time_text") or hit.get("time_text") or "")[:32],
        "filter_ok": filter_ok,
        "filter_note": str(payload.get("filter_note") or "")[:500],
        "intent_ok": 1,
        "intent_note": (
            str(payload.get("intent_note") or "").strip()
            or "；".join(str(x.get("note") or "").strip() for x in items if x.get("note"))
        )[:500],
        "score_ok": _bool(payload.get("score_ok")),
        "score_note": str(payload.get("score_note") or "")[:500],
        "process_ok": process_ok,
        "process_note": str(payload.get("process_note") or "")[:500],
        "passed": 1,
        "passed_note": str(payload.get("passed_note") or "")[:500],
        "correct_useful": correct_useful,
        "correct_intent": selected_intent,
        "correct_score": selected_score,
        "selected_intent": selected_intent,
        "selected_score": selected_score,
        "selected_label": selected_label,
        "selected_intents": json.dumps(items, ensure_ascii=False),
        "should_filter": 1 if should_filter else 0,
        "reviewer": str(payload.get("reviewer") or "组长")[:40],
        "updated_at": now,
    }
    with connect() as con:
        exist = con.execute(
            "SELECT id FROM intent_reviews WHERE session_id=? AND message_id=?",
            (session_id, message_id),
        ).fetchone()
        if exist:
            sets = ", ".join(k + "=?" for k in row)
            con.execute("UPDATE intent_reviews SET " + sets + " WHERE id=?", [*row.values(), exist["id"]])
        else:
            cols = ", ".join(row)
            qs = ", ".join("?" for _ in row)
            con.execute("INSERT INTO intent_reviews (" + cols + ") VALUES (" + qs + ")", list(row.values()))
        con.commit()
    _learn_from_review(payload, row)
    apply_review_to_hit(session_id, message_id, row)
    return get_review(source_id, session_id, message_id)


def get_review(source_id: str, session_id: str, message_id: str) -> dict:
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM intent_reviews WHERE session_id=? AND message_id=?",
            (session_id, str(message_id)),
        ).fetchone()
    return _dump_review(row) if row else {}


def list_reviews(session_id: str = "") -> list[dict]:
    init_db()
    sql = "SELECT * FROM intent_reviews"
    args: list = []
    if session_id:
        sql += " WHERE session_id=?"
        args.append(session_id)
    sql += " ORDER BY updated_at DESC LIMIT 2000"
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [_dump_review(r) for r in rows]


def review_map(session_id: str) -> dict[str, dict]:
    init_db()
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM intent_reviews WHERE session_id=?",
            (session_id,),
        ).fetchall()
    return {str(r["message_id"]): _dump_review(r) for r in rows}


def _dump_review(row: object) -> dict:
    data = dict(row)
    for key in ("filter_ok", "intent_ok", "score_ok", "process_ok", "passed", "correct_useful", "should_filter"):
        val = data.get(key)
        data[key] = None if val is None else bool(val)
    parsed: list = []
    raw = data.get("selected_intents")
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                parsed = loaded
        except json.JSONDecodeError:
            parsed = []
    elif isinstance(raw, list):
        parsed = raw
    if not parsed:
        parsed = _selected_items(data)
    data["selected_intents"] = parsed
    return data


def _norm_drop_key(text: str) -> str:
    return " ".join(str(text or "").split())[:500]


def filtered_message_ids(session_id: str) -> set[str]:
    init_db()
    sid = str(session_id or "").strip()
    if not sid:
        return set()
    with connect() as con:
        rows = con.execute(
            "SELECT message_id FROM intent_reviews WHERE session_id=? AND IFNULL(should_filter,0)=1",
            (sid,),
        ).fetchall()
    return {str(r["message_id"]) for r in rows if r["message_id"] not in (None, "")}


def drop_text_keys() -> set[str]:
    init_db()
    keys: set[str] = set()
    with connect() as con:
        rows = con.execute(
            """
            SELECT text FROM prompt_examples
            WHERE kind='filter' AND label='drop' AND IFNULL(enabled,1)=1
            """
        ).fetchall()
        revs = con.execute(
            """
            SELECT h.text FROM intent_reviews r
            JOIN pipeline_hits h ON h.session_id=r.session_id AND h.message_id=r.message_id
            WHERE IFNULL(r.should_filter,0)=1
            """
        ).fetchall()
    for r in list(rows) + list(revs):
        key = _norm_drop_key(r["text"] or "")
        if key:
            keys.add(key)
    return keys


def drop_match_reason(*, message_id: str = "", text: str = "", filtered_ids: set | None = None, drop_keys: set | None = None) -> str:
    mid = str(message_id or "").strip()
    if mid and filtered_ids is not None and mid in filtered_ids:
        return "组长标记应过滤"
    key = _norm_drop_key(text)
    keys = drop_keys if drop_keys is not None else set()
    if key and key in keys:
        return "命中应过滤样本"
    return ""


def apply_review_to_hit(session_id: str, message_id: str, row: dict) -> None:
    """审核结果立刻改写本条：应过滤退出待审；改回业务意图则重新进入。"""
    hit = get_hit(session_id, message_id)
    if not hit:
        return
    cands = hit.get("candidates") if isinstance(hit.get("candidates"), list) else []
    hit["candidates"] = [c for c in cands if isinstance(c, dict) and not c.get("fallback")]
    should_filter = bool(row.get("should_filter"))
    items = row.get("selected_intents") if isinstance(row.get("selected_intents"), list) else _selected_items(row)
    chatter = bool(items) and all(is_skip_intent(x.get("intent"), x.get("label")) for x in items)
    if should_filter or chatter:
        hit["gemini_useful"] = False
        hit["gemini_reason"] = "组长标记应过滤" if should_filter else "组长改为闲聊/确认"
        if should_filter:
            hit["intent"] = "chat"
            hit["intent_label"] = "应过滤"
            hit["score"] = 0
    else:
        hit["gemini_useful"] = True
        if str(hit.get("gemini_reason") or "").startswith("组长"):
            hit["gemini_reason"] = ""
        if items:
            first = items[0]
            hit["intent"] = str(first.get("intent") or hit.get("intent") or "")
            hit["intent_label"] = str(first.get("label") or hit.get("intent_label") or "")
            if first.get("score") is not None:
                hit["score"] = first["score"]
        try:
            score = int(hit.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score < SCORE_MIN:
            hit["score"] = SCORE_MIN
    save_hits(session_id, str(hit.get("source_id") or "*"), [hit])


def _learn_from_review(payload: dict, row: dict) -> None:
    """把纠正过的样本写入 prompt_examples，后续分析会读进去。"""
    text = str(payload.get("text") or "")[:500]
    filename = str(payload.get("filename") or "")[:240]
    if not text and not filename:
        hit = get_hit(row["session_id"], row["message_id"])
        text = str(hit.get("text") or "")[:500]
        filename = str(hit.get("filename") or "")[:240]
    if not text and not filename:
        return
    reviewer = row.get("reviewer") or "组长"
    sid = row["session_id"]
    mid = row["message_id"]

    if row.get("should_filter"):
        add_example({
            "kind": "filter",
            "text": text,
            "filename": filename,
            "label": "drop",
            "reviewer": reviewer,
            "session_id": sid,
            "message_id": mid,
        })
        return

    with connect() as con:
        con.execute(
            """
            DELETE FROM prompt_examples
            WHERE kind='filter' AND label='drop' AND session_id=? AND message_id=? AND IFNULL(pinned,0)=0
            """,
            (sid, mid),
        )
        con.commit()

    if row.get("filter_ok") == 0 and row.get("correct_useful") is not None:
        add_example({
            "kind": "filter",
            "text": text,
            "filename": filename,
            "label": "keep" if row.get("correct_useful") else "drop",
            "reviewer": reviewer,
            "session_id": sid,
            "message_id": mid,
        })
    elif row.get("filter_ok") == 1 and payload.get("gemini_useful") is not None:
        add_example({
            "kind": "filter",
            "text": text,
            "filename": filename,
            "label": "keep" if payload.get("gemini_useful") else "drop",
            "reviewer": reviewer,
            "session_id": sid,
            "message_id": mid,
        })

    items = row.get("selected_intents") if isinstance(row.get("selected_intents"), list) else _selected_items(row)
    if not items:
        code = str(row.get("selected_intent") or row.get("correct_intent") or payload.get("intent") or "").strip()
        if code:
            items = [{"intent": code, "label": intent_label(code), "score": row.get("selected_score")}]
    if items:
        with connect() as con:
            con.execute(
                """
                DELETE FROM prompt_examples
                WHERE kind='intent' AND session_id=? AND message_id=? AND IFNULL(pinned,0)=0
                """,
                (sid, mid),
            )
            con.commit()
        for it in items:
            note = str(it.get("note") or "").strip()
            blob = text
            if note:
                blob = (text + "\n（组长说明：" + note + "）")[:500]
            add_example({
                "kind": "intent",
                "text": blob,
                "filename": filename,
                "label": it.get("intent") or it.get("label"),
                "score": it.get("score") if it.get("score") is not None else row.get("selected_score"),
                "reviewer": reviewer,
                "session_id": sid,
                "message_id": mid,
            })


def add_example(item: dict) -> None:
    init_db()
    kind = str(item.get("kind") or "").strip()
    if kind not in ("filter", "intent"):
        return
    text = str(item.get("text") or "")[:500]
    filename = str(item.get("filename") or "")[:240]
    label = str(item.get("label") or "")[:40]
    if not label or (not text and not filename):
        return
    sid = str(item.get("session_id") or "")
    mid = str(item.get("message_id") or "")
    score = None if item.get("score") in (None, "") else int(item.get("score") or 0)
    reviewer = str(item.get("reviewer") or "")[:40]
    now = _now()
    with connect() as con:
        exist = None
        if sid and mid:
            exist = con.execute(
                """
                SELECT id, enabled, pinned FROM prompt_examples
                WHERE kind=? AND session_id=? AND message_id=? AND label=?
                """,
                (kind, sid, mid, label),
            ).fetchone()
        if exist:
            con.execute(
                """
                UPDATE prompt_examples
                SET text=?, filename=?, label=?, score=?, reviewer=?, created_at=?
                WHERE id=?
                """,
                (text, filename, label, score, reviewer, now, exist["id"]),
            )
        else:
            con.execute(
                """
                INSERT INTO prompt_examples
                (kind, text, filename, label, score, reviewer, session_id, message_id, created_at, enabled, pinned)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (kind, text, filename, label, score, reviewer, sid, mid, now, 1, 0),
            )
        con.commit()


def _dump_example(row: object) -> dict:
    item = dict(row)
    item["enabled"] = bool(int(item["enabled"])) if item.get("enabled") is not None else True
    item["pinned"] = bool(int(item["pinned"] or 0))
    if item.get("kind") == "intent":
        item["intentLabel"] = intent_label(str(item.get("label") or ""))
    return item


def load_examples(kind: str, limit: int = 16) -> list[dict]:
    """启用中的样本：置顶优先，再按意图/标签交叉取样，供 Gemini / Jev 使用。"""
    init_db()
    cap = max(1, min(int(limit or 16), 48))
    with connect() as con:
        rows = [
            _dump_example(r)
            for r in con.execute(
                """
                SELECT * FROM prompt_examples
                WHERE kind=? AND IFNULL(enabled,1)=1
                ORDER BY IFNULL(pinned,0) DESC, id DESC
                """,
                (kind,),
            ).fetchall()
        ]
    if len(rows) <= cap:
        return rows
    pinned = [r for r in rows if r.get("pinned")]
    rest = [r for r in rows if not r.get("pinned")]
    buckets: dict[str, list] = {}
    order: list[str] = []
    for r in rest:
        lab = str(r.get("label") or "")
        if lab not in buckets:
            buckets[lab] = []
            order.append(lab)
        buckets[lab].append(r)
    mixed: list[dict] = list(pinned)
    i = 0
    while len(mixed) < cap and order:
        lab = order[i % len(order)]
        if buckets.get(lab):
            mixed.append(buckets[lab].pop(0))
            if not buckets[lab]:
                order = [x for x in order if x != lab]
                if not order:
                    break
                i = i % len(order)
                continue
        i += 1
        if i > cap * 8:
            break
    return mixed[:cap]


def list_examples(kind: str = "", *, limit: int = 400) -> list[dict]:
    init_db()
    cap = max(1, min(int(limit or 400), 2000))
    sql = "SELECT * FROM prompt_examples"
    args: list = []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    sql += " ORDER BY IFNULL(pinned,0) DESC, id DESC LIMIT ?"
    args.append(cap)
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [_dump_example(r) for r in rows]


_UNSET = object()


def set_example(
    eid: int,
    *,
    enabled: bool | None = None,
    pinned: bool | None = None,
    text: str | None = None,
    filename: str | None = None,
    label: str | None = None,
    score=_UNSET,
) -> dict:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM prompt_examples WHERE id=?", (int(eid),)).fetchone()
        if not row:
            raise KeyError("样本不存在")
        cur = dict(row)
        if text is not None:
            cur["text"] = str(text)[:500]
        if filename is not None:
            cur["filename"] = str(filename)[:240]
        if label is not None:
            lab = str(label or "").strip()[:40]
            if not lab:
                raise ValueError("意图或标签不能为空")
            if str(cur.get("kind") or "") == "intent":
                code, name = normalize_intent(lab, lab)
                lab = code or name
            elif str(cur.get("kind") or "") == "filter" and lab not in ("keep", "drop"):
                raise ValueError("过滤标签只能是 keep 或 drop")
            cur["label"] = lab
        if score is not _UNSET:
            if score in (None, ""):
                cur["score"] = None
            else:
                try:
                    cur["score"] = int(score)
                except (TypeError, ValueError) as e:
                    raise ValueError("评分须为整数") from e
                if cur["score"] < 0 or cur["score"] > 100:
                    raise ValueError("评分须在 0–100")
        if not str(cur.get("text") or "").strip() and not str(cur.get("filename") or "").strip():
            raise ValueError("原文和文件名不能都为空")
        content_changed = text is not None or filename is not None or label is not None or score is not _UNSET
        if enabled is not None:
            cur["enabled"] = 1 if enabled else 0
        if pinned is not None:
            cur["pinned"] = 1 if pinned else 0
        elif content_changed:
            cur["pinned"] = 1
        con.execute(
            """
            UPDATE prompt_examples
            SET text=?, filename=?, label=?, score=?, enabled=?, pinned=?
            WHERE id=?
            """,
            (
                cur.get("text") or "",
                cur.get("filename") or "",
                cur.get("label") or "",
                cur.get("score"),
                1 if cur.get("enabled") else 0,
                1 if cur.get("pinned") else 0,
                int(eid),
            ),
        )
        con.commit()
        row = con.execute("SELECT * FROM prompt_examples WHERE id=?", (int(eid),)).fetchone()
    return _dump_example(row)


def delete_example(eid: int) -> None:
    init_db()
    with connect() as con:
        con.execute("DELETE FROM prompt_examples WHERE id=?", (int(eid),))
        con.commit()


def example_stats() -> dict:
    init_db()
    with connect() as con:
        ex = con.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN kind='intent' THEN 1 ELSE 0 END) AS intent,
              SUM(CASE WHEN kind='filter' THEN 1 ELSE 0 END) AS filter,
              SUM(CASE WHEN kind='intent' AND IFNULL(enabled,1)=1 THEN 1 ELSE 0 END) AS intentOn,
              SUM(CASE WHEN kind='filter' AND IFNULL(enabled,1)=1 THEN 1 ELSE 0 END) AS filterOn,
              SUM(CASE WHEN IFNULL(pinned,0)=1 THEN 1 ELSE 0 END) AS pinned
            FROM prompt_examples
            """
        ).fetchone()
        rv = con.execute("SELECT COUNT(*) AS n FROM intent_reviews").fetchone()
        runs = con.execute("SELECT COUNT(*) AS n FROM pipeline_runs").fetchone()
    st = dict(ex) if ex else {}
    rv_n = dict(rv) if rv is not None else {}
    run_n = dict(runs) if runs is not None else {}
    return {
        "reviews": int(rv_n.get("n") or 0),
        "runs": int(run_n.get("n") or 0),
        "examples": int(st.get("total") or 0),
        "intent": int(st.get("intent") or 0),
        "filter": int(st.get("filter") or 0),
        "intentOn": int(st.get("intentOn") or 0),
        "filterOn": int(st.get("filterOn") or 0),
        "pinned": int(st.get("pinned") or 0),
        "jevLimit": 16,
    }


def rebuild_examples_from_reviews() -> dict:
    """用全部审核记录重建样本。置顶/停用状态按会话+消息保留。"""
    reviews = list_reviews()
    keep: dict[tuple, tuple] = {}
    with connect() as con:
        for r in con.execute(
            "SELECT kind, session_id, message_id, label, enabled, pinned FROM prompt_examples"
        ).fetchall():
            key = (
                str(r["kind"] or ""),
                str(r["session_id"] or ""),
                str(r["message_id"] or ""),
                str(r["label"] or ""),
            )
            if key[1] and key[2]:
                keep[key] = (int(r["enabled"] if r["enabled"] is not None else 1), int(r["pinned"] or 0))
        con.execute("DELETE FROM prompt_examples WHERE IFNULL(pinned,0)=0")
        con.commit()
    for rev in reviews:
        sid = str(rev.get("session_id") or "")
        mid = str(rev.get("message_id") or "")
        hit = get_hit(sid, mid) if sid and mid else {}
        _learn_from_review(
            {
                "text": hit.get("text") or "",
                "filename": hit.get("filename") or "",
                "gemini_useful": hit.get("gemini_useful"),
            },
            rev,
        )
    with connect() as con:
        for (kind, sid, mid, label), (enabled, pinned) in keep.items():
            con.execute(
                """
                UPDATE prompt_examples SET enabled=?, pinned=?
                WHERE kind=? AND session_id=? AND message_id=? AND label=?
                """,
                (enabled, pinned, kind, sid, mid, label),
            )
        con.commit()
    st = example_stats()
    st["reviewsUsed"] = len(reviews)
    return st
