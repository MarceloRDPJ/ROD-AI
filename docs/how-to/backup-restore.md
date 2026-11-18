# Backup e restauração

Um arquivo no mesmo SSD não é backup contra falha do SSD. O script exige, por padrão, outro dispositivo.

## Preparar o cartão SD

Primeiro inventarie sem alterar nada:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS
findmnt -no SOURCE,TARGET,FSTYPE,OPTIONS /
```

Não formate nem monte automaticamente até confirmar qual partição é o SD. Depois de preparar manualmente um filesystem e uma entrada estável por UUID, monte-o, por exemplo, em `/mnt/rod-backup`.

## Criar e verificar

```bash
cd /opt/bot/jarvis-do-cerrado
python scripts/backup_db.py --destination /mnt/rod-backup/rod --keep 7
```

O script usa `sqlite3.Connection.backup`, executa `PRAGMA integrity_check`, publica o arquivo atomicamente e mantém os sete mais recentes. Ele recusa origem e destino no mesmo dispositivo; `--allow-same-device` existe apenas para cópias deliberadamente temporárias.

## Restaurar

```bash
cd /opt/bot/jarvis-do-cerrado
docker compose stop homebot
cp src/jarvis/database/homebot.db /tmp/homebot-before-restore.db
sqlite3 /mnt/rod-backup/rod/homebot-AAAA.db 'PRAGMA integrity_check;'
cp /mnt/rod-backup/rod/homebot-AAAA.db src/jarvis/database/homebot.db
docker compose up -d homebot
curl -fsS --max-time 10 http://127.0.0.1:8000/api/system/health
```

Substitua `AAAA` pelo arquivo validado. Se a aplicação não recuperar, pare o serviço e restaure `/tmp/homebot-before-restore.db`.
