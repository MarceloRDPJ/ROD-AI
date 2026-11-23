"""Contrato dos menus do Telegram.

A versão anterior despejava o catálogo inteiro — mais de 40 linhas de bullet
numa mensagem só. Estes testes fixam a divulgação progressiva e, principalmente,
garantem que todo botão leva a algum lugar: botão que não faz nada é pior que
botão nenhum.
"""

import pytest

from jarvis.core import menus
from jarvis.core.router import route
from jarvis.core.telegram_safe import normalize_markup


def _buttons(menu):
    markup = menu["reply_markup"]
    if markup is None:  # pragma: no cover - só sem o pacote do telegram
        pytest.skip("pacote telegram ausente")
    return [button for row in markup.inline_keyboard for button in row]


ALL_MENUS = ["help", "command_list", "menu_rede", "menu_agenda", "menu_agua",
             "menu_sistema", "menu_automacoes"]


@pytest.mark.parametrize("intent", ALL_MENUS)
def test_every_menu_builds_with_text_and_buttons(intent):
    menu = menus.build(intent)

    assert menu["text"].strip()
    assert _buttons(menu)


def test_unknown_intent_is_not_a_menu():
    assert menus.build("network_scan") is None


def test_help_and_command_list_share_the_main_menu():
    """`comandos` despejava o catalogo; agora abre o mesmo menu que `ajuda`."""
    assert menus.build("command_list") == menus.build("help")


def test_main_menu_is_short_enough_to_read():
    """O menu antigo tinha mais de 40 linhas; os botoes e que sao a lista."""
    assert len(menus.main_menu()["text"].splitlines()) <= 6


@pytest.mark.parametrize("intent", [i for i in ALL_MENUS if i.startswith("menu_")])
def test_every_submenu_offers_a_way_back(intent):
    assert any(b.callback_data == menus.MAIN_MENU_CALLBACK for b in _buttons(menus.build(intent)))


def test_main_menu_covers_every_area_exactly_once():
    data = [b.callback_data for b in _buttons(menus.main_menu())]

    assert len(data) == len(set(data))
    for area in ("menu_rede", "menu_agenda", "menu_agua", "menu_sistema", "menu_automacoes"):
        assert area in data


@pytest.mark.parametrize("intent", ALL_MENUS)
@pytest.mark.asyncio
async def test_every_button_routes_to_a_real_intent(intent):
    """Cada callback_data precisa virar intent reconhecida pelo roteador."""
    for button in _buttons(menus.build(intent)):
        resolved = await route(button.callback_data, chat_id=None)
        assert resolved, f"botão '{button.text}' não resolveu nada"
        # "chat" e "small_talk" sao o fallback de quem nao entendeu: um botao
        # que cai neles parece funcionar e nao faz nada.
        assert resolved.get("intent") not in (None, "", "unknown", "chat", "small_talk"), (
            f"botão '{button.text}' ({button.callback_data}) caiu em fallback"
        )


@pytest.mark.parametrize("intent", ALL_MENUS)
def test_menu_markup_survives_bold_normalisation(intent):
    """O codigo usa `**negrito**`, que o Telegram legado nao entende."""
    text = menus.build(intent)["text"]

    assert "**" not in normalize_markup(text)


def test_menus_do_not_advertise_absent_features():
    """A ajuda anunciava Pix e boleto em PDF, que nunca existiram no ROD."""
    todo_o_texto = " ".join(menus.build(i)["text"].lower() for i in ALL_MENUS)

    for ausente in ("pix", "boleto", "fatura"):
        assert ausente not in todo_o_texto
