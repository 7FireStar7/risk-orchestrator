import { useCallback, useEffect, useState } from 'react'

/* ---------- утилиты ---------- */
const api = async (path, opts) => {
  const r = await fetch('/api' + path, opts)
  if (!r.ok) {
    let m = r.statusText
    try { m = (await r.json()).detail || m } catch {}
    throw new Error(typeof m === 'string' ? m : JSON.stringify(m))
  }
  return r.json()
}
const post = (path, body) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })

const T = (s) => (s ? Date.parse(s) : null)
const clock = (s) => (s ? new Date(s).toLocaleTimeString('ru-RU') : '—')
const dur = (ms) => {
  const s = Math.max(0, Math.round(ms / 1000))
  return s >= 60 ? `${Math.floor(s / 60)}м ${s % 60}с` : `${s}с`
}
const COLORS = ['#4f8cff', '#2dd4bf', '#f59e0b', '#a78bfa', '#f472b6', '#84cc16']
const STATUS_RU = { QUEUED: 'В очереди', RUNNING: 'Выполняется', DONE: 'Готово', FAILED: 'Ошибка', CANCELLED: 'Отменён' }
const TRIG_RU = { SCHEDULED: 'Регламент', MANUAL: 'Ручной' }
const TABS = [['dash', 'Дашборд'], ['new', 'Новый запуск'], ['gantt', 'Таймлайн'], ['log', 'Журнал и матрица']]
const DEMO = [
  ['MARKET', '2026-11-01', 'SCHEDULED', 'scheduler'],
  ['LIQUIDITY', '2026-11-01', 'SCHEDULED', 'scheduler'],
  ['OPERATIONAL', '2026-11-01', 'SCHEDULED', 'scheduler'],
  ['CREDIT', '2026-11-01', 'MANUAL', 'ivan'],
  ['STRESS', '2026-11-01', 'MANUAL', 'anna'],
  ['COUNTERPARTY', '2026-11-01', 'MANUAL', 'petr'],
  ['CREDIT', '2026-11-02', 'MANUAL', 'olga'],
  ['CREDIT', '2026-11-01', 'MANUAL', 'max'], // дубликат -> будет отклонён
  ['STRESS', '2026-11-02', 'SCHEDULED', 'scheduler'],
]

