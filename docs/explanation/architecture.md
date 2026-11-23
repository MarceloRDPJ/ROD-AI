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

Alertas proativos também seguem esse contrato. Um novo MAC observado na rede gera um dossiê com primeira e última observação, quantidade de leituras, IPs usados, nome cadastrado, hostname, tipo provável, fabricante quando o OUI é confiável e uma amostra da atividade DNS do AdGuard. O cartão oferece `Detalhes`, `Cadastrar`, `Bloquear` e `Ignorar`; tocar em `Bloquear` ainda abre uma segunda confirmação. Respostas desconhecidas oferecem atalhos relevantes em vez de encerrar a conversa com um texto genérico.

## Limites do guardião de rede

- O prefixo OUI ajuda a inferir fabricante somente em MACs globalmente administrados; ele não prova o modelo do aparelho.
- iPhone, iPad, Android e outros sistemas podem usar MAC privado ou aleatório. Nesse caso o ROD sinaliza a limitação e não atribui fabricante pelo prefixo.
- Hostname e nome anunciado podem sugerir `iPhone` ou um eletrodoméstico, mas o nome cadastrado pelo usuário é a única vinculação de dono tratada como confirmada.
- O AdGuard informa consultas DNS, bloqueios e domínios. Isso é atividade observável, não consumo de banda em bytes.
- Como o Pi não é o roteador ou ponto de acesso, ele não conhece cômodo, ponto de acesso associado, RSSI nem tráfego total do cliente. Esses dados exigiriam telemetria compatível do roteador/AP ou transformar o Pi em gateway, o que não faz parte da arquitetura atual.

## Interrupções e horários agendados

O ROD não afirma a causa de uma interrupção sem evidência. Um intervalo sem snapshot indica apenas que ele ficou sem coletar dados, e isso acontece tanto numa queda de energia quanto num deploy, atualização ou crash do contêiner.

A distinção é verificável pelo uptime do host: numa queda real o Raspberry Pi reinicia, então seu uptime não pode ser maior que o intervalo observado. Com base nisso:

- Host reiniciado durante o intervalo: o ROD relata a ausência e o reinício, sem nomear a causa. Queda de energia, trava do sistema e reset do watchdog de hardware produzem o mesmo reinício, e o Pi não expõe sinal que os separe — o driver BCM2835 devolve `bootstatus` 0 em qualquer caso.
- Host ligado o tempo todo: foi o serviço que reiniciou. Interrupções curtas ficam só no log; acima de 15 minutos ele avisa, deixando explícito que o Pi não reiniciou.
- Uptime indisponível: nada é afirmado e nada é enviado.

Relatórios e automações por horário disparam uma única vez por período, dentro de uma janela de tolerância de cinco minutos. Os laços de serviço acordam a cada 60 segundos e derivam alguns segundos a cada volta; comparar o minuto por igualdade exata perdia o disparo quando a volta caía fora do minuto, e repetia quando duas voltas caíam dentro dele. A mesma janela impede que um horário vencido há horas seja reenviado atrasado depois de um restart.

Todo horário agendado é lido em `Config.TIMEZONE`, nunca na hora local do processo. Não por falta de fuso no contêiner: a imagem `python:3.12-slim` já traz `tzdata`, e o `TZ` do Compose resolve corretamente — verificado no Pi, onde `date` dentro do contêiner responde `-03`. O `tzdata` consta do `Dockerfile` apenas para declarar a dependência em vez de herdá-la da imagem base.

A razão de ler sempre `Config.TIMEZONE` é outra: data e hora **ingênuas** atravessam camadas com significados diferentes. O scheduler trata um `target_date` sem fuso como UTC, enquanto quem o gravava usava a hora local do processo. Com o fuso resolvendo em `-03`, um lembrete criado pelo fallback de `flows` disparava três horas adiantado. Gravar sempre com fuso explícito elimina a ambiguidade na origem.
