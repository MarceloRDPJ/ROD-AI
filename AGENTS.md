# AGENTS.md — manual operacional para agentes

## Missão

Manter o ROD do Cerrado confiável no Raspberry Pi 3B. Ele é assistente pessoal de Marcelo pelo Telegram e guardião da rede doméstica. Priorize skills reais, baixa latência e verdade operacional; não o transforme em chatbot genérico.

## Ambiente real

- Raspberry Pi 3B, quatro núcleos ARM e aproximadamente 1 GB de RAM.
- `/` em SSD USB (`/dev/sda1`); cartão SD inserido, mas sem papel presumido.
- AdGuard Home e CasaOS compartilham o host.
- Produção em `/opt/bot/jarvis-do-cerrado`, branch `main`.
- Remote canônico: `MarceloRDPJ/ROD-AI`.
- Compose: `docker-compose.yml`; contêiner: `rod_cerrado`; API local: porta 8000.
- Deploy: `/usr/local/bin/deploy_rod.sh`.
- Não existe hardware auxiliar funcional, ventoinha controlável ou sensor externo.

## Arquitetura obrigatória

O atendimento não usa LLM. Não conecte runtime, healthcheck ou Compose a modelos locais ou APIs generativas sem pedido explícito e medição no Pi real.

1. `normalizer.py` normaliza texto.
2. `rules.py` trata regras exatas e ações sensíveis.
3. `intent_engine.py` usa RapidFuzz somente para variações seguras.
4. `brain.py` consulta skill/fonte real ou pede esclarecimento.
5. `executor.py` executa e protege efeitos colaterais.
6. `modules/`, `services/` e `tools/` coletam dados reais.

## Regras de produto e segurança

- `speed`, `sped treste`, `status`, `tempratura do pi` e menus devem responder.
- Tolerar typos apenas em consultas sem efeito colateral.
- Bloquear ou alterar estado exige correspondência forte e confirmação.
- Nunca apresentar valor pronto como medição real ou anunciar hardware ausente.
- Preserve `ALLOWED_USER_ID`; mantenha o dashboard apenas na LAN/VPN.
- Nunca leia, imprima ou versione o `.env` real, tokens, credenciais, IDs ou banco.
- Ações sensíveis ficam em `SENSITIVE_ACTIONS` e usam pending action/confirmação.

## Git e dados

- Inspecione status e diffs antes de editar; o worktree pode estar sujo.
- `.env`, bancos, `env`, `es`, `models/`, `storage/` e artefatos locais são dados do usuário.
- Nunca use `git reset --hard` ou limpeza destrutiva.
- Stage apenas caminhos da tarefa; confirme branch, remote, commit e testes antes de push.

## Testes mínimos

```bash
export PYTHONPATH=src
pytest --no-cov
docker compose config --quiet
```

Simule: `speed`, `sped`, `sped treste`, `status`, `status do raspi`, `tempratura do pi`, `menu`, `menu rede`, `menu agenda`, `meus lembretes`, `qual a velocidade da net`, `batata com banana?` e `bluqear sit`. A última frase não pode bloquear nada.

O gate de cobertura é separado da suíte funcional; reporte os dois resultados sem ocultar falhas.

## Conclusão

Uma mudança só está pronta com comportamento baseado em dado real, testes relevantes, Compose válido, documentação atualizada, nenhum segredo no diff e procedimento de validação/recuperação no Pi.
