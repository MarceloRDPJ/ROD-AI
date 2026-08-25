# ROD do Cerrado

Assistente pessoal e guardião da rede doméstica executado 24/7 em um Raspberry Pi 3B. A interface principal é o Telegram. O atendimento usa regras, NLP local tolerante a erros, contexto curto e skills que consultam dados reais; IA generativa não participa do fluxo de mensagens.

## O que ele faz

- Mostra CPU, RAM, disco, temperatura e uptime reais do Raspberry Pi.
- Verifica internet, ping, velocidade, dispositivos e estatísticas da rede.
- Integra com AdGuard Home para consultas e ações protegidas por confirmação.
- Cria, lista, edita, remove e entrega lembretes persistentes.
- Registra hidratação e apresenta histórico e análise.
- Executa Wake-on-LAN e consulta o estado do computador configurado.
- Monitora rede, energia e eventos, enviando alertas pelo Telegram.
- Oferece menus e botões para rede, agenda, automações e sistema.
- Usa o Poco X3 NFC como nó Android pela rede Wi-Fi para diagnóstico, cofre local
  e consultas assistidas de Saneago/Equatorial.
- Expõe dashboard e API somente na rede local, na porta `8000`.

## Princípios

- Dado real no lugar de texto inventado.
- Resposta imediata no lugar de timeout de LLM.
- Tolerância maior para consultas; ações perigosas continuam estritas.
- Confirmação humana antes de reinício e bloqueios.
- Persistência local em SQLite e volumes Docker.

## Produção real

- Projeto: `/opt/bot/jarvis-do-cerrado`
- Branch: `main`
- Contêiner: `rod_cerrado`
- Compose: `/opt/bot/jarvis-do-cerrado/docker-compose.yml`
- API local: `http://IP_DO_PI:8000/`
- Healthcheck: `http://127.0.0.1:8000/api/system/health`

## Desenvolvimento

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
pytest --no-cov
```

O CI também mede cobertura. A suíte funcional pode passar mesmo quando o limite global de cobertura ainda não for atingido.

## Documentação

- `docs/architecture/technical_architecture.md`: arquitetura física, lógica, rede e nuvem.
- `docs/structure.md`: árvore e responsabilidades do código.
- `docs/user_guide.md`: funcionalidades, frases, menus e botões.
- `docs/deployment.md`: atualização, verificação e recuperação no Pi.
- `docs/specifications/reminders_system.md`: comportamento dos lembretes.
- `AGENTS.md`: regras operacionais para futuros agentes de código.

## Aplicativo ROD no Poco

O APK está em `android/poco-agent`. A tela usa a identidade visual oficial RDP,
mostra o estado real do Pi e do telefone e possui um cofre de contas protegido
por Android Keystore. CPF, data de nascimento, login, senha e números das contas
ficam somente no aparelho; não entram no repositório, no Telegram nem nos logs.
O provisionamento inicial pode ser feito localmente por ADB em builds de depuração;
o payload é removido do `Intent` após ser incorporado ao cofre. O fluxo Saneago
suporta login SSO e seleção de unidades vinculadas. Para a Equatorial, o ROD tenta
primeiro a sessão web oficial e usa a Clara oficial no WhatsApp como segundo canal.
O WhatsApp funciona no Poco sem SIM, vinculado como aparelho adicional; nenhuma API
não oficial ou serviço de terceiros participa da conversa.

O identificador técnico Android `br.com.jarviscerrado.poco`, o nome interno do
serviço de acessibilidade e o pacote Python `jarvis` foram preservados de forma
intencional para atualizar o app sem perder o Keystore, manter a permissão de
acessibilidade e preservar banco/imports existentes. Toda identidade apresentada
ao usuário é ROD.

## Configuração mínima

Copie `.env.example` para `.env` e configure pelo menos:

```env
TELEGRAM_TOKEN=token_do_bot
ALLOWED_USER_ID=id_numerico_autorizado
LOCAL_LLM_ENABLED=false
LOCAL_LLM_BACKEND=disabled
TIMEZONE=America/Sao_Paulo
```

Nunca envie `.env`, token do Telegram, banco de produção ou credenciais ao Git.

## Licença

MIT.
