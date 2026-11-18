import {
  ActionRowBuilder, ButtonBuilder, ButtonStyle, ChannelType, Client, Events,
  GatewayIntentBits, MessageFlags, ModalBuilder, PermissionFlagsBits as P,
  REST, Routes, SlashCommandBuilder, TextInputBuilder, TextInputStyle,
} from 'discord.js';
import { openStore, parseActivityTime } from './store.mjs';

const { DISCORD_TOKEN: token, DISCORD_APP_ID: appId, DISCORD_GUILD_ID: guildId } = process.env;
if (!token || !/^\d{17,20}$/.test(appId ?? '') || !/^\d{17,20}$/.test(guildId ?? '')) {
  console.error('Preenche DISCORD_TOKEN, DISCORD_APP_ID e DISCORD_GUILD_ID no ambiente.');
  process.exit(1);
}

const db = openStore(process.env.DATA_DIR || './data');
const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMembers, GatewayIntentBits.GuildInvites],
  allowedMentions: { parse: [], repliedUser: false },
});
const rest = new REST({ version: '10', timeout: 10_000 }).setToken(token);
const command = (name, description) => new SlashCommandBuilder().setName(name).setDescription(description);
const str = (builder, name, description, limit, required = true) => builder.addStringOption(
  option => option.setName(name).setDescription(description).setMaxLength(limit).setRequired(required),
);
const commands = [
  command('configurar', 'Configura os cargos e canais do PASTEL DE NATA.')
    .setDefaultMemberPermissions(P.Administrator)
    .addRoleOption(o => o.setName('verificado').setDescription('Cargo após verificação').setRequired(true))
    .addRoleOption(o => o.setName('cla').setDescription('Cargo de membro do clã').setRequired(true))
    .addRoleOption(o => o.setName('equipa').setDescription('Cargo da equipa de gestão').setRequired(true))
    .addChannelOption(o => o.setName('registos').setDescription('Canal privado de logs e candidaturas')
      .addChannelTypes(ChannelType.GuildText).setRequired(true))
    .addChannelOption(o => o.setName('tickets').setDescription('Categoria para tickets')
      .addChannelTypes(ChannelType.GuildCategory).setRequired(true)),
  command('painel', 'Publica os botões de verificação, candidaturas e suporte.')
    .setDefaultMemberPermissions(P.Administrator),
  command('avisos', 'Ativa ou desativa os lembretes de atividades por DM.')
    .addBooleanOption(o => o.setName('ativar').setDescription('Receber lembretes').setRequired(true)),
  command('convite', 'Cria um convite de utilização única, válido por 24 horas.'),
  command('atividade', 'Gere as atividades ROB RUN.')
    .addSubcommand(s => str(str(s.setName('criar').setDescription('Abre uma votação no canal atual.'),
      'titulo', 'Título da atividade', 100), 'inicio', 'Exemplo 2026-12-01T21:30+00:00', 22))
    .addSubcommand(s => str(s.setName('fechar').setDescription('Encerra a votação e publica o resultado.'),
      'id', 'ID apresentado na atividade', 20)),
  command('membro', 'Consulta ou gere a participação no clã.')
    .addUserOption(o => o.setName('utilizador').setDescription('Membro do servidor').setRequired(true))
    .addStringOption(o => o.setName('acao').setDescription('Ação da equipa').setRequired(true).addChoices(
      { name: 'Consultar', value: 'consultar' },
      { name: 'Adicionar ao clã', value: 'adicionar' },
      { name: 'Retirar do clã', value: 'retirar' },
    )),
];

const sensitiveRolePermissions = P.Administrator | P.ManageGuild | P.ManageRoles | P.ManageChannels |
  P.BanMembers | P.KickMembers | P.ModerateMembers | P.ManageWebhooks | P.ManageMessages;

async function grantRole(member, roleId, reason) {
  const role = await member.guild.roles.fetch(roleId, { force: true });
  if (!role?.editable || role.permissions.any(sensitiveRolePermissions)) {
    fail('O cargo deixou de estar disponível ou ganhou permissões de gestão.');
  }
  await member.roles.add(role, reason);
}

