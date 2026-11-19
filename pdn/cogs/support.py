import asyncio

import discord
from discord.ext import commands

from pdn.common import RANKS, UserError, View, audit, embed, in_guild, module_enabled, reply

TYPES = {'geral': '💬 Suporte geral', 'recrutamento': '📝 Recrutamento',
         'denuncia': '🚨 Denúncia', 'lideranca': '🔒 Contactar liderança'}
TICKET_CATEGORIES = {
    'geral': '🎫 TICKETS — SUPORTE GERAL',
    'recrutamento': '📝 TICKETS — RECRUTAMENTO',
    'denuncia': '🚨 TICKETS — DENÚNCIAS',
    'lideranca': '🔒 TICKETS — LIDERANÇA',
}
SUPPORT_MENU_ID = 'pdn:ticket:type'
SUPPORT_PLACEHOLDER = 'Abrir ticket'
SUPPORT_DESCRIPTION = 'A equipa responde no teu canal privado.'

def support_heading(description):
    return f'### Suporte ROB RUN\n{description}'

class TicketPanel(discord.ui.LayoutView):
    def __init__(self, cog, description):
        super().__init__(timeout=None)
        details = {
            'geral': 'Dúvidas e ajuda com o servidor',
            'recrutamento': 'Candidaturas e entrada no clã',
            'denuncia': 'Comunicar um incidente em privado',
            'lideranca': 'Falar em privado com a liderança',
        }
        menu = discord.ui.Select(custom_id=SUPPORT_MENU_ID, placeholder=SUPPORT_PLACEHOLDER,
            options=[discord.SelectOption(label=label.split(' ', 1)[1], emoji=label.split(' ', 1)[0],
                                          description=details[key], value=key)
                     for key, label in TYPES.items()])
        async def callback(i):
            kind = menu.values[0]
            await i.response.defer(ephemeral=True, thinking=True)
            await cog.open_ticket(i, kind)
        menu.callback = callback
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(support_heading(description)),
            discord.ui.ActionRow(menu),
            accent_colour=discord.Colour(0xe6a43b),
        ))

    async def on_error(self, interaction, error, item):
        await reply(interaction, str(error) if isinstance(error, UserError) else
                    'Não foi possível abrir o ticket. Tenta novamente.')

class CloseForm(discord.ui.Modal, title='Fechar ticket'):
    reason = discord.ui.TextInput(label='Motivo', max_length=300, style=discord.TextStyle.paragraph)

    def __init__(self, cog):
        super().__init__(timeout=180)
        self.cog = cog

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.cog.close_ticket(i, self.reason.value.strip())
        except UserError as error:
            await reply(i, str(error))

class TicketActions(View):
    def __init__(self, cog):
        super().__init__()
        claim = discord.ui.Button(label='Assumir', custom_id='pdn:ticket:claim')
        close = discord.ui.Button(label='Fechar', custom_id='pdn:close', style=discord.ButtonStyle.danger)
        claim.callback = cog.claim

        async def close_form(i):
            cog.require_ticket(i, allow_owner=True)
            await i.response.send_modal(CloseForm(cog))
        close.callback = close_form
        self.add_item(claim)
        self.add_item(close)

