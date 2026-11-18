# Arquitetura

O ROD é um monólito Python pequeno, orientado a eventos do Telegram e executado em um único contêiner no Raspberry Pi. FastAPI serve healthcheck e dashboard; SQLite guarda estado persistente.

```text
Telegram -> normalização -> regras exatas -> intents seguras -> Brain
                                                         |
                                                         v
                              executor -> módulos/serviços -> fontes reais

Browser LAN/VPN -> FastAPI autenticada -> métricas e ações operacionais
```

## Decisão central

Em um Pi 3B com cerca de 1 GB de RAM, previsibilidade é mais valiosa que uma camada generativa. O Brain é um coordenador local: seleciona regras, conhecimento curto e ferramentas. Quando não há skill, ele esclarece os limites do produto.

## Limites de confiança

- Consultas toleram variações controladas por RapidFuzz.
- Ações destrutivas não herdam contexto como autorização e exigem confirmação.
- Métricas vêm de `psutil`, sistema operacional, rede ou API oficial configurada.
- Dashboard e Telegram possuem controles de acesso independentes.
- Segredos entram por `.env`; nenhum valor sensível é devolvido pela API.

## Componentes

- `core/`: roteamento, regras, contexto, execução e eventos.
- `nlp/`: normalização e classificação local.
- `modules/`: Pi, rede, AdGuard, lembretes e hidratação.
- `services/`: agendamento, coleta, relatórios e monitoramento.
- `api/`: healthcheck e painel administrativo autenticado.
- `database/`: persistência SQLite e migrações idempotentes.

Não fazem parte da arquitetura: LLM, Android, celular, GPIO, ventoinha, sensores, concessionárias, MCP genérico ou webhooks genéricos.
