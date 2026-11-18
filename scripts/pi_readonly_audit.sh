#!/usr/bin/env bash
# Inventário somente leitura do Raspberry Pi. Não imprime .env, Docker inspect,
# query log DNS, banco ou qualquer credencial.
set -u

section() {
  printf '\n## %s\n' "$1"
}

section "Identidade e uptime"
date --iso-8601=seconds 2>/dev/null || date
hostnamectl 2>/dev/null || hostname
uname -a
uptime

section "CPU, memória, temperatura e energia"
free -h
getconf _NPROCESSORS_ONLN 2>/dev/null || true
if command -v vcgencmd >/dev/null 2>&1; then
  vcgencmd measure_temp 2>/dev/null || true
  vcgencmd get_throttled 2>/dev/null || true
elif test -r /sys/class/thermal/thermal_zone0/temp; then
  awk '{printf "temperature_c=%.1f\n", $1/1000}' /sys/class/thermal/thermal_zone0/temp
fi

section "Discos e montagem da raiz"
findmnt -no SOURCE,FSTYPE,SIZE,USED,AVAIL,USE%,TARGET / 2>/dev/null || true
lsblk -o NAME,MODEL,SIZE,FSTYPE,MOUNTPOINTS 2>/dev/null || true
df -hT /

section "Portas locais"
ss -lntup 2>/dev/null || ss -lntp 2>/dev/null || true

section "Docker (sem ambiente nem secrets)"
if command -v docker >/dev/null 2>&1; then
  docker version --format 'Docker server {{.Server.Version}} / {{.Server.Os}}/{{.Server.Arch}}' 2>/dev/null || true
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>/dev/null || true
  docker system df 2>/dev/null || true
else
  echo "Docker indisponível para este usuário"
fi

section "Serviços principais"
for unit in AdGuardHome docker ssh; do
  printf '%s=' "$unit"
  systemctl is-active "$unit" 2>/dev/null || true
done
pgrep -af 'AdGuardHome|portainer|rod_cerrado|jarvis-do-cerrado' 2>/dev/null || true

section "ROD"
curl -fsS --max-time 10 http://127.0.0.1:8000/api/system/health 2>/dev/null || echo "healthcheck indisponível"

section "DNS pelo AdGuard"
for domain in pagead2.googlesyndication.com securepubads.g.doubleclick.net googleadservices.com youtube.com instagram.com; do
  if command -v dig >/dev/null 2>&1; then
    printf '%s=' "$domain"
    dig +short +time=2 +tries=1 @127.0.0.1 "$domain" A 2>/dev/null | paste -sd, -
  else
    getent ahostsv4 "$domain" 2>/dev/null | awk 'NR==1 {print name "=" $1}' name="$domain"
  fi
done

section "Versões web não autenticadas"
curl -fsS --max-time 5 http://127.0.0.1:9000/api/system/status 2>/dev/null || echo "Portainer sem status público em :9000"

echo
echo "Auditoria concluída. Nenhum speedtest foi executado e nenhum segredo foi lido."
