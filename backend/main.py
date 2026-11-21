import threading, time
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from db import conn, init_db
import orchestrator as orch


def _ticker():  # периодический пересчёт очереди (aging) и страховка
    while True:
        time.sleep(2)
        try:
            orch.dispatch()
        except Exception as e:
            print("dispatch error:", e)


@asynccontextmanager
async def lifespan(app):
    init_db()
    orch.recover()
    threading.Thread(target=_ticker, daemon=True).start()
    yield


app = FastAPI(title="Risk Orchestrator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")

class RunIn(BaseModel):
    process_code: str
    business_date: date
    trigger_type: Literal["SCHEDULED", "MANUAL"] = "MANUAL"
    user_name: Optional[str] = "anonymous"


class PreviewIn(BaseModel):
    process_code: str
    business_date: date


@app.get("/api/meta")
def meta():
    with conn() as c:
        procs = c.execute("SELECT * FROM processes ORDER BY id").fetchall()
        srcs = c.execute("SELECT * FROM data_sources ORDER BY id").fetchall()
        mat = c.execute("""SELECT p.code pc, s.code sc FROM process_sources ps
            JOIN processes p ON p.id=ps.process_id JOIN data_sources s ON s.id=ps.source_id""").fetchall()
    return {"processes": procs, "sources": srcs, "matrix": mat}


@app.get("/api/runs")
def runs(status: Optional[str] = None, limit: int = 300):
    q = """SELECT r.*, p.code process_code, p.name process_name FROM runs r
           JOIN processes p ON p.id=r.process_id"""
    args = []
    if status:
        q += " WHERE r.status=%s"; args.append(status)
    q += " ORDER BY r.id DESC LIMIT %s"; args.append(limit)
    with conn() as c:
        return c.execute(q, args).fetchall()


@app.post("/api/runs", status_code=201)
def create_run(body: RunIn):
    try:
        rid = orch.submit(body.process_code, body.business_date, body.trigger_type, body.user_name)
    except orch.Duplicate as d:
        raise HTTPException(409, f"Такой запуск уже в работе: #{d.run_id}")
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"id": rid}


@app.post("/api/runs/preview")
def preview(body: PreviewIn):
    try:
        return orch.preview(body.process_code, body.business_date)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: int):
    if not orch.cancel(run_id):
        raise HTTPException(409, "Запуск нельзя отменить")
    return {"ok": True}


@app.get("/api/events")
def events(limit: int = 300):
    with conn() as c:
        return c.execute("SELECT * FROM event_log ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


@app.get("/api/stats")
def stats():
    with conn() as c:
        by_status = c.execute("SELECT status, count(*) n FROM runs GROUP BY status").fetchall()
        wait = c.execute("""SELECT trigger_type, round(avg(EXTRACT(EPOCH FROM started_at-created_at))::numeric,1) avg_wait_sec
                            FROM runs WHERE started_at IS NOT NULL GROUP BY trigger_type""").fetchall()
        locks = c.execute("SELECT count(*) n FROM run_locks").fetchone()
    return {"by_status": by_status, "avg_wait": wait, "locked_resources": locks["n"]}


@app.post("/api/reset")
def reset():
    orch.reset()
    return {"ok": True}