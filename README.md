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

## Versão Python com systemd

O bot modular usa Python 3.12, discord.py e a mesma base SQLite da versão Docker JavaScript. O projeto Node.js continua disponível para instalações que já o utilizam. Executa uma única instância da aplicação Discord.

### Portal Discord

1. Na [aplicação PASTEL DE NATA](https://discord.com/developers/applications/1572729756829356164/bot), ativa **Server Members Intent**. A versão Python usa acontecimentos de mensagens sem ler o seu conteúdo e não requer **Message Content Intent**.
2. Usa os escopos `bot` e `applications.commands` na instalação para o servidor. Concede ver canais, enviar mensagens, ler histórico, inserir ligações, anexar ficheiros, criar convites, gerir cargos e gerir canais. Mantém o cargo do bot acima das patentes do clã. Gerir Servidor é opcional para associar AFK.
3. Concede as permissões nativas de moderação apenas para as funções ativadas: **Moderar Membros**, **Expulsar Membros**, **Banir Membros** e **Gerir Mensagens**. A proteção automática começa desativada. O servidor existente já tem a aplicação instalada.
4. Guarda o token novo em `/etc/pastel-de-nata/bot.env`. O ficheiro de exemplo serve apenas de modelo. Nunca coloques o token no GitHub ou em comandos que fiquem no histórico.

### Instalar em Ubuntu 24.04

Num servidor novo, obtém o instalador a partir deste mesmo repositório.

```sh
git clone https://github.com/costinhabtw/pastel-de-nata.git pastel-installer
cd pastel-installer
sudo bash scripts/install.sh
sudoedit /etc/pastel-de-nata/bot.env
sudo bash scripts/install.sh
```

Preenche `DISCORD_TOKEN` e `DISCORD_GUILD_ID=1572718581505065060`. O serviço guarda os dados em `/var/lib/pastel-de-nata`, independentemente do caminho de configuração utilizado no Docker.

### Migrar a VPS que já usa Docker

A partir da pasta do Compose, sincroniza primeiro os ficheiros do repositório. Para o contentor antes de copiar os dados. Mantém o volume Docker para recuperação. Estes passos assumem que ainda não existe uma base de dados de destino.

```sh
git pull --ff-only
docker compose stop bot
sudo bash scripts/install.sh
test ! -e /var/lib/pastel-de-nata/pastel.sqlite
docker compose cp bot:/app/data/. /var/lib/pastel-de-nata/
sudo chown -R pastel-de-nata:pastel-de-nata /var/lib/pastel-de-nata
sudoedit /etc/pastel-de-nata/bot.env
sudo bash scripts/install.sh
sudo journalctl -u pastel-de-nata -n 50 --no-pager
```

Se o teste da base de dados falhar, para antes da cópia e faz uma cópia de segurança do destino. Se a versão Python falhar e precisares de voltar ao Docker, para e desativa primeiro o serviço e depois inicia o contentor existente. Os dados no volume Docker ficam no estado da migração inicial.

### Operar, atualizar e recuperar

```sh
sudo systemctl start pastel-de-nata
sudo systemctl stop pastel-de-nata
sudo systemctl restart pastel-de-nata
sudo systemctl status pastel-de-nata --no-pager
sudo journalctl -u pastel-de-nata -f
sudo bash /opt/pastel-de-nata/scripts/update.sh
sudo bash /opt/pastel-de-nata/scripts/backup.sh
```

Os backups usam a API de cópia consistente da SQLite em `/var/backups/pastel-de-nata`. Para recuperar, para o serviço, faz backup da base atual, move a base antiga e eventuais ficheiros `-wal` e `-shm` para outro diretório, instala o backup selecionado como `/var/lib/pastel-de-nata/pastel.sqlite` com proprietário `pastel-de-nata:pastel-de-nata` e modo `0600`, e inicia o serviço. Confirma o arranque nos logs.

### Configurar no Discord

- `/setup` cria a estrutura com emojis e os painéis dedicados. Os canais e cargos manuais anteriores ficam disponíveis e os IDs existentes são conservados. Atribui a patente **Fundador** à liderança e usa as patentes novas para os canais privados. Administradores Discord também podem operar os módulos. Um canal AFK já definido é preservado.
- `/config` altera definições de cargos, canais, candidaturas, lembretes, tickets, logs, moderação e textos. As definições de textos atualizam o painel associado quando a mensagem ainda existe.
- O formulário de recrutamento tem cinco perguntas. A avaliação privada permite aceitar, recusar com motivo ou marcar entrevista. `/convidar` envia um convite de clã por DM a um membro do Discord, ou gera um link de servidor para partilhar com alguém que ainda não entrou.
- `/membros`, `/registar`, `/perfil`, `/promover`, `/despromover`, `/expulsar` e `/historico` gerem o registo de membros. A expulsão de clã retira a patente e preserva o acesso de visitante.
- `/atividade criar` recebe início e limite de votação em ISO 8601, com `+00:00` no inverno ou `+01:00` no verão em Lisboa. Seleciona até três cargos. Só participam membros convocados que consigam ver o canal. O trabalhador fecha votações atrasadas depois de um reinício e publica um resumo privado. As notificações são tentadas uma vez por destinatário e fase. Uma interrupção após registar a tentativa pode impedir a entrega, para evitar duplicados.
- `/recurso configurar` cria ou renomeia materiais. Os depósitos e levantamentos guardam um movimento auditável na mesma transação do saldo.
- Os tickets têm quatro tipos, responsáveis configuráveis, atribuição e encerramento com motivo. O histórico fica no canal privado arquivado.
- `/warn`, `/warnings`, `/timeout`, `/kick`, `/ban` e `/unban` respeitam permissões nativas. O bot regista metadados de mensagens editadas e eliminadas, sem arquivar o conteúdo. `/anuncio`, `/sugestao` e `/status` completam as operações da comunidade.

### Testes da versão Python

`python -m unittest discover -s tests -v` verifica o esquema dos comandos, as migrações e fluxos simulados. A configuração, o login, os botões e as DMs reais devem ser verificados no Discord depois de migrar a VPS. Para validar permissões, usa uma conta comum para verificação e tickets e uma conta com patente de equipa para operações de recrutamento. Confirma que os registos e resumos de faltas aparecem exclusivamente nos canais privados previstos.