class Support(commands.Cog):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        self.lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_view(TicketActions(self))
        self.bot.add_view(self.panel('support'))

    def panel(self, key, description=None):
        if description is None:
            description = self.db.setting('message:support', SUPPORT_DESCRIPTION)
        return TicketPanel(self, description)

    @staticmethod
    def is_card(message):
        def has_menu(component):
            return getattr(component, 'custom_id', None) == SUPPORT_MENU_ID or any(
                has_menu(child) for child in getattr(component, 'children', ()))
        return any(isinstance(component, discord.Container) and has_menu(component)
                   for component in message.components)

    def is_current_panel(self, message):
        heading = support_heading(self.db.setting('message:support', SUPPORT_DESCRIPTION))
        for component in message.components:
            if not isinstance(component, discord.Container) or len(component.children) != 2:
                continue
            text, row = component.children
            if (getattr(text, 'content', None) == heading and any(
                getattr(item, 'custom_id', None) == SUPPORT_MENU_ID
                and getattr(item, 'placeholder', None) == SUPPORT_PLACEHOLDER
                for item in getattr(row, 'children', ())
            )):
                return True
        return False

    async def update_panel(self, message, description=None):
        await message.edit(content=None, embed=None, attachments=[], view=self.panel('support', description))

    def responders(self, guild, kind):
        chosen = self.db.setting('ticket_roles', {}).get(kind)
        if chosen is None:
            layout = self.db.setting('layout', {}).get('roles', {})
            ranks = RANKS[:2] if kind in ('denuncia', 'lideranca') else RANKS[:3]
            chosen = [layout[rank] for rank in ranks if rank in layout]
            if not layout:
                previous = self.db.setting('guild', {}).get('staff')
                if previous and kind not in ('denuncia', 'lideranca'):
                    chosen.append(previous)
        return [role for identifier in chosen if (role := guild.get_role(int(identifier))) and not role.is_default()]

    def require_ticket(self, i, allow_owner=False):
        in_guild(i)
        row = self.db.get('SELECT * FROM tickets WHERE channel=? AND closed=0', str(i.channel_id))
        if not row:
            raise UserError('Este ticket já está fechado ou não existe.')
        if (allow_owner and row['user'] == str(i.user.id)) or i.user.guild_permissions.administrator:
            return row
        if not any(role in i.user.roles for role in self.responders(i.guild, row['kind'])) or not i.channel.permissions_for(i.user).view_channel:
            raise UserError('Este ticket está reservado à equipa responsável.')
        return row

    async def open_ticket(self, i, kind):
        guild = in_guild(i)
        module_enabled(i, 'tickets')
        if kind not in TYPES:
            raise UserError('Escolhe um tipo de ticket válido.')
        async with self.lock:
            previous = self.db.get('SELECT channel FROM tickets WHERE user=? AND kind=? AND closed=0', str(i.user.id), kind)
            if previous:
                if guild.get_channel(int(previous['channel'])):
                    return await reply(i, f"Já tens um ticket aberto em <#{previous['channel']}>.")
                self.db.run('UPDATE tickets SET closed=1 WHERE channel=?', previous['channel'])
            config = self.db.setting('guild', {})
            layout = self.db.setting('layout', {})
            category_id = layout.get('ticket_categories', {}).get(kind)
            if not category_id:
                category_id = layout.get('categories', {}).get('🎫 SUPORTE') or config.get('tickets')
            category = guild.get_channel(int(category_id or 0))
            if not isinstance(category, discord.CategoryChannel):
                raise UserError('Um administrador tem de executar /setup.')
            access = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            permissions = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                           guild.me: access, i.user: access}
            permissions.update({role: access for role in self.responders(guild, kind)})
            channel = await guild.create_text_channel(f'ticket-{kind}-{i.user.id}', category=category,
                                                       overwrites=permissions, reason='Ticket ROB RUN')
            self.db.run('INSERT INTO tickets(channel,user,kind,opened) VALUES (?,?,?,unixepoch())',
                        str(channel.id), str(i.user.id), kind)
            try:
                await channel.send(embed=embed(TYPES[kind], 'Descreve como a equipa te pode ajudar.'),
                                   view=TicketActions(self))
            except discord.HTTPException:
                raise UserError(f'O canal foi criado em {channel.mention}, mas não foi possível publicar os botões.')
        await reply(i, f'Ticket aberto em {channel.mention}.')
        await audit(self.bot, guild, 'Ticket aberto', f'{kind} · {channel.id}', i.user.id)

    async def claim(self, i):
        row = self.require_ticket(i)
        if not self.db.run('UPDATE tickets SET assignee=? WHERE channel=? AND assignee IS NULL AND closed=0',
                           str(i.user.id), row['channel']):
            raise UserError('O ticket já foi assumido.')
        await reply(i, 'Ticket atribuído a ti.')
        await audit(self.bot, i.guild, 'Ticket assumido', row['channel'], i.user.id)

    async def close_ticket(self, i, reason):
        if not reason:
            raise UserError('Indica o motivo de encerramento.')
        async with self.lock:
            row = self.require_ticket(i, allow_owner=True)
            author = i.guild.get_member(int(row['user']))
            if author:
                await i.channel.set_permissions(author, view_channel=False, send_messages=False,
                                                reason='Ticket encerrado')
            self.db.run('UPDATE tickets SET closed=1,closed_at=unixepoch(),reason=?,closer=? WHERE channel=?',
                        reason, str(i.user.id), row['channel'])
            try:
                await i.channel.edit(name=f"fechado-{row['kind']}-{row['user']}"[:100], reason='Ticket encerrado')
                await i.channel.send(embed=embed('Ticket arquivado', reason))
                if i.message:
                    await i.message.edit(view=None)
            except discord.HTTPException:
                pass
        await audit(self.bot, i.guild, 'Ticket encerrado', row['channel'], i.user.id)
        await reply(i, 'Ticket fechado e arquivado em privado.')

async def setup(bot):
    await bot.add_cog(Support(bot))
