import logging
import asyncio
from datetime import datetime, timezone, timedelta
from jarvis.database.persistence import Persistence
from jarvis.config import Config
from jarvis.modules.system import SystemModule
from jarvis.modules.network import NetworkModule
from jarvis.core.utils import schedule_is_due

logger = logging.getLogger("services.reporter")

class ReporterService:
    DAILY_HOUR = 7
    DAILY_MINUTE = 0
    WEEKLY_WEEKDAY = 0  # segunda-feira
    WEEKLY_HOUR = 7
    WEEKLY_MINUTE = 5

    def __init__(self, application):
        self.app = application
        self.running = False
        self._last_report_date = None
        self._last_week_report = None

    async def start(self):
        if self.running:
            return
        self.running = True
        logger.info("ReporterService iniciado (relatorios automaticos)")

        while self.running:
            try:
                now = datetime.now(timezone.utc).astimezone(Config.TZ)
                await self._check_schedule(now)
            except Exception as e:
                logger.error(f"Erro no ReporterService: {e}")
            await asyncio.sleep(60)

    async def _check_schedule(self, now: datetime):
        today_str = now.strftime("%Y-%m-%d")

        if schedule_is_due(
            now,
            self.DAILY_HOUR,
            self.DAILY_MINUTE,
            period_key=today_str,
            last_fired=self._last_report_date,
        ):
            self._last_report_date = today_str
            await self._send_daily_report()

        if now.weekday() != self.WEEKLY_WEEKDAY:
            return

        week_str = now.strftime("%Y-W%V")
        if schedule_is_due(
            now,
            self.WEEKLY_HOUR,
            self.WEEKLY_MINUTE,
            period_key=week_str,
            last_fired=self._last_week_report,
        ):
            self._last_week_report = week_str
            await self._send_weekly_report()

    async def _send_daily_report(self):
        report = await self._build_daily_report()
        try:
            await self.app.bot.send_message(
                chat_id=Config.ALLOWED_USER_ID,
                text=report,
            )
            logger.info("Relatorio diario enviado com sucesso")
        except Exception as e:
            logger.error(f"Falha ao enviar relatorio diario: {e}")

    async def _send_weekly_report(self):
        report = await self._build_weekly_report()
        try:
            await self.app.bot.send_message(
                chat_id=Config.ALLOWED_USER_ID,
                text=report,
            )
            logger.info("Relatorio semanal enviado com sucesso")
        except Exception as e:
            logger.error(f"Falha ao enviar relatorio semanal: {e}")

    async def _build_daily_report(self) -> str:
        tokens = Persistence.get_token_usage_today()
        unknown = Persistence.get_unknown_queries_today()
        errors = Persistence.get_api_errors_today()

        sys_info = "N/A"
        uptime = "N/A"
        system_error = None
        try:
            raw = await SystemModule.get_raw_status()
            t = f"{raw['temperature_c']}C" if raw.get('temperature_c') else "N/A"
            uptime = str(timedelta(seconds=raw['uptime_seconds']))
            sys_info = f"CPU: {raw['cpu_percent']}% | RAM: {raw['memory']['percent']}% | Temp: {t}"
        except Exception as e:
            system_error = str(e)
            logger.exception("Falha ao coletar dados de sistema para relatório")

        net_info = "N/A"
        network_error = None
        try:
            ping = await NetworkModule.get_ping_metrics()
            s = "Online" if ping.get('success') else "Offline"
            l = ping.get('latency_ms', 'N/A')
            net_info = f"{s} ({l}ms)"
        except Exception as e:
            network_error = str(e)
            logger.exception("Falha ao coletar dados de rede para relatório")

        msg = "Bom dia! Relatorio Diario — ROD do Cerrado\n\n"
        msg += f"Sistema: {sys_info}\n"
        msg += f"Uptime: {uptime}\n"
        msg += f"Internet: {net_info}\n\n"

        if system_error:
            msg += f"Aviso sistema: dados indisponiveis ({system_error[:80]})\n"
        if network_error:
            msg += f"Aviso rede: dados indisponiveis ({network_error[:80]})\n"

        if tokens['calls'] > 0:
            msg += f"IA registrada: {tokens['calls']} chamadas | {tokens['total']} tokens | ${tokens['cost']:.6f}\n"
        else:
            msg += "IA: nenhuma chamada externa registrada hoje. Isso depende da instrumentacao atual.\n"

        if unknown:
            msg += f"{len(unknown)} consultas nao reconhecidas\n"

        if errors:
            msg += f"{len(errors)} erros de API\n"
        else:
            msg += "Zero erros de API\n"

        msg += "\nDica: Me pergunte 'gastos' pra ver o consumo de IA em detalhes."
        return msg

    async def _build_weekly_report(self) -> str:
        tokens = Persistence.get_token_usage_today()
        unknown_7d = Persistence.get_unknown_queries_count(days=7)

        msg = "Relatorio Semanal — ROD do Cerrado\n\n"
        msg += f"Semana: {datetime.now(Config.TZ).strftime('%d/%m/%Y')}\n\n"
        msg += "IA local / gratuita\n"
        msg += f"* Chamadas hoje: {tokens['calls']}\n"
        msg += f"* Tokens hoje: {tokens['total']}\n"
        msg += f"* Custo hoje: ${tokens['cost']:.6f}\n\n"

        if unknown_7d > 0:
            msg += f"Melhoria continua: {unknown_7d} consultas nao reconhecidas nos ultimos 7 dias.\n"
            msg += "Me pergunte 'o que nao sabe' para ver a lista.\n\n"

        msg += "Estatisticas verificaveis:\n"
        msg += "* Relatorio gerado localmente\n"
        msg += "* Telegram exige internet para entregar mensagens\n"
        msg += "* Backup automatico: nao verificado por este relatorio\n"

        return msg

    def stop(self):
        self.running = False
        logger.info("ReporterService parado.")
