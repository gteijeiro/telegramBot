## Telegram Invoice Extractor Bot (Azure OpenAI)

This bot listens to a Telegram chat, accepts invoice images, and extracts structured invoice data using Azure OpenAI (vision). It replies with a compact JSON where all property names are in English.

### Prerequisites
- Python 3.10+
- A Telegram Bot Token (create via BotFather)
- An Azure OpenAI resource with a vision-capable model deployment (e.g., `gpt-4o`)

### Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure environment variables. Create a `.env` file based on `.env.example`:
   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC...
   AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
   AZURE_OPENAI_API_KEY=...your key...
   AZURE_OPENAI_API_VERSION=2024-08-01-preview
   AZURE_OPENAI_DEPLOYMENT=gpt-4o
   DEFAULT_CURRENCY=USD
   ```

### Run
```bash
python bot.py
```

Send a clear invoice photo to the bot (or an image document). Optionally send a text message first with hints (e.g., “default currency EUR”). The bot will respond with JSON like:

```json
{
  "vendor_name": "ACME Corp",
  "vendor_tax_id": "US123456789",
  "invoice_number": "INV-2024-001",
  "invoice_date": "2024-08-31",
  "due_date": "2024-09-30",
  "currency": "USD",
  "subtotal_amount": 100.0,
  "tax_amount": 21.0,
  "total_amount": 121.0,
  "purchase_order_number": null,
  "payment_terms": "Net 30",
  "bill_to": {"name": "Contoso LLC", "address": "...", "tax_id": null},
  "ship_to": {"name": null, "address": null, "tax_id": null},
  "line_items": [
    {"description": "Widgets", "sku": "W-01", "quantity": 10, "unit_price": 10.0, "total": 100.0}
  ],
  "notes": null
}
```

### Notes
- The bot forces JSON output and tries to keep it compact to fit Telegram message limits.
- If fields are missing on the invoice, they will be `null`.
- If your invoices are multi-page, send the most relevant page or one image at a time.
- For production, consider using webhooks instead of polling.