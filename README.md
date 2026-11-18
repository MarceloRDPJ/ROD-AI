# ROD do Cerrado

Assistente pessoal local de Marcelo e guardião da rede doméstica, executado em um Raspberry Pi 3B. O nome anterior do repositório era Jarvis do Cerrado; o produto atual é ROD.

O ROD privilegia dados reais, comandos rápidos e poucos componentes. Ele não usa LLM no atendimento e não depende de celular, sensores externos ou serviços pagos.

## O que funciona

- Telegram restrito ao `ALLOWED_USER_ID`.
- Status real de CPU, memória, temperatura, disco e uptime do Pi.
- Ping, descoberta da rede e speedtest executados no próprio Pi.
- Consultas e ações confirmadas no AdGuard Home.
- Lembretes, hidratação, Wake-on-LAN e fontes web/RSS opcionais.
- Dashboard autenticado na porta 8000, restrito à LAN ou VPN.
- SQLite persistente, healthcheck e deploy por Docker Compose.

## Limites assumidos

- O Pi 3B tem Ethernet de 100 Mbit/s; um speedtest nele não representa um link mais rápido que isso.
- Não há controle de ventoinha, sensores externos, automação residencial ou consulta de concessionárias.
- O cartão SD só deve receber backups depois de identificado, formatado e montado de forma persistente. O sistema em produção está no SSD.
- Respostas desconhecidas são esclarecidas localmente; não são enviadas a uma IA generativa.

## Desenvolvimento

Requer Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
export PYTHONPATH=src
pytest --no-cov
docker compose config --quiet
```

Copie `.env.example` para `.env` e preencha apenas no ambiente local. O `.env` real, bancos e estado operacional não pertencem ao Git.

## Produção

- Diretório: `/opt/bot/jarvis-do-cerrado`
- Branch: `main`
- Contêiner: `rod_cerrado`
- Deploy: `/usr/local/bin/deploy_rod.sh`
- Healthcheck: `http://127.0.0.1:8000/api/system/health`

Não execute deploy diretamente de um worktree sujo. O procedimento e a recuperação estão em [docs/how-to/deploy.md](docs/how-to/deploy.md).

## Documentação

- [Índice](docs/index.md)
- [Arquitetura](docs/explanation/architecture.md)
- [Comandos suportados](docs/reference/commands.md)
- [Configuração](docs/reference/configuration.md)
- [Runbook do Pi](docs/operations/runbook.md)
- [Backup e restauração](docs/how-to/backup-restore.md)
- [Decisões arquiteturais](docs/decisions/0001-pi-only-no-llm.md)
