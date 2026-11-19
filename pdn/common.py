import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord

log = logging.getLogger('pastel')
TZ = ZoneInfo('Europe/Lisbon')
RANKS = ('Fundador', 'Subchefe', 'Oficial', 'Veterano', 'Membro', 'Recruta', 'Visitante')

class UserError(Exception):
    pass

def millis():
    return int(datetime.now(timezone.utc).timestamp() * 1000)

def parse_time(value):
    try:
        local = datetime.fromisoformat(value)
    except ValueError as error:
        raise UserError('Usa AAAA-MM-DDTHH:MM com o fuso, por exemplo 2026-12-20T21:00+00:00.') from error
    if 'T' not in value or local.tzinfo is None:
        raise UserError('Indica data, hora e fuso horário de Lisboa (+00:00 no inverno ou +01:00 no verão).')
    return int(local.timestamp() * 1000)

async def reply(i, text, **kwargs):
    if i.response.is_done():
        return await i.followup.send(text, ephemeral=True, **kwargs)
    return await i.response.send_message(text, ephemeral=True, **kwargs)

def in_guild(i):
    if i.guild_id != i.client.guild_id or not isinstance(i.user, discord.Member):
        raise UserError('Usa este comando no servidor ROB RUN.')
    return i.guild

def admin(i):
    in_guild(i)
    if not i.user.guild_permissions.administrator:
        raise UserError('Esta operação é reservada a administradores.')

def module_enabled(i, name):
    in_guild(i)
    if not i.client.db.setting('modules', {}).get(name, True):
        raise UserError('Este módulo está desativado pela administração.')

def staff(i):
    in_guild(i)
    if i.user.guild_permissions.administrator:
        return
    config = i.client.db.setting('guild', {})
    roles = i.client.db.setting('layout', {}).get('roles', {})
    allowed = {config.get('staff'), *(roles.get(name) for name in RANKS[:3])}
    if not any(str(role.id) in allowed for role in i.user.roles):
        raise UserError('Esta operação é reservada à equipa.')

def verified(i):
    in_guild(i)
    if i.user.guild_permissions.administrator:
        return
    config = i.client.db.setting('guild', {})
    layout = i.client.db.setting('layout', {})
    ids = {config.get('verified'), config.get('clan'), *layout.get('roles', {}).values()}
    if not any(str(role.id) in ids for role in i.user.roles):
        raise UserError('Verifica-te primeiro.')

def private_channel(guild, channel_id, allowed=()):
    channel = guild.get_channel(int(channel_id or 0))
    if not isinstance(channel, discord.TextChannel) or channel.permissions_for(guild.default_role).view_channel:
        return None
    valid = {guild.me.id, guild.default_role.id, *allowed}
    valid.update(role.id for role in guild.me.roles if role.is_bot_managed())
    if any(target.id not in valid and overwrite.view_channel is True
           for target, overwrite in channel.overwrites.items()):
        return None
    if not channel.permissions_for(guild.me).send_messages:
        return None
    return channel

async def audit(bot, guild, event, detail, actor=None):
    bot.db.run('INSERT INTO audit(actor,event,details) VALUES (?,?,?)',
               str(actor) if actor else None, event, detail)
    config = bot.db.setting('guild', {})
    layout = bot.db.setting('layout', {})
    allowed = {int(value) for value in [config.get('staff'), *(layout.get('roles', {}).get(r) for r in RANKS[:3])] if value}
    channel = private_channel(guild, layout.get('channels', {}).get('logs') or config.get('logs'), allowed)
    if channel:
        try:
            await channel.send(f'**{event}** · {detail}'[:1900])
        except discord.HTTPException:
            log.warning('Registo Discord indisponível para %s', event)

class View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def on_error(self, interaction, error, item):
        if not isinstance(error, UserError):
            log.warning('Interação falhou (%s)', type(error).__name__)
        await reply(interaction, str(error) if isinstance(error, UserError) else 'A operação falhou. Consulta os registos do bot.')

def embed(title, description=''):
    return discord.Embed(title=title, description=description, colour=0xe6a43b,
                         timestamp=datetime.now(timezone.utc))