const settings = () => JSON.parse(db.get("SELECT value FROM settings WHERE key = 'guild'")?.value ?? 'null');
const isStaff = (i, config) => i.memberPermissions.has(P.Administrator) || i.member.roles.cache.has(config.staff);
const isVerified = (i, config) => isStaff(i, config) || i.member.roles.cache.has(config.verified);
const button = (id, label, style = ButtonStyle.Secondary) =>
  new ButtonBuilder().setCustomId(id).setLabel(label).setStyle(style);
const row = (...buttons) => new ActionRowBuilder().addComponents(buttons);
const response = content => ({ content, flags: MessageFlags.Ephemeral });
const fail = message => { throw new UserError(message); };
class UserError extends Error {}

function report(context, error) {
  const code = typeof error?.code === 'number' || /^[A-Z_0-9]+$/.test(error?.code ?? '')
    ? error.code : 'indisponível';
  console.error(JSON.stringify({ context, code }));
}

const work = new Map();
async function exclusively(key, task) {
  const previous = work.get(key) ?? Promise.resolve();
  const next = previous.catch(() => {}).then(task);
  work.set(key, next);
  try {
    return await next;
  } finally {
    if (work.get(key) === next) work.delete(key);
  }
}

async function logChannel(guild, config) {
  const channel = await guild.channels.fetch(config.logs);
  if (channel?.type !== ChannelType.GuildText || channel.permissionsFor(guild.roles.everyone)?.has(P.ViewChannel)) {
    fail('O canal de registos tem de ser privado.');
  }
  const permitted = new Set([config.staff, client.user.id, guild.members.me?.roles.botRole?.id]);
  if (channel.permissionOverwrites.cache.some(overwrite =>
    overwrite.allow.has(P.ViewChannel) && !permitted.has(overwrite.id))) {
    fail('Restringe o acesso explícito aos registos ao cargo da equipa e ao bot.');
  }
  return channel;
}

async function audit(guild, content) {
  console.info(JSON.stringify({ event: 'audit', guild: guild.id, content }));
  const config = settings();
  if (!config) return;
  try {
    const channel = await logChannel(guild, config);
    await channel.send({ content });
  } catch (error) {
    report('registos', error);
  }
}

function panel() {
  return {
    content: '**PASTEL DE NATA · ROB RUN**\nVerifica-te para acederes ao servidor. Usa os botões para te candidatares ao clã ou pedires apoio.',
    components: [row(
      button('pdn:verify', 'Verificar', ButtonStyle.Success),
      button('pdn:apply', 'Candidatar-me', ButtonStyle.Primary),
      button('pdn:ticket', 'Abrir ticket'),
    )],
  };
}

async function configure(i) {
  if (!i.memberPermissions.has(P.Administrator)) fail('Apenas administradores podem configurar o bot.');
  const verified = i.options.getRole('verificado', true);
  const clan = i.options.getRole('cla', true);
  const staff = i.options.getRole('equipa', true);
  const roles = [verified, clan, staff];
  if (new Set(roles.map(r => r.id)).size !== 3 || roles.some(r => r.id === guildId || r.managed) ||
      [verified, clan].some(r => !r.editable || r.permissions.any(sensitiveRolePermissions))) {
    fail('Escolhe três cargos distintos. Os cargos de acesso devem ficar abaixo do bot e sem poderes de moderação.');
  }
  const config = {
    verified: verified.id, clan: clan.id, staff: staff.id,
    logs: i.options.getChannel('registos', true).id,
    tickets: i.options.getChannel('tickets', true).id,
  };
  const logs = await logChannel(i.guild, config);
  const bot = await i.guild.members.fetchMe();
  if (!logs.permissionsFor(bot)?.has([P.ViewChannel, P.SendMessages]) ||
      !logs.permissionsFor(staff)?.has(P.ViewChannel) ||
      [verified, clan].some(role => logs.permissionsFor(role)?.has(P.ViewChannel))) {
    fail('Nos registos, dá acesso ao bot e à equipa e retira acesso aos cargos de verificado e clã.');
  }
  db.run("INSERT INTO settings VALUES ('guild', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
    JSON.stringify(config));
  await audit(i.guild, `Configuração atualizada por <@${i.user.id}>.`);
  return 'Configuração guardada. Usa /painel no canal de entrada.';
}

