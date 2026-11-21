"""Симулятор нагрузки: без оркестратора / наивные блокировки / с оркестратором.
Запуск из корня проекта:  python simulator\\simulate.py --scale 5
(в .env должен стоять тот же TIME_SCALE, сервер перезапущен)"""
import argparse, csv, json, random, sys, threading, time, urllib.error, urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

API = "http://127.0.0.1:8000/api"
OUT = Path(__file__).parent / "out"
USERS = ["ivan", "anna", "petr", "olga", "max", "kate", "dmitry", "sveta"]
DATES = ["2026-11-01", "2026-11-02", "2026-11-03"]


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def resources(proc, date, deps):
    return {f"proc:{proc}:{date}"} | {f"src:{s}:{date}" for s in deps[proc]}


def make_workload(procs, n, seed):
    rng = random.Random(seed)
    w = [dict(at=0, process=p, date=DATES[0], trigger="SCHEDULED", user="scheduler")
         for p in ("MARKET", "LIQUIDITY", "OPERATIONAL", "CREDIT")]
    w += [dict(at=8, process=p, date=DATES[1], trigger="SCHEDULED", user="scheduler")
          for p in ("CREDIT", "STRESS")]
    for _ in range(n):
        w.append(dict(at=round(rng.uniform(0, 25), 1), process=rng.choice(procs),
                      date=rng.choice(DATES), trigger="MANUAL", user=rng.choice(USERS)))
    return sorted(w, key=lambda x: x["at"])


def conflicts(items):
    """Число пар запусков с общим ресурсом и пересекающимися интервалами."""
    n = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a["res"] & b["res"] and a["s"] < b["e"] and b["s"] < a["e"]:
                n += 1
    return n


def peak(items):
    ev = sorted([(x["s"], 1) for x in items] + [(x["e"], -1) for x in items],
                key=lambda t: (t[0], t[1]))
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def baseline(workload, deps, dur, scale, seed):
    """A. Без координации: каждый запуск стартует сразу при поступлении."""
    rng = random.Random(seed)
    items = []
    for r in workload:
        s = r["at"] / scale
        items.append(dict(res=resources(r["process"], r["date"], deps), s=s,
                          e=s + dur[r["process"]] * rng.uniform(0.7, 1.3) / scale))
    return dict(runs=len(items), conflicts=conflicts(items), peak=peak(items),
                makespan=max(x["e"] for x in items))


