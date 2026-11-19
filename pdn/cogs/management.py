import re
from collections import defaultdict, deque
from datetime import timedelta
from time import monotonic

import discord
from discord import app_commands
from discord.ext import commands

from pdn.common import RANKS, UserError, View, admin, audit, embed, in_guild, module_enabled, reply, staff, verified
from pdn.cogs.people import QUESTIONS
from pdn.cogs.setup import PANELS
from pdn.cogs.support import TYPES

def welcome_card(member, message, verification_channel=None):
    guild = member.guild
    description = f'{member.mention} {message}'
    if verification_channel:
        description += f'\n\nPara começares, verifica-te em <#{verification_channel}>.'
    card = embed('👋 Novo membro!', description)
    card.colour = discord.Colour.green()
    card.set_thumbnail(url=member.display_avatar.url)
    card.add_field(name='Membro', value=f'{member.mention}\n{discord.utils.escape_markdown(member.name)}')
    card.add_field(name='Conta criada', value=discord.utils.format_dt(member.created_at, style='R'))
    total = guild.member_count
    card.add_field(name='Total de membros', value=str(total) if total is not None else 'Indisponível')
    if guild.icon:
        card.set_footer(text=guild.name, icon_url=guild.icon.url)
    else:
        card.set_footer(text=guild.name)
    return card

class SuggestionVote(discord.ui.DynamicItem[discord.ui.Button], template=r'pdn:suggestion:(?P<id>[0-9]+):(?P<vote>yes|no)'):
    def __init__(self, identifier, vote):
        self.identifier, self.vote = identifier, vote
        super().__init__(discord.ui.Button(label='A favor' if vote == 'yes' else 'Contra',
                         custom_id=f'pdn:suggestion:{identifier}:{vote}'))

    @classmethod
    async def from_custom_id(cls, i, item, match):
        return cls(match['id'], match['vote'])

    async def callback(self, i):
        try:
            verified(i)
            db = i.client.db
            db.run('INSERT INTO suggestions VALUES (?,?,?) ON CONFLICT(id,user) DO UPDATE SET vote=excluded.vote',
                   self.identifier, str(i.user.id), self.vote)
            totals = {row['vote']: row['n'] for row in db.all(
                'SELECT vote,count(*) AS n FROM suggestions WHERE id=? GROUP BY vote', self.identifier)}
            await reply(i, f"Voto registado. A favor: {totals.get('yes',0)} · Contra: {totals.get('no',0)}.")
        except UserError as error:
            await reply(i, str(error))

