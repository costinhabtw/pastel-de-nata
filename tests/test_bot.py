from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import COGS, Pastel
from pdn.common import UserError, millis, parse_time
from pdn.cogs.activities import Vote
from pdn.cogs.management import welcome_card
from pdn.cogs.people import ApplicationForm
from pdn.cogs.setup import LAYOUT
from pdn.cogs.support import TICKET_CATEGORIES
from pdn.db import Database, MIGRATIONS

class DatabaseTests(unittest.TestCase):
    def test_welcome_card_preserves_text_and_verification_link(self):
        guild = SimpleNamespace(name='PASTEL DE NATA | ROB RUN', member_count=22, icon=None)
        member = SimpleNamespace(guild=guild, name='rob_run', mention='<@42>',
                                 created_at=datetime(2023, 11, 1, tzinfo=timezone.utc),
                                 display_avatar=SimpleNamespace(url='https://cdn.discordapp.com/embed/avatars/0.png'))
        card = welcome_card(member, 'Bem-vindo ao clã!', 43)
        self.assertEqual(card.title, '👋 Novo membro!')
        self.assertIn('<#43>', card.description)
        self.assertIn('Bem-vindo ao clã!', card.description)
        self.assertEqual(card.fields[2].value, '22')
        self.assertTrue(card.fields[1].value.endswith(':R>'))
        self.assertEqual(card.thumbnail.url, member.display_avatar.url)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'bot.sqlite'

    def open(self):
        database = Database(self.path)
        self.addCleanup(database.conn.close)
        return database

    def test_legacy_migration_preserves_panel_settings_activity_and_ticket(self):
        with closing(sqlite3.connect(self.path)) as legacy, legacy:
            legacy.executescript(MIGRATIONS[0])
            legacy.execute("INSERT INTO settings VALUES ('guild','{\"logs\":\"18\"}')")
            legacy.execute("INSERT INTO activities(id,title,starts,channel,message) VALUES ('1','Treino',1000,'2','3')")
            legacy.execute("INSERT INTO applications(id,user,answer) VALUES ('2','16','Legado')")
            legacy.execute("INSERT INTO tickets(channel,user) VALUES ('4','16')")
            legacy.execute('CREATE UNIQUE INDEX open_ticket ON tickets(user) WHERE closed=0')
        db = self.open()
        self.assertEqual(db.setting('guild'), {'logs': '18'})
        self.assertEqual(db.get("SELECT title FROM activities WHERE id='1'")['title'], 'Treino')
        self.assertEqual(db.get("SELECT answer FROM applications WHERE id='2'")['answer'], 'Legado')
        self.assertEqual(db.get("SELECT kind FROM tickets WHERE channel='4'")['kind'], 'geral')
        db.run("INSERT INTO tickets(channel,user,kind) VALUES ('5','16','recrutamento')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.run("INSERT INTO tickets(channel,user,kind) VALUES ('6','16','geral')")
        self.assertEqual(self.open().get('PRAGMA user_version')[0], len(MIGRATIONS))

    def test_vote_audience_deadline_and_change(self):
        db = self.open()
        db.run("INSERT INTO activities(id,title,starts,channel,message,deadline,roles) VALUES ('1','Treino',1000,'2','3',800,'[4]')")
        db.run("INSERT INTO activity_audience(activity,user) VALUES ('1','10')")
        self.assertFalse(db.vote('1', 11, 'sim', 100))
        self.assertTrue(db.vote('1', 10, 'sim', 100))
        self.assertTrue(db.vote('1', 10, 'nao', 799))
        self.assertFalse(db.vote('1', 10, 'talvez', 800))
        self.assertEqual([row['vote'] for row in db.all('SELECT vote FROM votes')], ['nao'])

    def test_inventory_movements_are_atomic(self):
        db = self.open()
        db.run("INSERT INTO resources(name,normalized) VALUES ('Kits','kits')")
        self.assertEqual(db.move('KITS', 8, 10, 'entrega'), 8)
        with self.assertRaises(ValueError):
            db.move('Kits', -9, 10, 'levantamento')
        self.assertEqual(db.move('Kits', -3, 11, 'levantamento'), 5)
        self.assertEqual(self.open().get('SELECT balance FROM resources')[0], 5)
        self.assertEqual([row[0] for row in db.all('SELECT delta FROM movements ORDER BY id')], [8, -3])

    def test_timezone_offsets(self):
        self.assertEqual(parse_time('2026-08-01T22:00+01:00'), parse_time('2026-08-01T21:00Z'))
        for value in ('2026-02-31T12:00+00:00', '2026-08-01T22:00'):
            with self.assertRaises(UserError):
                parse_time(value)

    def test_backup_snapshots_an_open_wal_database(self):
        db = self.open()
        db.set('snapshot', 'persistido')
        output = self.path.parent / 'backups'
        output.mkdir()
        script = (Path(__file__).resolve().parents[1] / 'scripts' / 'backup.sh').read_text()
        program = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        with patch.object(sys, 'argv', ['backup.sh', str(self.path), str(output)]), patch('builtins.print'):
            exec(compile(program, 'scripts/backup.sh', 'exec'), {})
        saved = next(output.glob('*.sqlite'))
        with closing(sqlite3.connect(saved)) as backup:
            self.assertEqual(backup.execute("SELECT value FROM settings WHERE key='snapshot'").fetchone()[0],
                             '"persistido"')

class SimulatedDiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(Path(self.directory.name) / 'test.sqlite')
        self.addCleanup(self.db.conn.close)
        self.bot = Pastel(123456789012345678, self.db, sync=False)
        await self.bot.__aenter__()
        self.addAsyncCleanup(self.bot.__aexit__, None, None, None)
        await self.bot.setup_hook()

    async def test_command_schema_and_views(self):
        names = {command.name for command in self.bot.tree.get_commands(guild=__import__('discord').Object(id=self.bot.guild_id))}
        self.assertEqual(len(self.bot.cogs), len(COGS))
        self.assertTrue({'setup','atividade','config','recurso','convidar','expulsar','status'} <= names)
        for command in self.bot.tree.get_commands():
            self.assertEqual(command.to_dict(self.bot.tree)['name'], command.name)
        self.assertTrue(self.bot.get_cog('People').panel('legacy').is_persistent())
        panel = self.bot.get_cog('Support').panel('support')
        self.assertTrue(panel.is_persistent())
        container, = panel.to_components()
        self.assertEqual(container['type'], 17)
        menu = container['components'][-1]['components'][0]
        self.assertEqual(menu['custom_id'], 'pdn:ticket:type')
        self.assertEqual(len(menu['options']), 4)
        self.assertEqual(len(ApplicationForm(self.bot.get_cog('People')).children), 5)
        self.assertEqual(Vote('42', 'sim').item.custom_id, 'pdn:vote:42:sim')

    async def test_worker_closes_overdue_activity_after_restart(self):
        self.db.run("INSERT INTO activities(id,title,starts,deadline,channel,message) VALUES ('1','Treino',?,?,'20','21')", millis() - 1, millis() - 2)
        self.db.run("INSERT INTO activity_audience(activity,user) VALUES ('1','15'),('1','16')")
        self.db.run("INSERT INTO votes VALUES ('1','15','sim')")
        cog = self.bot.get_cog('Activities')
        guild = SimpleNamespace()
        with patch.object(self.bot, 'get_guild', return_value=guild), patch.object(cog, 'refresh', new_callable=AsyncMock) as update, patch.object(cog, 'summary', new_callable=AsyncMock):
            await cog.worker.coro(cog)
            await cog.worker.coro(cog)
        update.assert_awaited_once()
        row = self.db.get("SELECT closed,summary FROM activities WHERE id='1'")
        self.assertEqual((row['closed'], row['summary']), (1, '["16"]'))

    async def test_dm_attempt_is_not_repeated(self):
        self.db.run("INSERT INTO activities(id,title,starts,deadline,channel,message) VALUES ('1','Treino',?,?,'20','21')", millis() + 30000, millis() + 20000)
        self.db.run("INSERT INTO activity_audience(activity,user) VALUES ('1','15')")
        cog = self.bot.get_cog('Activities')
        member = SimpleNamespace(send=AsyncMock())
        channel = SimpleNamespace(id=20, permissions_for=lambda _: SimpleNamespace(view_channel=True))
        guild = SimpleNamespace(id=123, get_channel=lambda _:channel, get_member=lambda _:member)
        activity = self.db.get("SELECT * FROM activities WHERE id='1'")
        await cog.notify(guild, activity, False)
        await cog.notify(guild, activity, False)
        member.send.assert_awaited_once()
        self.assertEqual(self.db.get("SELECT notified FROM activity_audience WHERE user='15'")[0], 'entregue')

    async def test_review_records_interview_and_refusal(self):
        self.db.run("INSERT INTO applications(id,user,answer) VALUES ('1','15','Respostas')")
        cog = self.bot.get_cog('People')
        applicant = SimpleNamespace(send=AsyncMock())
        message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(user=SimpleNamespace(id=20), guild=SimpleNamespace(), message=message)
        overrides = {'staff': lambda _: None, 'audit': AsyncMock(), 'reply': AsyncMock()}
        with patch.dict(cog.review.__func__.__globals__, overrides), patch.object(self.bot, 'fetch_user', return_value=applicant):
            await cog.review(interaction, '1', 'interview')
            self.assertEqual(self.db.get("SELECT interview FROM applications WHERE id='1'")[0], 1)
            await cog.review(interaction, '1', 'reject', 'Indisponibilidade')
            with self.assertRaises(UserError):
                await cog.review(interaction, '1', 'reject', 'Duplicado')
        row = self.db.get("SELECT status,reason,reviewer FROM applications WHERE id='1'")
        self.assertEqual(tuple(row), ('recusada', 'Indisponibilidade', '20'))
        applicant.send.assert_awaited_once()
        message.edit.assert_awaited_once()

    async def test_rank_checks_protect_higher_roles(self):
        cog = self.bot.get_cog('People')
        guild = SimpleNamespace(owner_id=1, me=SimpleNamespace(top_role=10))
        officer = SimpleNamespace(id=2, top_role=5)
        target = SimpleNamespace(id=3, bot=False, top_role=6)
        with self.assertRaises(UserError):
            cog.check_rank(guild, officer, target)
        target.top_role = 3
        with patch.object(cog, 'rank_role', return_value=5):
            with self.assertRaises(UserError):
                cog.check_rank(guild, officer, target, 'Oficial')
        with patch.object(cog, 'rank_role', return_value=4):
            cog.check_rank(guild, officer, target, 'Veterano')

    async def test_setup_is_idempotent_and_restores_deleted_panel(self):
        import discord

        class Role:
            managed = False
            def __init__(self, id, name):
                self.id, self.name = id, name
                self.permissions = discord.Permissions.none()

            def __ge__(self, other):
                return self.id >= other.id

            def is_default(self):
                return self.id == 1

        class Text:
            def __init__(self, id, name):
                self.id, self.name, self.messages = id, name, {}

            async def fetch_message(self, id):
                if id not in self.messages:
                    response = SimpleNamespace(status=404, reason='not found')
                    raise discord.NotFound(response, 'not found')
                return self.messages[id]

            async def history(self, limit):
                for message in self.messages.values():
                    yield message

            async def send(self, *, embed=None, view):
                from discord.components import _component_factory
                components = [_component_factory(component) for component in view.to_components()]
                message = SimpleNamespace(id=next(sequence), author=bot_member, components=components,
                                          embeds=[embed] if embed is not None else [])
                async def edit(*, content, embed, attachments, view):
                    message.embeds = []
                    message.components = [_component_factory(component) for component in view.to_components()]
                message.edit = AsyncMock(side_effect=edit)
                self.messages[message.id] = message
                return message

        class Voice(Text):
            pass

        sequence = iter(range(10, 200))
        permissions = discord.Permissions(manage_roles=True, manage_channels=True)
        class BotMember:
            id = 2
            guild_permissions = permissions
            top_role = Role(900, 'bot')
        bot_member = BotMember()
        categories, channels, roles = [], {}, []
        def create_channel(kind):
            async def create(name, category, **kwargs):
                channel = kind(next(sequence), name)
                channels[channel.id] = channel
                category.channels.append(channel)
                return channel
            return create

        async def create_role(name, **kwargs):
            role = Role(next(sequence), name)
            roles.append(role)
            return role

        async def create_category(name, **kwargs):
            category = SimpleNamespace(id=next(sequence), name=name, channels=[],
                                       position=kwargs.get('position', len(categories)),
                                       overwrites=kwargs.get('overwrites', {}))
            categories.append(category)
            return category

        async def fetch_channel(id):
            return channels[id]

        guild = SimpleNamespace(me=bot_member, default_role=Role(1,'everyone'), roles=roles, categories=categories, afk_channel=None,
            get_role=lambda id: next((role for role in roles if role.id == id), None),
            create_role=create_role, create_category=create_category, get_channel=channels.get,
            fetch_channel=fetch_channel, create_text_channel=create_channel(Text), create_voice_channel=create_channel(Voice))
        provision = self.bot.get_cog('Setup').provision
        with patch.object(discord, 'TextChannel', Text), patch.object(discord, 'VoiceChannel', Voice):
            created, _ = await provision(guild)
            expected = 7 + len(LAYOUT) + sum(map(len, LAYOUT.values())) + 3 + len(TICKET_CATEGORIES)
            self.assertEqual(created, expected)
            self.assertEqual([category.name for category in categories[-4:]], list(TICKET_CATEGORIES.values()))
            self.assertTrue(all(category.position > categories[-5].position for category in categories[-4:]))
            self.assertTrue(all(category.overwrites[guild.default_role].view_channel is False
                                for category in categories[-4:]))
            self.assertEqual((await provision(guild))[0], 0)
            support = self.db.setting('layout')['panels']['support']
            original = channels[int(support['channel'])].messages[int(support['id'])]
            original.components = []
            self.assertEqual((await provision(guild))[0], 0)
            original.edit.assert_awaited_once()
            self.assertTrue(self.bot.get_cog('Support').is_card(original))
            self.assertEqual((await provision(guild))[0], 0)
            original.edit.assert_awaited_once()
            panel = self.db.setting('layout')['panels']['verification']
            channels[int(panel['channel'])].messages.clear()
            self.assertEqual((await provision(guild))[0], 1)
