import asyncio
import logging
import time
from typing import Dict, Any, List, Set, Optional

from jarvis.core.context_reader import ContextReader
from jarvis.core.context import ContextEngine
from jarvis.modules.network import NetworkModule
from jarvis.modules.system import SystemModule
from jarvis.database.persistence import Persistence
from jarvis.services.automation_helpers import build_unknown_device_markup
from jarvis.services.device_intelligence import DeviceIntelligence
from jarvis.core.telegram_safe import safe_send_message

logger = logging.getLogger("services.guardian")


class GuardianService:
    """
    GuardianService — O Guardião da Casa e da Rede.
    """

    DEBOUNCE_SECONDS = 300

    def __init__(
        self,
        application,
        chat_id: int,
        interval_seconds: int = 1,
    ):
        self.app = application
        self.chat_id = chat_id
        self.running = False

        self.PING_INTERVAL = 15
        self.DEVICE_SCAN_INTERVAL = 60
        self.POWER_GAP_THRESHOLD_MINUTES = 2
        # Folga para o custo de subir o processo entre o último snapshot e esta
        # leitura; sem ela um reboot real poderia passar por reinício de serviço.
        self.REBOOT_MARGIN_MINUTES = 3
        # Abaixo disso, reinício de serviço não vira mensagem.
        self.SERVICE_RESTART_REPORT_MINUTES = 15

        self.internet_state = "online"
        self.consecutive_ping_failures = 0
        self.last_ping_time = 0
        self.latency_history: List[float] = []

        self.last_device_scan_time = 0

        # State: Dispositivos online no último ciclo
        self.online_macs: Optional[Set[str]] = None

        self.pending_power_msg: Optional[str] = None
        self._debounce_tracker: Dict[str, float] = {}

    async def start(self):
        if self.running: return
        self.running = True
        logger.info("🛡 GuardianService iniciado (Modo: Guardião Silencioso).")
        await self.check_power_status()

        while self.running:
            try:
                now = time.time()
                if now - self.last_ping_time >= self.PING_INTERVAL:
                    await self.check_internet_status()
                    self.last_ping_time = now

                if now - self.last_device_scan_time >= self.DEVICE_SCAN_INTERVAL:
                    await self.check_device_changes()
                    self.last_device_scan_time = now

            except Exception as e:
                logger.error(f"Erro no loop do Guardian: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(1)

    async def check_power_status(self):
        try:
            result = ContextReader.detect_gap(max_gap_minutes=self.POWER_GAP_THRESHOLD_MINUTES)
            if result.get("status") != "ok" or not result.get("exceeded"):
                return

            gap = result.get("gap_minutes", 0)
            msg = await self._explain_downtime(gap)
            if not msg:
                return

            self.pending_power_msg = msg
            logger.info(f"Interrupcao confirmada por uptime do host. Mensagem na fila: {msg}")
        except Exception as e:
            logger.error(f"Erro ao verificar status de energia: {e}")

    async def _explain_downtime(self, gap_minutes: float) -> Optional[str]:
        """Relata a interrupção pelo que o uptime do host comprova, e só isso.

        Um intervalo sem snapshot aparece tanto quando o Pi reinicia quanto
        quando apenas o serviço reinicia — deploy, atualização ou crash do
        contêiner. O uptime separa os dois casos: se o host ficou ligado o
        tempo todo, ele não pode ter reiniciado.

        O que o uptime não diz é *por que* o host reiniciou. Por isso nenhuma
        causa é nomeada: o ROD informa a ausência e o reinício observados.
        """
        try:
            raw = await SystemModule.get_raw_status()
            host_uptime_minutes = float(raw["uptime_seconds"]) / 60
        except Exception:
            logger.exception("Sem uptime do host; nao da para atribuir causa ao intervalo")
            return None

        host_rebooted = host_uptime_minutes <= gap_minutes + self.REBOOT_MARGIN_MINUTES
        if not host_rebooted:
            # O Pi continuou ligado o tempo todo: foi o serviço que caiu.
            logger.info(
                "Intervalo de %.1f min sem queda de energia: host ligado ha %.1f min.",
                gap_minutes, host_uptime_minutes,
            )
            if gap_minutes < self.SERVICE_RESTART_REPORT_MINUTES:
                # Reinício curto de serviço não é notícia; avisar a cada deploy
                # virava spam. Fica no log.
                return None
            return (
                f"Fiquei fora do ar por uns {int(gap_minutes)} minutos. "
                "O Pi não reiniciou, então não foi queda de energia: foi o meu serviço. "
                "Já voltei."
            )

        # O host reiniciou, e só isso é fato. Queda de energia, trava do sistema
        # e reset do watchdog de hardware produzem exatamente o mesmo reinício,
        # e o Pi não expõe sinal confiável para separá-los: o driver BCM2835
        # devolve bootstatus 0 em qualquer caso. Nomear a causa seria chute.
        return (
            f"Fiquei fora do ar por uns {int(gap_minutes)} minutos e o Pi reiniciou. "
            "Não sei dizer o porquê: queda de energia, trava do sistema ou reset "
            "automático dão o mesmo resultado. Já conferi os serviços e tá tudo de pé."
        )

    async def check_internet_status(self):
        metrics = await NetworkModule.get_ping_metrics()
        success = metrics.get("success", False)
        latency = metrics.get("latency_ms")

        if success:
            self.consecutive_ping_failures = 0
            if self.internet_state == "offline":
                await self.send_message("A internet voltou agora. Tudo subindo aos poucos por aqui.")
                self.internet_state = "online"
            elif self.internet_state == "unstable":
                self.internet_state = "online"

            if latency:
                self.latency_history.append(latency)
                self.latency_history = self.latency_history[-10:]

            if self.pending_power_msg:
                sent = await self.send_message(self.pending_power_msg)
                if sent: self.pending_power_msg = None
        else:
            self.consecutive_ping_failures += 1
            if self.consecutive_ping_failures == 1:
                return
            if self.consecutive_ping_failures == 2:
                if self.internet_state == "online":
                    self.internet_state = "unstable"
                    logger.warning("Dois testes externos consecutivos falharam no Raspberry Pi.")
                return
            if self.internet_state != "offline":
                sent = await self.send_message(
                    "O Raspberry Pi continua sem acesso externo. "
                    "A rede pode estar indisponível; sigo verificando."
                )
                if sent:
                    self.internet_state = "offline"

    # =========================================================================
    # 🕵️‍♂️ DISPOSITIVOS (SPAM FIX)
    # =========================================================================
    async def check_device_changes(self):
        try:
            snapshot = await NetworkModule.get_raw_snapshot()
            devices = snapshot.get("devices", [])
            current_macs = {d["mac"].lower() for d in devices if d.get("mac")}

            # A transient empty ARP scan must not become the baseline. If it did,
            # the next successful scan would look like every device just joined.
            if not current_macs:
                logger.warning("Scan de dispositivos vazio; mantendo baseline anterior.")
                return

            # Inicialização silenciosa
            if self.online_macs is None:
                self.online_macs = current_macs
                for device in devices:
                    if device.get("mac"):
                        Persistence.register_device_seen(device["mac"], ip=device.get("ip"))
                return

            # Diferenças
            joined = current_macs - self.online_macs
            left = self.online_macs - current_macs
            self.online_macs = current_macs

            # Processar Entradas
            for mac in joined:
                await self.handle_device_joined(mac, devices)

            for device in devices:
                mac = str(device.get("mac") or "").lower()
                if mac and mac not in joined:
                    Persistence.register_device_seen(mac, ip=device.get("ip"))

            # Processar Saídas
            for mac in left:
                await self.handle_device_left(mac)

        except Exception as e:
            logger.error(f"Erro no scan de dispositivos: {e}")

    async def handle_device_joined(self, mac: str, all_devices: List[Dict]):
        mac = mac.lower()
        # Se já existe no banco, é CONHECIDO (mesmo sem nome customizado)
        # Isso previne spam se o dispositivo cai e volta ("flapping")
        is_known = Persistence.device_exists(mac)

        device_info = next((d for d in all_devices if d["mac"] == mac), None)
        ip = device_info["ip"] if device_info else "?"
        name = Persistence.get_device_name(mac)

        if is_known:
            # Se já é conhecido, só avisa se tiver nome customizado (relevante)
            # E se for a politica de "Silent Guardian":
            # "Dispositivo conhecido voltou" -> Geralmente ignorar, exceto se for critico?
            # Usuário disse: "Entrou um dispositivo novo... 192...".
            # Se for conhecido mas sem nome, o usuário já viu antes.
            # LOGICA: Se tem nome, avisa "Fulano entrou". Se não tem nome, ignora (flapping de desconhecido).
            if name:
                # Opcional: avisar "Fulano entrou"?
                # Para evitar spam de celular entrando/saindo wifi, melhor silenciar
                # A menos que seja um evento raro?
                # Vamos manter silencioso para conhecidos por enquanto, como solicitado ("Silent Guardian").
                pass
            else:
                # Conhecido sem nome (já avisado antes). Ignora.
                pass
        else:
            # NOVO DISPOSITIVO REAL (Nunca visto antes)
            # Debounce: evitar spam se o WiFi ficar flapping
            now = time.time()
            last_notified = self._debounce_tracker.get(mac, 0)
            if now - last_notified < self.DEBOUNCE_SECONDS:
                logger.info(f"Debounced notification for new device {mac} (last notified {now - last_notified:.0f}s ago)")
            else:
                dossier = await DeviceIntelligence.dossier(ip, mac, observe=True)
                msg = DeviceIntelligence.format_dossier(dossier, alert=True)
                msg += "\n\nEscolha uma acao. Bloquear sempre exige confirmacao."
                ContextEngine.set_network_target(self.chat_id, ip, mac)
                logger.info("Novo dispositivo observado: ip=%s mac=%s", ip, mac)
                await self.send_message(msg, reply_markup=build_unknown_device_markup(ip, mac))
                self._debounce_tracker[mac] = now

            # A observacao foi persistida com os sinais usados no dossie.

    async def handle_device_left(self, mac: str):
        # Apenas loga ou ignora.
        # Avisar saída gera muito spam em WiFi moderno (sleep mode).
        pass

    async def send_message(self, text: str, reply_markup=None) -> bool:
        # safe_send_message renderiza a marcacao e, se o Telegram recusar o
        # parse, reenvia sem ela. Antes o guardiao chamava bot.send_message
        # direto e sem parse_mode, entao crase e asterisco apareciam na tela.
        try:
            return await safe_send_message(
                self.app.bot, self.chat_id, text, reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Falha ao enviar mensagem do guardiao")
            return False
