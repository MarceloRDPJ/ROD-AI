import asyncio
import logging
import time
from typing import Dict, Any, List, Set, Optional

from jarvis.core.context_reader import ContextReader
from jarvis.modules.network import NetworkModule
from jarvis.database.persistence import Persistence

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
            if result.get("status") == "ok" and result.get("exceeded"):
                gap = result.get("gap_minutes", 0)
                if gap >= 10:
                    msg = f"Ei, a energia caiu e ficou fora por uns {int(gap)} minutos. Já conferi os serviços e tá tudo ok."
                else:
                    msg = "Parece que a energia caiu por um tempo. Agora voltou e tô subindo tudo de novo."
                self.pending_power_msg = msg
                logger.info(f"Queda de energia detectada. Mensagem na fila: {msg}")
        except Exception as e:
            logger.error(f"Erro ao verificar status de energia: {e}")

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
                # Auto-register all current devices to avoid spam on restart
                for mac in current_macs:
                    if not Persistence.device_exists(mac):
                        Persistence.register_device_seen(mac)
                return

            # Diferenças
            joined = current_macs - self.online_macs
            left = self.online_macs - current_macs
            self.online_macs = current_macs

            # Processar Entradas
            for mac in joined:
                await self.handle_device_joined(mac, devices)

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
                # Avisa
                msg = (
                    f"🕵️‍♂️ *Novo dispositivo detectado*\n"
                    f"IP: `{ip}`\n"
                    f"MAC: `{mac}`\n\n"
                    f"Para nomear, diga:\n"
                    f"`Renomear {ip} para [NOME]`"
                )
                await self.send_message(msg)
                self._debounce_tracker[mac] = now

            # Registra imediatamente para não avisar de novo
            Persistence.register_device_seen(mac)

    async def handle_device_left(self, mac: str):
        # Apenas loga ou ignora.
        # Avisar saída gera muito spam em WiFi moderno (sleep mode).
        pass

    async def send_message(self, text: str) -> bool:
        try:
            await self.app.bot.send_message(chat_id=self.chat_id, text=text)
            return True
        except Exception:
            return False
