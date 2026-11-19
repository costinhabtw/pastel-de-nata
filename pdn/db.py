import json
import sqlite3
from pathlib import Path

MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS applications (id TEXT PRIMARY KEY, user TEXT NOT NULL, answer TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pendente');
    CREATE UNIQUE INDEX IF NOT EXISTS pending_application ON applications(user) WHERE status = 'pendente';
    CREATE TABLE IF NOT EXISTS tickets (channel TEXT PRIMARY KEY, user TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS activities (id TEXT PRIMARY KEY, title TEXT NOT NULL, starts INTEGER NOT NULL, channel TEXT NOT NULL, message TEXT, closed INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS votes (activity TEXT NOT NULL, user TEXT NOT NULL, vote TEXT NOT NULL, PRIMARY KEY(activity,user));
    CREATE TABLE IF NOT EXISTS subscribers (user TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, creator TEXT NOT NULL);
    """,
    """
    ALTER TABLE activities ADD COLUMN description TEXT NOT NULL DEFAULT '';
    ALTER TABLE activities ADD COLUMN deadline INTEGER;
    ALTER TABLE activities ADD COLUMN mandatory INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE activities ADD COLUMN roles TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE activities ADD COLUMN reminder INTEGER NOT NULL DEFAULT 30;
    ALTER TABLE activities ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE activities ADD COLUMN summary TEXT;
    ALTER TABLE activities ADD COLUMN closed_by_worker INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE activity_audience (
        activity TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
        user TEXT NOT NULL,
        notified TEXT,
        reminded TEXT,
        PRIMARY KEY(activity,user)
    );
    CREATE TABLE resources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE, balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0));
    CREATE TABLE movements (id INTEGER PRIMARY KEY, resource INTEGER NOT NULL REFERENCES resources(id), delta INTEGER NOT NULL, actor TEXT NOT NULL, at INTEGER NOT NULL DEFAULT (unixepoch()), reason TEXT NOT NULL);
    CREATE TABLE audit (id INTEGER PRIMARY KEY, actor TEXT, event TEXT NOT NULL, details TEXT NOT NULL, at INTEGER NOT NULL DEFAULT (unixepoch()));
    CREATE TABLE roster (user TEXT PRIMARY KEY, discord_name TEXT NOT NULL, nickname TEXT NOT NULL, rank TEXT NOT NULL, recruiter TEXT NOT NULL, joined INTEGER NOT NULL DEFAULT (unixepoch()), active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE rank_history (id INTEGER PRIMARY KEY, user TEXT NOT NULL, actor TEXT NOT NULL, previous TEXT, next TEXT, reason TEXT NOT NULL, at INTEGER NOT NULL DEFAULT (unixepoch()));
    """,
    """
    ALTER TABLE applications ADD COLUMN submitted INTEGER;
    ALTER TABLE applications ADD COLUMN interview INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE applications ADD COLUMN reviewer TEXT;
    ALTER TABLE applications ADD COLUMN reason TEXT;
    ALTER TABLE applications ADD COLUMN review_channel TEXT;
    ALTER TABLE applications ADD COLUMN review_message TEXT;
    ALTER TABLE tickets ADD COLUMN kind TEXT NOT NULL DEFAULT 'geral';
    ALTER TABLE tickets ADD COLUMN opened INTEGER;
    ALTER TABLE tickets ADD COLUMN closed_at INTEGER;
    ALTER TABLE tickets ADD COLUMN closer TEXT;
    ALTER TABLE tickets ADD COLUMN reason TEXT;
    ALTER TABLE tickets ADD COLUMN assignee TEXT;
    DROP INDEX IF EXISTS open_ticket;
    CREATE UNIQUE INDEX open_ticket_kind ON tickets(user,kind) WHERE closed=0;
    CREATE TABLE direct_invites (
        id TEXT PRIMARY KEY, user TEXT NOT NULL, actor TEXT NOT NULL, nickname TEXT NOT NULL,
        expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pendente'
    );
    CREATE TABLE sanctions (
        id INTEGER PRIMARY KEY, user TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        reason TEXT NOT NULL, at INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE suggestions (id TEXT NOT NULL,user TEXT NOT NULL,vote TEXT NOT NULL,PRIMARY KEY(id,user));
    """,
)

class Database:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        try:
            version = self.get('PRAGMA user_version')[0]
            if version > len(MIGRATIONS):
                raise RuntimeError('A base de dados foi criada por uma versão mais recente.')
            for index, migration in enumerate(MIGRATIONS[version:], version + 1):
                self.conn.executescript(f'BEGIN IMMEDIATE;\n{migration}\nPRAGMA user_version={index};\nCOMMIT;')
        except BaseException:
            self.conn.close()
            raise

    def get(self, query, *args):
        return self.conn.execute(query, args).fetchone()

    def all(self, query, *args):
        return self.conn.execute(query, args).fetchall()

    def run(self, query, *args):
        with self.conn:
            return self.conn.execute(query, args).rowcount

    def setting(self, key, default=None):
        row = self.get('SELECT value FROM settings WHERE key=?', key)
        return json.loads(row['value']) if row else default

    def set(self, key, value):
        self.run('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 key, json.dumps(value))

    def move(self, name, delta, actor, reason):
        with self.conn:
            resource = self.conn.execute(
                'UPDATE resources SET balance=balance+? WHERE normalized=? AND balance+? BETWEEN 0 AND 1000000000 RETURNING id,balance',
                (delta, name.casefold(), delta)).fetchone()
            if resource is None:
                raise ValueError('Recurso inexistente ou movimento fora dos limites do inventário.')
            self.conn.execute('INSERT INTO movements(resource,delta,actor,reason) VALUES (?,?,?,?)',
                              (resource['id'], delta, str(actor), reason))
            return resource['balance']

    def vote(self, activity, user, choice, now):
        with self.conn:
            return self.conn.execute("""
                INSERT INTO votes(activity,user,vote)
                SELECT id,?,? FROM activities
                WHERE id=? AND closed=0 AND cancelled=0 AND message IS NOT NULL
                  AND COALESCE(deadline,starts)>?
                  AND (roles='[]' OR EXISTS (
                    SELECT 1 FROM activity_audience WHERE activity=activities.id AND user=?
                  ))
                ON CONFLICT(activity,user) DO UPDATE SET vote=excluded.vote
                """, (str(user), choice, str(activity), now, str(user))).rowcount == 1
