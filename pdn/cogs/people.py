import asyncio
import json
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from pdn.common import RANKS, UserError, View, audit, embed, in_guild, module_enabled, private_channel, reply, staff, verified
from pdn.cogs.resources import clan

QUESTIONS = (
    'Qual é o teu nickname no ROB RUN?',
    'Quantos anos tens?',
    'Tens experiência no jogo?',
    'Porque queres entrar no PASTEL DE NATA?',
    'Qual é a tua disponibilidade para jogar?',
)

def role_ids(db):
    return db.setting('layout', {}).get('roles', {})

def safe_role(guild, role):
    dangerous = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
                                    manage_channels=True, ban_members=True, kick_members=True,
                                    moderate_members=True, manage_webhooks=True, manage_messages=True)
    if (not role or role.is_default() or role.managed or role >= guild.me.top_role
            or role.permissions.value & dangerous.value):
        raise UserError('O cargo configurado está indisponível ou excede as permissões permitidas.')
    return role

class ApplicationForm(discord.ui.Modal, title='Candidatura ROB RUN'):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
        self.questions = cog.db.setting('questions', QUESTIONS)
        for index, label in enumerate(self.questions):
            self.add_item(discord.ui.Label(text=label, component=discord.ui.TextInput(
                max_length=40 if index < 2 else 500,
                style=discord.TextStyle.short if index < 2 else discord.TextStyle.paragraph)))

    async def on_submit(self, i):
        try:
            await i.response.defer(ephemeral=True)
            await self.cog.apply(i, self.questions, [item.component.value.strip() for item in self.children])
        except UserError as error:
            await reply(i, str(error))

class RejectForm(discord.ui.Modal, title='Recusar candidatura'):
    reason = discord.ui.TextInput(label='Motivo', max_length=500, style=discord.TextStyle.paragraph)

    def __init__(self, cog, identifier):
        super().__init__(timeout=180)
        self.cog, self.identifier = cog, identifier

    async def on_submit(self, i):
        try:
            await i.response.defer(ephemeral=True)
            await self.cog.review(i, self.identifier, 'reject', self.reason.value.strip())
        except UserError as error:
            await reply(i, str(error))

class Entry(View):
    def __init__(self, cog, kind='legacy'):
        super().__init__()
        if kind in ('legacy', 'verification'):
            verify = discord.ui.Button(label='✅ Verificar', custom_id='pdn:verify', style=discord.ButtonStyle.success)
            verify.callback = cog.verify
            self.add_item(verify)
        if kind in ('legacy', 'applications'):
            apply = discord.ui.Button(label='📝 Candidatar-me', custom_id='pdn:apply', style=discord.ButtonStyle.primary)
            async def form(i):
                verified(i)
                module_enabled(i, 'recruitment')
                await i.response.send_modal(ApplicationForm(cog))
            apply.callback = form
            self.add_item(apply)
        if kind == 'legacy':
            support = discord.ui.Button(label='Abrir ticket', custom_id='pdn:ticket')
            async def support_menu(i):
                in_guild(i)
                await reply(i, 'Escolhe o tipo de ticket.', view=cog.bot.get_cog('Support').panel('support'))
            support.callback = support_menu
            self.add_item(support)

class Review(discord.ui.DynamicItem[discord.ui.Button], template=r'pdn:(?P<action>approve|reject|interview):(?P<identifier>[0-9]+)'):
    def __init__(self, identifier, action):
        self.identifier, self.action = identifier, action
        label, style = {'approve': ('✅ Aceitar', discord.ButtonStyle.success),
                        'reject': ('❌ Recusar', discord.ButtonStyle.danger),
                        'interview': ('💬 Entrevista', discord.ButtonStyle.secondary)}[action]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f'pdn:{action}:{identifier}'))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match['identifier'], match['action'])

    async def callback(self, i):
        try:
            staff(i)
            cog = i.client.get_cog('People')
            if self.action == 'reject':
                return await i.response.send_modal(RejectForm(cog, self.identifier))
            await i.response.defer(ephemeral=True, thinking=True)
            await cog.review(i, self.identifier, self.action)
        except UserError as error:
            await reply(i, str(error))

