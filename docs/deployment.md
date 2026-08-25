# Deploy e operação no Raspberry Pi

## Ambiente de produção

- Repositório: `/opt/bot/jarvis-do-cerrado`
- Remote: `https://github.com/MarceloRDPJ/MarceloRDPJ-jarvis-do-cerrado-prod.git`
- Branch: `main`
- Compose: `/opt/bot/jarvis-do-cerrado/docker-compose.yml`
- Contêiner: `rod_cerrado`
- Log do deploy: `/var/log/rod_deploy.log`

O diretório `/opt/bot/home_assistant_bot` é outro checkout e não deve ser usado para publicar o ROD do Cerrado.

## Deploy manual seguro

Execute no Pi:

```bash
cd /opt/bot/jarvis-do-cerrado
git status --short
git fetch origin main
git merge --ff-only origin/main
docker compose config --quiet
docker compose build homebot
docker compose up -d --no-deps --remove-orphans homebot
```

Depois valide antes de remover qualquer recurso antigo:

```bash
docker compose ps
curl -sS --max-time 10 http://127.0.0.1:8000/api/system/health
docker logs --tail 100 rod_cerrado
```

Resposta esperada do healthcheck: `status` igual a `ok`, banco `ok` e `assistant.mode` igual a `local_skills`.

## Retirada definitiva dos contêineres de teste do LLM

O Compose novo não cria LLM. `--remove-orphans` deve retirar o antigo `jarvis_llm` pertencente ao projeto. Contêineres de teste criados manualmente podem precisar de remoção explícita, somente depois de o ROD saudável ser confirmado:

```bash
docker ps -a --format '{{.Names}}' | grep -E '^jarvis_llm($|_)'
docker rm -f jarvis_llm jarvis_llm_tucano_test jarvis_llm_gemma_test 2>/dev/null || true
```

Isso remove contêineres, não apaga os arquivos GGUF do SSD. Os modelos podem ser limpos posteriormente em manutenção separada.

## Logs

```bash
docker logs -f rod_cerrado
docker logs --tail 200 rod_cerrado
tail -f /var/log/rod_deploy.log
```

Sair de `docker logs -f` com `Ctrl+C` não para o contêiner.

## Script `/usr/local/bin/deploy_rod.sh`

O script deve:

1. usar `set -euo pipefail`;
2. entrar em `/opt/bot/jarvis-do-cerrado`;
3. buscar `origin/main`;
4. aplicar somente fast-forward;
5. validar o Compose;
6. construir `homebot` antes da recriação;
7. subir com `--remove-orphans`;
8. verificar healthcheck e registrar falhas.

Evitar `git reset --hard`, porque ele apaga alterações locais. Evitar `docker compose down` antes do build, porque aumenta indisponibilidade e derruba uma versão ainda funcional se a compilação falhar.

## Verificação funcional após deploy

Enviar pelo Telegram:

```text
status
tempratura do pi
speed
sped treste
menu
menu rede
meus lembretes
batata com banana?
```

As primeiras mensagens devem cair nas skills corretas. A pergunta desconhecida deve receber esclarecimento imediato, sem log de `llama-server`, espera de 12 segundos ou resposta inventada.

Para validar o nó de contas, confirme no Poco que o pacote oficial `com.whatsapp`
está instalado, o serviço **ROD — automação local** está ativo e o WhatsApp está
vinculado como aparelho adicional. Depois consulte, pelo nome, cada imóvel de
energia configurado. O resultado precisa indicar leitura ao vivo ou “sem débitos”;
cache não conta como validação do canal Clara.

## Git e dados locais

Antes de atualizar, `git status --short` deve ser inspecionado. Diretórios `env`, `es`, `models/`, bancos e storage do Pi podem ser dados locais; não apagar ou incluir no Git automaticamente.

## Recuperação

Se o novo contêiner não ficar saudável:

```bash
docker compose ps
docker logs --tail 200 rod_cerrado
curl -v --max-time 10 http://127.0.0.1:8000/api/system/health
git log -3 --oneline
```

Não executar reset destrutivo como reação automática. Identificar primeiro se a falha é código, `.env`, volume, rede, permissão ou healthcheck.
