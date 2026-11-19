import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands

from pdn.common import UserError, reply
from pdn.db import Database

COGS = ('setup', 'people', 'activities', 'resources', 'support', 'management')

class Pastel(commands.Bot):
    def __init__(self, guild_id, database, *, sync=True):
        intents = discord.Intents.none()
        intents.guilds = intents.members = intents.guild_messages = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none(), help_command=None)
        self.guild_id = guild_id
        self.db = database
        self.sync_enabled = sync
        self.tree.on_error = self.command_error

    async def setup_hook(self):
        expected_app = os.environ.get('DISCORD_APP_ID')
        if self.sync_enabled and expected_app and str(self.application_id) != expected_app:
            raise SystemExit('DISCORD_APP_ID não corresponde à aplicação autenticada.')
        for module in COGS:
            await self.load_extension(f'pdn.cogs.{module}')
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        if self.sync_enabled:
            published = await self.tree.sync(guild=guild)
            logging.info('%s comandos registados.', len(published))

    async def on_ready(self):
        logging.info('PASTEL DE NATA online · servidor %s', self.guild_id)

    async def command_error(self, interaction, error):
        error = getattr(error, 'original', error)
        if not isinstance(error, UserError):
            logging.warning('Comando falhou (%s)', type(error).__name__)
        await reply(interaction, str(error) if isinstance(error, UserError) else 'Operação indisponível. Consulta os registos do bot.')

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    token = os.environ.get('DISCORD_TOKEN')
    guild = os.environ.get('DISCORD_GUILD_ID', '')
    if not token or not guild.isdecimal():
        raise SystemExit('Configura DISCORD_TOKEN e DISCORD_GUILD_ID no ambiente.')
    database = Database(Path(os.environ.get('DATA_DIR', './data')) / 'pastel.sqlite')
    try:
        async with Pastel(int(guild), database) as bot:
            await bot.start(token)
    except discord.LoginFailure:
        raise SystemExit('O Discord recusou o token.') from None
    finally:
        database.conn.close()

if __name__ == '__main__':
    asyncio.run(main())
