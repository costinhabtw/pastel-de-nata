import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { openStore, parseActivityTime } from './store.mjs';

test('valida a data, o fuso e o início no futuro', () => {
  const now = Date.parse('2026-11-18T10:00:00Z');
  assert.equal(parseActivityTime('2026-11-18T14:00+01:00', now), now + 3 * 3600000);
  for (const invalid of ['2026-02-31T14:00Z', '2026-12-01T14:00', '2026-11-18T10:00Z', '2026-12-01T24:00Z']) {
    assert.equal(parseActivityTime(invalid, now), null);
  }
});

test('persiste votos e impede duplicados ativos', t => {
  const directory = mkdtempSync(join(tmpdir(), 'pastel-test-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const db = openStore(directory);
  try {
    db.run('INSERT INTO applications (id, user, answer) VALUES (?, ?, ?)', 'a', 'u', 'Resposta');
    assert.throws(() => db.run('INSERT INTO applications (id, user, answer) VALUES (?, ?, ?)', 'b', 'u', 'Outra'));
    db.run("UPDATE applications SET status = 'recusada' WHERE id = 'a'");
    db.run('INSERT INTO applications (id, user, answer) VALUES (?, ?, ?)', 'b', 'u', 'Outra');
    db.run("INSERT INTO tickets (channel, user) VALUES ('a', 'u')");
    assert.throws(() => db.run("INSERT INTO tickets (channel, user) VALUES ('b', 'u')"));
    db.run("UPDATE tickets SET closed = 1 WHERE channel = 'a'");
    db.run("INSERT INTO tickets (channel, user) VALUES ('b', 'u')");
    db.run("INSERT INTO votes VALUES ('x', 'u', 'sim')");
    db.run("INSERT INTO votes VALUES ('x', 'u', 'nao') ON CONFLICT(activity, user) DO UPDATE SET vote = excluded.vote");
  } finally {
    db.close();
  }
  const reopened = openStore(directory);
  try {
    assert.equal(reopened.get("SELECT vote FROM votes WHERE activity = 'x' AND user = 'u'").vote, 'nao');
    assert.equal(reopened.get("SELECT count(*) AS total FROM votes WHERE activity = 'x'").total, 1);
  } finally {
    reopened.close();
  }
});
