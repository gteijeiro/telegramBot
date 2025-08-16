import os, io, base64, logging, asyncio
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv, find_dotenv
from pydantic import BaseModel, Field
from openai import AzureOpenAI
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# ─────────── logging ───────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("invoice-bot")

# ─────────── .env ───────────
load_dotenv(find_dotenv(usecwd=True, raise_error_if_not_found=False))

def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v

# ─────────── config ───────────
TELEGRAM_TOKEN = require_env("TELEGRAM_TOKEN")
AZURE_OPENAI_ENDPOINT = require_env("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = require_env("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

# PDF render config
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "5"))   # límite de páginas por PDF
PDF_DPI = int(os.getenv("PDF_DPI", "200"))             # resolución de render
PDF_JPEG_QUALITY = int(os.getenv("PDF_JPEG_QUALITY", "85"))

# ─────────── azure client ───────────
client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)

# ─────────── modelos (Structured Outputs) ───────────
class Party(BaseModel):
    name: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None

class Tax(BaseModel):
    name: str
    amount: float
    rate: Optional[float] = None

class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None

class Invoice(BaseModel):
    is_invoice: bool = Field(..., description="True if the document is an invoice; false otherwise")
    document_type: str = Field(..., description="Detected document type (e.g., invoice, receipt)")
    language: Optional[str] = None
    ocr_confidence: Optional[float] = None

    invoice_number: Optional[str] = None
    issue_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    po_number: Optional[str] = None
    currency: Optional[str] = Field(None, description="ISO 4217")

    seller: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)

    subtotal_amount: Optional[float] = None
    total_tax_amount: Optional[float] = None
    total_amount: Optional[float] = None

    taxes: List[Tax] = Field(default_factory=list)
    line_items: List[LineItem] = Field(default_factory=list)

    payment_terms: Optional[str] = None
    notes: Optional[str] = None

SYSTEM_PROMPT = (
    "You are an expert OCR and invoice parser. "
    "Read the provided image(s) of an invoice and extract fields strictly into the provided schema. "
    "Rules: dates in ISO 8601 (YYYY-MM-DD); currency as ISO 4217; numbers with dot decimal; "
    "return null when unknown; do not invent values; "
    "set is_invoice=false if it's not an invoice."
)

# ─────────── helpers ───────────
def bytes_to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"

def pdf_to_image_data_urls(pdf_bytes: bytes, dpi: int, max_pages: int) -> List[str]:
    import fitz  # PyMuPDF
    urls: List[str] = []
    pages_rendered = 0
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        log.info("PDF abierto: %d páginas (máx a procesar: %d) | DPI=%d | Quality=%d",
                 doc.page_count, max_pages, dpi, PDF_JPEG_QUALITY)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes(output="jpg", jpg_quality=PDF_JPEG_QUALITY)
            urls.append(bytes_to_data_url(img_bytes, mime="image/jpeg"))
            pages_rendered += 1
    log.info("PDF renderizado: %d página(s) convertidas a imagen", pages_rendered)
    return urls

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("Comando /start de %s (%s)", update.effective_user.username, update.effective_user.id)
    await update.message.reply_text(
        "Envíame una **foto** o un **PDF** de la factura y te devuelvo JSON (propiedades en inglés)."
    )

# Fotos enviadas como PHOTO
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.photo:
        return
    log.info("Foto recibida de chat %s. Resoluciones: %s",
             msg.chat_id, [ (p.width, p.height) for p in msg.photo ])
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    photo = msg.photo[-1]
    f = await photo.get_file()
    bio = io.BytesIO()
    await f.download_to_memory(out=bio)
    size = bio.tell()
    bio.seek(0)
    log.info("Foto descargada: %d bytes", size)

    data_url = bytes_to_data_url(bio.read(), mime="image/jpeg")
    await analyze_and_reply([data_url], msg, context)

# Imágenes adjuntas como DOCUMENT
async def handle_image_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.document:
        return
    mime = (msg.document.mime_type or "").lower()
    if not mime.startswith("image/"):
        return
    log.info("Imagen (document) recibida: mime=%s nombre=%s tamaño=%s bytes",
             mime, msg.document.file_name, msg.document.file_size)
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    f = await msg.document.get_file()
    bio = io.BytesIO()
    await f.download_to_memory(out=bio)
    size = bio.tell()
    bio.seek(0)
    log.info("Imagen descargada: %d bytes", size)

    data_url = bytes_to_data_url(bio.read(), mime=mime if mime.startswith("image/") else "image/jpeg")
    await analyze_and_reply([data_url], msg, context)

# PDF adjunto como DOCUMENT
async def handle_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.document:
        return
    mime = (msg.document.mime_type or "").lower()
    if mime != "application/pdf":
        return
    log.info("PDF recibido: nombre=%s tamaño=%s bytes",
             msg.document.file_name, msg.document.file_size)
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    f = await msg.document.get_file()
    pdf_io = io.BytesIO()
    await f.download_to_memory(out=pdf_io)
    size = pdf_io.tell()
    pdf_io.seek(0)
    log.info("PDF descargado: %d bytes", size)

    try:
        # enviar al pool para no bloquear el loop
        images_data_urls = await asyncio.to_thread(
            pdf_to_image_data_urls,
            pdf_io.read(),
            PDF_DPI,
            MAX_PDF_PAGES
        )
        if not images_data_urls:
            log.warning("PDF sin páginas renderizadas")
            await msg.reply_text("No pude renderizar páginas del PDF.")
            return
        await analyze_and_reply(images_data_urls, msg, context)
    except Exception as e:
        log.exception("Fallo en PDF->imagen")
        await msg.reply_text(f"Error al convertir el PDF a imágenes: {e}")

# Llamada a Azure + respuesta JSON
async def analyze_and_reply(image_urls: List[str], msg, context):
    try:
        log.info("Preparando petición a Azure: %d imagen(es)", len(image_urls))
        content_parts = [{"type": "text", "text": "Extract all invoice fields following the response schema."}]
        for url in image_urls:
            content_parts.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})

        # === IMPORTANTE: temperature y max_completion_tokens removidos (no necesarios) ===
        completion = client.beta.chat.completions.parse(
            model=AZURE_OPENAI_DEPLOYMENT,
            # temperature=0,
            # max_completion_tokens=1500,
            response_format=Invoice,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content_parts},
            ],
        )
        log.info("Respuesta recibida de Azure")

        parsed = completion.choices[0].message.parsed
        # pydantic v2: sin ensure_ascii
        json_text = parsed.model_dump_json(indent=2)

        # Log de campos clave sin volcar todo el JSON en logs
        log.info(
            "Parseo OK | is_invoice=%s | invoice_number=%s | total_amount=%s",
            parsed.is_invoice,
            getattr(parsed, "invoice_number", None),
            getattr(parsed, "total_amount", None),
        )

        await msg.reply_text(f"```json\n{json_text}\n```", parse_mode="Markdown")
    except Exception as e:
        log.exception("Azure OpenAI error")
        await msg.reply_text(f"Sorry, I couldn't parse the invoice.\nError: {e}")

def main():
    log.info(
        "Iniciando bot | Endpoint=%s | Deployment=%s | APIv=%s | MAX_PDF_PAGES=%d | PDF_DPI=%d | JPEG_Q=%d",
        AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION,
        MAX_PDF_PAGES, PDF_DPI, PDF_JPEG_QUALITY
    )
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.MimeType("application/pdf"), handle_pdf_document))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_image_document))
    log.info("Bot corriendo. Presioná Ctrl+C para salir.")
    app.run_polling()

if __name__ == "__main__":
    main()
