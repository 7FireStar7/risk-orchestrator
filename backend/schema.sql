CREATE TABLE IF NOT EXISTS processes (
  id SERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, avg_duration_sec INT NOT NULL);
CREATE TABLE IF NOT EXISTS data_sources (
  id SERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS process_sources (
  process_id INT REFERENCES processes, source_id INT REFERENCES data_sources,
  PRIMARY KEY (process_id, source_id));
CREATE TABLE IF NOT EXISTS runs (
  id SERIAL PRIMARY KEY,
  process_id INT NOT NULL REFERENCES processes,
  business_date DATE NOT NULL,
  trigger_type TEXT NOT NULL CHECK (trigger_type IN ('SCHEDULED','MANUAL')),
  user_name TEXT,
  priority INT NOT NULL,
  status TEXT NOT NULL DEFAULT 'QUEUED'
    CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','CANCELLED')),
  wait_reason TEXT, error TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE TABLE IF NOT EXISTS run_locks (
  resource_key TEXT PRIMARY KEY,
  run_id INT NOT NULL REFERENCES runs ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS event_log (
  id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
  run_id INT, event TEXT, details TEXT);

INSERT INTO processes(code,name,avg_duration_sec) VALUES
 ('CREDIT','Кредитный риск',20),('MARKET','Рыночный риск',15),
 ('LIQUIDITY','Риск ликвидности',12),('OPERATIONAL','Операционный риск',10),
 ('COUNTERPARTY','Контрагентский риск',18),('STRESS','Стресс-тестирование',30)
ON CONFLICT (code) DO NOTHING;
INSERT INTO data_sources(code,name) VALUES
 ('core_banking','Core Banking'),('market_feed','Рыночные котировки'),
 ('gl_ledger','Главная книга'),('cpty_registry','Реестр контрагентов'),('limits','Лимиты')
ON CONFLICT (code) DO NOTHING;
INSERT INTO process_sources
SELECT p.id, s.id FROM (VALUES
 ('CREDIT','core_banking'),('CREDIT','cpty_registry'),('CREDIT','limits'),
 ('MARKET','market_feed'),('MARKET','limits'),
 ('LIQUIDITY','core_banking'),('LIQUIDITY','gl_ledger'),('LIQUIDITY','market_feed'),
 ('OPERATIONAL','gl_ledger'),
 ('COUNTERPARTY','cpty_registry'),('COUNTERPARTY','market_feed'),('COUNTERPARTY','limits'),
 ('STRESS','core_banking'),('STRESS','market_feed'),('STRESS','gl_ledger'),('STRESS','limits')
) v(pc,sc) JOIN processes p ON p.code=v.pc JOIN data_sources s ON s.code=v.sc
ON CONFLICT DO NOTHING;