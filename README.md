# IA-Admin para TrueNAS

O IA-Admin é um assistente que você conversa pelo Telegram para consultar e
realizar algumas tarefas em um servidor TrueNAS. Ele usa o Ollama para executar
um modelo de inteligência artificial na sua própria máquina e acessa o TrueNAS
pela API.

Este guia explica como preparar os serviços, configurar o projeto e conversar
com o bot pela primeira vez.

## O que você vai precisar

Em qualquer forma de instalação:

- Um bot do Telegram e o token fornecido pelo BotFather.
- Um servidor TrueNAS acessível pela rede, o nome do usuário associado e uma
  chave de API.
- Ollama e um modelo compatível com chamadas de ferramentas.

Para instalar no computador, você também precisa de Python 3.12 ou mais recente.
Para instalar como Custom App no TrueNAS SCALE, use a seção [Executar como
aplicativo no TrueNAS SCALE](#executar-como-aplicativo-no-truenas-scale-2410) e
prepare a imagem Docker em um computador com Docker. Não é necessário instalar
Python no sistema base do TrueNAS.

O modelo de IA roda localmente pelo Ollama. O bot do Telegram, porém, recebe e
envia suas mensagens pela plataforma Telegram. CPU, memória e espaço em disco
necessários para o modelo dependem do computador e do modelo escolhido.

## O que o bot pode fazer

Você pode perguntar, em linguagem natural, sobre:

- Informações gerais do TrueNAS, CPU e memória.
- Pools, datasets, discos e espaço de armazenamento.
- Serviços, alertas, temperatura de discos e tarefas de scrub.
- Interfaces de rede, atualizações e aplicativos instalados.

O bot também pode reiniciar um serviço ou criar um dataset. Antes dessas ações,
ele mostra o que pretende fazer e pede que você confirme ou cancele pelo botão
no Telegram. Não confirme uma operação se não tiver certeza do que ela fará.

## 1. Baixe o projeto

Baixe o ZIP deste repositório pelo GitHub usando **Code > Download ZIP** e
extraia o conteúdo. Abra um terminal dentro da pasta extraída. Ela deve conter
arquivos como `telegram_ia_agent.py`, `requirements.txt` e `.env.example`.

## 2. Instale o Ollama e o modelo

1. Baixe e instale o Ollama para o seu sistema operacional em
   [ollama.com/download](https://ollama.com/download).
2. Abra o terminal e baixe o modelo usado por padrão pelo projeto:

   ```sh
   ollama pull qwen3:1.7b
   ```

3. Verifique se o modelo aparece na lista:

   ```sh
   ollama list
   ```

O download pode levar algum tempo. Deixe o Ollama instalado e em execução
enquanto usar o bot. Se escolher outro modelo, altere `OLLAMA_MODEL` no arquivo
`.env` e use um modelo Ollama compatível com chamadas de ferramentas.

## 3. Crie o bot do Telegram

1. No Telegram, abra uma conversa com [@BotFather](https://t.me/BotFather).
2. Envie `/newbot` e siga as instruções para escolher nome e usuário.
3. Copie o token gerado. Ele funciona como uma senha: não o publique nem envie
   para outras pessoas.
4. Obtenha seu ID numérico de chat do Telegram. Uma opção é conversar com um
   bot de consulta de ID, como `@userinfobot`. Em uma conversa privada com seu
   próprio bot, use o ID numérico da sua conta.

O projeto só aceita mensagens dos IDs configurados. Se outras pessoas também
precisarem usar o bot, adicione os IDs delas à configuração.

## 4. Prepare o acesso ao TrueNAS

No TrueNAS, crie uma chave de API para o usuário que o bot usará e guarde-a.
Você também precisará do nome desse usuário e da URL usada para acessar a
interface do TrueNAS, por exemplo `https://192.168.1.10`.

O cliente exige HTTPS e usa WebSocket seguro (`wss://`) para que a chave de API
e os dados sejam criptografados durante o tráfego. Endereços `http://` são
rejeitados. Se o TrueNAS usar um certificado de uma autoridade certificadora
privada, tenha o arquivo PEM dessa CA disponível para configurá-lo. O cliente
se conecta à API JSON-RPC 2.0 por WebSocket, evitando a API REST legada removida
no TrueNAS 26.04.

### Configurar um certificado de CA privada

Se o certificado HTTPS do TrueNAS foi emitido por uma CA privada, o bot precisa
confiar no certificado da CA para validar a conexão TLS:

1. Na interface do TrueNAS, abra **Credentials > Certificates** (o nome pode
   variar entre versões) e identifique a CA que emitiu o certificado usado pela
   interface web. Exporte o certificado público da CA raiz e, se necessário,
   da CA intermediária em formato PEM. Se o certificado do TrueNAS for
   autoassinado, use o próprio certificado público exportado. **Não exporte
   nem compartilhe a chave privada** do certificado.
2. Confira se o nome usado em `TRUENAS_URL` está incluído nos nomes alternativos
   (SAN) do certificado do TrueNAS. Por exemplo, para
   `TRUENAS_URL=https://truenas.exemplo.local`, o certificado precisa ser válido
   para `truenas.exemplo.local`. Se usar um endereço IP, esse IP também precisa
   constar no certificado.
3. Salve o arquivo PEM em um local persistente. Para execução local, defina
   `TRUENAS_CA_BUNDLE` como o caminho desse arquivo no computador. Em Docker,
   monte o arquivo no container e use como valor o caminho **dentro** do
   container.
4. Reinicie o bot ou recrie o Custom App para aplicar a configuração.

Com Docker Compose, adicione ao serviço `bot` um bind mount somente leitura e
aponte a variável para o destino dentro do container. O caminho antes dos dois
pontos precisa existir no host; não use `/certs/...` como caminho do host:

```yaml
    environment:
      TRUENAS_CA_BUNDLE: "/certs/truenas-ca.pem"
      # mantenha aqui as demais variáveis de ambiente do bot
    volumes:
      - /mnt/POOL/apps/ia-admin/certs/truenas-ca.pem:/certs/truenas-ca.pem:ro
```

Substitua `POOL` pelo nome do pool. Crie a pasta
`/mnt/POOL/apps/ia-admin/certs` no TrueNAS e copie o arquivo PEM para ela
(por exemplo, usando SMB ou SCP) antes de iniciar o aplicativo. O caminho
`/certs/truenas-ca.pem` à direita dos dois pontos é interno ao container; o
caminho à esquerda é um arquivo existente no host. Para uma instalação com
certificado emitido por uma CA pública já confiável no sistema, deixe
`TRUENAS_CA_BUNDLE` vazio e não adicione esse volume. Não desative a validação
TLS para contornar erros de certificado: confira a cadeia da CA, o caminho
montado e se o hostname da URL corresponde ao certificado.

Se estiver atualizando uma instalação existente, adicione `TRUENAS_USERNAME`
ao `.env` ou à configuração do Custom App. Informe o usuário associado à chave
de API. Depois, atualize o código e reconstrua/republique a imagem Docker antes
de recriar o container ou atualizar o aplicativo no TrueNAS.

## 5. Configure o arquivo `.env`

Na pasta do projeto, faça uma cópia de `.env.example` chamada `.env`.

Linux ou macOS:

```sh
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Abra `.env` em um editor de texto e substitua os valores de exemplo pelos seus:

```dotenv
TRUENAS_URL=https://endereco-do-truenas
TRUENAS_USERNAME=usuario_da_chave
TRUENAS_API_KEY=sua_chave_de_api_do_truenas
TRUENAS_CA_BUNDLE=
TELEGRAM_BOT_TOKEN=token_fornecido_pelo_botfather
TELEGRAM_ALLOWED_CHAT_ID=seu_id_numerico_do_telegram
OLLAMA_MODEL=qwen3:1.7b
OLLAMA_HOST=http://127.0.0.1:11434
TEMPERATURE_MONITOR_INTERVAL_MINUTES=5
DISK_CRITICAL_TEMP_C=50
SYSTEM_CRITICAL_TEMP_C=85
```

O significado de cada opção:

| Opção | O que preencher |
|---|---|
| `TRUENAS_URL` | URL do seu TrueNAS, sem `/api` no final. |
| `TRUENAS_USERNAME` | Nome do usuário do TrueNAS associado à chave de API. |
| `TRUENAS_API_KEY` | Chave de API criada no TrueNAS. |
| `TRUENAS_CA_BUNDLE` | Opcional. Caminho do certificado PEM da CA privada; deixe vazio se não precisar. |
| `TELEGRAM_BOT_TOKEN` | Token fornecido pelo BotFather. |
| `TELEGRAM_ALLOWED_CHAT_ID` | Seu ID numérico do Telegram. IDs adicionais podem ser separados por vírgula. |
| `OLLAMA_MODEL` | Nome do modelo baixado no Ollama. O padrão é `qwen3:1.7b`. |
| `OLLAMA_HOST` | Endereço do Ollama. Para execução local, mantenha o valor padrão. |
| `TEMPERATURE_MONITOR_INTERVAL_MINUTES` | Intervalo do monitor automático; mínimo de 5 minutos por limite da API de temperatura dos discos. |
| `DISK_CRITICAL_TEMP_C` | Temperatura crítica padrão dos discos em °C. Limites críticos informados pelo próprio disco, quando disponíveis, também são respeitados. |
| `SYSTEM_CRITICAL_TEMP_C` | Temperatura crítica da CPU em °C, se o TrueNAS disponibilizar a métrica `cputemp`. |

Não coloque aspas em volta dos valores. O bot não inicia sem URL, usuário e
chave do TrueNAS, token do Telegram e pelo menos um ID de chat permitido.
Mantenha o arquivo `.env` privado: ele contém credenciais.

O monitor automático inicia junto com o bot, verifica discos e sensores de CPU
disponíveis pela API do TrueNAS e envia alertas para todos os chats autorizados
ao entrar em estado crítico e ao normalizar. Os padrões são 5 minutos, 50 °C
para discos e 85 °C para CPU. Para temperatura de discos, a API do TrueNAS
atualiza as leituras no máximo a cada 5 minutos. Sensores de CPU dependem do
hardware e da disponibilidade da métrica `cputemp`; ausência de leitura não é
tratada como temperatura normal.

## 6. Instale as dependências do projeto

Abra um terminal na pasta do projeto e crie um ambiente Python separado para
ele.

### Linux ou macOS

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação do ambiente, consulte a mensagem exibida e
as instruções oficiais do Python/Windows para a política de execução. Não é
necessário instalar as dependências globalmente.

## 7. Inicie e teste o bot

Com o ambiente virtual ativado e o Ollama em execução, inicie o projeto:

```sh
python telegram_ia_agent.py
```

Mantenha esse terminal aberto. No Telegram, abra seu bot e envie `/start`.
Depois, experimente perguntar, por exemplo:

- “Quais são os pools e quanto espaço livre tenho?”
- “Como está o uso de CPU e memória?”
- “Há algum alerta no TrueNAS?”

Comandos disponíveis:

- `/start` inicia uma conversa nova.
- `/help` mostra a ajuda do bot.
- `/status` mostra o estado da conversa e algumas configurações.
- `/reset` limpa o histórico e qualquer confirmação pendente.

## Executar com Docker em outro computador (opcional)

Use estes passos para construir a imagem e executar o bot manualmente com
Docker. O Ollama precisa estar instalado no computador que executa os
containers, em execução e com o modelo baixado:

```sh
ollama pull qwen3:1.7b
```

Na pasta do projeto, crie e preencha o arquivo `.env` conforme a seção
[Configuração do arquivo `.env`](#5-configure-o-arquivo-env). Defina nele o
endereço do Ollama que o container poderá acessar:

```dotenv
OLLAMA_HOST=http://host.docker.internal:11434
```

Ainda na pasta do projeto, construa a imagem:

```sh
docker build -t ia-admin-telegram .
```

Depois, inicie o bot. No Linux, adicione o mapeamento de `host.docker.internal`
para o computador host:

```sh
docker run --rm --env-file .env \
  --add-host=host.docker.internal:host-gateway \
  ia-admin-telegram
```

No macOS e no Windows com Docker Desktop, use o mesmo comando sem
`--add-host`:

```sh
docker run --rm --env-file .env ia-admin-telegram
```

O bot continuará rodando no terminal; pressione `Ctrl+C` para pará-lo. A opção
`--rm` remove o container ao parar, mas mantém a imagem construída. O endereço
`127.0.0.1` dentro de um container aponta para o próprio container, não para o
computador que executa Ollama. O Ollama também precisa aceitar conexões do
container; restrinja essas conexões à rede confiável necessária.

## Executar como aplicativo no TrueNAS SCALE 24.10+

No TrueNAS SCALE 24.10 ou mais recente, o caminho recomendado é instalar o bot
como um **Custom App** usando Docker Compose. Não instale Python nem pacotes
diretamente no sistema base do TrueNAS: uma atualização do sistema pode remover
essas alterações. O Ollama e o bot podem rodar como containers separados dentro
do mesmo aplicativo.

### 1. Publique a imagem do bot em um registry

A tela de Custom App precisa baixar uma imagem Docker. Em um computador com
Docker, dentro da pasta do projeto, crie a imagem e publique-a em um registry
acessível pelo TrueNAS. Exemplo usando GitHub Container Registry (substitua
`USUARIO` e `REPOSITORIO` pelos seus):

```sh
docker buildx build --platform linux/amd64 \
  -t ghcr.io/USUARIO/REPOSITORIO/ia-admin-telegram:latest \
  --push .
```

Faça login no registry pelo Docker antes do comando, se necessário. Não coloque
tokens do registry em arquivos do projeto. Se a imagem for privada, configure
as credenciais de pull do registry no TrueNAS; uma imagem pública não exige
esse passo. Sempre que mudar o código, publique uma nova imagem e atualize o
aplicativo para baixá-la.

### 2. Crie o Custom App

Na interface web do TrueNAS, abra **Apps** e escolha a opção para criar um
**Custom App** ou instalar usando YAML/Compose. Os nomes dos botões podem variar
um pouco entre versões. Cole o Compose abaixo, substituindo os valores
indicados:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    volumes:
      - ollama_data:/root/.ollama

  bot:
    image: ghcr.io/USUARIO/REPOSITORIO/ia-admin-telegram:latest
    restart: unless-stopped
    depends_on:
      - ollama
    environment:
      TRUENAS_URL: "https://IP_OU_NOME_DO_TRUENAS"
      TRUENAS_USERNAME: "USUARIO_DA_CHAVE"
      TRUENAS_API_KEY: "SUA_CHAVE_DE_API"
      TRUENAS_CA_BUNDLE: "/certs/truenas-ca.pem"
      TELEGRAM_BOT_TOKEN: "TOKEN_DO_BOT"
      TELEGRAM_ALLOWED_CHAT_ID: "SEU_ID_NUMERICO"
      OLLAMA_MODEL: "qwen3:1.7b"
      OLLAMA_HOST: "http://ollama:11434"
      TEMPERATURE_MONITOR_INTERVAL_MINUTES: "5"
      DISK_CRITICAL_TEMP_C: "50"
      SYSTEM_CRITICAL_TEMP_C: "85"
    volumes:
      - /mnt/POOL/apps/ia-admin/certs/truenas-ca.pem:/certs/truenas-ca.pem:ro

volumes:
  ollama_data:
```

Use em `TRUENAS_URL` o endereço HTTPS da interface de rede do próprio TrueNAS,
por exemplo `https://192.168.1.50`. Endereços HTTP não são aceitos. Não use
`localhost` ou `127.0.0.1`: dentro do container esses endereços apontam para o
próprio container, não para o TrueNAS. O serviço Ollama é acessado pelo nome
`ollama` porque os dois containers estão na mesma rede do Compose.

Preencha os valores de token e chave somente na configuração privada do
aplicativo. Não publique o YAML preenchido nem o inclua no GitHub. Restrinja o
bot a IDs de chat conhecidos e crie uma chave TrueNAS com apenas as permissões
necessárias. Não é preciso publicar a porta `11434` do Ollama para a Internet.
O bot remove o token do Telegram de mensagens de log e desativa os logs de
requisição do cliente HTTP que poderiam incluir URLs autenticadas.

Se o certificado HTTPS do TrueNAS for assinado por uma CA privada, siga
[Configurar um certificado de CA privada](#configurar-um-certificado-de-ca-privada)
e substitua `POOL` pelo pool usado no seu sistema.

### 3. Baixe o modelo no Ollama

Depois de instalar o aplicativo, abra o **Shell** do container `ollama` pela
interface do TrueNAS e execute:

```sh
ollama pull qwen3:1.7b
```

O modelo fica guardado no volume persistente `ollama_data`; portanto, não deve
ser baixado novamente quando o aplicativo reiniciar. Aguarde o download
terminar antes de testar o bot.

### 4. Inicie uma conversa

Confirme na página de Apps que os containers estão em execução. No Telegram,
abra o bot e envie `/start`. Faça uma pergunta de consulta, como “Quais são os
pools e quanto espaço livre tenho?”. O terminal/logs do container `bot` mostram
erros de configuração ou conexão.

Esse modo usa CPU/RAM do TrueNAS para executar o modelo. Verifique se o
equipamento tem recursos livres suficientes; caso contrário, execute Ollama em
outro computador e altere `OLLAMA_HOST` para o endereço desse serviço.

### Alternativa: recriar manualmente o container no TrueNAS

Use estes passos apenas se o bot foi instalado manualmente com `docker run`, e
não como Custom App. A conexão com a API do TrueNAS deve usar HTTPS: no `.env`,
confirme que `TRUENAS_URL` começa com `https://` (nunca `http://`) e que
`TRUENAS_CA_BUNDLE` aponta para o certificado montado no container. O TrueNAS
rejeita a autenticação da chave de API quando a conexão é feita por HTTP. Isso
não é corrigido recriando o container: ajuste `TRUENAS_URL` para HTTPS antes de
iniciá-lo. Se o certificado não for confiável, configure também a CA conforme
as instruções acima; não troque a URL para HTTP nem desative a validação TLS.

No Shell do TrueNAS, dentro da pasta do repositório, construa a imagem antes de
parar o container em execução:

```sh
cd ~/truenas-ai-assistant
sudo docker build -t ia-admin-telegram:latest .
```

Depois, recrie o container:

```sh
sudo docker rm -f ia-agent

sudo docker run -d \
  --name ia-agent \
  --network host \
  --restart unless-stopped \
  --env-file "$PWD/.env" \
  -e OLLAMA_HOST=http://127.0.0.1:11434 \
  --add-host=truenas.home.arpa:192.168.100.34 \
  -v "$PWD/certs/truenas-local.crt:/certs/truenas-local.crt:ro" \
  ia-admin-telegram:latest
```

Este exemplo pressupõe que `certs/truenas-local.crt` existe no repositório e
que o `.env` contém `TRUENAS_CA_BUNDLE=/certs/truenas-local.crt` e
`TRUENAS_URL=https://truenas.home.arpa`. O endereço após `--add-host` deve ser
o IP LAN atual do TrueNAS; ajuste o hostname e o IP se sua configuração for
diferente. A montagem do certificado é somente leitura. `docker rm -f`
interrompe e remove o container existente, mas preserva a imagem e os arquivos
de configuração.

Verifique se o novo container iniciou e consulte o log:

```sh
sudo docker ps --filter name=ia-agent
sudo docker logs --tail 50 ia-agent
```

## Problemas comuns

- **O bot encerra logo após iniciar:** confira se todas as variáveis obrigatórias
  estão preenchidas no `.env`.
- **“Acesso não autorizado”:** confira se `TELEGRAM_ALLOWED_CHAT_ID` contém o
  ID numérico correto e reinicie o bot.
- **Ollama indisponível ou modelo não encontrado:** verifique se Ollama está em
  execução, se `ollama list` mostra o modelo e se `OLLAMA_HOST` está correto.
- **Falha ao acessar o TrueNAS:** confira a URL, a chave de API, a conexão de
  rede e o certificado HTTPS. Confira também se `TRUENAS_USERNAME` é o usuário
  associado à chave. Para uma CA privada, configure `TRUENAS_CA_BUNDLE` com o
  caminho do arquivo PEM.
- **Uma pergunta não obtém dados do TrueNAS:** mantenha o modelo compatível com
  chamadas de ferramentas e examine as mensagens de erro no terminal onde o
  bot está em execução.