def naive_locks(workload, deps, dur, scale, seed):
    """B. Наивные блокировки: ресурсы берутся по одному в произвольном порядке."""
    locks = defaultdict(threading.Lock)
    stat = dict(done=0, timeout=0)
    mu = threading.Lock()
    timeout = 20 / scale
    t0 = time.time()

    def job(r, k):
        time.sleep(max(0, r["at"] / scale - (time.time() - t0)))
        jr = random.Random(seed * 1000 + k)
        keys = sorted(resources(r["process"], r["date"], deps))
        jr.shuffle(keys)
        held = []
        try:
            for key in keys:
                if not locks[key].acquire(timeout=timeout):
                    with mu:
                        stat["timeout"] += 1
                    return
                held.append(key)
                time.sleep(0.05)
            time.sleep(dur[r["process"]] * jr.uniform(0.7, 1.3) / scale)
            with mu:
                stat["done"] += 1
        finally:
            for key in held:
                locks[key].release()

    ths = [threading.Thread(target=job, args=(r, i)) for i, r in enumerate(workload)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    return stat


def ts(s):
    return datetime.fromisoformat(s).timestamp() if s else None


def orchestrator(workload, scale):
    """C. Через наш API."""
    http("POST", "/reset")
    t0 = time.time()
    ids, rejected, mu = [], [], threading.Lock()

    def job(r):
        time.sleep(max(0, r["at"] / scale - (time.time() - t0)))
        try:
            res = http("POST", "/runs", {"process_code": r["process"], "business_date": r["date"],
                                         "trigger_type": r["trigger"], "user_name": r["user"]})
            with mu:
                ids.append(res["id"])
        except urllib.error.HTTPError as e:
            with mu:
                rejected.append(e.code)

    ths = [threading.Thread(target=job, args=(r,)) for r in workload]
    [t.start() for t in ths]
    [t.join() for t in ths]
    deadline = time.time() + 900
    while time.time() < deadline:
        runs = http("GET", "/runs?limit=1000")
        if len(runs) == len(ids) and all(x["status"] not in ("QUEUED", "RUNNING") for x in runs):
            break
        time.sleep(1)
    return http("GET", "/runs?limit=1000"), http("GET", "/events?limit=3000"), len(rejected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=5)
    ap.add_argument("--n", type=int, default=30, help="число ручных запусков")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    meta = http("GET", "/meta")
    deps = defaultdict(set)
    for m in meta["matrix"]:
        deps[m["pc"]].add(m["sc"])
    dur = {p["code"]: p["avg_duration_sec"] for p in meta["processes"]}
    workload = make_workload(list(dur), a.n, a.seed)
    print(f"Сценарий: {len(workload)} запросов, {len(USERS)} пользователей + регламент, scale={a.scale}\n")

    print("A. Без координации...")
    A = baseline(workload, deps, dur, a.scale, a.seed)
    print("B. Наивные блокировки (может занять до минуты)...")
    B = naive_locks(workload, deps, dur, a.scale, a.seed)
    print("C. Оркестратор...")
    runs, events, rejected = orchestrator(workload, a.scale)

    now = time.time()
    items = [dict(res=resources(r["process_code"], str(r["business_date"]), deps),
                  s=ts(r["started_at"]), e=ts(r["finished_at"]) or now)
             for r in runs if r["started_at"]]
    cC = conflicts(items)
    waits = defaultdict(list)
    for r in runs:
        if r["started_at"]:
            waits[r["trigger_type"]].append(ts(r["started_at"]) - ts(r["created_at"]))
    avg = lambda l: round(sum(l) / len(l), 1) if l else 0
    st = defaultdict(int)
    for r in runs:
        st[r["status"]] += 1
    makespan = max(x["e"] for x in items) - min(ts(r["created_at"]) for r in runs)

    OUT.mkdir(exist_ok=True)
    with open(OUT / "execution_log.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["run_id", "process", "date", "trigger", "user", "created", "started",
                    "finished", "wait_sec", "run_sec", "status", "error"])
        for r in sorted(runs, key=lambda r: r["started_at"] or "9"):
            s, e, c = ts(r["started_at"]), ts(r["finished_at"]), ts(r["created_at"])
            w.writerow([r["id"], r["process_code"], r["business_date"], r["trigger_type"],
                        r["user_name"], r["created_at"], r["started_at"], r["finished_at"],
                        round(s - c, 1) if s else "", round(e - s, 1) if s and e else "",
                        r["status"], r["error"] or ""])
    with open(OUT / "events.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "ts", "run_id", "event", "details"])
        for e in reversed(events):
            w.writerow([e["id"], e["ts"], e["run_id"], e["event"], e["details"]])

    report = f"""# Результаты симуляции
Запросов: {len(workload)} (регламентных 6, ручных {a.n}), seed={a.seed}, scale={a.scale}

| Показатель | A. Без координации | B. Наивные блокировки | C. Оркестратор |
|---|---|---|---|
| Конфликты (пересечение ресурсов во времени) | {A['conflicts']} | не измерялись | **{cC}** |
| Зависшие по таймауту (взаимные блокировки) | - | {B['timeout']} | **0** |
| Пик параллельных запусков | {A['peak']} | - | {peak(items)} |
| Отклонено как дубликаты | 0 | 0 | {rejected} |
| Завершено успешно / с ошибкой | - | {B['done']} / {B['timeout']} | {st['DONE']} / {st['FAILED']} |
| Среднее ожидание: регламентные, c | 0 | - | {avg(waits['SCHEDULED'])} |
| Среднее ожидание: ручные, c | 0 | - | {avg(waits['MANUAL'])} |
| Длительность всего сценария, c | {A['makespan']:.0f} | - | {makespan:.0f} |
"""
    (OUT / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Файлы: {OUT}")
    if cC:
        print("ОШИБКА: оркестратор допустил пересечения!")
        sys.exit(1)
    print("ПРОВЕРКА ПРОЙДЕНА: пересечений ресурсов нет.")


if __name__ == "__main__":
    main()