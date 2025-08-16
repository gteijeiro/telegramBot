# Invoice Telegram Bot (Python) — Extractor de datos de facturas con Azure OpenAI

Bot de **Telegram** en **Python** que recibe **fotos** o **PDFs** de facturas, convierte los PDFs a imágenes y extrae los datos principales usando **Azure OpenAI** (visión + Structured Outputs). Devuelve un **JSON** con propiedades **en inglés** (por ejemplo: `invoice_number`, `seller.name`, `total_amount`).

```
Telegram (imagen/PDF) → Bot (PDF→imágenes) → Azure OpenAI (visión) → JSON → Telegram
```

---

## Tabla de contenidos

* [Requisitos](#requisitos)
* [Instalación](#instalación)
* [Configuración (.env)](#configuración-env)
* [Ejecución](#ejecución)
* [Cómo usarlo](#cómo-usarlo)
* [Ejemplo de salida JSON](#ejemplo-de-salida-json)
* [Arquitectura](#arquitectura)
* [Variables de entorno (detalle)](#variables-de-entorno-detalle)
* [Logging](#logging)
* [Solución de problemas](#solución-de-problemas)
* [Rendimiento y costos](#rendimiento-y-costos)
* [Personalización](#personalización)
* [Estructura sugerida del proyecto](#estructura-sugerida-del-proyecto)
* [Licencia](#licencia)
* [Contribuir](#contribuir)

---

## Requisitos

* **Python 3.10 – 3.12**
* Cuenta en **Azure OpenAI** con un **deployment** compatible con visión (por ej. `gpt-4o`)
* Un **bot de Telegram** (token vía **@BotFather**)
* Sistema operativo: Windows, macOS o Linux

---

## Instalación

```bash
git clone <tu-repo>
cd <tu-repo>

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

**requirements.txt (referencia):**

```
python-telegram-bot==21.8
openai>=1.42.0
pydantic>=2.8.2
python-dotenv>=1.0.1
PyMuPDF>=1.24.10
```

---

## Configuración (.env)

Crea un archivo **.env** en la raíz del proyecto:

```
# Telegram
TELEGRAM_TOKEN=123456:ABC-DEF...

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<tu-recurso>.openai.azure.com
AZURE_OPENAI_API_KEY=<tu-api-key>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# Conversión PDF → imágenes (opcional)
MAX_PDF_PAGES=5
PDF_DPI=200
PDF_JPEG_QUALITY=85
```

> **Notas**
>
> * `AZURE_OPENAI_DEPLOYMENT` es el **nombre del deployment** que creaste en Azure (no el nombre genérico del modelo).
> * Ajusta `MAX_PDF_PAGES`, `PDF_DPI` y `PDF_JPEG_QUALITY` para balancear calidad / costo / tiempo.

---

## Ejecución

```bash
python bot.py
```

Salida esperada (logs):

```
YYYY-mm-dd HH:MM:SS | INFO | invoice-bot | Iniciando bot | Endpoint=... | Deployment=gpt-4o | APIv=2024-10-21 | MAX_PDF_PAGES=5 | PDF_DPI=200 | JPEG_Q=85
YYYY-mm-dd HH:MM:SS | INFO | invoice-bot | Bot corriendo. Presioná Ctrl+C para salir.
```

---

## Cómo usarlo

En Telegram, envía al bot:

* Una **foto** de la factura (como **Photo** o **Document** de imagen); o
* Un **PDF** con la factura: el bot lo convertirá a imágenes (hasta `MAX_PDF_PAGES`) y enviará **todas** las páginas al modelo en una sola solicitud.

El bot responderá con un bloque de **JSON** con las propiedades en **inglés**.

---

## Ejemplo de salida JSON

```json
{
  "is_invoice": true,
  "document_type": "invoice",
  "language": "es",
  "ocr_confidence": 0.95,

  "invoice_number": "A-0001-00001234",
  "issue_date": "2025-08-09",
  "due_date": "2025-08-24",
  "po_number": null,
  "currency": "ARS",

  "seller": {
    "name": "ACME S.A.",
    "tax_id": "30-12345678-9",
    "address": "Av. Siempre Viva 123, CABA"
  },
  "buyer": {
    "name": "Cliente SRL",
    "tax_id": "30-87654321-0",
    "address": "Calle Falsa 742, CABA"
  },

  "subtotal_amount": 100000.0,
  "total_tax_amount": 21000.0,
  "total_amount": 121000.0,

  "taxes": [
    { "name": "VAT", "amount": 21000.0, "rate": 0.21 }
  ],
  "line_items": [
    { "description": "Servicio X", "quantity": 1, "unit_price": 100000.0, "amount": 100000.0 }
  ],

  "payment_terms": "Pago a 15 días",
  "notes": null
}
```

> Si un dato no está presente en la factura, el bot devuelve `null`.
> El campo `is_invoice` puede ser `false` si el documento no es una factura.

---

## Arquitectura

```
┌───────────────────┐
│   Usuario (TG)    │
│   Imagen / PDF    │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐       Convierte PDF→imágenes (PyMuPDF)
│  Bot de Telegram  │ ──────────────────────────────────────► data URLs (JPEG)
│  (python-telegram-bot)  │
└─────────┬─────────┘
          │ mensajes con partes {text, image_url}
          ▼
┌─────────────────────────────────────────────────────────┐
│      Azure OpenAI (Chat Completions + Visión)          │
│   Structured Outputs (Pydantic/JSON Schema)            │
└─────────┬──────────────────────────────────────────────┘
          │ JSON validado
          ▼
┌───────────────────┐
│  Respuesta en TG  │
│  (bloque JSON)    │
└───────────────────┘
```

---

## Variables de entorno (detalle)

* `TELEGRAM_TOKEN`: token del bot de Telegram.
* `AZURE_OPENAI_ENDPOINT`: endpoint de tu recurso (por ej. `https://…azure.com`).
* `AZURE_OPENAI_API_KEY`: API key del recurso.
* `AZURE_OPENAI_API_VERSION`: versión de la API (ej. `2024-10-21`).
* `AZURE_OPENAI_DEPLOYMENT`: nombre del deployment (ej. `gpt-4o`).
* `MAX_PDF_PAGES`: máximo de páginas a procesar por PDF (predeterminado `5`).
* `PDF_DPI`: resolución al renderizar el PDF (predeterminado `200`).
* `PDF_JPEG_QUALITY`: calidad JPEG 1–100 (predeterminado `85`).

---

## Logging

* Formato de logs: `timestamp | nivel | logger | mensaje`.
* Mensajes clave:

  * Inicio de la app y parámetros efectivos.
  * Recepción/descarga de fotos y PDFs (tamaño, mime).
  * Progreso de PDF→imágenes (páginas renderizadas).
  * Llamada a Azure y parseo final (`is_invoice`, `invoice_number`, `total_amount`).
  * Errores con `traceback`.

Para ver más detalle:

```python
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
```

---

## Solución de problemas

* **No toma el `.env`**
  Asegúrate de tener `python-dotenv` y que se llama a `load_dotenv(find_dotenv(usecwd=True))`. Ejecuta el bot desde la raíz del proyecto o usa rutas absolutas.

* **Error `There is no current event loop in thread 'asyncio_0'`**
  No envuelvas `app.run_polling()` en `asyncio.run(...)` ni en `asyncio.to_thread(...)`. Invócalo **directamente** en `main()`.

* **`max_tokens` no soportado en Azure**
  Algunos despliegues no aceptan `max_tokens` y usan `max_completion_tokens`. En este proyecto están **comentados** (no son necesarios).

* **`BaseModel.model_dump_json(..., ensure_ascii=...)`**
  En **Pydantic v2** no existe `ensure_ascii` en `model_dump_json`. Se utiliza `model_dump_json(indent=2)`.

* **PDFs muy pesados / tiempo alto**
  Reduce `MAX_PDF_PAGES`, baja `PDF_DPI` o sube la compresión `PDF_JPEG_QUALITY`.

* **Rate limits o costos altos en Azure**
  Considera usar menos páginas, menor DPI, agrupar menos imágenes por solicitud, o revisar cuotas en Azure.

---

## Rendimiento y costos

* Más páginas y mayor DPI ⇒ más tokens ⇒ **mayor costo y latencia**.
* JPEG con menor calidad reduce tamaño sin afectar demasiado el OCR.
* Envío de múltiples imágenes en una sola llamada mejora coherencia entre páginas, pero aumenta tokens.

---

## Personalización

* **Guardar JSON en disco**: agrega escritura a archivo (por fecha/chat) y loguea la ruta.
* **Campos adicionales**: amplía el esquema Pydantic (`Invoice`) y ajusta el *system prompt*.
* **Álbum (media group)**: agrupa múltiples fotos en una sola llamada.
* **Exportar a CSV/Excel**: mapea `line_items` y genera archivos para descarga.
* **Validaciones locales**: normalización de CUIT/CUIL, reglas fiscales, etc.

---

## Estructura sugerida del proyecto

```
.
├─ bot.py
├─ requirements.txt
├─ .env.example
└─ README.md
```

**.env.example:**

```
TELEGRAM_TOKEN=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=gpt-4o
MAX_PDF_PAGES=5
PDF_DPI=200
PDF_JPEG_QUALITY=85
```

---

## Licencia

Este proyecto puede distribuirse bajo **MIT** (ajústalo según tu necesidad).

---

## Contribuir

¡PRs y sugerencias bienvenidas!
Ideas útiles:

* Agregar bases de datos o almacenamiento en la nube.
* Post-procesamiento de totales e IVA.
* Integración con ERPs o renombrado/ordenado de archivos según CUIT/razón social.
