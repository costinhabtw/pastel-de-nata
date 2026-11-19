import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

from pdn.common import RANKS, UserError, audit, embed, log, millis, module_enabled, parse_time, private_channel, reply, staff, verified

CHOICES = {'sim': '✅ Vou', 'nao': '❌ Não vou', 'talvez': '⏰ Talvez'}

class Vote(discord.ui.DynamicItem[discord.ui.Button], template=r'pdn:vote:(?P<activity>[0-9]+):(?P<choice>sim|nao|talvez)'):
    def __init__(self, activity, choice):
        self.activity, self.choice = activity, choice
        super().__init__(discord.ui.Button(label=CHOICES[choice], custom_id=f'pdn:vote:{activity}:{choice}'))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match['activity'], match['choice'])

    async def callback(self, interaction):
        try:
            verified(interaction)
            cog = interaction.client.get_cog('Activities')
            await interaction.response.defer(ephemeral=True, thinking=True)
            await cog.record(interaction, self.activity, self.choice)
        except UserError as error:
            await reply(interaction, str(error))

class Activities(commands.GroupCog, group_name='atividade', group_description='Organiza atividades no ROB RUN.'):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        self.lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_dynamic_items(Vote)
        if self.bot.sync_enabled:
            self.worker.start()

    def cog_unload(self):
        self.worker.cancel()
        self.bot.remove_dynamic_items(Vote)

    def get(self, activity):
        result = self.db.get('SELECT * FROM activities WHERE id=? AND message IS NOT NULL', activity)
        if not result:
            raise UserError('Atividade não encontrada.')
        return result

    def card(self, activity):
        status = 'Cancelada' if activity['cancelled'] else 'Encerrada' if activity['closed'] else 'Aberta'
        card = embed(activity['title'], activity['description'])
        card.add_field(name='Início', value=f"<t:{activity['starts']//1000}:F>")
        card.add_field(name='Votação', value=f"{status} · <t:{(activity['deadline'] or activity['starts'])//1000}:F>")
        card.add_field(name='Participação', value='Obrigatória' if activity['mandatory'] else 'Opcional')
        votes = self.db.all('SELECT user,vote FROM votes WHERE activity=? ORDER BY user', activity['id'])
        for key, label in CHOICES.items():
            card.add_field(name=label, value=str(sum(v['vote'] == key for v in votes)))
        confirmed = [f"<@{v['user']}>" for v in votes if v['vote'] == 'sim']
        if confirmed:
            card.add_field(name='Confirmados', value=' '.join(confirmed[:38]) + (' …' if len(confirmed) > 38 else ''), inline=False)
        card.set_footer(text=f"ID {activity['id']}")
        return card

    def view(self, activity):
        view = discord.ui.View(timeout=None)
        for key in CHOICES:
            view.add_item(Vote(activity, key))
        return view

    async def message(self, guild, activity):
        channel = guild.get_channel(int(activity['channel']))
        if not isinstance(channel, discord.TextChannel):
            raise UserError('O canal da atividade já não existe.')
        return channel.get_partial_message(int(activity['message']))

    async def refresh(self, guild, activity):
        try:
            message = await self.message(guild, activity)
            await message.edit(content=None, embed=self.card(activity),
                               view=None if activity['closed'] else self.view(activity['id']))
        except (discord.HTTPException, UserError):
            log.warning('Publicação da atividade %s indisponível', activity['id'])

    async def record(self, i, activity_id, choice):
        async with self.lock:
            activity = self.get(activity_id)
            if str(i.message.id) != activity['message'] or not self.db.vote(activity_id, i.user.id, choice, millis()):
                raise UserError('Votação encerrada ou sem acesso para esta atividade.')
            await self.refresh(i.guild, activity)
        await reply(i, f'Voto registado: {CHOICES[choice]}.')

    def parse_dates(self, start, close):
        starts, deadline = parse_time(start), parse_time(close)
        if not millis() < deadline <= starts:
            raise UserError('O fecho deve ficar no futuro e até à hora de início.')
        return starts, deadline

    @app_commands.command(name='criar', description='Publica uma atividade e convoca os cargos escolhidos.')
    async def create(self, i: discord.Interaction, nome: app_commands.Range[str, 1, 100],
                     descricao: app_commands.Range[str, 1, 1000], inicio: str, fechar: str,
                     canal: discord.TextChannel, cargo: discord.Role | None = None,
                     obrigatoria: bool = False, cargo2: discord.Role | None = None,
                     cargo3: discord.Role | None = None):
        staff(i)
        module_enabled(i, 'activities')
        starts, deadline = self.parse_dates(inicio, fechar)
        if not canal.permissions_for(i.user).view_channel:
            raise UserError('Não tens acesso ao canal escolhido.')
        roles = {role.id for role in (cargo, cargo2, cargo3) if role is not None}
        if not roles:
            roles = {int(role_id) for role_id in self.db.setting('activity_roles', [])
                     if i.guild.get_role(int(role_id))}
        if not roles:
            raise UserError('Escolhe pelo menos um cargo ou define um cargo em /config atividades.')
        if i.guild.id in roles:
            raise UserError('Escolhe cargos específicos para a convocatória.')
        await i.response.defer(ephemeral=True)
        await i.guild.chunk(cache=True)
        audience = [str(member.id) for member in i.guild.members if not member.bot
                    and any(role.id in roles for role in member.roles)
                    and canal.permissions_for(member).view_channel]
        activity_id = str(i.id)
        reminder = self.db.setting('activity_reminder', 30)
        async with self.lock:
            with self.db.conn:
                self.db.conn.execute("""
                    INSERT INTO activities(id,title,starts,channel,description,deadline,mandatory,roles,reminder)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """, (activity_id, nome.strip(), starts, str(canal.id), descricao, deadline, obrigatoria,
                          json.dumps(sorted(roles)), reminder))
                self.db.conn.executemany('INSERT INTO activity_audience(activity,user) VALUES (?,?)',
                                         [(activity_id, user) for user in audience])
            try:
                record = self.db.get('SELECT * FROM activities WHERE id=?', activity_id)
                message = await canal.send(embed=self.card(record), view=self.view(activity_id))
            except discord.HTTPException:
                self.db.run('DELETE FROM activities WHERE id=?', activity_id)
                raise
            self.db.run('UPDATE activities SET message=? WHERE id=?', str(message.id), activity_id)
        await reply(i, f'Atividade publicada para {len(audience)} membro(s). ID: {activity_id}.')
        await audit(self.bot, i.guild, 'Atividade criada', activity_id, i.user.id)

    @app_commands.command(name='editar', description='Edita o nome, descrição ou horário de uma atividade aberta.')
    async def edit(self, i: discord.Interaction, id: str, nome: app_commands.Range[str, 1, 100],
                   descricao: app_commands.Range[str, 1, 1000], inicio: str, fechar: str):
        staff(i)
        starts, deadline = self.parse_dates(inicio, fechar)
        await i.response.defer(ephemeral=True)
        async with self.lock:
            current = self.get(id)
            if current['closed'] or (current['deadline'] or current['starts']) <= millis():
                raise UserError('A votação já terminou.')
            self.db.run('UPDATE activities SET title=?,description=?,starts=?,deadline=? WHERE id=?',
                        nome, descricao, starts, deadline, id)
            await self.refresh(i.guild, self.get(id))
        await audit(self.bot, i.guild, 'Atividade editada', id, i.user.id)
        await reply(i, 'Atividade atualizada.')

    async def close_activity(self, guild, activity_id, cancelled=False):
        async with self.lock:
            activity = self.get(activity_id)
            if activity['closed']:
                return
            result = self.db.all("""
                SELECT a.user FROM activity_audience a LEFT JOIN votes v
                    ON v.activity=a.activity AND v.user=a.user
                WHERE a.activity=? AND v.user IS NULL ORDER BY a.user
                """, activity_id)
            self.db.run('UPDATE activities SET closed=1,cancelled=?,summary=? WHERE id=?',
                        cancelled, json.dumps([row['user'] for row in result]), activity_id)
            await self.refresh(guild, self.get(activity_id))

    async def summary(self, guild, activity):
        layout = self.db.setting('layout', {})
        allowed = {int(layout['roles'][rank]) for rank in RANKS[:3] if rank in layout.get('roles', {})}
        channel = private_channel(guild, layout.get('channels', {}).get('leadership'), allowed)
        if not channel:
            return
        users = json.loads(activity['summary'] or '[]')
        if users:
            import io
            attachment = discord.File(io.BytesIO(('ID Discord\n' + '\n'.join(users)).encode()),
                                      filename=f"presencas-{activity['id']}.txt")
            await channel.send(f"Votação `{activity['id']}` encerrada. {len(users)} convocado(s) sem resposta.", file=attachment)
        else:
            await channel.send(f"Votação `{activity['id']}` encerrada. Todos os convocados responderam.")
        self.db.run('UPDATE activities SET closed_by_worker=1 WHERE id=?', activity['id'])

    @app_commands.command(name='fechar', description='Fecha uma votação em curso.')
    async def close_vote(self, i: discord.Interaction, id: str):
        staff(i)
        await i.response.defer(ephemeral=True)
        await self.close_activity(i.guild, id)
        await reply(i, 'Votação encerrada.')

    @app_commands.command(name='cancelar', description='Cancela uma atividade em curso.')
    async def cancel(self, i: discord.Interaction, id: str):
        staff(i)
        await i.response.defer(ephemeral=True)
        await self.close_activity(i.guild, id, True)
        await audit(self.bot, i.guild, 'Atividade cancelada', id, i.user.id)
        await reply(i, 'Atividade encerrada.')

    @app_commands.command(name='resultados', description='Consulta os resultados de uma atividade.')
    async def results(self, i: discord.Interaction, id: str):
        verified(i)
        activity = self.get(id)
        channel = i.guild.get_channel(int(activity['channel']))
        if not channel or not channel.permissions_for(i.user).view_channel:
            raise UserError('Sem acesso a esta atividade.')
        await reply(i, None, embed=self.card(activity))

    async def listing(self, i, closed):
        verified(i)
        records = self.db.all('SELECT * FROM activities WHERE closed=? AND message IS NOT NULL ORDER BY starts DESC', closed)
        visible = []
        for activity in records:
            channel = i.guild.get_channel(int(activity['channel']))
            if channel and channel.permissions_for(i.user).view_channel:
                visible.append(f"`{activity['id']}` · {activity['title']} · <t:{activity['starts']//1000}:f>")
            if len(visible) == 10:
                break
        await reply(i, '\n'.join(visible) or 'Sem atividades disponíveis.')

    @app_commands.command(name='listar', description='Lista atividades em curso.')
    async def list_open(self, i: discord.Interaction):
        await self.listing(i, False)

    @app_commands.command(name='historico', description='Lista atividades encerradas.')
    async def history(self, i: discord.Interaction):
        await self.listing(i, True)

    async def notify(self, guild, activity, reminder):
        field = 'reminded' if reminder else 'notified'
        channel = guild.get_channel(int(activity['channel']))
        if not channel:
            return
        if reminder:
            records = self.db.all("""
                SELECT a.user FROM activity_audience a LEFT JOIN votes v
                ON v.activity=a.activity AND v.user=a.user
                WHERE a.activity=? AND a.reminded IS NULL AND v.user IS NULL LIMIT 10
                """, activity['id'])
        else:
            records = self.db.all('SELECT user FROM activity_audience WHERE activity=? AND notified IS NULL LIMIT 10', activity['id'])
        for entry in records:
            if millis() >= (activity['deadline'] or activity['starts']) or self.get(activity['id'])['closed']:
                break
            if reminder and self.db.get('SELECT 1 FROM votes WHERE activity=? AND user=?', activity['id'], entry['user']):
                continue
            member = guild.get_member(int(entry['user']))
            result = 'indisponível'
            self.db.run(f'UPDATE activity_audience SET {field}=? WHERE activity=? AND user=?',
                        'tentativa', activity['id'], entry['user'])
            if member and channel.permissions_for(member).view_channel:
                url = f"https://discord.com/channels/{guild.id}/{channel.id}/{activity['message']}"
                try:
                    kind = 'Lembrete' if reminder else 'Convocatória'
                    await member.send(f"**{kind} ROB RUN** · {activity['title']}\n"
                                      f"<t:{activity['starts']//1000}:F>\n{activity['description']}\n{url}")
                    result = 'entregue'
                except discord.HTTPException:
                    result = 'falhou'
            self.db.run(f'UPDATE activity_audience SET {field}=? WHERE activity=? AND user=?',
                        result, activity['id'], entry['user'])

    @tasks.loop(seconds=15)
    async def worker(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return
        now = millis()
        for activity in self.db.all('SELECT * FROM activities WHERE message IS NOT NULL AND closed=0'):
            try:
                deadline = activity['deadline'] or activity['starts']
                if deadline <= now:
                    await self.close_activity(guild, activity['id'])
                else:
                    await self.notify(guild, activity, False)
                    if 0 < deadline - now <= activity['reminder'] * 60000:
                        await self.notify(guild, activity, True)
            except (discord.HTTPException, UserError):
                log.warning('Processamento pendente para atividade %s', activity['id'])
        for activity in self.db.all('SELECT * FROM activities WHERE summary IS NOT NULL AND closed_by_worker=0'):
            try:
                if activity['cancelled']:
                    self.db.run('UPDATE activities SET closed_by_worker=1 WHERE id=?', activity['id'])
                else:
                    await self.summary(guild, activity)
            except discord.HTTPException:
                log.warning('Resumo privado pendente para atividade %s', activity['id'])

    @worker.before_loop
    async def wait_ready(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Activities(bot))
