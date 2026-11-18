import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export function openStore(directory) {
  mkdirSync(directory, { recursive: true });
  const db = new DatabaseSync(join(directory, 'pastel.sqlite'));
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA busy_timeout = 5000;
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS applications (
      id TEXT PRIMARY KEY, user TEXT NOT NULL, answer TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pendente'
    );
    CREATE UNIQUE INDEX IF NOT EXISTS pending_application
      ON applications(user) WHERE status = 'pendente';
    CREATE TABLE IF NOT EXISTS tickets (
      channel TEXT PRIMARY KEY, user TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0
    );
    CREATE UNIQUE INDEX IF NOT EXISTS open_ticket ON tickets(user) WHERE closed = 0;
    CREATE TABLE IF NOT EXISTS activities (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, starts INTEGER NOT NULL,
      channel TEXT NOT NULL, message TEXT, closed INTEGER NOT NULL DEFAULT 0,
      reminded INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS votes (
      activity TEXT NOT NULL, user TEXT NOT NULL, vote TEXT NOT NULL,
      PRIMARY KEY (activity, user)
    );
    CREATE TABLE IF NOT EXISTS subscribers (user TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, creator TEXT NOT NULL);
  `);
  return {
    get: (sql, ...args) => db.prepare(sql).get(...args),
    all: (sql, ...args) => db.prepare(sql).all(...args),
    run: (sql, ...args) => db.prepare(sql).run(...args),
    close: () => db.close(),
  };
}

export function parseActivityTime(value, now = Date.now()) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const local = new Date(value.slice(0, 16) + ':00Z');
  if (Number.isNaN(+local) || local.toISOString().slice(0, 16) !== value.slice(0, 16)) return null;
  const time = Date.parse(value);
  return time > now ? time : null;
}
