# Arquitetura

O ROD é um monólito Python pequeno, orientado a eventos do Telegram e executado em um único contêiner no Raspberry Pi. FastAPI serve healthcheck e dashboard; SQLite guarda estado persistente.

```text
Telegram -> entidades tipadas -> normalização -> regras exatas -> intents seguras -> Brain
                                                                      |
                                                                      v
                               sessão persistente -> executor -> módulos/serviços -> fontes reais

Browser LAN/VPN -> FastAPI autenticada -> métricas e ações operacionais
```

## Decisão central

Em um Pi 3B com cerca de 1 GB de RAM, previsibilidade é mais valiosa que uma camada generativa. O Brain é um coordenador local: seleciona regras, conhecimento curto e ferramentas. Quando não há skill, ele esclarece os limites do produto.

## Limites de confiança

- Consultas toleram variações controladas por RapidFuzz.
- Ações destrutivas nunca são escolhidas pelo fuzzy matcher.
- Referências como `ele` só resolvem um alvo explícito e recente da mesma conversa.
- Contexto ajuda a preencher o alvo, mas nunca concede autorização: a confirmação continua obrigatória.
- Ações pendentes e fluxos sobrevivem ao reinício do processo e expiram por tempo.
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

## Contrato de interação

O ROD usa um diálogo orientado a tarefas:

1. Extrai IP, MAC, domínio e URL do texto original antes de remover pontuação.
2. Seleciona uma skill por regra determinística ou fuzzy apenas para consultas seguras.
3. Preenche dados ausentes com contexto curto e tipado; se o alvo estiver ausente ou expirado, pede esclarecimento.
4. Mostra a ação e o alvo antes de qualquer alteração de estado.
5. Confirma ou cancela por botão ou texto, com estado persistente e prazo de 15 minutos.
6. Executa a ferramenta real e informa sucesso ou falha verificável.

Alertas proativos também seguem esse contrato. Um novo MAC observado na rede gera um cartão com `Cadastrar`, `Bloquear` e `Ignorar`; tocar em `Bloquear` ainda abre uma segunda confirmação. Respostas desconhecidas oferecem atalhos relevantes em vez de encerrar a conversa com um texto genérico.