/* ---------- приложение ---------- */
export default function App() {
  const [tab, setTab] = useState('dash')
  const [meta, setMeta] = useState(null)
  const [runs, setRuns] = useState([])
  const [events, setEvents] = useState([])
  const [stats, setStats] = useState(null)
  const [online, setOnline] = useState(true)
  const [now, setNow] = useState(Date.now())
  const [toast, setToast] = useState(null)

  const notify = (msg, kind = 'ok') => {
    setToast({ msg, kind })
    setTimeout(() => setToast(null), 4000)
  }

  const load = useCallback(async () => {
    try {
      const [m, r, e, s] = await Promise.all([api('/meta'), api('/runs?limit=500'), api('/events?limit=500'), api('/stats')])
      setMeta(m); setRuns(r); setEvents(e); setStats(s); setOnline(true)
    } catch { setOnline(false) }
  }, [])

  useEffect(() => {
    load()
    const a = setInterval(load, 1500)
    const b = setInterval(() => setNow(Date.now()), 1000)
    return () => { clearInterval(a); clearInterval(b) }
  }, [load])

  const colorOf = (code) => COLORS[Math.max(0, meta?.processes.findIndex((p) => p.code === code) ?? 0) % COLORS.length]
  const sig = runs.filter((r) => r.status === 'RUNNING' || r.status === 'QUEUED').map((r) => r.id + r.status).join(',')

  const cancel = async (id) => {
    try { await post(`/runs/${id}/cancel`); notify(`Запуск #${id} отменён`); load() } catch (e) { notify(e.message, 'bad') }
  }
  const reset = async () => {
    if (!window.confirm('Очистить все запуски и журнал?')) return
    await post('/reset'); notify('Данные очищены'); load()
  }
  const demo = async () => {
    let rejected = 0
    for (const [process_code, business_date, trigger_type, user_name] of DEMO) {
      try { await post('/runs', { process_code, business_date, trigger_type, user_name }) } catch { rejected++ }
      await new Promise((r) => setTimeout(r, 150))
    }
    notify(`Демо отправлено: ${DEMO.length - rejected} принято, ${rejected} отклонено (дубликат)`)
    load()
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">◈</span>
          <div><h1>Risk Orchestrator</h1><small>Оркестратор процессов оценки рисков</small></div>
        </div>
        <nav>
          {TABS.map(([k, v]) => (
            <button key={k} className={'tab' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{v}</button>
          ))}
        </nav>
        <div className="actions">
          <span className={'dot ' + (online ? 'ok' : 'bad')} />
          <small>{online ? 'API online' : 'API недоступен'}</small>
          <button className="btn ghost" onClick={demo}>▶ Демо-сценарий</button>
          <button className="btn ghost danger" onClick={reset}>Сброс</button>
        </div>
      </header>

      <main>
        {!meta ? <p className="mute">Подключение к API…</p> : (
          <>
            {tab === 'dash' && <Dashboard runs={runs} stats={stats} now={now} colorOf={colorOf} onCancel={cancel} />}
            {tab === 'new' && <NewRun meta={meta} runs={runs} sig={sig} notify={notify} colorOf={colorOf} />}
            {tab === 'gantt' && <Timeline runs={runs} meta={meta} now={now} colorOf={colorOf} />}
            {tab === 'log' && <LogTab meta={meta} events={events} />}
          </>
        )}
      </main>
      {toast && <div className={'toast ' + toast.kind}>{toast.msg}</div>}
    </div>
  )
}

/* ---------- дашборд ---------- */
function Badge({ r }) {
  return <span className={'badge ' + r.trigger_type}>{TRIG_RU[r.trigger_type]}</span>
}

function Dashboard({ runs, stats, now, colorOf, onCancel }) {
  const by = (s) => runs.filter((r) => r.status === s)
  const running = by('RUNNING')
  const queued = by('QUEUED').sort((a, b) => a.id - b.id)
  const recent = runs.filter((r) => ['DONE', 'FAILED', 'CANCELLED'].includes(r.status)).slice(0, 8)
  const w = Object.fromEntries((stats?.avg_wait || []).map((x) => [x.trigger_type, Number(x.avg_wait_sec)]))
  const kpi = [
    ['Выполняются', running.length, 'run'], ['В очереди', queued.length, 'queue'],
    ['Завершено', by('DONE').length, 'done'], ['Ошибки', by('FAILED').length, 'fail'],
    ['Занято ресурсов', stats?.locked_resources ?? 0, 'lock'],
    ['Ср. ожидание, с (рег. / ручн.)', `${w.SCHEDULED ?? '—'} / ${w.MANUAL ?? '—'}`, 'wait'],
  ]
  return (
    <>
      <section className="kpis">
        {kpi.map(([l, v, c]) => (
          <div key={l} className={'kpi ' + c}><div className="v">{v}</div><div className="l">{l}</div></div>
        ))}
      </section>
      <div className="cols">
        <section className="card">
          <h2>Выполняются <span className="cnt">{running.length}</span></h2>
          {running.length === 0 && <p className="mute">Нет активных запусков</p>}
          {running.map((r) => (
            <div key={r.id} className="item" style={{ borderLeftColor: colorOf(r.process_code) }}>
              <div className="row"><b>#{r.id} {r.process_name}</b><span className="pulse" /></div>
              <div className="sub">дата {r.business_date} · {r.user_name} · <Badge r={r} /></div>
              <div className="sub">идёт {dur(now - T(r.started_at))}, ждал {dur(T(r.started_at) - T(r.created_at))}</div>
              <button className="btn tiny" onClick={() => onCancel(r.id)}>Отменить</button>
            </div>
          ))}
        </section>
        <section className="card">
          <h2>Очередь <span className="cnt">{queued.length}</span></h2>
          {queued.length === 0 && <p className="mute">Очередь пуста</p>}
          {queued.map((r) => (
            <div key={r.id} className="item queued" style={{ borderLeftColor: colorOf(r.process_code) }}>
              <div className="row"><b>#{r.id} {r.process_name}</b><span className="mute">ждёт {dur(now - T(r.created_at))}</span></div>
              <div className="sub">дата {r.business_date} · {r.user_name} · <Badge r={r} /></div>
              <div className="why">⏳ {r.wait_reason || 'определяется…'}</div>
              <button className="btn tiny" onClick={() => onCancel(r.id)}>Отменить</button>
            </div>
          ))}
        </section>
      </div>
      <section className="card">
        <h2>Последние завершённые</h2>
        <table>
          <thead><tr><th>#</th><th>Процесс</th><th>Дата</th><th>Тип</th><th>Пользователь</th><th>Ожидание</th><th>Выполнение</th><th>Статус</th></tr></thead>
          <tbody>
            {recent.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td><td>{r.process_name}</td><td>{r.business_date}</td><td><Badge r={r} /></td><td>{r.user_name}</td>
                <td>{r.started_at ? dur(T(r.started_at) - T(r.created_at)) : '—'}</td>
                <td>{r.started_at && r.finished_at ? dur(T(r.finished_at) - T(r.started_at)) : '—'}</td>
                <td><span className={'st ' + r.status}>{STATUS_RU[r.status]}</span>{r.error ? <small className="mute"> {r.error}</small> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}

/* ---------- новый запуск ---------- */
function NewRun({ meta, runs, sig, notify, colorOf }) {
  const [proc, setProc] = useState('')
  const [date, setDate] = useState('2026-11-01')
  const [trigger, setTrigger] = useState('MANUAL')
  const [user, setUser] = useState('ivan')
  const [prev, setPrev] = useState(null)
  const [busy, setBusy] = useState(false)
  const code = proc || meta.processes[0]?.code

  useEffect(() => {
    if (!code || !date) return
    const t = setTimeout(() => post('/runs/preview', { process_code: code, business_date: date }).then(setPrev).catch(() => setPrev(null)), 200)
    return () => clearTimeout(t)
  }, [code, date, sig])

  const submit = async () => {
    setBusy(true)
    try {
      const r = await post('/runs', { process_code: code, business_date: date, trigger_type: trigger, user_name: user || 'anonymous' })
      notify(`Запуск #${r.id} создан`)
    } catch (e) { notify(e.message, 'bad') }
    setBusy(false)
  }

  const holder = (id) => runs.find((r) => r.id === id)
  const busyMap = Object.fromEntries((prev?.busy || []).map((b) => [b.resource_key, b.run_id]))
  const state = !prev ? null : prev.duplicate_run_id ? 'dup' : prev.can_start_now ? 'free' : 'wait'

  return (
    <div className="cols">
      <section className="card">
        <h2>Новый запуск</h2>
        <label>Процесс
          <select value={code} onChange={(e) => setProc(e.target.value)}>
            {meta.processes.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
          </select>
        </label>
        <label>Дата расчёта
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <label>Тип запуска
          <div className="seg2">
            {['MANUAL', 'SCHEDULED'].map((t) => (
              <button key={t} className={trigger === t ? 'on' : ''} onClick={() => setTrigger(t)}>{TRIG_RU[t]}</button>
            ))}
          </div>
        </label>
        <label>Пользователь
          <input value={user} onChange={(e) => setUser(e.target.value)} />
        </label>
        <button className="btn primary" disabled={busy || state === 'dup'} onClick={submit}>
          {state === 'wait' ? 'Поставить в очередь' : 'Запустить'}
        </button>
      </section>

      <section className="card">
        <h2>Предпроверка конфликтов</h2>
        {!prev && <p className="mute">Проверяем…</p>}
        {state === 'free' && <div className="verdict ok">✔ Все ресурсы свободны — запуск стартует сразу</div>}
        {state === 'wait' && <div className="verdict warn">⏳ Часть ресурсов занята — запуск встанет в очередь и стартует автоматически</div>}
        {state === 'dup' && <div className="verdict bad">✖ Такой запуск уже в работе (#{prev.duplicate_run_id}). Повторный будет отклонён</div>}
        {prev && (
          <>
            <p className="mute">Ресурсы, которые займёт запуск:</p>
            <div className="chips">
              {prev.resources.map((k) => {
                const h = busyMap[k] && holder(busyMap[k])
                return (
                  <span key={k} className={'chip' + (busyMap[k] ? ' busy' : '')}>
                    {k}{busyMap[k] ? ` ← #${busyMap[k]}${h ? ' ' + h.process_name : ''}` : ''}
                  </span>
                )
              })}
            </div>
          </>
        )}
      </section>
    </div>
  )
}

/* ---------- таймлайн ---------- */
function Timeline({ runs, meta, now, colorOf }) {
  const [win, setWin] = useState(5)
  const created = runs.map((r) => T(r.created_at))
  const start = win ? now - win * 60000 : Math.min(...created, now - 30000)
  const span = Math.max(now - start, 1000)
  const pct = (t) => Math.min(100, Math.max(0, ((t - start) / span) * 100))

  const rows = meta.processes.map((p) => {
    const rs = runs
      .filter((r) => r.process_code === p.code)
      .map((r) => ({ r, s: T(r.created_at), run0: T(r.started_at), e: T(r.finished_at) ?? now }))
      .filter((x) => x.e >= start)
      .sort((a, b) => a.s - b.s)
    const lanes = []
    rs.forEach((x) => {
      let i = lanes.findIndex((end) => end <= x.s)
      if (i < 0) { i = lanes.length; lanes.push(0) }
      lanes[i] = x.e; x.lane = i
    })
    return { p, rs, n: Math.max(1, lanes.length) }
  })
  const ticks = [0, 1, 2, 3, 4, 5].map((i) => start + (span * i) / 5)

  return (
    <section className="card">
      <div className="row">
        <h2>Таймлайн выполнения</h2>
        <div className="seg2">
          {[[2, '2 мин'], [5, '5 мин'], [15, '15 мин'], [0, 'Всё']].map(([v, l]) => (
            <button key={v} className={win === v ? 'on' : ''} onClick={() => setWin(v)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="legend">
        <span><i className="sw hatch" /> ожидание в очереди</span>
        <span><i className="sw" style={{ background: '#4f8cff' }} /> выполнение</span>
        <span><i className="sw fail" /> ошибка</span>
        <span className="mute">(цвет = процесс; параллельные запуски на отдельных дорожках)</span>
      </div>
      <div className="gantt">
        {rows.map(({ p, rs, n }) => (
          <div key={p.code} className="grow">
            <div className="glabel" style={{ color: colorOf(p.code) }}>{p.name}</div>
            <div className="gtrack" style={{ height: n * 26 + 6 }}>
              {rs.map((x) => {
                const tip = `#${x.r.id} ${p.name}\nдата ${x.r.business_date}\n${TRIG_RU[x.r.trigger_type]} · ${x.r.user_name}\n${STATUS_RU[x.r.status]}\nожидание ${dur((x.run0 ?? x.e) - x.s)}`
                const top = x.lane * 26 + 3
                const waitEnd = x.run0 ?? x.e
                return (
                  <div key={x.r.id}>
                    <div className={'bar wait' + (x.r.status === 'CANCELLED' ? ' cancelled' : '')} title={tip}
                      style={{ top, left: pct(x.s) + '%', width: Math.max(0.2, pct(waitEnd) - pct(x.s)) + '%' }} />
                    {x.run0 && (
                      <div className={'bar run ' + x.r.status} title={tip}
                        style={{ top, left: pct(x.run0) + '%', width: Math.max(0.6, pct(x.e) - pct(x.run0)) + '%', background: x.r.status === 'FAILED' ? undefined : colorOf(p.code) }}>
                        #{x.r.id} {x.r.business_date.slice(5)}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ))}
        <div className="grow axis">
          <div className="glabel" />
          <div className="gtrack axisrow">
            {ticks.map((t, i) => <span key={i} style={{ left: (i / 5) * 100 + '%' }}>{clock(t)}</span>)}
          </div>
        </div>
      </div>
    </section>
  )
}

/* ---------- журнал и матрица ---------- */
function LogTab({ meta, events }) {
  const [runF, setRunF] = useState('')
  const [evF, setEvF] = useState('')
  const has = (pc, sc) => meta.matrix.some((m) => m.pc === pc && m.sc === sc)
  const shared = (sc) => meta.matrix.filter((m) => m.sc === sc).length
  const list = events.filter((e) => (!runF || String(e.run_id) === runF.trim()) && (!evF || e.event === evF)).slice(0, 200)
  const kinds = [...new Set(events.map((e) => e.event))]

  return (
    <>
      <section className="card">
        <h2>Матрица «процесс × источник данных»</h2>
        <p className="mute">Источник, который читают несколько процессов, — потенциальная точка конфликта.</p>
        <table className="matrix">
          <thead><tr><th />{meta.sources.map((s) => <th key={s.code}>{s.name}</th>)}</tr></thead>
          <tbody>
            {meta.processes.map((p) => (
              <tr key={p.code}><td className="pn">{p.name}</td>{meta.sources.map((s) => <td key={s.code}>{has(p.code, s.code) ? <span className="mdot" /> : ''}</td>)}</tr>
            ))}
            <tr className="sum"><td className="pn">Процессов на источник</td>{meta.sources.map((s) => <td key={s.code}><b>{shared(s.code)}</b></td>)}</tr>
          </tbody>
        </table>
      </section>
      <section className="card">
        <div className="row">
          <h2>Журнал событий</h2>
          <div className="filters">
            <input placeholder="№ запуска" value={runF} onChange={(e) => setRunF(e.target.value)} />
            <select value={evF} onChange={(e) => setEvF(e.target.value)}>
              <option value="">все события</option>
              {kinds.map((k) => <option key={k}>{k}</option>)}
            </select>
          </div>
        </div>
        <table>
          <thead><tr><th>Время</th><th>Запуск</th><th>Событие</th><th>Детали</th></tr></thead>
          <tbody>
            {list.map((e) => (
              <tr key={e.id}><td>{clock(e.ts)}</td><td>#{e.run_id}</td><td><span className={'ev ' + e.event}>{e.event}</span></td><td className="det">{e.details}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}