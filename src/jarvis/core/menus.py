"""Menus do ROD no Telegram.

Apresentação vive aqui, separada da execução. O princípio é divulgação
progressiva: o menu principal mostra as áreas, e cada área mostra só os seus
comandos. A versão anterior despejava o catálogo inteiro — mais de 40 linhas de
bullet numa mensagem só — o que é referência, não interface.

Os botões são a lista. Repetir cada rótulo no texto acima deles só dobra o que
o leitor precisa varrer, então o texto diz o mínimo e deixa o teclado falar.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.menus")

MAIN_MENU_CALLBACK = "help"


def _markup(rows: List[List[tuple]]) -> Optional[Any]:
    """Monta o teclado inline; devolve None se o pacote do Telegram faltar.

    O ambiente de teste roda sem o pacote, e um menu sem botões ainda é uma
    resposta útil — por isso a ausência não derruba o fluxo.
    """
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError:
        return None

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


def _back_row() -> List[tuple]:
    return [("🔙 Menu principal", MAIN_MENU_CALLBACK)]


def main_menu() -> Dict[str, Any]:
    """Menu principal: só as áreas, sem catálogo."""
    return {
        "text": (
            "🤖 *ROD do Cerrado*\n"
            "O que você quer fazer?\n\n"
            "_Ou fale comigo direto: \"me lembra de ligar pro dentista amanhã\"._"
        ),
        "reply_markup": _markup([
            [("🌐 Rede", "menu_rede"), ("⏰ Agenda", "menu_agenda")],
            [("💧 Água", "menu_agua"), ("🖥 Sistema", "menu_sistema")],
            [("⚙️ Automações", "menu_automacoes"), ("ℹ️ Sobre mim", "quem é você")],
        ]),
    }


def network_menu() -> Dict[str, Any]:
    return {
        "text": (
            "🌐 *Rede e segurança*\n\n"
            "Também dá para digitar:\n"
            "• `bloquear 192.168.1.20` — bloqueia um dispositivo\n"
            "• `bloquear youtube.com` — bloqueia um site\n"
            "• `renomear 192.168.1.15 para TV da sala` — dá nome\n"
            "• `investigar 192.168.1.20` — dossiê do dispositivo\n\n"
            "_Bloqueio altera regra do AdGuard e sempre pede confirmação._"
        ),
        "reply_markup": _markup([
            [("🔍 Quem tá na rede", "quem ta na rede"), ("📡 Status da internet", "status da internet")],
            [("🚀 Velocidade", "velocidade da internet"), ("📊 Estatísticas", "estatisticas de rede")],
            _back_row(),
        ]),
    }


def agenda_menu() -> Dict[str, Any]:
    return {
        "text": (
            "⏰ *Agenda e lembretes*\n\n"
            "Também dá para digitar:\n"
            "• `lembrar de tirar o lixo às 18h` — lembrete único\n"
            "• `lembrar de tomar remédio a cada 8h` — recorrente\n"
            "• `cancelar lembrete 3` — apaga pelo número\n\n"
            "_Cada lembrete entregue traz botões de concluir e adiar._"
        ),
        "reply_markup": _markup([
            [("📋 Ver lembretes", "listar lembretes"), ("➕ Criar lembrete", "criar lembrete")],
            [("📅 Para hoje", "lembretes de hoje"), ("⚠️ Atrasados", "lembretes atrasados")],
            _back_row(),
        ]),
    }


def hydration_menu() -> Dict[str, Any]:
    """Hidratação ganhou área própria; antes dividia espaço com a agenda."""
    return {
        "text": (
            "💧 *Hidratação*\n\n"
            "Também dá para digitar:\n"
            "• `bebi` ou `bebi 500ml` — registra consumo\n"
            "• `mudar meta para 3000` — ajusta a meta do dia\n"
            "• `pausar hidratação` / `retomar hidratação`\n\n"
            "_A análise usa só o histórico que você registrou._"
        ),
        "reply_markup": _markup([
            [("✅ Bebi água", "bebi agua"), ("📈 Status do dia", "status hidratacao")],
            [("⚙️ Ativar", "ativar hidratacao"), ("📊 Análise de 30 dias", "analise de hidratacao")],
            _back_row(),
        ]),
    }


def system_menu() -> Dict[str, Any]:
    return {
        "text": (
            "🖥 *Sistema*\n\n"
            "Também dá para digitar:\n"
            "• `temperatura do pi` — leitura térmica\n"
            "• `reiniciar adguard` — reinicia o contêiner de DNS\n"
            "• `reiniciar sistema` — reinicia o Raspberry Pi\n\n"
            "_Reinícios pedem confirmação antes de executar._"
        ),
        "reply_markup": _markup([
            [("📊 Diagnóstico", "status do sistema"), ("📜 Ver logs", "logs do sistema")],
            _back_row(),
        ]),
    }


def automations_menu() -> Dict[str, Any]:
    return {
        "text": (
            "⚙️ *Automações*\n\n"
            "Regras locais que reagem a eventos da casa, como queda de internet "
            "ou dispositivo novo na rede.\n\n"
            "_As regras vêm desligadas de fábrica; nada dispara sem você ativar._"
        ),
        "reply_markup": _markup([
            [("📋 Ver automações", "listar automacoes"), ("⚙️ Configuração", "config automacoes")],
            _back_row(),
        ]),
    }


# A chave é o intent; o valor monta a resposta. Mantém o executor sem uma
# escada de ifs só para escolher menu.
MENUS = {
    "help": main_menu,
    "command_list": main_menu,
    "menu_rede": network_menu,
    "menu_agenda": agenda_menu,
    "menu_agua": hydration_menu,
    "menu_sistema": system_menu,
    "menu_automacoes": automations_menu,
}


def build(intent: str) -> Optional[Dict[str, Any]]:
    """Devolve o menu do intent, ou None se ele não for um menu."""
    builder = MENUS.get(intent)
    return builder() if builder else None