class Management(commands.Cog):
    config = app_commands.Group(name='config', description='Configuração administrativa do bot.',
                                default_permissions=discord.Permissions(administrator=True))

    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        self.message_times = defaultdict(deque)

    async def cog_load(self):
        self.bot.add_dynamic_items(SuggestionVote)

    async def changed(self, i, name):
        await audit(self.bot, i.guild, 'Configuração', name, i.user.id)
        await reply(i, 'Configuração guardada.')

    @config.command(name='atividades', description='Configura os lembretes e o cargo convocado por predefinição.')
    async def activity_config(self, i: discord.Interaction, minutos: app_commands.Range[int, 0, 1440] | None = None,
                              cargo: discord.Role | None = None):
        admin(i)
        if minutos is None and not cargo:
            raise UserError('Indica a antecedência ou o cargo.')
        if cargo and cargo.is_default():
            raise UserError('Escolhe um cargo específico.')
        if minutos is not None:
            self.db.set('activity_reminder', minutos)
        if cargo:
            self.db.set('activity_roles', [str(cargo.id)])
        await self.changed(i, 'atividades')

    @config.command(name='modulos', description='Ativa ou suspende novas operações de um módulo.')
    @app_commands.choices(modulo=[app_commands.Choice(name=label, value=key) for key, label in
                                [('recruitment', 'Recrutamento'), ('activities', 'Atividades'),
                                 ('resources', 'Recursos'), ('tickets', 'Tickets'),
                                 ('suggestions', 'Sugestões'), ('announcements', 'Anúncios')]])
    async def modules(self, i: discord.Interaction, modulo: str, ativo: bool):
        admin(i)
        state = self.db.setting('modules', {})
        state[modulo] = ativo
        self.db.set('modules', state)
        await self.changed(i, 'módulo ' + modulo)

    @config.command(name='recrutamento', description='Altera uma pergunta do formulário ou o canal privado de avaliação.')
    async def recruiting(self, i: discord.Interaction, pergunta: app_commands.Range[int, 1, 5] | None = None,
                         texto: app_commands.Range[str, 1, 45] | None = None,
                         canal: discord.TextChannel | None = None):
        admin(i)
        if (pergunta is None) != (texto is None) or not (pergunta or canal):
            raise UserError('Indica o número e texto da pergunta, ou um canal privado.')
        if canal:
            if canal.permissions_for(i.guild.default_role).view_channel:
                raise UserError('Escolhe um canal privado.')
            self.db.set('application_channel', str(canal.id))
        if pergunta:
            questions = list(self.db.setting('questions', QUESTIONS))
            questions[pergunta - 1] = texto.strip()
            if not questions[pergunta - 1]:
                raise UserError('A pergunta não pode ficar vazia.')
            self.db.set('questions', questions)
        await self.changed(i, 'recrutamento')

    @config.command(name='tickets', description='Define o cargo responsável por uma categoria de tickets futuros.')
    @app_commands.choices(tipo=[app_commands.Choice(name=label, value=key) for key, label in TYPES.items()])
    async def tickets(self, i: discord.Interaction, tipo: str, cargo: discord.Role):
        admin(i)
        if cargo.is_default() or cargo.managed:
            raise UserError('Escolhe um cargo de equipa.')
        settings = self.db.setting('ticket_roles', {})
        settings[tipo] = [str(cargo.id)]
        self.db.set('ticket_roles', settings)
        await self.changed(i, 'tickets ' + tipo)

    @config.command(name='cargos', description='Define o cargo adicional que pode gerir módulos do clã.')
    async def roles(self, i: discord.Interaction, equipa: discord.Role):
        admin(i)
        if equipa.is_default() or equipa.managed:
            raise UserError('Escolhe um cargo de equipa.')
        config = self.db.setting('guild', {})
        config['staff'] = str(equipa.id)
        self.db.set('guild', config)
        await self.changed(i, 'cargo da equipa')

    @config.command(name='canais', description='Define os canais de sugestões ou boas-vindas.')
    async def channels(self, i: discord.Interaction, sugestoes: discord.TextChannel | None = None,
                       boas_vindas: discord.TextChannel | None = None):
        admin(i)
        if not sugestoes and not boas_vindas:
            raise UserError('Indica pelo menos um canal.')
        config = self.db.setting('layout', {})
        channels = config.setdefault('channels', {})
        if sugestoes:
            channels['suggestions'] = str(sugestoes.id)
        if boas_vindas:
            channels['welcome'] = str(boas_vindas.id)
        self.db.set('layout', config)
        await self.changed(i, 'canais')

    @config.command(name='logs', description='Define um canal privado para logs administrativos.')
    async def logs(self, i: discord.Interaction, canal: discord.TextChannel):
        admin(i)
        from pdn.common import private_channel
        layout = self.db.setting('layout', {})
        config = self.db.setting('guild', {})
        allowed = {int(value) for value in [config.get('staff'), *(layout.get('roles', {}).get(rank) for rank in RANKS[:3])] if value}
        if not private_channel(i.guild, canal.id, allowed):
            raise UserError('O canal tem de ser privado, com acesso limitado à equipa e ao bot.')
        layout.setdefault('channels', {})['logs'] = str(canal.id)
        self.db.set('layout', layout)
        await self.changed(i, 'logs')

    @config.command(name='moderacao', description='Ativa proteção automática e define limites por mensagem e por dez segundos.')
    async def moderation(self, i: discord.Interaction, ativar: bool,
                         mencoes: app_commands.Range[int, 2, 25] = 6,
                         mensagens: app_commands.Range[int, 3, 20] = 8,
                         cargo: discord.Role | None = None):
        admin(i)
        if ativar and not i.guild.me.guild_permissions.manage_messages:
            raise UserError('O bot precisa de Gerir Mensagens.')
        if cargo and (cargo.is_default() or cargo.managed):
            raise UserError('Escolhe um cargo de moderação.')
        settings = self.db.setting('moderation', {})
        settings.update(enabled=ativar, mentions=mencoes, messages=mensagens)
        if cargo:
            settings['role'] = str(cargo.id)
        self.db.set('moderation', settings)
        await self.changed(i, 'moderação')

    @config.command(name='mensagens', description='Personaliza um painel ou o texto de boas-vindas.')
    @app_commands.choices(painel=[app_commands.Choice(name=label, value=key)
                                for key, label in [('verification','Verificação'),('applications','Candidaturas'),
                                                   ('support','Suporte'),('welcome','Boas-vindas')]])
    async def messages(self, i: discord.Interaction, painel: str, texto: app_commands.Range[str, 1, 1000]):
        admin(i)
        await i.response.defer(ephemeral=True)
        layout = self.db.setting('layout', {})
        panel = layout.get('panels', {}).get(painel)
        if panel:
            channel = i.guild.get_channel(int(panel['channel']))
            if channel:
                try:
                    message = await channel.fetch_message(int(panel['id']))
                    if painel == 'support':
                        await self.bot.get_cog('Support').update_panel(message, texto)
                    else:
                        card = embed(f'PASTEL DE NATA · {PANELS[painel][0]}', texto)
                        card.set_footer(text=f'PASTEL DE NATA · {painel}')
                        await message.edit(embed=card)
                except discord.NotFound:
                    pass
        self.db.set(f'message:{painel}', texto)
        await self.changed(i, 'mensagem ' + painel)

    def moderator(self, i, permission, target=None):
        guild = in_guild(i)
        settings = self.db.setting('moderation', {})
        if not getattr(i.user.guild_permissions, permission):
            raise UserError('Falta-te a permissão Discord para esta ação.')
        role = settings.get('role')
        if role and not i.user.guild_permissions.administrator and not any(str(r.id) == role for r in i.user.roles):
            raise UserError('Esta operação está reservada ao cargo de moderação.')
        if target and (target == i.user or target.id == guild.owner_id or guild.me.top_role <= target.top_role or
                       (i.user.id != guild.owner_id and i.user.top_role <= target.top_role)):
            raise UserError('A hierarquia Discord impede esta ação.')

    async def sanction(self, i, target, action, reason):
        self.db.run('INSERT INTO sanctions(user,actor,action,reason) VALUES (?,?,?,?)',
                    str(target.id), str(i.user.id), action, reason)
        await audit(self.bot, i.guild, 'Moderação ' + action, f'<@{target.id}> · {reason}', i.user.id)
        await reply(i, 'Sanção registada.')

    @app_commands.command(name='warn', description='Regista um aviso de moderação.')
    async def warn(self, i: discord.Interaction, membro: discord.Member, motivo: app_commands.Range[str, 1, 250]):
        self.moderator(i, 'moderate_members', membro)
        await self.sanction(i, membro, 'aviso', motivo)

    @app_commands.command(name='warnings', description='Consulta o histórico de sanções de um utilizador.')
    async def warnings(self, i: discord.Interaction, utilizador: discord.User):
        self.moderator(i, 'moderate_members')
        rows = self.db.all('SELECT * FROM sanctions WHERE user=? ORDER BY id DESC LIMIT 10', str(utilizador.id))
        await reply(i, None, embed=embed('Histórico de sanções', '\n'.join(
            f"<t:{row['at']}:f> · {row['action']} · {row['reason']}" for row in rows) or 'Sem sanções.'))

    @app_commands.command(name='timeout', description='Aplica um timeout a um membro.')
    async def timeout(self, i: discord.Interaction, membro: discord.Member,
                      minutos: app_commands.Range[int, 1, 40320], motivo: app_commands.Range[str, 1, 250]):
        self.moderator(i, 'moderate_members', membro)
        await i.response.defer(ephemeral=True)
        await membro.timeout(timedelta(minutes=minutos), reason=motivo)
        await self.sanction(i, membro, 'timeout', motivo)

    @app_commands.command(name='kick', description='Expulsa um membro do servidor Discord.')
    async def kick(self, i: discord.Interaction, membro: discord.Member, motivo: app_commands.Range[str, 1, 250]):
        self.moderator(i, 'kick_members', membro)
        await i.response.defer(ephemeral=True)
        await membro.kick(reason=motivo)
        await self.sanction(i, membro, 'kick', motivo)

    @app_commands.command(name='ban', description='Bane um membro do servidor Discord.')
    async def ban(self, i: discord.Interaction, membro: discord.Member, motivo: app_commands.Range[str, 1, 250]):
        self.moderator(i, 'ban_members', membro)
        await i.response.defer(ephemeral=True)
        await membro.ban(reason=motivo, delete_message_seconds=0)
        await self.sanction(i, membro, 'ban', motivo)

    @app_commands.command(name='unban', description='Retira um banimento pelo ID Discord.')
    async def unban(self, i: discord.Interaction, utilizador: str, motivo: app_commands.Range[str, 1, 250]):
        self.moderator(i, 'ban_members')
        if not re.fullmatch(r'[0-9]{17,20}', utilizador):
            raise UserError('Indica um ID Discord válido.')
        await i.response.defer(ephemeral=True)
        target = discord.Object(id=int(utilizador))
        await i.guild.unban(target, reason=motivo)
        await self.sanction(i, target, 'unban', motivo)

    @app_commands.command(name='anuncio', description='Publica um anúncio no canal escolhido.')
    async def announce(self, i: discord.Interaction, canal: discord.TextChannel,
                       titulo: app_commands.Range[str, 1, 100], mensagem: app_commands.Range[str, 1, 1500],
                       cargo: discord.Role | None = None):
        staff(i)
        module_enabled(i, 'announcements')
        if not canal.permissions_for(i.user).view_channel:
            raise UserError('Não tens acesso ao canal escolhido.')
        if cargo and (cargo.is_default() or not i.user.guild_permissions.mention_everyone):
            raise UserError('Não tens autorização para mencionar esse cargo.')
        await i.response.defer(ephemeral=True)
        await canal.send(content=cargo.mention if cargo else None, embed=embed(titulo, mensagem),
                         allowed_mentions=discord.AllowedMentions(roles=[cargo] if cargo else [],
                                                                 users=False, everyone=False, replied_user=False))
        await audit(self.bot, i.guild, 'Anúncio', str(canal.id), i.user.id)
        await reply(i, 'Anúncio publicado.')

    @app_commands.command(name='sugestao', description='Publica uma sugestão para votação.')
    @app_commands.checks.cooldown(1, 60)
    async def suggestion(self, i: discord.Interaction, texto: app_commands.Range[str, 1, 1000]):
        verified(i)
        module_enabled(i, 'suggestions')
        channel = i.guild.get_channel(int(self.db.setting('layout', {}).get('channels', {}).get('suggestions') or 0))
        if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(i.user).view_channel:
            raise UserError('O canal de sugestões está indisponível.')
        await i.response.defer(ephemeral=True)
        view = View()
        for choice in ('yes', 'no'):
            view.add_item(SuggestionVote(str(i.id), choice))
        await channel.send(embed=embed('Sugestão', f'<@{i.user.id}>\n{texto}'), view=view)
        await reply(i, 'Sugestão publicada.')

    @app_commands.command(name='status', description='Mostra a latência e o estado do bot.')
    async def status(self, i: discord.Interaction):
        in_guild(i)
        auto = self.db.setting('moderation', {}).get('enabled', False)
        names = ', '.join(self.bot.cogs)
        await reply(i, f'Online · {self.bot.latency * 1000:.0f} ms · {names}. Moderação automática: {"ativa" if auto else "inativa"}.')

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != self.bot.guild_id:
            return
        await audit(self.bot, member.guild, 'Entrada', f'<@{member.id}>')
        channel_id = self.db.setting('layout', {}).get('channels', {}).get('welcome')
        channel = member.guild.get_channel(int(channel_id or 0))
        if isinstance(channel, discord.TextChannel):
            verify = self.db.setting('layout', {}).get('channels', {}).get('verification')
            try:
                message = self.db.setting('message:welcome', 'Bem-vindo ao **PASTEL DE NATA | ROB RUN**! Fica à vontade.')
                await channel.send(embed=welcome_card(member, message, verify))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild.id == self.bot.guild_id:
            await audit(self.bot, member.guild, 'Saída', f'<@{member.id}>')

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if after.guild.id == self.bot.guild_id and before.roles != after.roles:
            added = set(after.roles) - set(before.roles)
            removed = set(before.roles) - set(after.roles)
            changes = [*(f'+{role.id}' for role in added), *(f'-{role.id}' for role in removed)]
            await audit(self.bot, after.guild, 'Cargos alterados', f'<@{after.id}> · ' + ', '.join(changes))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, event):
        if event.guild_id == self.bot.guild_id:
            guild = self.bot.get_guild(event.guild_id)
            if guild:
                await audit(self.bot, guild, 'Mensagem eliminada', f'Canal {event.channel_id} · mensagem {event.message_id}')

    @commands.Cog.listener()
    async def on_raw_message_edit(self, event):
        if event.guild_id == self.bot.guild_id and 'edited_timestamp' in event.data:
            guild = self.bot.get_guild(event.guild_id)
            if guild:
                await audit(self.bot, guild, 'Mensagem editada', f'Canal {event.channel_id} · mensagem {event.message_id}')

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if after.guild.id == self.bot.guild_id and (before.name != after.name or before.overwrites != after.overwrites):
            await audit(self.bot, after.guild, 'Canal alterado', f'ID {after.id}')

    @commands.Cog.listener()
    async def on_message(self, message):
        guild, user = message.guild, message.author
        if not guild or guild.id != self.bot.guild_id or user.bot or user.guild_permissions.manage_messages:
            return
        settings = self.db.setting('moderation', {})
        if not settings.get('enabled'):
            self.message_times.clear()
            return
        timestamp = monotonic()
        if len(self.message_times) > 10000:
            self.message_times = defaultdict(deque, {k:q for k,q in self.message_times.items() if q and timestamp-q[-1]<10})
        recent = self.message_times[user.id]
        while recent and timestamp - recent[0] >= 10:
            recent.popleft()
        recent.append(timestamp)
        mentions = len(message.mentions) + len(message.role_mentions)
        if mentions < settings['mentions'] and len(recent) < settings['messages']:
            return
        try:
            await message.delete()
        except discord.HTTPException:
            return
        reason = 'Menções excessivas' if mentions >= settings['mentions'] else 'Mensagens repetidas em dez segundos'
        self.db.run('INSERT INTO sanctions(user,actor,action,reason) VALUES (?,?,?,?)',
                    str(user.id), str(self.bot.user.id), 'proteção automática', reason)
        await audit(self.bot, guild, 'Proteção automática', f'<@{user.id}> · {reason}')

async def setup(bot):
    await bot.add_cog(Management(bot))
