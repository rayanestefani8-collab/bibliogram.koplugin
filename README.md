# BiblioGram — KOReader + Telegram OPDS Integration

KOReader plugin that manages a private OPDS server powered by a Telegram channel.

## What is it?

**BiblioGram** is a complete solution for:
- Maintaining a **private book library** in a Telegram channel
- **Serving as an OPDS catalog** for KOReader automatically
- **Managing metadata** (titles, authors, covers, tags, genres)
- **Synchronizing** new books and updates on a schedule

It works in two components:

1. **`telegram-opds`** (Python, notebook) — Collects books from Telegram and serves the OPDS catalog
2. **`BiblioGram`** (Lua plugin, Kobo) — KOReader interface to configure everything

## Requirements

### Notebook (server)
- Python 3.8+
- Libraries: `telethon`, `flask`, `python-dotenv`, `requests`
- Internet connection
- A Telegram account with API access

### Kobo and Kindle
- KOReader installed
- `opds.koplugin` or `opds_plus.koplugin` plugin
- Local Wi‑Fi network shared with the notebook

## Installation

### 1. Configure the server (notebook)

```bash
cd ~/telegram-opds

# Install dependencies
pip install telethon flask python-dotenv requests

# Create .env file with Telegram credentials
cat > .env << 'EOF'
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
TG_PHONE=+55999999999
TG_BOT_TOKEN=  # optional, leave empty if using phone login
EOF
```

**How to obtain Telegram credentials:**
1. Go to https://my.telegram.org/apps
2. Create a new application
3. Copy **API_ID** and **API_HASH**
4. Use the phone number configured in Telegram

### 2. Collect books from the channel

```bash
# First synchronization (may take time)
python collector.py --channel @your_private_channel

# Future synchronizations (incremental)
python collector.py --channel @your_private_channel --sync

# Clean messy titles and download covers
python collector.py --fix-titles
python collector.py --update-covers

# View statistics
python collector.py --list
```

### 3. Install the plugin on Kobo/kindle

```bash
scp -P 2222 main.lua _meta.lua root@KOBO_IP:/mnt/onboard/.adds/koreader/plugins/bibliogram.koplugin/
```

### 4. Start the server

```bash
# Manually
python sync_daemon.py

# Or as a systemd service (persistent)
sudo cp telegram-opds.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-opds
```

## Usage

### On KOReader (Kobo, kindle)

1. **Main Menu** → **BiblioGram**
   - **Add to OPDS** → Injects the server into the OPDS list
   - **Settings** → Edit URL, folder, synchronization, credentials
   - **About** → Plugin information

2. **Open your OPDS reader** (native or OPDSPlus)
   - See "BiblioGram" in the server list
   - Browse categories: All, EPUB, CBR, Genre, Author, Recent
   - Download books directly to Kobo

### Available settings

**In the KOReader plugin:**

| Option | Default | Description |
|-------|--------|-----------|
| Server URL | `http://192.168.1.100:8081` | Notebook IP:port (adjust to your network) |
| Download folder | `/mnt/onboard/BiblioGram` | Where books are saved |
| Automatic sync | OFF | Synchronize periodically |
| Frequency (min) | 15 | Synchronize every N minutes |
| Update covers | ON | Automatically fetch covers |
| Clean titles | ON | Remove title clutter |
| API_ID | — | Telegram credential |
| API_HASH | — | Telegram credential |
| Phone | — | Telegram phone number |

## How to use the Telegram channel

### Post format

Post a **file** (EPUB, PDF, CBR) followed by a **message with metadata**:

```
Book Title
Book Author

Book description or synopsis.

#genre1 #genre2 #tag
```

### Example

```
O Cortiço
Aluísio Azevedo

A naturalist novel portraying life in a tenement
in Rio de Janeiro during the 19th century.

#Novel #Classic #Literature #Brazil
```

The collector.py script will:
- ✅ Extract title, author, description, tags
- ✅ Automatically separate "Title - Author"
- ✅ Remove markdown, emojis, and invalid characters
- ✅ Fetch covers from Open Library or Google Books
- ✅ Index by format, genre, and author
- ✅ Create a navigable OPDS catalog

## Telegram Credentials Configuration

**⚠️ IMPORTANT: Security**

Never share your Telegram credentials:
- Do not include them in Git commits
- Do not send them via email or messages
- Do not publish them on forums or social networks

### Option 1: Credentials on the notebook (.env) — RECOMMENDED

```bash
# ~/.env (secure on the notebook, not stored on Kobo)
TG_API_ID=your_id
TG_API_HASH=your_hash
TG_PHONE=+55999999999
```

Leave these fields blank in KOReader. `collector.py` will synchronize automatically.

### Option 2: Credentials on KOReader

If you need to configure them through the plugin:
- Use a Telegram bot (less risky) instead of a personal account
- Keep the Kobo in a secure location
- If you lose the Kobo, revoke the session at my.telegram.org

---

## Troubleshooting

### "Server unreachable"
```bash
# Check connectivity (replace NOTEBOOK_IP and KOBO_IP with your values)
ssh -p 2222 root@KOBO_IP "ping -c 2 NOTEBOOK_IP"
```

### "No books in the catalog"
```bash
# Check the database
python collector.py --list
```

### Books with messy titles
```bash
python collector.py --fix-titles
```

### Covers do not appear
```bash
python collector.py --update-covers
```

## Project files

```
~/telegram-opds/
├── collector.py         # Collects books from Telegram
├── sync_daemon.py       # Automatic synchronization
├── server.py            # Flask OPDS server (port 8081)
├── webapp.py            # Web interface (port 8082)
├── .env                 # Credentials (confidential!)
├── data/books.db        # SQLite database
└── cache/               # Downloaded covers
```

KOReader plugin:
```
/mnt/onboard/.adds/koreader/plugins/bibliogram.koplugin/
├── main.lua
├── _meta.lua
└── l10n/                # Translations (optional)
```

## OPDS API

```
GET /opds                 → Root catalog
GET /opds/all             → All books
GET /opds/format/epub     → EPUB only
GET /opds/format/cbr      → CBR only
GET /opds/genres          → By genre
GET /opds/authors         → By author
GET /opds/recent          → Recent books
```

## Performance

- **First collection:** 5–30 min (depends on number of books)
- **Synchronizations:** 30 sec – 2 min
- **Covers:** 1–3 min per 100 books
- **Native OPDS:** instant navigation

## Future improvement ideas

- [ ] Cover cache 
- [ ] Full-text search in OPDS
- [ ] Wishlist/bookmarks

## License

MIT.

## Credits

Developed with assistance from [Claude](https://claude.ai) (Anthropic).

---

**BiblioGram** — Your Telegram library on Kobo,kindle without intermediaries.