class Invitation(discord.ui.DynamicItem[discord.ui.Button], template=r'pdn:invite:(?P<identifier>[0-9]+):(?P<decision>yes|no)'):
    def __init__(self, identifier, decision):
        self.identifier, self.decision = identifier, decision
        super().__init__(discord.ui.Button(label='Aceitar' if decision == 'yes' else 'Recusar',
                         custom_id=f'pdn:invite:{identifier}:{decision}'))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match['identifier'], match['decision'])

    async def callback(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await i.client.get_cog('People').answer_invite(i, self.identifier, self.decision)
        except UserError as error:
            await reply(i, str(error))

class People(commands.Cog):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        self.lock = asyncio.Lock()
        self.app_locks = defaultdict(asyncio.Lock)

    async def cog_load(self):
        self.bot.add_view(self.panel('legacy'))
        self.bot.add_dynamic_items(Review, Invitation)

    def panel(self, key):
        return Entry(self, key)

    def rank_role(self, guild, rank):
        config = self.db.setting('guild', {})
        fallback = {'Recruta': 'clan', 'Visitante': 'verified'}.get(rank)
        identifier = role_ids(self.db).get(rank) or config.get(fallback)
        return safe_role(guild, guild.get_role(int(identifier or 0)))

    def check_rank(self, guild, actor, target, destination=None):
        if target.bot or (actor.id == target.id and actor.id != guild.owner_id):
            raise UserError('Escolhe outro membro humano.')
        if target.id == guild.owner_id and actor.id != guild.owner_id:
            raise UserError('O proprietário do servidor está protegido.')
        if target.id != guild.owner_id and guild.me.top_role <= target.top_role:
            raise UserError('O cargo mais alto do utilizador está acima do bot.')
        if actor.id == guild.owner_id:
            return
        if actor.top_role <= target.top_role:
            raise UserError('A hierarquia Discord impede esta ação.')
        if destination and self.rank_role(guild, destination) >= actor.top_role:
            raise UserError('Só podes atribuir patentes inferiores ao teu cargo.')

    async def set_rank(self, guild, member, rank, actor, nickname=None, reason='Atualização do clã'):
        async with self.lock:
            role_map = role_ids(self.db)
            current = self.db.get('SELECT * FROM roster WHERE user=?', str(member.id))
            if current and current['active'] and current['rank'] == rank and nickname is None:
                raise UserError('O membro já tem essa patente.')
            self.check_rank(guild, actor, member, rank)
            destination = self.rank_role(guild, rank)
            ranks = {role_id for role_id in role_map.values()}
            old_clan = self.db.setting('guild', {}).get('clan')
            if old_clan:
                ranks.add(old_clan)
            for role in member.roles:
                if str(role.id) in ranks and role != destination:
                    safe_role(guild, role)
                    if member.id != actor.id and actor.id != guild.owner_id and role >= actor.top_role:
                        raise UserError('O cargo atual está acima da tua autorização.')
            kept = [role for role in member.roles if not role.is_default() and
                    (str(role.id) not in ranks or role == destination)]
            if destination not in kept:
                kept.append(destination)
            await member.edit(roles=kept, reason=reason)
            old = current['rank'] if current and current['active'] else None
            nickname = nickname or (current['nickname'] if current else member.display_name)
            with self.db.conn:
                self.db.conn.execute("""
                    INSERT INTO roster(user,discord_name,nickname,rank,recruiter,active) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(user) DO UPDATE SET discord_name=excluded.discord_name,
                    nickname=excluded.nickname, rank=excluded.rank, active=excluded.active,
                    recruiter=CASE WHEN roster.active=0 THEN excluded.recruiter ELSE roster.recruiter END,
                    joined=CASE WHEN roster.active=0 THEN unixepoch() ELSE roster.joined END
                    """, (str(member.id), str(member), nickname, rank, str(actor.id), rank != 'Visitante'))
                self.db.conn.execute('INSERT INTO rank_history(user,actor,previous,next,reason) VALUES (?,?,?,?,?)',
                                     (str(member.id), str(actor.id), old, rank, reason))
        await audit(self.bot, guild, 'Patente alterada', f'<@{member.id}> · {old or "—"} → {rank}', actor.id)

    async def verify(self, i):
        guild = in_guild(i)
        destination = self.rank_role(guild, 'Visitante')
        if destination in i.user.roles or any(str(role.id) in role_ids(self.db).values() for role in i.user.roles):
            return await reply(i, 'Já estás verificado.')
        await i.response.defer(ephemeral=True, thinking=True)
        await i.user.add_roles(destination, reason='Verificação ROB RUN')
        await audit(self.bot, guild, 'Verificação', f'<@{i.user.id}>', i.user.id)
        await reply(i, 'Estás verificado. Bem-vindo ao ROB RUN!')

    def application_channel(self, guild):
        config = self.db.setting('guild', {})
        layout = self.db.setting('layout', {})
        staff_ids = {int(value) for value in [config.get('staff'), *(role_ids(self.db).get(rank) for rank in RANKS[:3])] if value}
        channel_id = self.db.setting('application_channel') or layout.get('channels', {}).get('records') or config.get('logs')
        channel = private_channel(guild, channel_id, staff_ids)
        if not channel:
            raise UserError('O canal privado de candidaturas está indisponível.')
        return channel

    async def apply(self, i, questions, values):
        verified(i)
        module_enabled(i, 'recruitment')
        if len(values) != 5 or any(not value for value in values):
            raise UserError('Responde a todas as perguntas.')
        if tuple(questions) == QUESTIONS and (not values[1].isdecimal() or not 1 <= int(values[1]) <= 120):
            raise UserError('Indica uma idade válida em anos.')
        if self.db.get('SELECT 1 FROM roster WHERE user=? AND active=1', str(i.user.id)):
            raise UserError('Já fazes parte do clã.')
        async with self.lock:
            if self.db.get("SELECT 1 FROM applications WHERE user=? AND status='pendente'", str(i.user.id)):
                raise UserError('Já tens uma candidatura pendente.')
            channel = self.application_channel(i.guild)
            identifier = str(i.id)
            self.db.run('INSERT INTO applications(id,user,answer,submitted) VALUES (?,?,?,unixepoch())',
                        identifier, str(i.user.id), json.dumps(values))
            card = embed('Candidatura ROB RUN', f'<@{i.user.id}> · ID {identifier}')
            for label, value in zip(questions, values):
                card.add_field(name=label, value=value, inline=False)
            view = View()
            for action in ('approve', 'reject', 'interview'):
                view.add_item(Review(identifier, action))
            try:
                message = await channel.send(embed=card, view=view)
                self.db.run('UPDATE applications SET review_channel=?,review_message=? WHERE id=?',
                            str(channel.id), str(message.id), identifier)
            except discord.HTTPException:
                self.db.run('DELETE FROM applications WHERE id=?', identifier)
                raise UserError('Não foi possível entregar a candidatura à equipa.')
        await audit(self.bot, i.guild, 'Candidatura recebida', identifier, i.user.id)
        await reply(i, 'Candidatura enviada à equipa.')

    async def review(self, i, identifier, action, reason=''):
        staff(i)
        async with self.app_locks[identifier]:
            application = self.db.get("SELECT * FROM applications WHERE id=? AND status='pendente'", identifier)
            if not application:
                raise UserError('Esta candidatura já foi tratada.')
            if action == 'interview':
                self.db.run('UPDATE applications SET interview=1, reviewer=? WHERE id=?', str(i.user.id), identifier)
                await audit(self.bot, i.guild, 'Entrevista pendente', identifier, i.user.id)
                return await reply(i, 'Candidatura marcada para entrevista.')
            if action == 'approve':
                member = await i.guild.fetch_member(int(application['user']))
                try:
                    nickname = json.loads(application['answer'])[0]
                except (ValueError, TypeError, IndexError, KeyError):
                    nickname = member.display_name
                await self.set_rank(i.guild, member, 'Recruta', i.user, nickname, 'Candidatura aprovada')
            status = 'aprovada' if action == 'approve' else 'recusada'
            self.db.run('UPDATE applications SET status=?,reviewer=?,reason=? WHERE id=?',
                        status, str(i.user.id), reason, identifier)
            message = i.message
            if not message and application['review_channel'] and application['review_message']:
                channel = i.guild.get_channel(int(application['review_channel']))
                if channel:
                    message = channel.get_partial_message(int(application['review_message']))
            if message:
                try:
                    await message.edit(view=None)
                except discord.HTTPException:
                    pass
            try:
                user = await self.bot.fetch_user(int(application['user']))
                await user.send(f'A tua candidatura ROB RUN foi {status}.' + (f' Motivo: {reason}' if reason else ''))
            except discord.HTTPException:
                pass
        await audit(self.bot, i.guild, 'Candidatura ' + status, identifier, i.user.id)
        await reply(i, f'Candidatura {status}.')

    @app_commands.command(name='estado-candidatura', description='Consulta o estado da tua candidatura em privado.')
    async def application_status(self, i: discord.Interaction):
        in_guild(i)
        row = self.db.get('SELECT status,interview FROM applications WHERE user=? ORDER BY id DESC LIMIT 1', str(i.user.id))
        await reply(i, 'Sem candidatura.' if not row else
                    ('Pendente de entrevista.' if row['status'] == 'pendente' and row['interview'] else row['status']))

    @app_commands.command(name='convidar', description='Convida um jogador identificado para o clã ou gera um link do servidor.')
    async def invite(self, i: discord.Interaction, nickname: app_commands.Range[str, 1, 60],
                     utilizador: discord.Member | None = None):
        staff(i)
        module_enabled(i, 'recruitment')
        await i.response.defer(ephemeral=True)
        if utilizador is None:
            layout = self.db.setting('layout', {})
            channel = i.guild.get_channel(int(layout.get('channels', {}).get('verification', '0'))) or i.channel
            if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(i.guild.default_role).view_channel:
                raise UserError('Usa o comando num canal público para criar o convite.')
            invitation = await channel.create_invite(max_age=86400, max_uses=1, unique=True, reason='Convite ROB RUN')
            self.db.run('INSERT INTO invites VALUES (?,?)', invitation.code, str(i.user.id))
            await audit(self.bot, i.guild, 'Convite de servidor', nickname, i.user.id)
            return await reply(i, f'Link para partilhares com {nickname}: {invitation.url}. Usa /convidar com o membro quando entrar para oferecer a patente.')
        self.check_rank(i.guild, i.user, utilizador, 'Recruta')
        if self.db.get('SELECT 1 FROM roster WHERE user=? AND active=1', str(utilizador.id)):
            raise UserError('O jogador já faz parte do clã.')
        user = str(utilizador.id)
        async with self.app_locks['invite:' + user]:
            self.db.run("UPDATE direct_invites SET status='expirado' WHERE user=? AND status='pendente' AND expires<=unixepoch()", user)
            if self.db.get("SELECT 1 FROM direct_invites WHERE user=? AND status='pendente'", user):
                raise UserError('Este membro já tem um convite pendente.')
            identifier = str(i.id)
            self.db.run('INSERT INTO direct_invites(id,user,actor,nickname,expires) VALUES (?,?,?,?,unixepoch()+86400)',
                        identifier, user, str(i.user.id), nickname)
            view = View()
            for decision in ('yes', 'no'):
                view.add_item(Invitation(identifier, decision))
            try:
                await utilizador.send(embed=embed('Convite para o PASTEL DE NATA', 'Aceita ou recusa nas próximas 24 horas.'), view=view)
            except discord.HTTPException:
                self.db.run("UPDATE direct_invites SET status='DM indisponível' WHERE id=?", identifier)
                return await reply(i, 'A DM não foi entregue. Pede ao jogador para permitir mensagens do servidor e repete.')
        await audit(self.bot, i.guild, 'Convite do clã', f'<@{user}>', i.user.id)
        await reply(i, 'Convite enviado.')

    async def answer_invite(self, i, identifier, decision):
        async with self.app_locks[identifier]:
            row = self.db.get("SELECT * FROM direct_invites WHERE id=? AND user=? AND status='pendente' AND expires>unixepoch()", identifier, str(i.user.id))
            guild = self.bot.get_guild(self.bot.guild_id)
            if not row or not guild:
                raise UserError('Convite indisponível.')
            if decision == 'yes':
                member = await guild.fetch_member(i.user.id)
                actor = await guild.fetch_member(int(row['actor']))
                authorized = {self.db.setting('guild', {}).get('staff'), *(role_ids(self.db).get(rank) for rank in RANKS[:3])}
                if not actor.guild_permissions.administrator and not any(str(role.id) in authorized for role in actor.roles):
                    raise UserError('Quem te convidou já não tem autorização.')
                if self.db.get('SELECT 1 FROM roster WHERE user=? AND active=1', str(member.id)):
                    raise UserError('Já fazes parte do clã.')
                await self.set_rank(guild, member, 'Recruta', actor, row['nickname'], 'Convite aceite')
            status = 'aceite' if decision == 'yes' else 'recusado'
            self.db.run('UPDATE direct_invites SET status=? WHERE id=?', status, identifier)
            await audit(self.bot, guild, 'Convite ' + status, f'<@{i.user.id}>', i.user.id)
            await reply(i, f'Convite {status}.')

    @app_commands.command(name='registar', description='Regista um novo recruta no clã.')
    async def register(self, i: discord.Interaction, utilizador: discord.Member, nickname: app_commands.Range[str, 1, 60]):
        staff(i)
        if self.db.get('SELECT 1 FROM roster WHERE user=? AND active=1', str(utilizador.id)):
            raise UserError('O jogador já está registado.')
        await i.response.defer(ephemeral=True)
        await self.set_rank(i.guild, utilizador, 'Recruta', i.user, nickname, 'Registo manual')
        await reply(i, 'Recruta registado.')

    async def step(self, i, member, direction, reason):
        staff(i)
        row = self.db.get('SELECT rank FROM roster WHERE user=? AND active=1', str(member.id))
        if not row:
            raise UserError('Membro ativo não encontrado.')
        index = RANKS.index(row['rank']) + direction
        if not 0 <= index < len(RANKS) - 1:
            raise UserError('Já está na patente limite.')
        await i.response.defer(ephemeral=True)
        await self.set_rank(i.guild, member, RANKS[index], i.user, reason=reason)
        await reply(i, f'Patente atual: {RANKS[index]}.')

    @app_commands.command(name='promover', description='Promove um membro à patente imediatamente acima.')
    async def promote(self, i: discord.Interaction, utilizador: discord.Member, motivo: app_commands.Range[str, 1, 250]):
        await self.step(i, utilizador, -1, motivo)

    @app_commands.command(name='despromover', description='Despromove um membro à patente imediatamente abaixo.')
    async def demote(self, i: discord.Interaction, utilizador: discord.Member, motivo: app_commands.Range[str, 1, 250]):
        await self.step(i, utilizador, 1, motivo)

    @app_commands.command(name='expulsar', description='Retira um jogador do clã sem o expulsar do servidor.')
    async def remove(self, i: discord.Interaction, utilizador: discord.Member, motivo: app_commands.Range[str, 1, 250]):
        staff(i)
        if not self.db.get('SELECT 1 FROM roster WHERE user=? AND active=1', str(utilizador.id)):
            raise UserError('O jogador já não pertence ao clã.')
        await i.response.defer(ephemeral=True)
        await self.set_rank(i.guild, utilizador, 'Visitante', i.user, reason=motivo)
        await reply(i, 'Removido do clã. Continua no servidor como visitante.')

    @app_commands.command(name='membros', description='Lista os membros ativos pela hierarquia.')
    async def members(self, i: discord.Interaction, pagina: app_commands.Range[int, 1, 10000] = 1):
        clan(i)
        records = sorted(self.db.all('SELECT * FROM roster WHERE active=1'), key=lambda row: (RANKS.index(row['rank']), row['nickname'].casefold()))
        start = (pagina - 1) * 15
        await reply(i, None, embed=embed(f'Membros ROB RUN · página {pagina}',
                    '\n'.join(f"{row['rank']} · {row['nickname']} · <@{row['user']}>" for row in records[start:start + 15]) or 'Sem membros nesta página.'))

    @app_commands.command(name='perfil', description='Consulta o perfil e votos de presença de um membro.')
    async def profile(self, i: discord.Interaction, utilizador: discord.Member | None = None):
        clan(i)
        member = utilizador or i.user
        row = self.db.get('SELECT * FROM roster WHERE user=?', str(member.id))
        if not row:
            raise UserError('Perfil não encontrado.')
        votes = self.db.get("SELECT count(*) AS count FROM votes WHERE user=? AND vote='sim'", str(member.id))
        await reply(i, None, embed=embed(row['nickname'], f"<@{row['user']}> · {row['rank']}\nEntrada: <t:{row['joined']}:D>\nPresenças confirmadas por voto: {votes['count']}"))

    @app_commands.command(name='historico', description='Consulta alterações de patente registadas.')
    async def history(self, i: discord.Interaction, utilizador: discord.Member):
        staff(i)
        rows = self.db.all('SELECT * FROM rank_history WHERE user=? ORDER BY id DESC LIMIT 10', str(utilizador.id))
        await reply(i, None, embed=embed('Histórico de patentes', '\n'.join(
            f"<t:{row['at']}:f> · {row['previous'] or '—'} → {row['next']} · <@{row['actor']}> · {row['reason']}"
            for row in rows) or 'Sem alterações registadas.'))

async def setup(bot):
    await bot.add_cog(People(bot))