async function apply(i, config) {
  if (!isVerified(i, config)) fail('Verifica-te antes de te candidatares.');
  if (i.member.roles.cache.has(config.clan)) fail('Já fazes parte do clã.');
  return exclusively(`application:${i.user.id}`, async () => {
    if (db.get("SELECT id FROM applications WHERE user = ? AND status = 'pendente'", i.user.id)) {
      fail('Já tens uma candidatura pendente.');
    }
    const channel = await logChannel(i.guild, config);
    const answer = i.fields.getTextInputValue('answer').trim();
    if (!answer) fail('Escreve uma resposta antes de enviar.');
    db.run('INSERT INTO applications (id, user, answer) VALUES (?, ?, ?)', i.id, i.user.id, answer);
    try {
      await channel.send({
        content: `**Candidatura ROB RUN** · <@${i.user.id}> · \`${i.id}\`\n${answer}`,
        components: [row(button(`pdn:approve:${i.id}`, 'Aprovar', ButtonStyle.Success),
          button(`pdn:reject:${i.id}`, 'Recusar', ButtonStyle.Danger))],
      });
    } catch (error) {
      db.run('DELETE FROM applications WHERE id = ?', i.id);
      throw error;
    }
    return 'Candidatura enviada à equipa.';
  });
}

function review(i, config, id, approved) {
  if (!isStaff(i, config)) fail('A revisão é reservada à equipa.');
  return exclusively(`review:${id}`, async () => {
    const application = db.get('SELECT * FROM applications WHERE id = ?', id);
    if (!application || application.status !== 'pendente') fail('Esta candidatura já foi tratada.');
    if (approved) {
      const member = await i.guild.members.fetch(application.user);
      await grantRole(member, config.clan, `Candidatura aprovada por ${i.user.id}`);
    }
    const status = approved ? 'aprovada' : 'recusada';
    db.run('UPDATE applications SET status = ? WHERE id = ?', status, id);
    await i.message.edit({ components: [] }).catch(error => report('botões da candidatura', error));
    const user = await client.users.fetch(application.user).catch(() => null);
    const sent = await user?.send(`A tua candidatura ao clã ROB RUN foi ${status}.`).catch(() => null);
    await audit(i.guild, `Candidatura ${id} de <@${application.user}> ${status} por <@${i.user.id}>.`);
    return `Candidatura ${status}.${sent ? '' : ' A DM não foi entregue.'}`;
  });
}

function openTicket(i, config) {
  return exclusively(`ticket:${i.user.id}`, async () => {
    const previous = db.get('SELECT channel FROM tickets WHERE user = ? AND closed = 0', i.user.id);
    if (previous) {
      const exists = await i.guild.channels.fetch(previous.channel).catch(error => {
        if (error.code === 10003) return null;
        throw error;
      });
      if (exists) return `Já tens um ticket aberto em <#${previous.channel}>.`;
      db.run('UPDATE tickets SET closed = 1 WHERE channel = ?', previous.channel);
    }
    const access = [P.ViewChannel, P.SendMessages, P.ReadMessageHistory];
    const channel = await i.guild.channels.create({
      name: `ticket-${i.user.id}`, type: ChannelType.GuildText, parent: config.tickets,
      permissionOverwrites: [
        { id: guildId, deny: [P.ViewChannel] },
        { id: config.staff, allow: access },
        { id: client.user.id, allow: access },
        { id: i.user.id, allow: access },
      ],
      reason: 'Ticket PASTEL DE NATA',
    });
    db.run('INSERT INTO tickets (channel, user) VALUES (?, ?)', channel.id, i.user.id);
    try {
      await channel.send({ content: 'Descreve como a equipa te pode ajudar.',
        components: [row(button('pdn:close', 'Fechar ticket', ButtonStyle.Danger))] });
    } catch (error) {
      await channel.delete('Falha ao inicializar ticket').then(
        () => db.run('DELETE FROM tickets WHERE channel = ?', channel.id),
        cleanup => report('limpeza de ticket', cleanup),
      );
      throw error;
    }
    await audit(i.guild, `Ticket <#${channel.id}> aberto por <@${i.user.id}>.`);
    return `Ticket aberto em <#${channel.id}>.`;
  });
}

