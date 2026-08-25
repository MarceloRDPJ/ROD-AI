# Estrutura do projeto

```text
jarvis/
|-- AGENTS.md                     # manual para futuros agentes
|-- Dockerfile                    # imagem Python do bot/API
|-- docker-compose.yml            # único serviço: homebot/rod_cerrado
|-- .dockerignore                 # evita segredos e dados no build
|-- .env.example                  # variáveis sem credenciais reais
|-- README.md
|-- docs/
|   |-- architecture/
|   |   |-- technical_architecture.md
|   |   `-- poco_node.md
|   |-- deployment.md
|   |-- structure.md
|   |-- user_guide.md
|   `-- specifications/
|       `-- reminders_system.md
|-- scripts/
|   |-- backup_db.py
|   |-- ble_scan.py
|   |-- deploy_rod.sh
|   |-- poco_usb_inventory.ps1    # inventário do Poco por USB, somente leitura
|   `-- poco_usb_disable_apps.ps1 # desativação reversível, com confirmação
|-- android/
|   `-- poco-agent/               # satélite Android: UI, heartbeat e acessibilidade
|-- src/jarvis/
|   |-- main.py                   # Telegram, callbacks, serviços e FastAPI
|   |-- config.py                 # ambiente e configuração central
|   |-- config.yaml
|   |-- api/
|   |   |-- app.py                # dashboard e endpoints REST
|   |   |-- integration_engine.py
|   |   |-- mcp_handler.py
|   |   |-- webhook_manager.py
|   |   `-- static/index.html
|   |-- core/
|   |   |-- router.py             # pipeline de decisão
|   |   |-- rules.py              # comandos exatos e sensíveis
|   |   |-- brain.py              # conhecimento/consultas/fallback local
|   |   |-- executor.py           # executa intents e confirma ações
|   |   |-- context.py            # contexto curto e ações pendentes
|   |   |-- personality.py        # frases programadas
|   |   |-- telegram_safe.py      # envio robusto
|   |   `-- events.py             # eventos observáveis
|   |-- nlp/
|   |   |-- normalizer.py         # caixa, acentos e limpeza
|   |   |-- intent_engine.py      # RapidFuzz e extração de parâmetros
|   |   |-- local_brain.py        # conhecimento local programado
|   |   `-- time_parser.py        # datas e horários em português
|   |-- modules/
|   |   |-- system.py             # CPU, RAM, disco, temperatura, reboot
|   |   |-- network.py            # ping, scan, speedtest, WOL
|   |   |-- adguard.py            # DNS e bloqueios
|   |   |-- reminders.py          # agenda persistente
|   |   |-- hydration.py
|   |   `-- hydration_analytics.py
|   |-- services/
|   |   |-- guardian.py           # monitoramento e alertas
|   |   |-- scheduler.py          # entrega de lembretes
|   |   |-- collector.py          # snapshots
|   |   |-- energy.py             # estimativa/eventos de energia
|   |   |-- automations.py
|   |   `-- reporter.py
|   |-- tools/
|   |   |-- current_info.py       # dados atuais de fontes explícitas
|   |   |-- rss_reader.py
|   |   `-- web_fetch.py
|   |-- database/
|   |   `-- persistence.py        # SQLite e migrações
|   `-- storage/                   # estado persistido pelo volume
`-- tests/                         # suíte automatizada
```

## Contratos importantes

- `main.py` recebe Telegram e inicializa processos; lógica nova deve ir para as camadas próprias.
- `router.py` decide, mas não executa efeitos.
- `rules.py` protege comandos exatos e perigosos.
- `intent_engine.py` tolera erros em consultas seguras.
- `executor.py` é a fronteira para efeitos e confirmação.
- módulos não devem depender de Telegram para sua lógica central.
- persistência crítica deve usar `Persistence`, não arquivos soltos.
- respostas desconhecidas não podem chamar LLM nem fingir capacidade.
- `equatorial_providers.py` escolhe canais; web, Clara e cache têm estados de saúde
  separados e nenhum canal pode transformar cache em leitura atual.
- `ClaraConversation.java` decide o diálogo oficial sem IA generativa;
  `ClaraWhatsAppReader.java` lê o cofre em memória e a acessibilidade executa apenas
  consulta, nunca pagamento.

## Arquivos legados

`src/jarvis/core/llm_fallback.py` e seus testes permanecem como código histórico compatível. Eles não fazem parte do caminho de atendimento nem do Compose. Não reativar sem decisão arquitetural explícita e testes no hardware real.

`.coverage`, `test_repro.db`, `teste` e dados em `storage/` podem ser artefatos existentes do ambiente. Nunca incluí-los automaticamente em commits ou apagá-los sem verificar propriedade e necessidade.
