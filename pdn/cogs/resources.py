import discord
from discord import app_commands
from discord.ext import commands

from pdn.common import RANKS, UserError, audit, embed, in_guild, module_enabled, reply, staff

def clan(i):
    in_guild(i)
    if i.user.guild_permissions.administrator:
        return
    db = i.client.db
    layout = db.setting('layout', {}).get('roles', {})
    allowed = {db.setting('guild', {}).get('clan'), *(layout.get(rank) for rank in RANKS[:-1])}
    if not any(str(role.id) in allowed for role in i.user.roles):
        raise UserError('Este módulo está reservado ao clã.')

class Resources(commands.GroupCog, group_name='recurso', group_description='Inventário manual do clã.'):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db

    @app_commands.command(name='configurar', description='Cria um recurso ou altera o nome de um recurso existente.')
    async def configure(self, i: discord.Interaction, nome: app_commands.Range[str, 1, 60],
                        anterior: app_commands.Range[str, 1, 60] | None = None):
        staff(i)
        module_enabled(i, 'resources')
        name = nome.strip()
        if not name:
            raise UserError('Indica um nome.')
        if self.db.get('SELECT id FROM resources WHERE normalized=?', name.casefold()):
            raise UserError('Já existe um recurso com esse nome.')
        if anterior is None:
            self.db.run('INSERT INTO resources(name,normalized) VALUES (?,?)', name, name.casefold())
        elif not self.db.run('UPDATE resources SET name=?,normalized=? WHERE normalized=?',
                             name, name.casefold(), anterior.strip().casefold()):
            raise UserError('Recurso anterior não encontrado.')
        await reply(i, f'Recurso {name} guardado.')
        await audit(self.bot, i.guild, 'Recurso configurado', name, i.user.id)

    async def transfer(self, i, name, amount, reason):
        staff(i)
        module_enabled(i, 'resources')
        try:
            balance = self.db.move(name.strip(), amount, i.user.id, reason)
        except ValueError as error:
            raise UserError(str(error)) from error
        await reply(i, f'Movimento registado. Saldo de {name}: {balance}.')
        await audit(self.bot, i.guild, 'Movimento de recursos', f'{name} · {amount:+d} · saldo {balance}', i.user.id)

    @app_commands.command(name='adicionar', description='Regista um depósito.')
    async def add(self, i: discord.Interaction, recurso: str, quantidade: app_commands.Range[int, 1, 1000000000],
                  motivo: app_commands.Range[str, 1, 250]):
        await self.transfer(i, recurso, quantidade, motivo)

    @app_commands.command(name='retirar', description='Regista um levantamento.')
    async def remove(self, i: discord.Interaction, recurso: str, quantidade: app_commands.Range[int, 1, 1000000000],
                     motivo: app_commands.Range[str, 1, 250]):
        await self.transfer(i, recurso, -quantidade, motivo)

    @app_commands.command(name='inventario', description='Mostra os recursos e saldos.')
    async def inventory(self, i: discord.Interaction, pagina: app_commands.Range[int, 1, 10000] = 1):
        clan(i)
        rows = self.db.all('SELECT * FROM resources ORDER BY normalized LIMIT 20 OFFSET ?', (pagina - 1) * 20)
        await reply(i, None, embed=embed(f'Inventário · página {pagina}',
                    '\n'.join(f"{row['name']} · {row['balance']}" for row in rows) or 'Sem recursos nesta página.'))

    @app_commands.command(name='historico', description='Consulta os movimentos de um recurso.')
    async def history(self, i: discord.Interaction, recurso: str, pagina: app_commands.Range[int, 1, 10000] = 1):
        staff(i)
        rows = self.db.all("""
            SELECT m.* FROM movements m JOIN resources r ON r.id=m.resource
            WHERE r.normalized=? ORDER BY m.id DESC LIMIT 10 OFFSET ?
            """, recurso.strip().casefold(), (pagina - 1) * 10)
        description = '\n'.join(f"<t:{row['at']}:f> · {row['delta']:+} · <@{row['actor']}> · {row['reason']}" for row in rows)
        await reply(i, None, embed=embed(f'Movimentos · página {pagina}', description or 'Sem movimentos nesta página.'))

async def setup(bot):
    await bot.add_cog(Resources(bot))
