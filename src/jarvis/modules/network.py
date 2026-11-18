import asyncio
import logging
import subprocess
import sys
import time
import socket
import struct
from typing import List, Dict, Any, Set

import scapy.all as scapy
from wakeonlan import send_magic_packet
from mac_vendor_lookup import MacLookup

from jarvis.config import Config
from jarvis.core.utils import retry_with_backoff
from jarvis.database.persistence import Persistence

logger = logging.getLogger(__name__)


class NetworkModule:
    """
    NetworkModule

    RESPONSABILIDADES:
    - Operações de rede
    - Coleta RAW (memória)
    - Resposta HUMANA (chat)
    - Deep Scanning (Hostname, Ports)
    """

    @staticmethod
    def _detect_scan_range() -> str | None:
        try:
            if sys.platform.startswith("linux"):
                result = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    if "via" in line or "dev" in line:
                        parts = line.split()
                        for p in parts:
                            if p.count(".") > 0 and "/" in p:
                                return p
            elif sys.platform == "win32":
                result = subprocess.run(["route", "print"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and line[0].isdigit():
                        parts = line.split()
                        if len(parts) >= 4 and parts[0].count(".") == 3:
                            ip = parts[0]
                            mask = parts[1]
                            cidr = sum(bin(int(x)).count("1") for x in mask.split("."))
                            return f"{ip}/{cidr}"
        except Exception:
            pass
        return None

    # ==================================================
    # SCANNING HELPERS (mDNS / SSDP)
    # ==================================================
    @staticmethod
    def _scan_ssdp(timeout: float = 2.0) -> Set[str]:
        ips = set()
        msg = (
            'M-SEARCH * HTTP/1.1\r\n'
            'HOST:239.255.255.250:1900\r\n'
            'ST:ssdp:all\r\n'
            'MX:2\r\n'
            'MAN:"ssdp:discover"\r\n'
            '\r\n'
        ).encode('utf-8')

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(timeout)
        try:
            sock.sendto(msg, ('239.255.255.250', 1900))
            start = time.time()
            while time.time() - start < timeout:
                try:
                    data, addr = sock.recvfrom(65507)
                    ips.add(addr[0])
                except socket.timeout:
                    break
                except Exception:
                    break
        except Exception as e:
            logger.error(f"SSDP Scan Error: {e}")
        finally:
            sock.close()
        return ips

    @staticmethod
    def _scan_mdns(timeout: float = 2.0) -> Set[str]:
        ips = set()
        packet = (
            b'\x00\x00'  # Transaction ID
            b'\x00\x00'  # Flags
            b'\x00\x01'  # Questions
            b'\x00\x00'  # Answer RRs
            b'\x00\x00'  # Authority RRs
            b'\x00\x00'  # Additional RRs
            b'\x09_services\x07_dns-sd\x04_udp\x05local\x00'
            b'\x00\x0c'  # Type PTR
            b'\x00\x01'  # Class IN
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(timeout)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

        try:
            sock.sendto(packet, ('224.0.0.251', 5353))
            start = time.time()
            while time.time() - start < timeout:
                try:
                    data, addr = sock.recvfrom(65507)
                    ips.add(addr[0])
                except socket.timeout:
                    break
                except Exception:
                    break
        except Exception as e:
            logger.error(f"mDNS Scan Error: {e}")
        finally:
            sock.close()
        return ips

    # ==================================================
    # DEEP SCAN: HOSTNAME & PORTS
    # ==================================================
    @staticmethod
    async def _resolve_hostname_deep(ip: str) -> str | None:
        """
        Tenta resolver hostname via Reverse DNS, NetBIOS (simulado via socket) ou mDNS simples.
        """
        # 1. Reverse DNS (Standard)
        try:
            hostname = await asyncio.to_thread(socket.gethostbyaddr, ip)
            if hostname:
                return hostname[0]
        except:
            pass

        # 2. Probe Ports for Hints (HTTP Banner / SSH Banner) - Very Basic
        # Not implementing full banner grab to avoid being too invasive/slow,
        # but connecting to port 80 might reveal server headers in future.
        return None

    @staticmethod
    async def _check_common_ports(ip: str) -> List[int]:
        """
        Verifica portas comuns para tentar adivinhar o tipo de dispositivo.
        Timeout agressivo (200ms) para não demorar.
        """
        ports_to_check = [
            22,   # SSH (Linux/Server)
            80,   # HTTP (Webserver/IoT/Router)
            443,  # HTTPS
            8080, # Alt Web
            53,   # DNS (Router/Pi-hole)
            5353, # mDNS (Apple/IoT)
            62078 # iPhone Sync (às vezes)
        ]

        async def check_port(port):
            try:
                # connect_ex is sync, wrap in to_thread or use asyncio.open_connection
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port),
                    timeout=0.2
                )
                writer.close()
                await writer.wait_closed()
                return port
            except:
                return None

        tasks = [check_port(p) for p in ports_to_check]
        results = await asyncio.gather(*tasks)
        return [p for p in results if p is not None]

    @staticmethod
    def _guess_device_type(vendor: str, ports: List[int], hostname: str) -> str:
        """
        Heurística simples para nomear dispositivo desconhecido.
        """
        vendor = vendor.lower() if vendor else ""
        hostname = (hostname or "").lower()

        # Apple
        if "apple" in vendor:
            if "iphone" in hostname: return "iPhone"
            if "ipad" in hostname: return "iPad"
            if "macbook" in hostname or "mac" in hostname: return "MacBook"
            return "Dispositivo Apple"

        # Ports Heuristic
        if 22 in ports: return "Servidor Linux/SSH"
        if 80 in ports or 8080 in ports: return "Webserver/IoT"
        if 53 in ports: return "Roteador/DNS"

        # Vendor Heuristic
        if "espressif" in vendor: return "Smart Plug/Lâmpada (IoT)"
        if "intel" in vendor: return "PC/Laptop"
        if "raspberry" in vendor: return "Raspberry Pi"

        return "Dispositivo Desconhecido"

    # ==================================================
    # SCAN PRINCIPAL (ATUALIZADO)
    # ==================================================
    @staticmethod
    async def scan_network_deep(status_callback=None, app=None) -> List[Dict[str, Any]]:
        """
        Scan profundo com feedback.
        status_callback(msg): Função assíncrona para atualizar o usuário.
        app: Application instance (opcional) para eventos.
        """
        if status_callback:
            await status_callback("⏳ Iniciando varredura ARP...")

        # 1. Discovery (ARP + Hybrid)
        devices_raw = await NetworkModule.get_raw_snapshot()
        raw_list = devices_raw.get("devices", [])

        # Hybrid addition
        try:
            ssdp = NetworkModule._scan_ssdp(timeout=1.0)
            mdns = NetworkModule._scan_mdns(timeout=1.0)
            hybrid_ips = ssdp.union(mdns)
            known_ips = {d["ip"] for d in raw_list}

            for ip in hybrid_ips:
                if ip not in known_ips:
                    # Try to resolve MAC
                    mac = await NetworkModule.resolve_mac_by_ip(ip)
                    if mac:
                        raw_list.append({"ip": ip, "mac": mac})
        except Exception as e:
            logger.error(f"Hybrid scan partial fail: {e}")

        total = len(raw_list)
        try:
            from mac_vendor_lookup import AsyncMacLookup
            mac_lookup = AsyncMacLookup()
        except ImportError:
            mac_lookup = MacLookup()

        # 2. Deep Probing (Parallel)
        async def probe_device(idx, device):
            ip = device["ip"]
            mac = device["mac"]

            # Persisted Name
            custom_name = Persistence.get_device_name(mac)

            # Vendor (cached lookup)
            cached_vendor = Persistence.get_mac_vendor(mac)
            if cached_vendor:
                vendor = cached_vendor
            else:
                try:
                    try:
                        vendor = mac_lookup.lookup(mac)
                    except TypeError:
                        vendor = await mac_lookup.lookup(mac)
                    if asyncio.iscoroutine(vendor):
                        vendor = await vendor
                    Persistence.set_mac_vendor(mac, vendor)
                except:
                    vendor = "Desconhecido"

            # Hostname & Ports
            hostname = await NetworkModule._resolve_hostname_deep(ip)
            ports = await NetworkModule._check_common_ports(ip)

            guessed_type = NetworkModule._guess_device_type(vendor, ports, hostname)

            # Evento: Dispositivo Desconhecido
            if app and guessed_type == "Dispositivo Desconhecido" and not custom_name:
                automation = app.bot_data.get("automation")
                if automation:
                    from jarvis.core.events import Event
                    event = Event(
                        type="network.unknown_device",
                        source="network_module",
                        payload={"ip": ip, "mac": mac, "vendor": vendor}
                    )
                    await automation.on_event(event)

            # Feedback update occasionally
            if status_callback and idx % 3 == 0:
                 await status_callback(f"🕵️‍♂️ Analisando dispositivo {idx+1}/{total} ({ip})...")

            return {
                "ip": ip,
                "mac": mac,
                "vendor": vendor,
                "hostname": hostname,
                "ports": ports,
                "custom_name": custom_name,
                "guessed_type": guessed_type
            }

        if status_callback:
            await status_callback(f"🔬 Analisando {total} dispositivos profundamente...")

        tasks = [probe_device(i, d) for i, d in enumerate(raw_list)]
        probed_results = await asyncio.gather(*tasks)

        # Sort
        probed_results.sort(key=lambda x: int(x["ip"].split('.')[-1]) if x["ip"].count('.')==3 else 0)

        return probed_results

    # ==================================================
    # RAW NETWORK DATA (PASSO 5)
    # ==================================================
    @staticmethod
    async def get_raw_snapshot() -> Dict[str, Any]:
        return await asyncio.to_thread(NetworkModule._get_raw_snapshot_sync)

    @staticmethod
    def _get_raw_snapshot_sync() -> Dict[str, Any]:
        devices: List[Dict[str, str]] = []
        scan_range = NetworkModule._detect_scan_range() or Config.get("network.scan_range") or "192.168.0.0/24"
        try:
            # Simple ARP
            arp = scapy.ARP(pdst=scan_range)
            ether = scapy.Ether(dst="ff:ff:ff:ff:ff:ff")
            result = scapy.srp(ether / arp, timeout=2, verbose=0)[0]

            for element in result:
                devices.append({
                    "ip": element[1].psrc,
                    "mac": element[1].hwsrc
                })
        except Exception as e:
            logger.error(f"RAW network snapshot error: {e}")

        return {
            "timestamp": time.time(),
            "device_count": len(devices),
            "devices": devices,
        }

    # ==================================================
    # HELPER: RESOLVE IP -> MAC
    # ==================================================
    @staticmethod
    async def resolve_mac_by_ip(ip: str) -> str | None:
        return await asyncio.to_thread(NetworkModule._resolve_mac_by_ip_sync, ip)

    @staticmethod
    def _resolve_mac_by_ip_sync(ip: str) -> str | None:
        try:
            arp = scapy.ARP(pdst=ip)
            ether = scapy.Ether(dst="ff:ff:ff:ff:ff:ff")
            result = scapy.srp(ether / arp, timeout=1, verbose=0)[0]
            if result:
                return result[0][1].hwsrc
        except Exception:
            pass
        return None

    # ==================================================
    # LEGACY SCAN (WRAPPER)
    # ==================================================
    @staticmethod
    async def scan_network(ip_range: str | None = None) -> str:
        if ip_range is None:
            ip_range = NetworkModule._detect_scan_range() or Config.get("network.scan_range") or "192.168.0.0/24"
        return await asyncio.to_thread(NetworkModule._scan_network_human_sync, ip_range)

    @staticmethod
    def _scan_network_human_sync(ip_range: str) -> List[str] | str:
        found_devices = {}
        try:
            arp = scapy.ARP(pdst=ip_range)
            ether = scapy.Ether(dst="ff:ff:ff:ff:ff:ff")
            result = scapy.srp(ether / arp, timeout=2, verbose=0)[0]
            for sent, received in result:
                found_devices[received.psrc] = received.hwsrc
        except: pass

        if not found_devices: return "⚠️ Nenhum dispositivo encontrado (Scan Rápido)."

        output = []
        mac_lookup = MacLookup()
        for ip in sorted(found_devices.keys(), key=lambda x: int(x.split('.')[-1]) if x.count('.')==3 else 0):
            mac = found_devices[ip]
            try: vendor = mac_lookup.lookup(mac)
            except: vendor = "Genérico"
            name = Persistence.get_device_name(mac)

            line = f"🖥️ `{ip}` - {name if name else vendor}"
            output.append(line)

        return "🕵️‍♂️ *Scan Rápido:*\n" + "\n".join(output)

    # ==================================================
    # WAKE ON LAN
    # ==================================================
    @staticmethod
    async def wake_on_lan(mac_address: str) -> Dict[str, Any]:
        """
        Envia pacote mágico Wake-on-LAN para ligar PC remotamente.

        Args:
            mac_address: MAC address do PC (formato: "AA:BB:CC:DD:EE:FF")

        Returns:
            {"success": bool, "message": str}
        """
        import re
        from wakeonlan import send_magic_packet

        try:
            # Valida formato do MAC address
            if not re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', mac_address):
                return {
                    "success": False,
                    "message": "MAC do PC inválido ou não configurado"
                }

            # Remove separadores para formato aceito pela lib
            mac_clean = mac_address.replace(":", "").replace("-", "")

            # Envia pacote mágico (broadcast)
            await asyncio.to_thread(send_magic_packet, mac_clean)

            logger.info("Pacote WOL enviado para o PC configurado")

            return {
                "success": True,
                "message": "Pacote mágico enviado para o PC configurado"
            }

        except Exception:
            logger.exception("Erro ao enviar Wake-on-LAN")
            return {
                "success": False,
                "message": "Falha ao enviar o pacote Wake-on-LAN"
            }

    @staticmethod
    async def check_device_online(ip: str, timeout: int = 2) -> bool:
        """
        Verifica se dispositivo está online via ping.

        Args:
            ip: Endereço IP
            timeout: Timeout em segundos

        Returns:
            True se online, False se offline
        """
        import subprocess
        import platform

        try:
            # Comando ping varia por OS
            param = '-n' if platform.system().lower() == 'windows' else '-c'
            command = ['ping', param, '1', '-W', str(timeout), ip]

            result = await asyncio.to_thread(
                subprocess.run,
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            return result.returncode == 0

        except Exception as e:
            logger.error(f"Erro ao pingar {ip}: {e}")
            return False

    @staticmethod
    @retry_with_backoff(retries=2)
    async def wake_pc() -> str:
        # Mantido para retrocompatibilidade, mas usando a nova implementação
        if not Config.PC_MAC:
            return "❌ MAC do PC não configurado."

        res = await NetworkModule.wake_on_lan(Config.PC_MAC)
        if res["success"]:
            return "⚡ Wake-on-LAN enviado para o PC configurado."
        return f"❌ {res['message']}"

    # ==================================================
    # PING
    # ==================================================
    @staticmethod
    @retry_with_backoff()
    async def check_ping() -> str:
        metrics = await NetworkModule.get_ping_metrics()
        if metrics['success']:
            return f"🟢 Online (Latência: {metrics['latency_ms']}ms)"
        return "🔴 Offline"

    @staticmethod
    async def get_ping_metrics(hosts: List[str] = None) -> Dict[str, Any]:
        if hosts is None:
            hosts = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
        return await asyncio.to_thread(NetworkModule._ping_metrics_sync, hosts)

    @staticmethod
    def _ping_metrics_sync(hosts: List[str]) -> Dict[str, Any]:
        import re
        for host in hosts:
            try:
                output = subprocess.check_output(
                    ["ping", "-c", "1", "-W", "2", host],
                    stderr=subprocess.STDOUT,
                    text=True
                )
                match = re.search(r"time=([\d\.]+)", output)
                latency = float(match.group(1)) if match else None
                return {"success": True, "latency_ms": latency, "error": None}
            except Exception as e:
                continue

        return {"success": False, "latency_ms": None, "error": "All pings failed"}

    # ==================================================
    # SPEEDTEST
    # ==================================================
    @staticmethod
    @retry_with_backoff(retries=1)
    async def run_speedtest() -> str:
        return await asyncio.to_thread(NetworkModule._speedtest_sync)

    @staticmethod
    def _speedtest_sync() -> str:
        try:
            res = subprocess.run(
                ["speedtest-cli", "--simple", "--secure"],
                capture_output=True,
                text=True,
                check=True,
                timeout=45
            )
            return f"🚀 *Velocidade da Internet:*\n\n{res.stdout}"
        except Exception as e:
            return f"❌ Falha no speedtest: {e}"
