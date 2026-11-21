import os, random, threading, time
from db import conn

AGING_SEC = float(os.getenv("AGING_SEC", "10"))
TIME_SCALE = float(os.getenv("TIME_SCALE", "1"))
FAIL_PROB = float(os.getenv("FAIL_PROB", "0.1"))
PRIORITY = {"SCHEDULED": 100, "MANUAL": 50}
LOCK_ID = 1  # глобальный advisory-lock: сериализует все решения диспетчера


class Duplicate(Exception):
    def __init__(self, run_id):
        self.run_id = run_id


def log(cur, run_id, event, details=""):
    cur.execute("INSERT INTO event_log(run_id,event,details) VALUES(%s,%s,%s)",
                (run_id, event, details))


def res_keys(cur, process_id, d):
    """Ресурсы запуска: сам процесс+дата и каждый источник+дата. Отсортированы."""
    cur.execute("SELECT code FROM processes WHERE id=%s", (process_id,))
    pc = cur.fetchone()["code"]
    cur.execute("""SELECT s.code FROM process_sources ps
                   JOIN data_sources s ON s.id=ps.source_id WHERE ps.process_id=%s""",
                (process_id,))
    keys = [f"proc:{pc}:{d}"] + [f"src:{r['code']}:{d}" for r in cur.fetchall()]
    return sorted(keys)


def submit(process_code, business_date, trigger, user):
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        cur.execute("SELECT id FROM processes WHERE code=%s", (process_code,))
        p = cur.fetchone()
        if not p:
            raise ValueError("Неизвестный процесс")
        cur.execute("""SELECT id FROM runs WHERE process_id=%s AND business_date=%s
                       AND status IN ('QUEUED','RUNNING')""", (p["id"], business_date))
        dup = cur.fetchone()
        if dup:
            raise Duplicate(dup["id"])
        cur.execute("""INSERT INTO runs(process_id,business_date,trigger_type,user_name,priority)
                       VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                    (p["id"], business_date, trigger, user, PRIORITY[trigger]))
        rid = cur.fetchone()["id"]
        log(cur, rid, "SUBMITTED", f"{process_code} {business_date} {trigger} {user}")
    dispatch()
    return rid


def dispatch():
    """Всё-или-ничего: запуск стартует, только если свободны ВСЕ его ресурсы.
    Очередь по эффективному приоритету (priority + aging). Ресурсы заблокированных
    запусков резервируются от более слабых, чтобы не было голодания."""
    started = []
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        cur.execute("""SELECT id, process_id, business_date, wait_reason FROM runs
                       WHERE status='QUEUED'
                       ORDER BY priority + EXTRACT(EPOCH FROM now()-created_at)/%s::float DESC,
                                created_at, id""", (AGING_SEC,))
        queued = cur.fetchall()
        cur.execute("SELECT resource_key, run_id FROM run_locks")
        held = {r["resource_key"]: r["run_id"] for r in cur.fetchall()}
        reserved = {}
        for r in queued:
            keys = res_keys(cur, r["process_id"], r["business_date"])
            busy = [(k, held[k]) for k in keys if k in held]
            ahead = [(k, reserved[k]) for k in keys if k in reserved]
            if not busy and not ahead:
                for k in keys:
                    cur.execute("INSERT INTO run_locks(resource_key,run_id) VALUES(%s,%s)",
                                (k, r["id"]))
                    held[k] = r["id"]
                cur.execute("""UPDATE runs SET status='RUNNING', started_at=now(),
                               wait_reason=NULL WHERE id=%s""", (r["id"],))
                log(cur, r["id"], "STARTED", f"ресурсы: {', '.join(keys)}")
                started.append(r["id"])
            else:
                if busy:
                    k, h = busy[0]
                    reason = f"занят ресурс {k} запуском #{h}"
                else:
                    k, h = ahead[0]
                    reason = f"в очереди после запуска #{h} (общий ресурс {k})"
                for k in keys:
                    reserved.setdefault(k, r["id"])
                if reason != r["wait_reason"]:
                    cur.execute("UPDATE runs SET wait_reason=%s WHERE id=%s", (reason, r["id"]))
                    log(cur, r["id"], "WAITING", reason)
    for rid in started:
        threading.Thread(target=_worker, args=(rid,), daemon=True).start()
    return started


def _worker(run_id):
    with conn() as c, c.cursor() as cur:
        cur.execute("""SELECT p.avg_duration_sec d FROM runs r
                       JOIN processes p ON p.id=r.process_id WHERE r.id=%s""", (run_id,))
        d = cur.fetchone()["d"]
    time.sleep(d * random.uniform(0.7, 1.3) / TIME_SCALE)
    failed = random.random() < FAIL_PROB
    finish(run_id, "FAILED" if failed else "DONE",
           "Имитация сбоя расчёта" if failed else None)
    dispatch()


def finish(run_id, status, error=None):
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        cur.execute("""UPDATE runs SET status=%s, finished_at=now(), error=%s
                       WHERE id=%s AND status='RUNNING' RETURNING id""",
                    (status, error, run_id))
        if cur.fetchone():
            cur.execute("DELETE FROM run_locks WHERE run_id=%s", (run_id,))
            log(cur, run_id, status, error or "")


def cancel(run_id):
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        cur.execute("SELECT status FROM runs WHERE id=%s", (run_id,))
        r = cur.fetchone()
        if not r or r["status"] not in ("QUEUED", "RUNNING"):
            return False
        cur.execute("""UPDATE runs SET status='CANCELLED', finished_at=now(), wait_reason=NULL
                       WHERE id=%s""", (run_id,))
        cur.execute("DELETE FROM run_locks WHERE run_id=%s", (run_id,))
        log(cur, run_id, "CANCELLED", "отменён пользователем")
    dispatch()
    return True


def preview(process_code, business_date):
    """Предпроверка конфликтов до запуска (для UI)."""
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM processes WHERE code=%s", (process_code,))
        p = cur.fetchone()
        if not p:
            raise ValueError("Неизвестный процесс")
        keys = res_keys(cur, p["id"], business_date)
        cur.execute("""SELECT l.resource_key, l.run_id FROM run_locks l
                       WHERE l.resource_key = ANY(%s)""", (keys,))
        busy = cur.fetchall()
        cur.execute("""SELECT id FROM runs WHERE process_id=%s AND business_date=%s
                       AND status IN ('QUEUED','RUNNING')""", (p["id"], business_date))
        dup = cur.fetchone()
    return {"resources": keys, "busy": busy, "can_start_now": not busy and not dup,
            "duplicate_run_id": dup["id"] if dup else None}


def recover():
    """После перезапуска сервиса: зависшие RUNNING помечаем FAILED, блокировки снимаем."""
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        cur.execute("""UPDATE runs SET status='FAILED', finished_at=now(),
                       error='Прерван перезапуском сервиса' WHERE status='RUNNING'
                       RETURNING id""")
        for r in cur.fetchall():
            log(cur, r["id"], "FAILED", "Прерван перезапуском сервиса")
        cur.execute("DELETE FROM run_locks")
    dispatch()


def reset():
    with conn() as c:
        c.execute("TRUNCATE run_locks, event_log, runs RESTART IDENTITY CASCADE")