function closeTicket(i, config) {
  return exclusively(`close:${i.channelId}`, async () => {
    const ticket = db.get('SELECT * FROM tickets WHERE channel = ?', i.channelId);
    if (!ticket || ticket.closed) fail('Este ticket já está fechado.');
    if (ticket.user !== i.user.id && !isStaff(i, config)) fail('Só o autor e a equipa podem fechar.');
    await i.channel.permissionOverwrites.edit(ticket.user, { ViewChannel: false, SendMessages: false });
    db.run('UPDATE tickets SET closed = 1 WHERE channel = ?', i.channelId);
    await i.message.edit({ components: [] }).catch(error => report('botão do ticket', error));
    await i.channel.setName(`fechado-${ticket.user}`).catch(error => report('nome do ticket', error));
    await audit(i.guild, `Ticket <#${i.channelId}> fechado por <@${i.user.id}>.`);
    return 'Ticket fechado e arquivado para a equipa.';
  });
}

const voteLabels = { sim: 'Vou', talvez: 'Talvez', nao: 'Não vou' };
function activityText(activity) {
  const totals = { sim: 0, talvez: 0, nao: 0 };
  for (const result of db.all('SELECT vote, count(*) AS total FROM votes WHERE activity = ? GROUP BY vote', activity.id)) {
    totals[result.vote] = result.total;
  }
  const results = Object.entries(voteLabels).map(([key, label]) => `${label}: ${totals[key]}`).join(' · ');
  return `**Atividade ROB RUN** — ${activity.title}\nInício: <t:${Math.floor(activity.starts / 1000)}:F>\n${activity.closed ? 'Votação encerrada · ' : ''}${results}\nID: \`${activity.id}\``;
}

async function activity(i) {
  if (i.options.getSubcommand() === 'criar') {
    if (i.channel?.type !== ChannelType.GuildText) fail('Escolhe um canal de texto para a votação.');
    const starts = parseActivityTime(i.options.getString('inicio', true));
    if (!starts) fail('Indica uma data futura com fuso horário, como 2026-12-01T21:30+00:00.');
    const title = i.options.getString('titulo', true).trim();
    if (!title) fail('Indica um título.');
    const record = { id: i.id, title, starts, channel: i.channelId };
    db.run('INSERT INTO activities (id, title, starts, channel) VALUES (?, ?, ?, ?)',
      record.id, record.title, record.starts, record.channel);
    try {
      const message = await i.channel.send({ content: activityText(record), components: [row(
        ...Object.entries(voteLabels).map(([key, label]) => button(`pdn:vote:${record.id}:${key}`, label)),
      )] });
      db.run('UPDATE activities SET message = ? WHERE id = ?', message.id, record.id);
    } catch (error) {
      db.run('DELETE FROM activities WHERE id = ?', record.id);
      throw error;
    }
    await audit(i.guild, `Atividade ${record.id} criada por <@${i.user.id}> em <#${record.channel}>.`);
    return `Atividade publicada. Os aderentes recebem um lembrete por DM até 30 minutos antes. ID: ${record.id}`;
  }
  const id = i.options.getString('id', true).trim();
  return exclusively(`activity:${id}`, async () => {
    const record = db.get('SELECT * FROM activities WHERE id = ? AND message IS NOT NULL', id);
    if (!record) fail('Atividade não encontrada.');
    const channel = await i.guild.channels.fetch(record.channel);
    const message = await channel.messages.fetch(record.message);
    await message.edit({ content: activityText({ ...record, closed: true }), components: [] });
    db.run('UPDATE activities SET closed = 1 WHERE id = ?', id);
    return 'Votação encerrada. O resultado está na publicação original.';
  });
}

function vote(i, config, id, choice) {
  if (!isVerified(i, config)) fail('Verifica-te antes de votar.');
  return exclusively(`activity:${id}`, async () => {
    const record = db.get('SELECT * FROM activities WHERE id = ?', id);
    if (!record || record.message !== i.message.id || record.closed || Date.now() >= record.starts ||
        !Object.hasOwn(voteLabels, choice)) fail('Esta votação terminou ou está indisponível.');
    db.run('INSERT INTO votes VALUES (?, ?, ?) ON CONFLICT(activity, user) DO UPDATE SET vote = excluded.vote',
      id, i.user.id, choice);
    await i.message.edit({ content: activityText(record) }).catch(error => report('totais da votação', error));
    return `Voto registado: ${voteLabels[choice]}.`;
  });
}

