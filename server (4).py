"""
Telegram → OPDS Catalog Server
Serve livros de um canal do Telegram como catálogo OPDS para o KOReader.
"""

from dotenv import load_dotenv
load_dotenv()
from flask import Flask, Response, request, abort, jsonify
from datetime import datetime, timezone
import sqlite3, os, hashlib, requests, logging, re, urllib.parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DB_PATH        = os.path.join(BASE_DIR, "data", "books.db")
CACHE_DIR      = os.path.join(BASE_DIR, "cache")
CATALOG_TITLE  = os.getenv("CATALOG_TITLE", "Biblioteca Telegram")
CATALOG_AUTHOR = os.getenv("CATALOG_AUTHOR", "telegram-opds")
BASE_URL       = os.getenv("BASE_URL", "http://localhost:8081")
PAGE_SIZE      = int(os.getenv("PAGE_SIZE", "20"))

MIME_MAP = {
    ".epub": "application/epub+zip",
    ".pdf":  "application/pdf",
    ".mobi": "application/x-mobipocket-ebook",
    ".fb2":  "application/fb2+zip",
    ".djvu": "image/vnd.djvu",
    ".cbz":  "application/vnd.comicbook+zip",
    ".cbr":  "application/vnd.comicbook-rar",
    ".txt":  "text/plain",
}

