# Estrutura do repositório

```text
.
├── .github/              # CI e atualizações de dependências
├── docs/
│   ├── decisions/        # decisões e contexto histórico
│   ├── explanation/      # arquitetura e conceitos
│   ├── how-to/           # procedimentos com objetivo definido
│   ├── operations/       # rotina de produção
│   ├── reference/        # contratos e inventários
│   └── specifications/   # especificações detalhadas
├── scripts/              # deploy, auditoria e backup
├── src/jarvis/
│   ├── api/              # FastAPI e dashboard
│   ├── core/             # roteamento e execução
│   ├── database/         # SQLite
│   ├── modules/          # skills operacionais
│   ├── nlp/              # classificação local
│   ├── services/         # tarefas de fundo
│   └── tools/            # fontes web opcionais
└── tests/
```

`pyproject.toml` é a fonte única de metadados, dependências e configuração de testes. Bancos, segredos, modelos, relatórios de cobertura e estado operacional são ignorados.