async function updateReminders(guild) {
  const now = Date.now();
  const config = settings();
  if (!config) return;
  const upcoming = db.all('SELECT * FROM activities WHERE reminded = 0 AND message IS NOT NULL AND starts > ? AND starts <= ?',
    now, now + 30 * 60_000);
  for (const activity of upcoming) {
    const channel = await guild.channels.fetch(activity.channel).catch(() => null);
    if (!channel) continue;
    db.run('UPDATE activities SET reminded = 1 WHERE id = ?', activity.id);
    let sent = 0;
    let failed = 0;
    for (const { user } of db.all('SELECT user FROM subscribers')) {
      if (Date.now() >= activity.starts) break;
      if (!db.get('SELECT user FROM subscribers WHERE user = ?', user)) continue;
      const member = await guild.members.fetch(user).catch(() => null);
      if (!member?.roles.cache.has(config.verified) && !member?.roles.cache.has(config.staff) &&
          !member?.permissions.has(P.Administrator)) continue;
      if (!channel.permissionsFor(member)?.has(P.ViewChannel)) continue;
      try {
        await member.send(`**ROB RUN** · ${activity.title} começa <t:${Math.floor(activity.starts / 1000)}:R>.\nhttps://discord.com/channels/${guild.id}/${activity.channel}/${activity.message}\nUsa /avisos no servidor para desativar lembretes.`);
        sent++;
      } catch {
        failed++;
      }
    }
    await audit(guild, `Atividade ${activity.id}: ${sent} lembrete(s) entregue(s), ${failed} falha(s) de DM.`);
  }
}

async function createInvite(i, config) {
  if (!isStaff(i, config)) fail('A criação de convites é reservada à equipa.');
  if (i.channel?.type !== ChannelType.GuildText) fail('Usa /convite num canal de texto acessível aos convidados.');
  const invite = await i.channel.createInvite({ maxAge: 86400, maxUses: 1, unique: true,
    reason: `Convite pedido por ${i.user.id}` });
  db.run('INSERT INTO invites VALUES (?, ?)', invite.code, i.user.id);
  await audit(i.guild, `Convite de uma utilização criado por <@${i.user.id}> para <#${i.channelId}>.`);
  return `Convite válido por 24 horas e uma utilização: ${invite.url}`;
}

async function manageMember(i, config) {
  const member = await i.guild.members.fetch(i.options.getUser('utilizador', true).id)
    .catch(error => { if (error.code === 10007) fail('O utilizador não faz parte do servidor.'); throw error; });
  const action = i.options.getString('acao', true);
  if (action === 'consultar') {
    return `<@${member.id}> · Verificado: ${member.roles.cache.has(config.verified) ? 'sim' : 'não'} · Clã: ${member.roles.cache.has(config.clan) ? 'sim' : 'não'}`;
  }
  if (member.user.bot) fail('Escolhe uma pessoa.');
  if (action === 'adicionar') {
    if (!member.roles.cache.has(config.verified)) fail('O membro tem de se verificar primeiro.');
    await grantRole(member, config.clan, `Pedido de ${i.user.id}`);
  } else {
    await member.roles.remove(config.clan, `Pedido de ${i.user.id}`);
  }
  await audit(i.guild, `Clã: ${action} <@${member.id}> por <@${i.user.id}>.`);
  return 'Cargo do clã atualizado.';
}

