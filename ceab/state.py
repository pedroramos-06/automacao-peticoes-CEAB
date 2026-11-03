"""Estado durável e comandos transacionais entre UI e worker."""
import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .models import AutomationError, Benefit
from .rules import decide

ACTIVE = ('INICIANDO','EXECUTANDO','PAUSADO','CONFERENCIA','CANCELANDO')
TERMINAL = ('FINALIZADO','IGNORADO')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def default_path():
    return Path(os.environ.get('CEAB_STATE_PATH', Path(__file__).resolve().parents[1] / 'runtime/state.db'))


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class State:
    def __init__(self, path=None):
        self.path = Path(path or default_path()).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS execucao (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL, test_mode INTEGER NOT NULL,
                cdp_url TEXT NOT NULL, created_at TEXT NOT NULL, heartbeat TEXT,
                connected INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', pid INTEGER);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON execucao((1))
                WHERE status IN ('INICIANDO','EXECUTANDO','PAUSADO','CONFERENCIA','CANCELANDO');
            CREATE TABLE IF NOT EXISTS processo (
                number TEXT PRIMARY KEY, execution_id INTEGER NOT NULL REFERENCES execucao(id),
                status TEXT NOT NULL, read_data TEXT, approved_data TEXT, decision TEXT,
                document TEXT, error_code TEXT, message TEXT NOT NULL DEFAULT '',
                simulated INTEGER NOT NULL DEFAULT 0, sent_at TEXT, send_started_at TEXT,
                updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS comando (
                id INTEGER PRIMARY KEY, execution_id INTEGER NOT NULL REFERENCES execucao(id),
                type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                consumed_at TEXT);
            CREATE TABLE IF NOT EXISTS log (
                id INTEGER PRIMARY KEY, execution_id INTEGER REFERENCES execucao(id),
                number TEXT, timestamp TEXT NOT NULL, action TEXT NOT NULL,
                values_json TEXT NOT NULL, result TEXT NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(execucao)')}
            migrations = {'browser_mode':"TEXT NOT NULL DEFAULT 'cdp'",'profile_path':'TEXT','executable_path':'TEXT',
                          'start_url':'TEXT','headless':'INTEGER NOT NULL DEFAULT 0','browser_open':'INTEGER NOT NULL DEFAULT 0',
                          'queue_started':'INTEGER NOT NULL DEFAULT 0'}
            for name,declaration in migrations.items():
                if name not in columns: db.execute(f'ALTER TABLE execucao ADD COLUMN {name} {declaration}')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _log(self, db, execution_id, number, action, values, result):
        db.execute('INSERT INTO log(execution_id,number,timestamp,action,values_json,result) VALUES(?,?,?,?,?,?)',
                   (execution_id,number,now(),action,encode(values),result))

    def log(self, run, number, action, values=None, result='OK'):
        with self.connect() as db: self._log(db,run,number,action,values or {},result)

    def create_execution(self, test_mode=True, cdp_url='http://localhost:9222', *, browser_mode='cdp', executable_path=None, start_url=None, headless=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM execucao WHERE status IN (?,?,?,?,?)', ACTIVE).fetchone():
                raise ValueError('Já existe uma execução ativa. Cancele ou conclua antes de iniciar outra.')
            run = db.execute('INSERT INTO execucao(status,test_mode,cdp_url,created_at,heartbeat) VALUES(?,?,?,?,?)',
                             ('INICIANDO',int(test_mode),cdp_url,now(),now())).lastrowid
            if browser_mode not in ('cdp','persistent'): raise ValueError('Modo de navegador inválido.')
            db.execute('UPDATE execucao SET browser_mode=?,profile_path=?,executable_path=?,start_url=?,headless=? WHERE id=?',
                       (browser_mode,str(self.path.parent/(self.path.stem+'-browser-profile')) if browser_mode=='persistent' else None,executable_path,start_url or 'https://eproc1g.trf6.jus.br/eproc/',int(headless),run))
            db.execute('INSERT INTO comando(execution_id,type,payload,created_at) VALUES(?,?,?,?)',(run,'abrir' if browser_mode=='persistent' else 'iniciar','{}',now()))
            self._log(db,run,None,'INICIAR',{'test_mode':test_mode},'SOLICITADO')
            return run

    def execution(self, run=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM execucao WHERE id=?', (run,)).fetchone() if run else db.execute('SELECT * FROM execucao ORDER BY id DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def claim_execution(self, run, pid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return db.execute("UPDATE execucao SET status='EXECUTANDO',pid=?,heartbeat=? WHERE id=? AND status='INICIANDO'",(pid,now(),run)).rowcount == 1

    def execution_update(self, run, **fields):
        allowed = {'status','connected','message','pid','heartbeat','browser_open','queue_started'}
        if not fields or not set(fields) <= allowed: raise ValueError('Campos de execução inválidos.')
        with self.connect() as db:
            db.execute('UPDATE execucao SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?',(*fields.values(),run))

    def add_process(self, run, number):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM processo WHERE number=?',(number,)).fetchone()
            if old:
                previous_run = db.execute('SELECT status FROM execucao WHERE id=?',(old['execution_id'],)).fetchone()
                retry_status = old['status'] in ('NA_FILA','LIDO','PREENCHIDO','APROVADO','ERRO_LEITURA','ERRO_PREENCHIMENTO') or (old['status']=='IGNORADO' and old['error_code'])
                previous_finished = previous_run['status'] in ('CANCELADO','ERRO','CONCLUIDO')
                restartable = old['execution_id'] != run and not old['sent_at'] and not old['send_started_at'] and (
                    old['simulated'] or (previous_finished and retry_status))
                if restartable:
                    db.execute('UPDATE processo SET execution_id=?,status=?,read_data=NULL,approved_data=NULL,decision=NULL,document=NULL,error_code=NULL,message=?,simulated=0,updated_at=? WHERE number=?',
                               (run,'NA_FILA','Nova leitura; aprovação anterior invalidada.',now(),number))
                    self._log(db,run,number,'REINICIAR_SEM_ENVIO',{},'Nova conferência obrigatória; auditoria anterior preservada.')
                    return True
                reason = ('Envio ou tentativa de envio anterior registrada; não será reenviado.' if old['sent_at'] or old['send_started_at'] else
                          'Processo já registrado nesta execução; não será aberto novamente.' if old['execution_id']==run else
                          'Registro anterior preservado; não está disponível para nova leitura automática. Veja o histórico.')
                self._log(db,run,number,'DUPLICADO',{},reason)
                return False
            db.execute('INSERT INTO processo(number,execution_id,status,updated_at) VALUES(?,?,?,?)',(number,run,'NA_FILA',now()))
            self._log(db,run,number,'NA_FILA',{},'OK')
            return True

    def process(self, number):
        with self.connect() as db:
            row = db.execute('SELECT * FROM processo WHERE number=?',(number,)).fetchone()
            return self._decode(dict(row)) if row else None

    @staticmethod
    def _decode(row):
        for key in ('read_data','approved_data','decision'):
            if row.get(key): row[key] = json.loads(row[key])
        return row

    def processes(self, run=None):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM processo WHERE execution_id=? ORDER BY number',(run,)).fetchall() if run else db.execute('SELECT * FROM processo ORDER BY number').fetchall()
            return [self._decode(dict(r)) for r in rows]

    def update_process(self, number, status, **fields):
        allowed = {'read_data','approved_data','decision','document','error_code','message','simulated','sent_at'}
        if not set(fields) <= allowed: raise ValueError('Campos de processo inválidos.')
        for k in ('read_data','approved_data','decision'):
            if k in fields: fields[k] = encode(fields[k])
        with self.connect() as db:
            old = db.execute('SELECT * FROM processo WHERE number=?',(number,)).fetchone()
            if not old: raise ValueError('Processo não registrado.')
            db.execute('UPDATE processo SET status=?,updated_at=?'+ ''.join(f',{k}=?' for k in fields)+' WHERE number=?',(status,now(),*fields.values(),number))
            self._log(db,old['execution_id'],number,status,fields,'OK')

    def command(self, run, kind, payload=None):
        if kind not in ('iniciar','fechar_navegador','pausar','retomar','cancelar','ignorar','resolver'): raise ValueError('Comando inválido.')
        with self.connect() as db:
            execution = db.execute('SELECT * FROM execucao WHERE id=?',(run,)).fetchone()
            if not execution or (execution['status'] not in ACTIVE and not (kind=='fechar_navegador' and execution['browser_open'])): raise ValueError('Execução encerrada.')
            if kind=='iniciar' and (execution['browser_mode']!='persistent' or not execution['browser_open'] or execution['queue_started']):
                raise ValueError('Navegador indisponível ou processamento já iniciado.')
            db.execute('INSERT INTO comando(execution_id,type,payload,created_at) VALUES(?,?,?,?)',(run,kind,encode(payload or {}),now()))
            self._log(db,run,None,kind.upper(),payload or {},'SOLICITADO')

    def approve(self, run, approvals):
        """Lote atômico: valida tudo antes de marcar qualquer registro APROVADO."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            execution = db.execute('SELECT status FROM execucao WHERE id=?',(run,)).fetchone()
            if not execution or execution['status'] not in ACTIVE: raise ValueError('Execução encerrada.')
            prepared = []
            for number, data in approvals.items():
                row = db.execute('SELECT * FROM processo WHERE number=? AND execution_id=?',(number,run)).fetchone()
                if not row or row['status'] != 'PREENCHIDO' or row['send_started_at'] or row['sent_at']:
                    raise ValueError(f'{number}: não está disponível para aprovação.')
                benefit = Benefit.from_dict(data)
                original = json.loads(row['read_data'])
                if benefit.process != number or any(data.get(k) != original.get(k) for k in ('process','kind','species','nb','amount')):
                    raise ValueError('A conferência só permite editar datas.')
                decision = decide(benefit)
                prepared.append((number,benefit.to_dict(),decision.to_dict()))
            for number,data,decision in prepared:
                db.execute('UPDATE processo SET status=?,approved_data=?,decision=?,updated_at=? WHERE number=?',('APROVADO',encode(data),encode(decision),now(),number))
                self._log(db,run,number,'APROVAR',data,'AUTORIZADO PELO USUÁRIO')
            db.execute('INSERT INTO comando(execution_id,type,payload,created_at) VALUES(?,?,?,?)',(run,'aprovar',encode({'numbers':list(approvals)}),now()))

    def commands(self, run):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM comando WHERE execution_id=? AND consumed_at IS NULL ORDER BY id',(run,)).fetchall()
            for row in rows: db.execute('UPDATE comando SET consumed_at=? WHERE id=?',(now(),row['id']))
            return [{**dict(row),'payload':json.loads(row['payload'])} for row in rows]

    def claim_send(self, number):
        """Persistir ANTES do clique: falhas/crash jamais autorizam um segundo envio."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM processo WHERE number=?',(number,)).fetchone()
            if not row or row['status'] != 'APROVADO' or row['sent_at'] or row['send_started_at']: return False
            db.execute('UPDATE processo SET send_started_at=?,updated_at=? WHERE number=?',(now(),now(),number))
            self._log(db,row['execution_id'],number,'ENVIO_RESERVADO',json.loads(row['approved_data']),'NÃO REPETIR')
            return True

    def notices(self, run):
        with self.connect() as db:
            rows = db.execute("SELECT number,action,values_json,result FROM log WHERE execution_id=? AND action IN ('ABAS_ABERTAS','ABA_NAO_IDENTIFICADA','DUPLICADO') ORDER BY id",(run,)).fetchall()
            return [{**dict(row),'values_json':json.loads(row['values_json'])} for row in rows]

    def export_csv(self):
        with self.connect() as db: rows = db.execute('SELECT * FROM log ORDER BY id').fetchall()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['id','execucao','processo','data_utc','acao','valores','resultado'])
        for row in rows:
            # Evitar fórmulas de planilhas na exportação de textos externos.
            writer.writerow([("'"+str(v)) if str(v).startswith(('=','+','-','@')) else v for v in row])
        return stream.getvalue().encode('utf-8-sig')
