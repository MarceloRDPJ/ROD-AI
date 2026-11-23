import logging
import re

try:
    from telegram.error import BadRequest, TelegramError
except Exception:  # pragma: no cover - protege ambiente de teste com pacote quebrado
    BadRequest = Exception
    TelegramError = Exception

logger = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 3900

# As mensagens do ROD são escritas com marcação Markdown (`comando`, *destaque*).
# Sem parse_mode o Telegram mostra crase e asterisco como texto, então o padrão
# aqui é Markdown. Quem precisa de texto cru passa parse_mode=None explicitamente.
DEFAULT_PARSE_MODE = "Markdown"

_UNSET = object()

# Apenas o que o Markdown legado do Telegram interpreta. Ponto, hífen e
# parêntese não são entidades nesse modo, e removê-los destruía IP, data e
# horário justamente na mensagem de fallback.
_MARKDOWN_ENTITY_CHARS = r"[`*_\[\]]"


def message_chunks(text: str, limit: int = TELEGRAM_TEXT_LIMIT):
    text = str(text or "")
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks or [""]


def plain_retry_text(text: str) -> str:
    """Remove a marcação para reenviar sem parse_mode, preservando os dados."""
    return re.sub(_MARKDOWN_ENTITY_CHARS, "", str(text or ""))


def sanitize_inline_value(value) -> str:
    """Limpa um valor de origem não confiável antes de embuti-lo na mensagem.

    Hostname, nome anunciado e fabricante vêm do próprio aparelho da rede. O
    Markdown legado do Telegram não aceita escape, então um hostname com `*`
    ou `_` desformataria a mensagem inteira. Em nome DNS e fabricante esses
    caracteres não carregam informação, então removê-los não perde nada.
    """
    if value is None:
        return ""
    return re.sub(_MARKDOWN_ENTITY_CHARS, "", str(value))


# O codigo foi escrito com `**negrito**`, sintaxe do Markdown moderno. O modo
# legado do Telegram usa um asterisco so, entao `**` renderiza errado. A
# conversao acontece aqui, no unico ponto por onde todo texto passa: fazer
# isso arquivo a arquivo esbarraria em `**kwargs` do proprio Python.
_DOUBLE_BOLD = re.compile(r"(?<!\*)\*\*(?!\*)(.+?)(?<!\*)\*\*(?!\*)", re.DOTALL)
_SINGLE_BOLD = r"*\1*"


def normalize_markup(text: str) -> str:
    """Converte `**negrito**` para o `*negrito*` que o Telegram legado entende."""
    return _DOUBLE_BOLD.sub(_SINGLE_BOLD, str(text or ""))


def _is_parse_error(error) -> bool:
    """O Telegram recusou a marcacao, e nao a mensagem."""
    text = str(error).lower()
    return "parse entit" in text or "can't parse" in text or "cant parse" in text


async def _dispatch(send, text, reply_markup, parse_mode):
    """Envia em blocos e nunca derruba o chamador.

    Em erro de parse, reenvia o mesmo conteúdo sem marcação: é melhor perder o
    negrito do que perder a mensagem.
    """

    async def retry_as_plain_text(chunk, kwargs, error):
        logger.warning(f"Falha ao enviar com parse_mode; retry texto puro: {error}")
        try:
            kwargs.pop("parse_mode", None)
            await send(plain_retry_text(chunk), **kwargs)
        except TelegramError as retry_error:
            logger.error(f"Falha ao enviar mensagem Telegram após retry: {retry_error}")

    for index, chunk in enumerate(message_chunks(normalize_markup(text) if parse_mode else text)):
        kwargs = {"reply_markup": reply_markup if index == 0 else None}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        try:
            await send(chunk, **kwargs)
        except BadRequest as e:
            await retry_as_plain_text(chunk, kwargs, e)
        except TelegramError as e:
            logger.error(f"Falha ao enviar mensagem Telegram: {e}")
        except Exception as e:
            # Alguns adaptadores/mocks não preservam BadRequest, mas mantêm a
            # mensagem oficial do Telegram. Só fazemos retry para esse caso.
            if parse_mode and _is_parse_error(e):
                await retry_as_plain_text(chunk, kwargs, e)
            else:
                logger.exception("Falha inesperada ao enviar mensagem Telegram")


async def safe_reply_text(message, text, reply_markup=None, parse_mode=_UNSET):
    """Responde no chat renderizando a marcação, sem derrubar o handler."""
    if parse_mode is _UNSET:
        parse_mode = DEFAULT_PARSE_MODE
    await _dispatch(message.reply_text, text, reply_markup, parse_mode)


async def safe_send_message(bot, chat_id, text, reply_markup=None, parse_mode=_UNSET, **extra) -> bool:
    """Envia mensagem proativa (guardião, lembretes, relatórios) com a mesma rede de proteção.

    Retorna se algo chegou a ser enviado, que é o sinal usado para decidir se
    uma mensagem pendente pode sair da fila.
    """
    if parse_mode is _UNSET:
        parse_mode = DEFAULT_PARSE_MODE

    sent = False

    async def send(chunk, **kwargs):
        nonlocal sent
        await bot.send_message(chat_id=chat_id, text=chunk, **kwargs, **extra)
        sent = True

    await _dispatch(send, text, reply_markup, parse_mode)
    return sent


async def safe_edit_text(edit, text, reply_markup=_UNSET, parse_mode=_UNSET) -> None:
    """Edita uma mensagem existente renderizando a marcação.

    Diferente do envio, aqui não há como quebrar em blocos: a edição substitui
    uma mensagem só. E só erro de parse é tratado — "message is not modified" e
    semelhantes sobem para o chamador, que já decide se reenvia como mensagem
    nova em vez de editar.
    """
    if parse_mode is _UNSET:
        parse_mode = DEFAULT_PARSE_MODE

    if parse_mode:
        text = normalize_markup(text)

    kwargs = {"text": text}
    if reply_markup is not _UNSET:
        kwargs["reply_markup"] = reply_markup
    if parse_mode:
        kwargs["parse_mode"] = parse_mode

    try:
        await edit(**kwargs)
    except Exception as error:
        if not (parse_mode and _is_parse_error(error)):
            raise
        logger.warning(f"Edicao recusada pelo parse_mode; retry texto puro: {error}")
        kwargs.pop("parse_mode", None)
        kwargs["text"] = plain_retry_text(text)
        await edit(**kwargs)