async function handle(i) {
  const [prefix, action, id, choice] = i.customId?.split(':') ?? [];
  if (i.guildId !== guildId || !i.inCachedGuild()) return;
  if (!i.isChatInputCommand() && prefix !== 'pdn') return;
  if (!i.isChatInputCommand() && !i.isButton() && !i.isModalSubmit()) return;
  const config = settings();
  if (i.isButton() && action === 'apply' && config && isVerified(i, config)) {
    return i.showModal(new ModalBuilder().setCustomId('pdn:application').setTitle('Candidatura ROB RUN')
      .addComponents(new ActionRowBuilder().addComponents(new TextInputBuilder().setCustomId('answer')
        .setLabel('Nick no jogo, experiência e disponibilidade').setStyle(TextInputStyle.Paragraph)
        .setMinLength(10).setMaxLength(1500).setRequired(true))));
  }
  await i.deferReply({ flags: MessageFlags.Ephemeral });
  let result;
  if (i.commandName === 'configurar') {
    result = await configure(i);
  } else {
    if (!config) fail('Um administrador tem de executar /configurar primeiro.');
    if (i.isChatInputCommand()) {
      if (i.commandName === 'painel') {
        if (!i.memberPermissions.has(P.Administrator)) fail('Apenas administradores podem publicar o painel.');
        if (i.channel?.type !== ChannelType.GuildText) fail('Publica o painel num canal de texto.');
        await i.channel.send(panel());
        result = 'Painel publicado.';
      }
      if (i.commandName === 'avisos') {
        if (i.options.getBoolean('ativar', true)) {
          if (!isVerified(i, config)) fail('Verifica-te antes de ativar os avisos.');
          db.run('INSERT OR IGNORE INTO subscribers VALUES (?)', i.user.id);
          result = 'Lembretes por DM ativados.';
        } else {
          db.run('DELETE FROM subscribers WHERE user = ?', i.user.id);
          result = 'Lembretes por DM desativados.';
        }
      }
      if (i.commandName === 'convite') result = await createInvite(i, config);
      if (['atividade', 'membro'].includes(i.commandName)) {
        if (!isStaff(i, config)) fail('Este comando é reservado à equipa.');
        result = await (i.commandName === 'atividade' ? activity(i) : manageMember(i, config));
      }
    } else if (i.isModalSubmit() && action === 'application') {
      result = await apply(i, config);
    } else if (i.isButton()) {
      if (action === 'verify') {
        if (!i.member.roles.cache.has(config.verified)) {
          await grantRole(i.member, config.verified, 'Verificação ROB RUN');
          await audit(i.guild, `<@${i.user.id}> verificou-se.`);
        }
        result = 'Estás verificado.';
      }
      if (action === 'apply') fail('Verifica-te antes de te candidatares.');
      if (action === 'approve' || action === 'reject') result = await review(i, config, id, action === 'approve');
      if (action === 'ticket') result = await openTicket(i, config);
      if (action === 'close') result = await closeTicket(i, config);
      if (action === 'vote') result = await vote(i, config, id, choice);
    }
  }
  await i.editReply(result || 'Ação indisponível.');
}

client.on(Events.InteractionCreate, i => {
  handle(i).catch(async error => {
    if (!(error instanceof UserError)) report('interação', error);
    if (!i.isRepliable()) return;
    const content = error instanceof UserError ? error.message :
      'A operação falhou. A equipa pode consultar os registos do bot.';
    try {
      if (i.deferred) await i.editReply(content);
      else if (!i.replied) await i.reply(response(content));
    } catch (replyError) {
      report('resposta', replyError);
    }
  });
});

client.on(Events.GuildMemberAdd, member => {
  if (member.guild.id === guildId) void audit(member.guild, `<@${member.id}> entrou no servidor.`);
});
client.on(Events.GuildMemberRemove, member => {
  if (member.guild.id !== guildId) return;
  db.run('DELETE FROM subscribers WHERE user = ?', member.id);
  void audit(member.guild, `<@${member.id}> saiu do servidor.`);
});
client.on(Events.InviteDelete, invite => {
  if (invite.guild?.id !== guildId) return;
  const tracked = db.get('SELECT creator FROM invites WHERE code = ?', invite.code);
  if (!tracked) return;
  db.run('DELETE FROM invites WHERE code = ?', invite.code);
  void audit(invite.guild, `Convite criado por <@${tracked.creator}> deixou de estar disponível.`);
});
client.on(Events.Error, error => report('gateway', error));

let timer;
async function start() {
  const ready = new Promise(resolve => client.once(Events.ClientReady, resolve));
  await client.login(token);
  const timeout = setTimeout(() => {
    console.error('O Discord não confirmou a ligação. Verifica o token e a intenção Guild Members.');
    client.destroy();
    process.exit(1);
  }, 30_000).unref();
  await ready;
  clearTimeout(timeout);
  if (client.application.id !== appId) fail('DISCORD_APP_ID não corresponde ao token.');
  const guild = await client.guilds.fetch(guildId);
  await rest.put(Routes.applicationGuildCommands(appId, guildId), { body: commands.map(c => c.toJSON()) });
  console.info(`PASTEL DE NATA online · servidor ${guild.id} · ${commands.length} comandos registados.`);
  const tick = () => {
    void exclusively('reminders', () => updateReminders(guild))
      .catch(error => report('lembretes', error))
      .finally(() => { timer = setTimeout(tick, 15_000); });
  };
  tick();
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    clearTimeout(timer);
    client.destroy();
    db.close();
    process.exit(0);
  });
}

start().catch(error => {
  if (error instanceof UserError) console.error(error.message);
  else report('arranque', error);
  client.destroy();
  db.close();
  process.exitCode = 1;
});

