import sqlite3
import json
import logging
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "homebot.db")
_db_path_override = None

def _get_db_path():
    return _db_path_override if _db_path_override is not None else DB_PATH


class Persistence:
    """
    Camada de Persistência do ROD.

    RESPONSABILIDADES:
    - Armazenar dados RAW
    - Histórico temporal
    - ZERO interpretação
    """

    _db_path = None

    @staticmethod
    def _add_column_if_missing(cursor, table: str, column_sql: str):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                return
            raise

    @classmethod
    def set_db_path(cls, path):
        global _db_path_override
        _db_path_override = path

    @classmethod
    def get_db_path(cls):
        return _get_db_path()

    # ==================================================
    # INIT
    # ==================================================
    @staticmethod
    def init_db():
        with closing(sqlite3.connect(_get_db_path())) as conn:
            # Otimizações para Raspberry Pi
            conn.execute("PRAGMA journal_mode=WAL")  # Menos I/O
            conn.execute("PRAGMA synchronous=NORMAL")  # Performance vs segurança
            conn.execute("PRAGMA cache_size=10000")  # Mais cache
            conn.execute("PRAGMA temp_store=MEMORY")  # Temp em RAM

            c = conn.cursor()

            c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                type TEXT,
                source TEXT,
                payload TEXT,
                timestamp TEXT
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            """)

            # Tabela de tarefas atualizada
            c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                text TEXT,
                action TEXT,
                type TEXT, -- 'unique' or 'recurring'
                interval_minutes INTEGER,
                next_run TEXT, -- ISO format datetime
                status TEXT, -- 'active', 'paused', 'cancelled', 'completed'
                meta TEXT, -- JSON with extra fields (meta_ml, cup_ml, etc)
                created_at TEXT
            )
            """)

            # Migração simples: ignora apenas coluna duplicada; outros erros são reais.
            Persistence._add_column_if_missing(c, "tasks", "action TEXT")
            Persistence._add_column_if_missing(c, "tasks", "type TEXT")
            Persistence._add_column_if_missing(c, "tasks", "next_run TEXT")
            Persistence._add_column_if_missing(c, "tasks", "status TEXT")
            Persistence._add_column_if_missing(c, "tasks", "meta TEXT")
            Persistence._add_column_if_missing(c, "tasks", "created_at TEXT")


            c.execute("""
            CREATE TABLE IF NOT EXISTS task_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                interaction_type TEXT, -- 'confirm', 'ignore', 'cancel'
                value TEXT, -- ex: '250' (ml)
                timestamp TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                mac TEXT PRIMARY KEY,
                name TEXT,
                updated_at TEXT,
                first_seen TEXT,
                last_seen TEXT,
                seen_count INTEGER DEFAULT 0,
                last_ip TEXT,
                hostname TEXT,
                vendor TEXT,
                device_type TEXT,
                mac_is_private INTEGER DEFAULT 0
            )
            """)

            for column in (
                "first_seen TEXT", "last_seen TEXT", "seen_count INTEGER DEFAULT 0",
                "last_ip TEXT", "hostname TEXT", "vendor TEXT", "device_type TEXT",
                "mac_is_private INTEGER DEFAULT 0",
            ):
                Persistence._add_column_if_missing(c, "devices", column)

            c.execute("""
            CREATE TABLE IF NOT EXISTS device_ips (
                mac TEXT NOT NULL,
                ip TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (mac, ip),
                FOREIGN KEY(mac) REFERENCES devices(mac)
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS baseline (
                key TEXT PRIMARY KEY,
                data TEXT,
                updated_at TEXT
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS hydration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                amount_ml INTEGER,
                timestamp TEXT,
                manual BOOLEAN,
                goal_ml INTEGER,
                consumed_so_far_ml INTEGER
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS token_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0,
                success INTEGER DEFAULT 1,
                error TEXT,
                timestamp TEXT
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS unknown_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                fallback_source TEXT,
                timestamp TEXT
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS api_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                error TEXT,
                timestamp TEXT
            )
            """)

            # Índices estratégicos
            c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON tasks(next_run, status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_token_log_timestamp ON token_log(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_api_errors_source ON api_errors(source, id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_device_ips_last_seen ON device_ips(last_seen)")
            c.execute("""
                UPDATE devices
                SET first_seen = COALESCE(first_seen, updated_at),
                    last_seen = COALESCE(last_seen, updated_at),
                    seen_count = CASE WHEN COALESCE(seen_count, 0) < 1 THEN 1 ELSE seen_count END
                WHERE updated_at IS NOT NULL
            """)

            conn.commit()

    # ==================================================
    # DEVICES (CUSTOM NAMES)
    # ==================================================
    @staticmethod
    def set_device_name(mac: str, name: str):
        mac = mac.lower()
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO devices (mac, name, updated_at, first_seen, last_seen, seen_count)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(mac) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at
                """,
                (mac, name, now, now, now),
            )
            conn.commit()

    @staticmethod
    def register_device_seen(
        mac: str,
        ip: str | None = None,
        hostname: str | None = None,
        vendor: str | None = None,
        device_type: str | None = None,
        mac_is_private: bool = False,
    ):
        """
        Registra que um dispositivo foi visto, mesmo sem nome.
        Garante que ele exista na tabela 'devices'.
        """
        mac = mac.lower()
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            # Upsert preserva o nome escolhido pelo usuario e atualiza a observacao.
            c.execute(
                """
                INSERT INTO devices (
                    mac, name, updated_at, first_seen, last_seen, seen_count,
                    last_ip, hostname, vendor, device_type, mac_is_private
                ) VALUES (?, '', ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    last_seen=excluded.last_seen,
                    seen_count=COALESCE(devices.seen_count, 0) + 1,
                    last_ip=COALESCE(excluded.last_ip, devices.last_ip),
                    hostname=COALESCE(excluded.hostname, devices.hostname),
                    vendor=COALESCE(excluded.vendor, devices.vendor),
                    device_type=COALESCE(excluded.device_type, devices.device_type),
                    mac_is_private=excluded.mac_is_private
                """,
                (mac, now, now, now, ip, hostname, vendor, device_type, int(mac_is_private)),
            )
            if ip:
                c.execute(
                    """
                    INSERT INTO device_ips (mac, ip, first_seen, last_seen, seen_count)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(mac, ip) DO UPDATE SET
                        last_seen=excluded.last_seen,
                        seen_count=device_ips.seen_count + 1
                    """,
                    (mac, ip, now, now),
                )
            conn.commit()

    @staticmethod
    def get_device_profile(mac: str) -> Optional[Dict[str, Any]]:
        """Retorna observacoes locais, sem classificar o dispositivo como invasor."""
        mac = mac.lower()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM devices WHERE mac=?", (mac,))
            row = c.fetchone()
            if not row:
                return None
            profile = dict(row)
            profile["mac_is_private"] = bool(profile.get("mac_is_private"))
            c.execute(
                """
                SELECT ip, first_seen, last_seen, seen_count
                FROM device_ips WHERE mac=? ORDER BY last_seen DESC LIMIT 10
                """,
                (mac,),
            )
            profile["ips"] = [dict(ip_row) for ip_row in c.fetchall()]
            return profile

    @staticmethod
    def device_exists(mac: str) -> bool:
        """
        Verifica se um MAC já foi registrado (com ou sem nome).
        """
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM devices WHERE mac=?", (mac,))
            row = c.fetchone()
            return bool(row)

    @staticmethod
    def update_task_meta(task_id: int, meta: Dict):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE tasks SET meta = ? WHERE id = ?",
                (json.dumps(meta), task_id),
            )
            conn.commit()

    @staticmethod
    def get_device_name(mac: str) -> Optional[str]:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute("SELECT name FROM devices WHERE mac=?", (mac,))
            row = c.fetchone()
            # Retorna o nome se existir E não for vazio
            return row[0] if row and row[0] else None

    # ==================================================
    # MAC VENDOR CACHE
    # ==================================================
    @staticmethod
    def get_mac_vendor(mac: str) -> Optional[str]:
        """Get cached vendor for a MAC address"""
        mac_clean = mac.lower().replace(":", "").replace("-", "")
        return Persistence.get_state(f"mac_vendor:{mac_clean}")

    @staticmethod
    def set_mac_vendor(mac: str, vendor: str):
        """Cache a MAC vendor lookup result"""
        mac_clean = mac.lower().replace(":", "").replace("-", "")
        Persistence.set_state(f"mac_vendor:{mac_clean}", vendor)

    # ==================================================
    # EVENTS
    # ==================================================
    @staticmethod
    def log_event(event):
        try:
            with closing(sqlite3.connect(_get_db_path())) as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.type,
                        event.source,
                        json.dumps(event.payload, default=str),
                        event.timestamp,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"DB Log Error: {e}")

    @staticmethod
    def get_recent_events(limit: int = 5) -> List[Dict[str, Any]]:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    # ==================================================
    # STATE
    # ==================================================
    @staticmethod
    def set_state(key: str, value: Any):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO state VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    @staticmethod
    def get_state(key: str, default=None):
        try:
            with closing(sqlite3.connect(_get_db_path())) as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM state WHERE key=?", (key,))
                row = c.fetchone()
                return json.loads(row[0]) if row else default
        except sqlite3.OperationalError:
            return default

    # ==================================================
    # TASKS (Enhanced)
    # ==================================================
    @staticmethod
    def add_task(
        chat_id: int,
        text: str,
        next_run: datetime,
        action: str = "default",
        task_type: str = "unique",
        interval_minutes: int = 0,
        meta: Dict = None,
        status: str = "active"
    ):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO tasks (
                    chat_id, text, action, type, interval_minutes,
                    next_run, status, meta, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    text,
                    action,
                    task_type,
                    interval_minutes,
                    next_run.isoformat(),
                    status,
                    json.dumps(meta or {}),
                    datetime.now(timezone.utc).isoformat()
                ),
            )
            task_id = c.lastrowid
            conn.commit()
            return task_id

    @staticmethod
    def update_task_next_run(task_id: int, next_run: datetime):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE tasks SET next_run = ? WHERE id = ?",
                (next_run.isoformat(), task_id),
            )
            conn.commit()

    @staticmethod
    def update_task_next_run_and_status(task_id: int, next_run: datetime, status: str):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE tasks SET next_run = ?, status = ? WHERE id = ?",
                (next_run.isoformat(), status, task_id),
            )
            conn.commit()

    @staticmethod
    def update_task_status(task_id: int, status: str):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )
            conn.commit()

    @staticmethod
    def get_task(task_id: int):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = c.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_active_tasks_due(now: datetime):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            # Seleciona tarefas ativas que já passaram da hora de execução
            c.execute(
                "SELECT * FROM tasks WHERE status = 'active' AND next_run <= ?",
                (now.isoformat(),),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_active_tasks(chat_id: int):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT * FROM tasks WHERE chat_id = ? AND status IN ('active', 'delivered') ORDER BY next_run ASC",
                (chat_id,),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_tasks_by_action(chat_id: int, action: str):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT * FROM tasks WHERE chat_id = ? AND action = ? AND status = 'active'",
                (chat_id, action),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def log_interaction(task_id: int, interaction_type: str, value: str = None):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO task_interactions (task_id, interaction_type, value, timestamp) VALUES (?, ?, ?, ?)",
                (task_id, interaction_type, value, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    @staticmethod
    def get_task_interactions(task_id: int):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT * FROM task_interactions WHERE task_id = ? ORDER BY timestamp ASC",
                (task_id,),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_events_by_type(event_type: str, limit: int = 100, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            params = [event_type]
            where = "WHERE type = ?"
            if since is not None:
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                params.append(since.astimezone(timezone.utc).isoformat())
                where += " AND timestamp >= ?"
            params.append(limit)
            c.execute(
                f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?",
                tuple(params),
            )
            events = []
            for row in c.fetchall():
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload") or "{}")
                except json.JSONDecodeError:
                    item["payload"] = {}
                events.append(item)
            return events

    @staticmethod
    def get_tasks_between(chat_id: int, start: datetime, end: datetime):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                """
                SELECT * FROM tasks
                WHERE chat_id = ?
                  AND status IN ('active', 'delivered')
                  AND next_run >= ?
                  AND next_run < ?
                ORDER BY next_run ASC
                """,
                (chat_id, start.isoformat(), end.isoformat()),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_overdue_tasks(chat_id: int, now: datetime):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                """
                SELECT * FROM tasks
                WHERE chat_id = ?
                  AND status IN ('active', 'delivered')
                  AND next_run < ?
                ORDER BY next_run ASC
                """,
                (chat_id, now.isoformat()),
            )
            rows = c.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_task_interactions_today(task_id: int):
        start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT value FROM task_interactions WHERE task_id = ? AND timestamp >= ? AND interaction_type = 'confirm'",
                (task_id, start_of_day)
            )
            rows = c.fetchall()
            return [row[0] for row in rows]

    @staticmethod
    def get_hydration_count_today(chat_id: int) -> int:
        """
        Conta registros reais de consumo de água salvos no histórico hoje.
        """
        from jarvis.config import Config
        local_now = datetime.now(timezone.utc).astimezone(Config.TZ)
        start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            query = "SELECT COUNT(*) FROM hydration_log WHERE chat_id = ? AND timestamp >= ?"
            c.execute(query, (chat_id, start_of_day))
            row = c.fetchone()
            return row[0] if row else 0

    @staticmethod
    def get_hydration_volume_today(chat_id: int) -> int:
        """
        Soma o volume real salvo no histórico de hidratação hoje.
        """
        from jarvis.config import Config
        local_now = datetime.now(timezone.utc).astimezone(Config.TZ)
        start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            query = "SELECT SUM(amount_ml) FROM hydration_log WHERE chat_id = ? AND timestamp >= ?"
            c.execute(query, (chat_id, start_of_day))
            row = c.fetchone()
            # row[0] pode ser None se não houver registros
            return row[0] if row and row[0] else 0

    @staticmethod
    def get_last_cancelled_task_by_action(chat_id: int, action: str) -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                """
                SELECT * FROM tasks
                WHERE chat_id = ? AND action = ? AND status = 'cancelled'
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, action),
            )
            row = c.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_tasks_by_status(chat_id: int, status: str, action: str = None):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            query = "SELECT * FROM tasks WHERE chat_id = ? AND status = ?"
            params = [chat_id, status]

            if action:
                query += " AND action = ?"
                params.append(action)

            c.execute(query, tuple(params))
            rows = c.fetchall()
            return [dict(row) for row in rows]

    # ==================================================
    # SNAPSHOTS (RAW)
    # ==================================================
    @staticmethod
    def save_snapshot(snapshot: Dict[str, Any]):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO snapshots (timestamp, data) VALUES (?, ?)",
                (snapshot["timestamp"], json.dumps(snapshot)),
            )
            conn.commit()

    @staticmethod
    def get_recent_snapshots(minutes: int, limit: int = 50) -> List[Dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT timestamp, data
                FROM snapshots
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (since.isoformat(), limit),
            )
            rows = c.fetchall()

            return [{"timestamp": r[0], **json.loads(r[1])} for r in rows]

    @staticmethod
    def get_snapshot_before(minutes: int) -> Optional[Dict[str, Any]]:
        before = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT timestamp, data
                FROM snapshots
                WHERE timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (before.isoformat(),),
            )
            row = c.fetchone()

            return {"timestamp": row[0], **json.loads(row[1])} if row else None

    @staticmethod
    def get_last_snapshot() -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT timestamp, data
                FROM snapshots
                ORDER BY timestamp DESC
                LIMIT 1
                """
            )
            row = c.fetchone()

            return {"timestamp": row[0], **json.loads(row[1])} if row else None

    # ==================================================
    # BASELINE
    # ==================================================
    @staticmethod
    def save_baseline(snapshot: Dict[str, Any]):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT OR REPLACE INTO baseline (key, data, updated_at)
                VALUES ('baseline', ?, ?)
                """,
                (json.dumps(snapshot), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    @staticmethod
    def get_baseline() -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute("SELECT data FROM baseline WHERE key='baseline'")
            row = c.fetchone()

            return json.loads(row[0]) if row else None

    # ==================================================
    # HYDRATION LOG
    # ==================================================
    @staticmethod
    def log_hydration_intake(chat_id: int, amount_ml: int, goal_ml: int, consumed_so_far_ml: int, manual: bool = True):
        """Registra consumo de água no histórico"""
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO hydration_log (chat_id, amount_ml, timestamp, manual, goal_ml, consumed_so_far_ml) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, amount_ml, datetime.now(timezone.utc).isoformat(), manual, goal_ml, consumed_so_far_ml)
            )
            conn.commit()

    @staticmethod
    def get_hydration_history(chat_id: int, days: int = 30) -> List[Dict[str, Any]]:
        """Retorna histórico de hidratação dos últimos X dias"""
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            c.execute(
                "SELECT * FROM hydration_log WHERE chat_id = ? AND timestamp > ? ORDER BY timestamp DESC",
                (chat_id, cutoff)
            )

            rows = c.fetchall()

            return [dict(row) for row in rows]

    # ==================================================
    # TOKEN USAGE LOG
    # ==================================================
    @staticmethod
    def log_token_usage(model: str, prompt_tokens: int, completion_tokens: int,
                        total_tokens: int, cost: float = 0.0, success: bool = True,
                        error: str = None):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO token_log (model, prompt_tokens, completion_tokens, total_tokens, cost, success, error, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (model, prompt_tokens, completion_tokens, total_tokens, cost, 1 if success else 0, error, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()

    @staticmethod
    def get_token_usage_today() -> dict:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT COUNT(*) as calls, COALESCE(SUM(prompt_tokens), 0) as prompt, COALESCE(SUM(completion_tokens), 0) as completion, COALESCE(SUM(total_tokens), 0) as total, COALESCE(SUM(cost), 0.0) as cost FROM token_log WHERE timestamp >= ? AND success = 1",
                (start,)
            )
            row = c.fetchone()
            return dict(row) if row else {"calls": 0, "prompt": 0, "completion": 0, "total": 0, "cost": 0.0}

    @staticmethod
    def get_token_usage_all_time() -> dict:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as calls, COALESCE(SUM(prompt_tokens), 0) as prompt, COALESCE(SUM(completion_tokens), 0) as completion, COALESCE(SUM(total_tokens), 0) as total, COALESCE(SUM(cost), 0.0) as cost FROM token_log WHERE success = 1")
            row = c.fetchone()
            return dict(row) if row else {"calls": 0, "prompt": 0, "completion": 0, "total": 0, "cost": 0.0}

    # ==================================================
    # UNKNOWN QUERIES
    # ==================================================
    @staticmethod
    def log_unknown_query(query: str, fallback_source: str):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO unknown_queries (query, fallback_source, timestamp) VALUES (?, ?, ?)",
                (query, fallback_source, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()

    @staticmethod
    def get_unknown_queries_today() -> list:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT query, fallback_source, timestamp FROM unknown_queries WHERE timestamp >= ? ORDER BY timestamp DESC", (start,))
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def get_unknown_queries_count(days: int = 7) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM unknown_queries WHERE timestamp >= ?", (since,))
            row = c.fetchone()
            return row[0] if row else 0

    # ==================================================
    # API ERRORS
    # ==================================================
    @staticmethod
    def log_api_error(source: str, error: str):
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO api_errors (source, error, timestamp) VALUES (?, ?, ?)",
                (source, error, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()

    @staticmethod
    def get_api_errors_today() -> list:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with closing(sqlite3.connect(_get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT source, error, timestamp FROM api_errors WHERE timestamp >= ? ORDER BY timestamp DESC", (start,))
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def get_consecutive_api_failures(source: str) -> int:
        with closing(sqlite3.connect(_get_db_path())) as conn:
            c = conn.cursor()
            c.execute("SELECT error FROM api_errors WHERE source = ? ORDER BY id DESC LIMIT 10", (source,))
            rows = c.fetchall()
            count = 0
            for row in rows:
                if row[0]:
                    count += 1
                else:
                    break
            return count
