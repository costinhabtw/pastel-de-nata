# PASTEL DE NATA · ROB RUN

Bot Discord em português para um servidor. Usa Node.js 24 e SQLite local.

| Função | Utilização |
| --- | --- |
| Verificação | O botão **Verificar** atribui o cargo escolhido. Configura o acesso aos canais através desse cargo. |
| Candidaturas | O formulário é enviado à equipa no canal privado de registos. A aprovação atribui o cargo do clã. O bot tenta comunicar a decisão por DM. |
| Convites | A equipa usa `/convite` num canal de entrada. Cada convite expira em 24 horas e aceita uma utilização. |
| Atividades | A equipa usa `/atividade criar`. Os verificados podem escolher Vou, Talvez ou Não vou e alterar o voto. `/atividade fechar` encerra a votação e apresenta os totais. |
| Lembretes | Membros verificados aderem com `/avisos ativar:Sim`. O bot tenta enviar uma DM cerca de 30 minutos antes da atividade, enquanto estiver online. |
| Tickets | O painel abre um canal privado para o autor e a equipa. O botão **Fechar ticket** retira o acesso do autor e guarda o canal para a equipa. |
| Registos e membros | Regista entradas, saídas e ações do bot. A equipa pode consultar, atribuir e retirar a participação no clã em `/membro`. |

## Discord

1. Abre a [app existente no Developer Portal](https://discord.com/developers/applications/1572729756829356164/bot) e ativa **Server Members Intent**. Usa **Guild Install** com os escopos `bot` e `applications.commands`.
2. Autoriza o bot no ROB RUN com **View Channels**, **Send Messages**, **Read Message History**, **Manage Roles**, **Manage Channels** e **Create Instant Invite**. Concede acesso ao canal de entrada, à categoria de tickets e ao canal de registos. Para receber eventos de remoção de convites no canal de entrada, concede também **Manage Channels** nesse canal.
3. Coloca o cargo do bot acima dos cargos **Verificado** e **Membro ROB RUN**. Cria ou escolhe um cargo de equipa, uma categoria de tickets e um canal de texto privado. No canal privado, nega **View Channel** a `@everyone` e permite acesso à equipa e ao bot.
4. Ativa o **Modo de Programador** nas opções avançadas do Discord, copia o ID do servidor e coloca-o em `DISCORD_GUILD_ID`. Guarda o token apenas em `.env`. As candidaturas, votos e configurações locais ficam na pasta `data/`, excluída do Git.

## Windows

Requer Node.js 24.17 ou superior.

```powershell
npm ci
Copy-Item .env.example .env
notepad .env
.\iniciar.cmd
```

Se `.env` já existir, abre esse ficheiro diretamente. Também podes arrancar com `node --env-file-if-exists=.env bot.mjs`.

Assim que aparecer **PASTEL DE NATA online**, executa `/configurar` e depois `/painel` no canal de entrada. Testa os botões com uma conta comum e a aprovação de candidaturas com um membro da equipa.

## VPS Ubuntu 24.04

Instala [Docker Engine e o plugin Compose](https://docs.docker.com/engine/install/ubuntu/) na VPS.

```sh
git clone https://github.com/costinhabtw/pastel-de-nata.git
cd pastel-de-nata
install -m 600 .env.example .env
nano .env
sudo docker compose up -d --build
sudo docker compose logs -f bot
```

Salta o comando `install` se já existir um `.env`. O Compose reinicia o bot após falhas e reinícios da VPS. A ligação ao Discord é de saída e dispensa portas de entrada.

Para atualizar, executa `git pull --ff-only` seguido de `sudo docker compose up -d --build`. Antes de migrar do PC para a VPS, para o bot, transfere o conteúdo da pasta `data/` para o volume `pastel-data` e garante que pertence ao utilizador de UID 1000 no volume. Mantém uma única instância ativa. Para cópias consistentes, para o serviço e guarda o volume completo. `docker compose down -v` apaga o volume.

## Testes locais

`npm test` verifica a sintaxe, datas com fuso horário, persistência e restrições de duplicação na base de dados. Os fluxos do servidor podem ser confirmados depois de ligar a app Discord.