# ── Banco de dados ────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id          INTEGER PRIMARY KEY,
                msg_id      INTEGER UNIQUE,
                title       TEXT NOT NULL,
                author      TEXT,
                description TEXT,
                filename    TEXT NOT NULL,
                ext         TEXT NOT NULL,
                size        INTEGER,
                date        TEXT,
                cover_url   TEXT,
                channel     TEXT,
                tags        TEXT,
                cached_path TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ext    ON books(ext)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date   ON books(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags   ON books(tags)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_author ON books(author)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_title  ON books(title)")
    log.info("Banco de dados inicializado: %s", DB_PATH)


# ── Helpers de capa ───────────────────────────────────────────────────────────
def cover_urls(raw_url: str) -> tuple:
    """
    Retorna (url_grande, url_miniatura) a partir da URL armazenada.
    Open Library: troca sufixo -M/-S por -L/-S
    Google Books: usa thumbnail como miniatura, thumbnail?zoom=1 como grande
    Outros: usa a mesma URL para os dois.
    """
    if not raw_url:
        return "", ""

    # Open Library: covers.openlibrary.org/b/id/XXXXX-M.jpg
    m = re.match(r"(https://covers\.openlibrary\.org/b/id/\d+)-[A-Z]\.jpg", raw_url)
    if m:
        base = m.group(1)
        return f"{base}-L.jpg", f"{base}-S.jpg"

    # Google Books: tem thumbnail na URL
    if "books.google" in raw_url or "googleapis.com" in raw_url:
        large = raw_url.replace("zoom=1", "zoom=0").replace("&edge=curl", "")
        return large, raw_url

    return raw_url, raw_url


# ── Helpers XML ───────────────────────────────────────────────────────────────
def xml_escape(s: str) -> str:
    if not s:
        return ""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def feed_header(feed_id: str, title: str, updated: str, kind: str = "acquisition") -> str:
    self_url = f"{BASE_URL}/opds{feed_id}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/terms/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:uuid:{hashlib.md5(feed_id.encode()).hexdigest()}</id>
  <title>{xml_escape(title)}</title>
  <updated>{updated}</updated>
  <author><name>{xml_escape(CATALOG_AUTHOR)}</name></author>
  <link rel="self"  type="application/atom+xml;profile=opds-catalog;kind={kind}" href="{self_url}"/>
  <link rel="start" type="application/atom+xml;profile=opds-catalog;kind=navigation" href="{BASE_URL}/opds"/>
  <link rel="search" type="application/opensearchdescription+xml" href="{BASE_URL}/opds/search-desc"/>
"""


def book_entry(book) -> str:
    ext  = book["ext"].lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")
    dl   = f"{BASE_URL}/opds/download/{book['id']}"
    date = book["date"] or now_iso()

    tags_xml = ""
    if book["tags"]:
        for tag in book["tags"].split(","):
            tag = tag.strip()
            if tag:
                tags_xml += f'  <category term="{xml_escape(tag)}" label="{xml_escape(tag)}"/>\n'

    # Capa grande + miniatura
    cover_large, cover_thumb = cover_urls(book["cover_url"])
    cover_xml = ""
    if cover_large:
        cover_xml += f'  <link type="image/jpeg" rel="http://opds-spec.org/image" href="{cover_large}"/>\n'
    if cover_thumb and cover_thumb != cover_large:
        cover_xml += f'  <link type="image/jpeg" rel="http://opds-spec.org/image/thumbnail" href="{cover_thumb}"/>\n'
    elif cover_large:
        cover_xml += f'  <link type="image/jpeg" rel="http://opds-spec.org/image/thumbnail" href="{cover_large}"/>\n'

    size_attr = f' length="{book["size"]}"' if book["size"] else ""

    return f"""<entry>
  <id>urn:telegram:{book['id']}</id>
  <title>{xml_escape(book['title'])}</title>
  <author><name>{xml_escape(book['author'] or 'Desconhecido')}</name></author>
  <updated>{date}</updated>
  <published>{date}</published>
  {f'<summary>{xml_escape(book["description"])}</summary>' if book["description"] else ""}
  {tags_xml}{cover_xml}  <dc:format>{mime}</dc:format>
  <link rel="http://opds-spec.org/acquisition/open-access"
        type="{mime}"
        href="{dl}"{size_attr}/>
</entry>
"""


# ── Rotas OPDS ────────────────────────────────────────────────────────────────

@app.route("/opds")
@app.route("/opds/")
def opds_root():
    """Navigation feed raiz — categorias do catálogo."""
    with get_db() as conn:
        total     = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        formats   = conn.execute("SELECT ext, COUNT(*) as n FROM books GROUP BY ext ORDER BY n DESC").fetchall()
        last_date = conn.execute("SELECT MAX(date) FROM books").fetchone()[0] or now_iso()
        all_tags  = conn.execute("SELECT tags FROM books WHERE tags IS NOT NULL AND tags != ''").fetchall()
        author_count = conn.execute("SELECT COUNT(DISTINCT author) FROM books WHERE author IS NOT NULL AND author != ''").fetchone()[0]

    updated = last_date or now_iso()
    xml = feed_header("/", CATALOG_TITLE, updated, kind="navigation")

    # Todos os livros
    xml += f"""<entry>
  <id>urn:all</id>
  <title>Todos os livros ({total})</title>
  <content type="text">Acervo completo do canal</content>
  <updated>{updated}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=acquisition" rel="subsection" href="{BASE_URL}/opds/all"/>
</entry>
"""
    # Por formato
    for row in formats:
        ext   = row["ext"].lower()
        count = row["n"]
        label = ext.lstrip(".")
        xml += f"""<entry>
  <id>urn:fmt:{ext}</id>
  <title>{label.upper()} ({count})</title>
  <content type="text">Livros no formato {label.upper()}</content>
  <updated>{updated}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=acquisition" rel="subsection" href="{BASE_URL}/opds/format/{label}"/>
</entry>
"""
    # Por gênero
    tag_count: dict = {}
    for row in all_tags:
        for t in row[0].split(","):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    if tag_count:
        xml += f"""<entry>
  <id>urn:genres</id>
  <title>Por gênero ({len(tag_count)})</title>
  <content type="text">Navegar por gênero literário</content>
  <updated>{updated}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=navigation" rel="subsection" href="{BASE_URL}/opds/genres"/>
</entry>
"""
    # Por autor
    if author_count:
        xml += f"""<entry>
  <id>urn:authors</id>
  <title>Por autor ({author_count})</title>
  <content type="text">Navegar por autor</content>
  <updated>{updated}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=navigation" rel="subsection" href="{BASE_URL}/opds/authors"/>
</entry>
"""
    # Recentes
    xml += f"""<entry>
  <id>urn:recent</id>
  <title>Adicionados recentemente</title>
  <content type="text">Últimos livros adicionados ao canal</content>
  <updated>{updated}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=acquisition" rel="subsection" href="{BASE_URL}/opds/recent"/>
</entry>
"""
    xml += "</feed>"
    return Response(xml, mimetype="application/atom+xml;profile=opds-catalog;kind=navigation")


def _book_feed(books, feed_path: str, title: str, page: int, total: int) -> Response:
    """Monta um Acquisition Feed paginado."""
    updated = now_iso()
    xml = feed_header(feed_path, title, updated)

    if total > PAGE_SIZE:
        last_page = (total - 1) // PAGE_SIZE
        if page > 0:
            xml += f'<link rel="previous" type="application/atom+xml;profile=opds-catalog;kind=acquisition" href="{BASE_URL}/opds{feed_path}?page={page-1}"/>\n'
        if page < last_page:
            xml += f'<link rel="next" type="application/atom+xml;profile=opds-catalog;kind=acquisition" href="{BASE_URL}/opds{feed_path}?page={page+1}"/>\n'

    for book in books:
        xml += book_entry(book)

    xml += "</feed>"
    return Response(xml, mimetype="application/atom+xml;profile=opds-catalog;kind=acquisition")


@app.route("/opds/all")
def opds_all():
    page = int(request.args.get("page", 0))
    sort = request.args.get("sort", "date")
    order = "title ASC" if sort == "title" else "date DESC"
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        books = conn.execute(
            f"SELECT * FROM books ORDER BY {order} LIMIT ? OFFSET ?",
            (PAGE_SIZE, page * PAGE_SIZE)
        ).fetchall()
    return _book_feed(books, f"/all?sort={sort}", f"Todos os livros — pág. {page+1}", page, total)


@app.route("/opds/genres")
def opds_genres():
    with get_db() as conn:
        all_tags  = conn.execute("SELECT tags FROM books WHERE tags IS NOT NULL AND tags != ''").fetchall()
        last_date = conn.execute("SELECT MAX(date) FROM books").fetchone()[0] or now_iso()

    tag_count: dict = {}
    for row in all_tags:
        for t in row[0].split(","):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1

    xml = feed_header("/genres", f"Gêneros — {CATALOG_TITLE}", last_date, kind="navigation")
    for tag, count in sorted(tag_count.items(), key=lambda x: -x[1]):
        xml += f"""<entry>
  <id>urn:genre:{xml_escape(tag)}</id>
  <title>{xml_escape(tag.capitalize())} ({count})</title>
  <content type="text">Livros de {xml_escape(tag)}</content>
  <updated>{last_date}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=acquisition" rel="subsection" href="{BASE_URL}/opds/genre/{urllib.parse.quote(tag)}"/>
</entry>
"""
    xml += "</feed>"
    return Response(xml, mimetype="application/atom+xml;profile=opds-catalog;kind=navigation")


@app.route("/opds/genre/<tag>")
def opds_genre(tag):
    page = int(request.args.get("page", 0))
    like = f"%{tag}%"
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books WHERE tags LIKE ?", (like,)).fetchone()[0]
        books = conn.execute(
            "SELECT * FROM books WHERE tags LIKE ? ORDER BY title ASC LIMIT ? OFFSET ?",
            (like, PAGE_SIZE, page * PAGE_SIZE)
        ).fetchall()
    return _book_feed(books, f"/genre/{tag}", f"{tag.capitalize()} — pág. {page+1}", page, total)


@app.route("/opds/authors")
def opds_authors():
    """Navigation feed com lista de autores ordenada alfabeticamente."""
    with get_db() as conn:
        authors   = conn.execute("""
            SELECT author, COUNT(*) as n FROM books
            WHERE author IS NOT NULL AND author != ''
            GROUP BY author ORDER BY author ASC
        """).fetchall()
        last_date = conn.execute("SELECT MAX(date) FROM books").fetchone()[0] or now_iso()

    xml = feed_header("/authors", f"Autores — {CATALOG_TITLE}", last_date, kind="navigation")
    for row in authors:
        author = row["author"]
        count  = row["n"]
        xml += f"""<entry>
  <id>urn:author:{xml_escape(author)}</id>
  <title>{xml_escape(author)} ({count})</title>
  <content type="text">Livros de {xml_escape(author)}</content>
  <updated>{last_date}</updated>
  <link type="application/atom+xml;profile=opds-catalog;kind=acquisition" rel="subsection" href="{BASE_URL}/opds/author/{urllib.parse.quote(author)}"/>
</entry>
"""
    xml += "</feed>"
    return Response(xml, mimetype="application/atom+xml;profile=opds-catalog;kind=navigation")


@app.route("/opds/author/<path:author>")
def opds_author(author):
    """Acquisition feed com todos os livros de um autor."""
    page = int(request.args.get("page", 0))
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books WHERE author=?", (author,)).fetchone()[0]
        books = conn.execute(
            "SELECT * FROM books WHERE author=? ORDER BY title ASC LIMIT ? OFFSET ?",
            (author, PAGE_SIZE, page * PAGE_SIZE)
        ).fetchall()
    return _book_feed(books, f"/author/{urllib.parse.quote(author)}", f"{author} — pág. {page+1}", page, total)


@app.route("/opds/format/<fmt>")
def opds_format(fmt):
    ext  = f".{fmt.lower()}"
    page = int(request.args.get("page", 0))
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books WHERE ext=?", (ext,)).fetchone()[0]
        books = conn.execute(
            "SELECT * FROM books WHERE ext=? ORDER BY title ASC LIMIT ? OFFSET ?",
            (ext, PAGE_SIZE, page * PAGE_SIZE)
        ).fetchall()
    return _book_feed(books, f"/format/{fmt}", f"{fmt.upper()} — pág. {page+1}", page, total)


@app.route("/opds/recent")
def opds_recent():
    with get_db() as conn:
        books = conn.execute("SELECT * FROM books ORDER BY date DESC LIMIT 50").fetchall()
    return _book_feed(books, "/recent", "Adicionados recentemente", 0, len(books))


@app.route("/opds/search")
def opds_search():
    q    = request.args.get("q", "").strip()
    page = int(request.args.get("page", 0))
    if not q:
        return opds_root()
    like = f"%{q}%"
    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM books WHERE title LIKE ? OR author LIKE ? OR description LIKE ?",
            (like, like, like)
        ).fetchone()[0]
        books = conn.execute(
            "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? OR description LIKE ? ORDER BY title ASC LIMIT ? OFFSET ?",
            (like, like, like, PAGE_SIZE, page * PAGE_SIZE)
        ).fetchall()
    return _book_feed(books, f"/search?q={urllib.parse.quote(q)}", f'Busca: "{q}"', page, total)


@app.route("/opds/search-desc")
def search_desc():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>{xml_escape(CATALOG_TITLE)}</ShortName>
  <Description>Buscar livros no catálogo Telegram</Description>
  <Url type="application/atom+xml;profile=opds-catalog;kind=acquisition"
       template="{BASE_URL}/opds/search?q={{searchTerms}}&amp;page={{startPage?}}"/>
</OpenSearchDescription>"""
    return Response(xml, mimetype="application/opensearchdescription+xml")


@app.route("/opds/download/<int:book_id>")
def opds_download(book_id):
    with get_db() as conn:
        book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        abort(404)

    # Cache local
    if book["cached_path"] and os.path.exists(book["cached_path"]):
        log.info("Servindo do cache: %s", book["cached_path"])
        mime = MIME_MAP.get(book["ext"].lower(), "application/octet-stream")
        with open(book["cached_path"], "rb") as f:
            data = f.read()
        return Response(data, mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="{book["filename"]}"'})

    # Proxy via Telegram
    from collector import TelegramDownloader
    downloader = TelegramDownloader()
    try:
        file_bytes = downloader.download_file_sync(book["msg_id"], book["channel"])
        if not file_bytes:
            abort(503, "Não foi possível baixar do Telegram")

        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(CACHE_DIR, f"{book_id}{book['ext']}")
        with open(cache_path, "wb") as f:
            f.write(file_bytes)
        with get_db() as conn:
            conn.execute("UPDATE books SET cached_path=? WHERE id=?", (cache_path, book_id))

        mime = MIME_MAP.get(book["ext"].lower(), "application/octet-stream")
        return Response(file_bytes, mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="{book["filename"]}"'})
    except Exception as e:
        log.error("Erro ao baixar livro %d: %s", book_id, e)
        abort(503, str(e))


@app.route("/status")
def status():
    with get_db() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        formats = dict(conn.execute("SELECT ext, COUNT(*) FROM books GROUP BY ext").fetchall())
        cached  = conn.execute("SELECT COUNT(*) FROM books WHERE cached_path IS NOT NULL").fetchone()[0]
    return jsonify({
        "status": "ok",
        "total_books": total,
        "cached_books": cached,
        "formats": formats,
        "catalog_url": f"{BASE_URL}/opds"
    })


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 8081))
    log.info("Servidor OPDS rodando em %s", BASE_URL)
    log.info("Catálogo disponível em %s/opds", BASE_URL)
    app.run(host="0.0.0.0", port=port, debug=False)
