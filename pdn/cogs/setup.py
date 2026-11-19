import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from pdn.common import RANKS, UserError, admin, audit, embed, log, reply
from pdn.cogs.support import TICKET_CATEGORIES

# Cada entrada define a identidade persistente, nome Discord e audiência.
LAYOUT = {
    '📌 INFORMAÇÕES': [
        ('welcome', '👋︱boas-vindas', 'public_read'), ('rules', '📜︱regras', 'public_read'),
        ('announcements', '📢︱anúncios', 'public_read'), ('verification', '✅︱verificação', 'public_read'),
        ('roles', '🎭︱cargos', 'public_read'),
    ],
    '🌍 COMUNIDADE': [
        ('chat', '💬︱chat-geral', 'verified'), ('media', '📷︱prints-e-clips', 'verified'),
        ('looking', '🎮︱procurar-equipa', 'verified'), ('suggestions', '💡︱sugestões', 'verified'),
    ],
    '🥧 PASTEL DE NATA — CLÃ': [
        ('members', '📋︱membros-do-clã', 'clan'), ('notices', '📣︱avisos-do-clã', 'clan'),
        ('operations', '⚔️︱operações', 'clan'), ('activities', '🏆︱atividades', 'clan'),
        ('resources', '📦︱recursos', 'clan'), ('stats', '📊︱estatísticas', 'clan'),
    ],
    '📝 RECRUTAMENTO': [
        ('recruitment_info', '📖︱informações', 'public_read'), ('applications', '📝︱candidaturas', 'verified'),
        ('applications_state', '📬︱estado-candidaturas', 'verified'),
    ],
    '🎫 SUPORTE': [
        ('support', '🎟️︱abrir-ticket', 'public_read'),
    ],
    '🔒 LIDERANÇA': [
        ('leadership', '💬︱chat-liderança', 'staff'), ('records', '📑︱registos', 'staff'),
        ('configuration', '⚙️︱configuração-bot', 'staff'), ('logs', '🛡️︱logs', 'staff'),
    ],
    '🔊 CANAIS DE VOZ': [
        ('voice_social', '🔊︱Convívio', 'verified'), ('voice_one', '⚔️︱Equipa 1', 'clan'),
        ('voice_two', '⚔️︱Equipa 2', 'clan'), ('voice_three', '⚔️︱Equipa 3', 'clan'),
        ('voice_staff', '👑︱Liderança', 'staff'), ('afk', '💤︱AFK', 'verified'),
    ],
}
ICONS = ('👑', '🔱', '🛡️', '💎', '⚔️', '🌱', '👤')
COLOURS = (0xefb52f, 0x9a5bd0, 0x427bdc, 0x31bbc4, 0x4aad69, 0x8fae47, 0x969aa1)
PANELS = {
    'verification': ('Verificação', 'Bem-vindo ao PASTEL DE NATA. Carrega em Verificar para acederes à comunidade.'),
    'applications': ('Candidaturas ROB RUN', 'Preenche o formulário para a equipa avaliar a tua candidatura.'),
    'support': ('Suporte ROB RUN', 'Escolhe um assunto para abrir um ticket privado.'),
}

def find(items, cached_id, name):
    if cached_id:
        match = next((item for item in items if str(item.id) == cached_id), None)
        if match:
            return match
    matches = [item for item in items if item.name.casefold() == name.casefold()]
    if len(matches) > 1:
        raise UserError(f'Existem vários componentes com o nome {name}. Resolve a duplicação e repete o /setup.')
    return next(iter(matches), None)

def overwrites(guild, role_map, audience, voice=False):
    permitted = {
        'verified': RANKS, 'clan': RANKS[:-1], 'staff': RANKS[:3],
        'public_read': (),
    }[audience]
    result = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=audience == 'public_read', send_messages=False,
            read_message_history=audience == 'public_read' if not voice else None,
            connect=False if voice else None),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True),
    }
    for rank in permitted:
        result[role_map[rank]] = discord.PermissionOverwrite(
            view_channel=True, send_messages=not voice, connect=True if voice else None,
            speak=True if voice else None, read_message_history=True)
    return result

