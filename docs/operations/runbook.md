# Runbook do Raspberry Pi

## Estado saudável

- `rod_cerrado` aparece `healthy`.
- `/api/system/health` retorna HTTP 200 e banco `ok`.
- Telegram responde apenas ao usuário autorizado.
- CPU, RAM, temperatura e disco exibem medições do Pi.
- Dashboard exige login e só é alcançável pela LAN/VPN.

## Diagnóstico somente leitura

```bash
uptime
free -h
df -h /
vcgencmd measure_temp
vcgencmd get_throttled
ip -br address
ip route
docker compose ps
docker stats --no-stream rod_cerrado
curl -fsS --max-time 10 http://127.0.0.1:8000/api/system/health
docker logs --tail 100 rod_cerrado
```

## Wi-Fi

Ethernet é a rota de produção. Se o Wi-Fi estiver bloqueado porque o domínio regulatório não foi configurado:

```bash
sudo raspi-config nonint do_wifi_country BR
sudo rfkill unblock wifi
rfkill list
iw reg get
```

`wlan0` continuar `DOWN` sem uma rede configurada é normal e não afeta o Ethernet.

## Ordem de resposta

1. Confirmar saúde, recursos, disco e temperatura.
2. Verificar logs sem imprimir segredos.
3. Confirmar AdGuard/CasaOS antes de atribuir lentidão ao ROD.
4. Fazer backup antes de mudanças de banco.
5. Reiniciar somente o serviço afetado; reboot do host é último recurso e não é ação do bot.
