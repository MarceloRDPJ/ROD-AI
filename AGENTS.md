# AGENTS.md — manual operacional para agentes

## Missão do projeto

Manter o ROD do Cerrado confiável no Raspberry Pi 3B. Ele é assistente pessoal de Marcelo pelo Telegram e guardião da rede doméstica. Priorize skills reais, respostas rápidas e verdade operacional. Não transforme o projeto em chatbot genérico.

## Ambiente real

- Hardware: Raspberry Pi 3B, 4 núcleos ARM, aproximadamente 1 GB RAM, sem sensores externos.
- Storage: SSD USB; em produção `/` está em `/dev/sda1`.
- Serviços vizinhos: AdGuard Home e CasaOS.
- Projeto de produção: `/opt/bot/jarvis-do-cerrado`.
- Remote: `MarceloRDPJ/MarceloRDPJ-jarvis-do-cerrado-prod`.
- Branch de produção: `main`.
- Compose correto: `/opt/bot/jarvis-do-cerrado/docker-compose.yml`.
- Contêiner correto: `rod_cerrado`.
- API/dashboard: porta local `8000`.
- Deploy habitual: `/usr/local/bin/deploy_rod.sh`; valide seu conteúdo, pois versões antigas apontavam incorretamente para `/opt/bot/home_assistant_bot`.

## Arquitetura obrigatória

O atendimento não usa LLM. Não conectar `Brain`, roteador, healthcheck ou Compose a `llama.cpp`, GGUF, OpenAI, Gemini, Groq ou outra IA sem pedido explícito do proprietário e medição no Pi real.

Pipeline:

1. `normalizer.py` normaliza texto.
2. `rules.py` trata regras exatas e ações sensíveis.
3. `intent_engine.py` usa RapidFuzz para variações seguras.
4. `brain.py` consulta conhecimento local/fonte real ou pede esclarecimento.
5. `executor.py` executa e protege efeitos colaterais.
6. `modules/`, `services/` e `tools/` coletam dados reais.

Contas Equatorial usam uma cadeia oficial: sessão web, Clara de Goiás no WhatsApp
e cache informativo. O WhatsApp roda no Poco como aparelho adicional, sem SIM. A
automação deve permanecer restrita ao contato oficial, somente leitura, com limite
de reenvio e resultado financeiro aceito apenas quando valor e referência/vencimento
forem coerentes ou quando a concessionária declarar explicitamente ausência de débito.

`llm_fallback.py` é legado fora do runtime. Não interpretar sua presença como arquitetura ativa.

## Regras de produto

- Consultas como `speed`, `sped treste`, `status` e `tempratura do pi` devem funcionar.
- Para consultas sem efeito colateral, tolerar erros de digitação com limite controlado.
- Para bloquear, reiniciar, apagar ou alterar estado, exigir correspondência forte e confirmação.
- Nunca apresentar valor pronto como se fosse medição real.
- Resposta técnica compacta é desejável: `CPU: 12%, RAM: 43%, Temp: 48 °C.`
- Humanizar escolha de palavras, não fatos.
- Quando não houver skill, responder em milissegundos orientando para Pi, rede, AdGuard, agenda ou menu.
- Contexto deve ser curto, associado ao `chat_id` e nunca conceder autorização implícita.
- Não anunciar sensor, integração ou automação que o hardware/código não confirme.

## Segurança

- Nunca ler, imprimir, editar ou versionar o `.env` real sem necessidade explícita.
- Nunca expor `TELEGRAM_TOKEN`, credenciais do AdGuard, IDs privados ou conteúdo do banco.
- O dashboard não possui autenticação documentada; mantenha-o restrito à LAN/VPN.
- Preserve a verificação de `ALLOWED_USER_ID`.
- Ações sensíveis devem continuar em `SENSITIVE_ACTIONS` e passar por pending action/confirmação.
- Não afrouxar fuzzy matching para comandos destrutivos.

## Git e arquivos do usuário

- O worktree pode estar sujo. Inspecione `git status --short` e diffs antes de editar.
- `.coverage` é um arquivo rastreado que frequentemente aparece modificado por testes; não incluí-lo automaticamente.
- `env`, `es`, `models/`, bancos, `storage/`, `test_repro.db` e `teste` podem ser dados locais/preexistentes. Não apagar, resetar ou versionar sem autorização específica.
- Nunca usar `git reset --hard` ou checkout destrutivo para limpar o projeto.
- Stage somente caminhos exatos pertencentes à tarefa.
- Antes de push, confirmar branch, remote, commit e testes.

## Como alterar intents

- Regra exata, regex perigosa ou confirmação: `core/rules.py`.
- Frases naturais e typos de consulta: `nlp/intent_engine.py`.
- Normalização geral: `nlp/normalizer.py`.
- Texto programado: `core/personality.py` ou `nlp/local_brain.py`.
- Execução: `core/executor.py` e módulo correspondente.
- Adicionar testes positivos, variações ortográficas e pelo menos um negativo que prove que ação perigosa não foi acionada.

## Testes mínimos

```bash
export PYTHONPATH=src
pytest --no-cov
```

Antes do deploy, simular no roteador:

```text
speed
sped
sped treste
status
status do raspi
tempratura do pi
menu
menu rede
menu agenda
meus lembretes
qual a velocidade da net
batata com banana?
bluqear sit
```

Esperado: skills corretas para consultas, esclarecimento local para assunto desconhecido e nenhuma ação de bloqueio para texto ambíguo. Validar também `docker compose config --quiet`.

O CI mede cobertura global e pode ficar vermelho pelo limiar histórico mesmo quando toda a suíte funcional passa. Reportar as duas coisas separadamente; não ocultar falha.

## Deploy seguro

No Pi:

```bash
cd /opt/bot/jarvis-do-cerrado
git status --short
git fetch origin main
git merge --ff-only origin/main
docker compose config --quiet
docker compose build homebot
docker compose up -d --no-deps --remove-orphans homebot
docker compose ps
curl -sS --max-time 10 http://127.0.0.1:8000/api/system/health
docker logs --tail 100 rod_cerrado
```

Não derrubar a versão saudável antes de saber que fetch, merge, configuração e build funcionaram. Após a migração sem LLM, remover contêineres antigos somente depois do healthcheck verde. Arquivos GGUF no SSD não afetam o runtime e podem ser tratados separadamente.

## Critério de conclusão

Uma mudança só está pronta quando:

- comportamento corresponde a dado real ou skill explícita;
- testes relevantes e suíte funcional passam;
- Compose é válido;
- documentação acompanha a mudança;
- nenhum segredo ou artefato local entrou no diff;
- existe procedimento de validação e recuperação no Pi.