class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        self.lock = asyncio.Lock()

    @app_commands.command(name='setup', description='Cria ou recupera a estrutura e os painéis do ROB RUN.')
    @app_commands.default_permissions(administrator=True)
    async def setup_server(self, i: discord.Interaction):
        admin(i)
        await i.response.defer(ephemeral=True)
        async with self.lock:
            self.step = 'verificar permissões'
            try:
                created, warnings = await self.provision(i.guild)
            except discord.HTTPException as error:
                log.warning('Setup interrompido em %s (HTTP %s, Discord %s)',
                            self.step, error.status, error.code)
                raise UserError(
                    f'O Discord interrompeu o setup ao {self.step}. '
                    f'Código HTTP {error.status}, Discord {error.code}. '
                    'O progresso foi guardado.'
                ) from error
            except Exception:
                log.exception('Setup interrompido em %s', self.step)
                raise
        await audit(self.bot, i.guild, 'Setup', f'{created} componente(s) criados', i.user.id)
        suffix = '\n' + '\n'.join(warnings) if warnings else ''
        await reply(i, f'Configuração concluída. {created} componente(s) criado(s) ou recuperado(s).{suffix}')

    async def provision(self, guild):
        if not guild.me.guild_permissions.manage_roles or not guild.me.guild_permissions.manage_channels:
            raise UserError('Dá ao bot permissões para gerir cargos e canais.')
        state = self.db.setting('layout', {})
        for field in ('roles', 'channels', 'categories', 'panels', 'ticket_categories'):
            state.setdefault(field, {})
        created, warnings = 0, []
        role_map = {}
        dangerous = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
                                        manage_channels=True, ban_members=True, kick_members=True,
                                        moderate_members=True, manage_webhooks=True, manage_messages=True)
        for rank, icon, colour in zip(RANKS, ICONS, COLOURS):
            name = f'{icon} {rank}'
            self.step = f'preparar o cargo {name}'
            role = find(guild.roles, state['roles'].get(rank), name)
            if role is None:
                role = await guild.create_role(name=name, colour=discord.Colour(colour), reason='Setup ROB RUN')
                created += 1
            if role.managed or role >= guild.me.top_role or role.permissions.value & dangerous.value:
                raise UserError(f'O cargo {role.name} tem de ficar abaixo do bot e sem permissões administrativas.')
            role_map[rank] = role
            state['roles'][rank] = str(role.id)
            self.db.set('layout', state)
        for name, definitions in LAYOUT.items():
            self.step = f'preparar a categoria {name}'
            category = find(guild.categories, state['categories'].get(name), name)
            if category is None:
                category = await guild.create_category(name, overwrites=overwrites(guild, role_map, 'staff' if name.startswith('🔒') else 'public_read'), reason='Setup ROB RUN')
                created += 1
            state['categories'][name] = str(category.id)
            self.db.set('layout', state)
            for key, label, audience in definitions:
                self.step = f'preparar o canal {label}'
                voice = name.startswith('🔊')
                expected = discord.VoiceChannel if voice else discord.TextChannel
                channels = [channel for channel in category.channels if isinstance(channel, expected)]
                cached = guild.get_channel(int(state['channels'].get(key) or 0))
                channel = cached if isinstance(cached, expected) else find(channels, None, label)
                if channel is None:
                    creation = guild.create_voice_channel if voice else guild.create_text_channel
                    channel = await creation(label, category=category,
                                             overwrites=overwrites(guild, role_map, audience, voice),
                                             reason='Setup ROB RUN')
                    created += 1
                state['channels'][key] = str(channel.id)
                self.db.set('layout', state)
        support = self.bot.get_cog('Support')
        position = max((category.position for category in guild.categories), default=-1) + 1
        for kind, label in TICKET_CATEGORIES.items():
            self.step = f'preparar a categoria {label}'
            category = find(guild.categories, state['ticket_categories'].get(kind), label)
            if category is None:
                access = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
                permissions = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    guild.me: access,
                    **{role: access for role in support.responders(guild, kind)},
                }
                category = await guild.create_category(label, position=position, overwrites=permissions,
                                                       reason='Categorias de tickets ROB RUN')
                position += 1
                created += 1
            state['ticket_categories'][kind] = str(category.id)
            self.db.set('layout', state)
        for key, (title, description) in PANELS.items():
            self.step = f'publicar o painel {title}'
            channel = await guild.fetch_channel(int(state['channels'][key]))
            previous = state['panels'].get(key, {})
            message = None
            if previous.get('channel') == str(channel.id):
                try:
                    message = await channel.fetch_message(int(previous['id']))
                except discord.NotFound:
                    pass
            if message is None:
                marker = f'PASTEL DE NATA · {key}'
                async for candidate in channel.history(limit=30):
                    if candidate.author.id == guild.me.id and (
                        any(card.footer.text == marker for card in candidate.embeds)
                        or (key == 'support' and support.is_card(candidate))
                    ):
                        message = candidate
                        break
            if message is None:
                if key == 'support':
                    message = await channel.send(view=support.panel(key))
                else:
                    card = embed(f'PASTEL DE NATA · {title}', self.db.setting(f'message:{key}', description))
                    card.set_footer(text=f'PASTEL DE NATA · {key}')
                    message = await channel.send(embed=card, view=self.bot.get_cog('People').panel(key))
                created += 1
            elif key == 'support' and message.author.id == guild.me.id and not support.is_card(message):
                await support.update_panel(message)
            state['panels'][key] = {'channel': str(channel.id), 'id': str(message.id)}
            self.db.set('layout', state)
        self.step = 'preparar o canal AFK'
        if not guild.afk_channel and guild.me.guild_permissions.manage_guild:
            afk = discord.Object(id=int(state['channels']['afk']))
            try:
                await guild.edit(afk_channel=afk, reason='Setup ROB RUN')
            except discord.Forbidden:
                warnings.append('O Discord recusou a associação automática do AFK. Define-o nas configurações do servidor.')
        elif not guild.afk_channel:
            warnings.append('O canal AFK foi criado. A associação automática requer Gerir Servidor.')
        return created, warnings

async def setup(bot):
    await bot.add_cog(Setup(bot))
