import asyncio
import base64
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from telegram import Update, File
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Azure OpenAI client
try:
    from openai import AzureOpenAI
except Exception as exc:
    AzureOpenAI = None  # type: ignore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("invoice-bot")


INVOICE_JSON_SCHEMA_EXAMPLE = {
    "vendor_name": None,
    "vendor_tax_id": None,
    "invoice_number": None,
    "invoice_date": None,  # ISO 8601, e.g. 2024-08-31
    "due_date": None,      # ISO 8601
    "currency": None,      # e.g. USD, EUR
    "subtotal_amount": None,
    "tax_amount": None,
    "total_amount": None,
    "purchase_order_number": None,
    "payment_terms": None,
    "bill_to": {
        "name": None,
        "address": None,
        "tax_id": None,
    },
    "ship_to": {
        "name": None,
        "address": None,
        "tax_id": None,
    },
    "line_items": [
        {
            "description": None,
            "sku": None,
            "quantity": None,
            "unit_price": None,
            "total": None,
        }
    ],
    "notes": None,
}


SYSTEM_PROMPT = (
    "You are an expert invoice information extractor. "
    "Given one or more invoice images and optional user text hints, extract as many fields as possible and respond strictly as a compact JSON object. "
    "All property names must be in English. Use ISO 8601 dates (YYYY-MM-DD). "
    "Include line items when present. If a field is unknown, use null. Do not add explanations. "
    "Ensure numeric fields are numbers, not strings."
)


@dataclass
class AzureConfig:
    endpoint: str
    api_key: str
    api_version: str
    deployment: str


def load_config_from_env() -> AzureConfig:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip()

    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is required")
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is required")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT is required")

    return AzureConfig(endpoint=endpoint, api_key=api_key, api_version=api_version, deployment=deployment)


def build_client(cfg: AzureConfig):
    if AzureOpenAI is None:
        raise RuntimeError("openai package with AzureOpenAI client is not available. Please check dependencies.")
    client = AzureOpenAI(
        api_key=cfg.api_key,
        api_version=cfg.api_version,
        azure_endpoint=cfg.endpoint,
    )
    return client


def image_bytes_to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def fetch_file_bytes(file: File) -> bytes:
    # python-telegram-bot v21: download_to_memory returns BytesIO
    bio = await file.download_to_memory()
    return bio.getvalue()


def ensure_json(text: str) -> str:
    """Best-effort to return a minified JSON string; fall back to original if parsing fails."""
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return text


async def call_azure_openai_with_image(
    client: Any,
    cfg: AzureConfig,
    image_bytes: bytes,
    hint_text: Optional[str] = None,
    default_currency: Optional[str] = None,
) -> str:
    data_url = image_bytes_to_data_url(image_bytes)

    user_text = (
        (hint_text or "").strip()
        + (f"\nDefault currency (if missing): {default_currency}" if default_currency else "")
    ).strip()

    content_blocks: list[dict[str, Any]] = []
    if user_text:
        content_blocks.append({"type": "text", "text": user_text})
    content_blocks.append({
        "type": "image_url",
        "image_url": {"url": data_url},
    })

    try:
        resp = client.chat.completions.create(
            model=cfg.deployment,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content_blocks},
            ],
        )
        text = resp.choices[0].message.content
        return ensure_json(text)
    except Exception as exc:
        logger.exception("Azure OpenAI request failed")
        return json.dumps({
            "error": "azure_openai_request_failed",
            "message": str(exc),
        })


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hola! Envía una foto de una factura (o un documento de imagen).\n"
        "Opcionalmente añade texto con pistas. Te devolveré un JSON con los datos extraídos."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Instrucciones:\n"
        "- Envía una foto nítida de la factura.\n"
        "- Puedes añadir un mensaje con pistas (p. ej., moneda por defecto).\n"
        "- Recibirás un JSON con propiedades en inglés."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Store last hint to use if next message is an image
    text = update.message.text or ""
    context.user_data["last_hint_text"] = text
    await update.message.reply_text(
        "Gracias. Ahora envía la imagen de la factura para extraer los datos."
    )


async def handle_photo_or_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hint_text: Optional[str] = context.user_data.get("last_hint_text")
    default_currency = os.getenv("DEFAULT_CURRENCY")

    # Determine if it's a photo or an image document
    file: Optional[File] = None
    mime: str = "image/jpeg"

    if update.message.photo:
        # Take the highest resolution photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        mime = "image/jpeg"
    elif update.message.document:
        doc = update.message.document
        if doc.mime_type and doc.mime_type.startswith("image/"):
            file = await context.bot.get_file(doc.file_id)
            mime = doc.mime_type
        else:
            await update.message.reply_text("El documento no es una imagen compatible.")
            return

    if not file:
        await update.message.reply_text("No se pudo obtener la imagen.")
        return

    try:
        image_bytes = await fetch_file_bytes(file)
    except Exception as exc:
        logger.exception("Error descargando la imagen de Telegram")
        await update.message.reply_text("Error descargando la imagen.")
        return

    try:
        cfg = load_config_from_env()
        client = build_client(cfg)
    except Exception as exc:
        logger.exception("Configuración de Azure OpenAI inválida")
        await update.message.reply_text(f"Configuración inválida: {exc}")
        return

    await update.message.chat.send_action(action="typing")

    result_json = await call_azure_openai_with_image(
        client=client,
        cfg=cfg,
        image_bytes=image_bytes,
        hint_text=hint_text,
        default_currency=default_currency,
    )

    # Telegram has message size limits; keep it compact
    compact = ensure_json(result_json)

    if len(compact) > 3800:
        compact = compact[:3700] + "..."

    await update.message.reply_text(compact, parse_mode=None)


async def main() -> None:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_or_document))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_photo_or_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started. Listening for messages...")
    await app.run_polling(close_loop=False)


if __name__ == "__main__":
    # Lightweight check flag to test imports without running polling
    if "--check" in sys.argv:
        try:
            load_dotenv()
            _ = load_config_from_env()
            print("OK: Configuration loaded")
        except Exception as e:
            print(f"Config error: {e}")
        sys.exit(0)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass