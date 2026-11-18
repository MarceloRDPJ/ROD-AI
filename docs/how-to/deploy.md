# Deploy e recuperação

O deploy deve preservar a versão saudável até fetch, merge, validação e build concluírem.

## Pré-verificação

```bash
cd /opt/bot/jarvis-do-cerrado
git status --short
git remote -v
git branch --show-current
docker compose ps
curl -fsS --max-time 10 http://127.0.0.1:8000/api/system/health
```

Interrompa se houver alterações rastreadas inesperadas. Dados locais ignorados não devem ser apagados.

## Deploy habitual

Revise `/usr/local/bin/deploy_rod.sh`: `PROJECT_DIR` deve apontar para `/opt/bot/jarvis-do-cerrado` e o script deve usar merge fast-forward, validar Compose, construir `homebot` e aguardar o healthcheck.

```bash
sudo /usr/local/bin/deploy_rod.sh
sudo tail -n 200 /var/log/rod_deploy.log
```

Validação final:

```bash
docker compose ps
curl -fsS --max-time 10 http://127.0.0.1:8000/api/system/health
docker logs --tail 100 rod_cerrado
```

## Recuperação

Se o build falhar, o contêiner anterior continua ativo. Se o novo contêiner iniciar mas não ficar saudável, identifique o commit anterior com `git log`, faça uma reversão explícita por novo commit ou use a imagem anterior identificada em `docker image ls`; não limpe o repositório com `reset --hard`.

Antes de recuperar banco, pare apenas `homebot`, preserve uma cópia do banco atual e siga [backup-restore.md](backup-restore.md).
