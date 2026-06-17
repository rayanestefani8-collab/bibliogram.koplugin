"""
Coletor de livros de canais do Telegram.
Usa Telethon (MTProto) para acessar mensagens com documentos.

Uso:
  python collector.py --channel @meucanal --sync
  python collector.py --channel @meucanal --limit 500
  python collector.py --list
  python collector.py --update-covers        (busca capas dos livros sem capa)
  python collector.py --verify               (verifica quais livros ainda existem)
  python collector.py --verify --delete      (remove do banco os que foram apagados)
"""

import asyncio, os, re, sqlite3, logging, argparse, sys, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "data", "books.db")
CACHE_DIR  = os.path.join(BASE_DIR, "cache")
SESSION    = os.path.join(BASE_DIR, "data", "telegram.session")

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

API_ID    = int(os.getenv("TG_API_ID", "0"))
API_HASH  = os.getenv("TG_API_HASH", "")
PHONE     = os.getenv("TG_PHONE", "")
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")

EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".fb2", ".djvu", ".cbz", ".cbr", ".txt", ".azw3", ".lit"}

# ── Busca de capas ────────────────────────────────────────────────────────────

def clean_title_for_search(title: str) -> str:
    """
    Remove lixo comum em títulos de canais de HQ/ebook antes de buscar capa.
    Ex: "Bibliomania   KCC" → "Bibliomania"
        "Rei Spawn 051 (2025) (GdG-SQ)" → "Rei Spawn"
        "Batman #45 (2024) (Leitor)" → "Batman"
    """
    t = title

    # Remove conteúdo entre parênteses e colchetes: (2025), [scan], (GdG-SQ)
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\[.*?\]", "", t)

    # Remove grupos de scan e conversores conhecidos
    # (palavras em maiúsculas isoladas como KCC, HQ, CBR, GdG, SQ, PT-BR)
    t = re.sub(r"\b[A-Z]{2,6}(-[A-Z]{2,4})?\b", "", t)

    # Remove numeração de edição: #45, 051, vol.3, v3, n.12
    t = re.sub(r"\b(?:v(?:ol)?\.?\s*\d+|n\.?\s*\d+|#\d+|\d{2,3})\b", "", t, flags=re.IGNORECASE)

    # Remove # solto que sobrou
    t = re.sub(r"#\s*\w*", "", t)

    # Remove "Volume" / "Vol" solto sem número
    t = re.sub(r"\b(?:volume|vol)\b", "", t, flags=re.IGNORECASE)

    # Remove asteriscos e marcações de markdown
    t = re.sub(r"[*_~`]", "", t)

    # Remove múltiplos espaços
    t = re.sub(r"\s+", " ", t).strip()

    # Remove pontuação solta no final
    t = re.sub(r"[\s\-_,;:]+$", "", t).strip()

    log.debug("Título limpo: '%s' → '%s'", title, t)
    return t or title  # fallback para o original se ficou vazio


def fetch_cover_url(title: str, author: str = "") -> str:
    """Busca capa limpando o título antes de consultar as APIs."""
    import requests as req

    clean = clean_title_for_search(title)

    def _openlibrary(q: str) -> str:
        try:
            r = req.get(
                f"https://openlibrary.org/search.json?q={urllib.parse.quote(q)}&limit=1&fields=cover_i,title",
                timeout=6
            )
            docs = r.json().get("docs", [])
            if docs and docs[0].get("cover_i"):
                return f"https://covers.openlibrary.org/b/id/{docs[0]['cover_i']}-M.jpg"
        except Exception as e:
            log.debug("Open Library falhou para '%s': %s", q, e)
        return ""

    def _googlebooks(q: str) -> str:
        try:
            r = req.get(
                f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(q)}&maxResults=1",
                timeout=6
            )
            items = r.json().get("items", [])
            if items:
                img = items[0].get("volumeInfo", {}).get("imageLinks", {})
                url = img.get("thumbnail") or img.get("smallThumbnail")
                if url:
                    return url.replace("http://", "https://")
        except Exception as e:
            log.debug("Google Books falhou para '%s': %s", q, e)
        return ""

    # 1. Tenta com título limpo + autor
    q = f"{clean} {author}".strip()
    url = _openlibrary(q) or _googlebooks(q)
    if url:
        return url

    # 2. Tenta só com título limpo (sem autor)
    if author:
        url = _openlibrary(clean) or _googlebooks(clean)
        if url:
            return url

    # 3. Tenta com título original (sem limpeza) como último recurso
    if clean != title:
        url = _openlibrary(title) or _googlebooks(title)
        if url:
            return url

    return ""


# ── Extração de metadados ─────────────────────────────────────────────────────

def extract_metadata(text: str, filename: str) -> dict:
    meta = {"title": "", "author": "", "description": "", "tags": ""}

    if not text:
        stem = Path(filename).stem
        meta["title"] = re.sub(r"[-_]", " ", stem).strip()
        return meta

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    clean_lines = [re.sub(r"^[^\w«»\"\']+", "", l) for l in lines]

    # Se todas as linhas são só hashtags, usa nome do arquivo como título
    all_hashtags = all(re.match(r"^(#\w+\s*)+$", l) for l in lines)
    if all_hashtags:
        stem = Path(filename).stem
        meta["title"] = re.sub(r"[-_]", " ", stem).strip()
        tags = re.findall(r"#(\w+)", text)
        meta["tags"] = ",".join(tags[:10])
        return meta

    if clean_lines:
        meta["title"] = clean_lines[0][:200]

    author_patterns = [
        r"(?:autor|author|by|автор)[:\s]+(.+)",
        r"(?:de|por)\s+([A-ZÀ-Ü][^\n]+)",
    ]
    for line in lines[1:4]:
        for pat in author_patterns:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                meta["author"] = m.group(1).strip()[:150]
                break
        if meta["author"]:
            break

    if not meta["author"] and len(clean_lines) > 1:
        second = clean_lines[1]
        if len(second) < 80 and second.count(".") < 2:
            meta["author"] = second

    if len(lines) > 2:
        meta["description"] = " ".join(lines[2:8])[:500]

    tags = re.findall(r"#(\w+)", text)
    meta["tags"] = ",".join(tags[:10])

    if not meta["title"]:
        meta["title"] = Path(filename).stem[:200]

    return meta


# ── Banco de dados ────────────────────────────────────────────────────────────

def save_book(msg_id, meta, filename, ext, size, date, channel, cover_url="") -> bool:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        if conn.execute("SELECT id FROM books WHERE msg_id=?", (msg_id,)).fetchone():
            return False
        conn.execute("""
            INSERT INTO books (msg_id, title, author, description, filename, ext, size, date, channel, tags, cover_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg_id, meta["title"], meta.get("author", ""),
            meta.get("description", ""), filename, ext, size,
            date, channel, meta.get("tags", ""), cover_url,
        ))
    return True


def get_last_msg_id(channel: str) -> int:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT MAX(msg_id) FROM books WHERE channel=?", (channel,)
            ).fetchone()
            return row[0] or 0
    except Exception:
        return 0


def update_all_covers():
    """Busca capas para todos os livros que ainda não têm."""
    with sqlite3.connect(DB_PATH) as conn:
        books = conn.execute(
            "SELECT id, title, author FROM books WHERE cover_url IS NULL OR cover_url=''"
        ).fetchall()

    print(f"Buscando capas para {len(books)} livros...")
    updated = 0
    for book in books:
        book_id, title, author = book
        url = fetch_cover_url(title, author or "")
        if url:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE books SET cover_url=? WHERE id=?", (url, book_id))
            print(f"  ✓ {title[:60]}")
            updated += 1
        else:
            print(f"  ✗ {title[:60]} (não encontrada)")

    print(f"\nCapas encontradas: {updated}/{len(books)}")


# ── Coletor Telethon ──────────────────────────────────────────────────────────

class TelegramCollector:
    def __init__(self):
        try:
            from telethon import TelegramClient
        except ImportError:
            log.error("Telethon não instalado. Execute: pip install telethon")
            sys.exit(1)
        self.client = TelegramClient(SESSION, API_ID, API_HASH)

    async def collect(self, channel: str, limit: int = 0, sync_new: bool = False):
        async with self.client:
            if BOT_TOKEN:
                await self.client.start(bot_token=BOT_TOKEN)
            else:
                await self.client.start(phone=PHONE)

            log.info("Coletando canal: %s (sync=%s, limit=%s)", channel, sync_new, limit or "∞")

            min_id = get_last_msg_id(channel) if sync_new else 0
            count_new = 0

            kwargs = {"entity": channel, "reverse": False}
            if limit:
                kwargs["limit"] = limit
            if min_id:
                kwargs["min_id"] = min_id

            # Coleta todas as mensagens em memória para poder olhar a próxima
            all_msgs = await self.client.get_messages(channel, limit=limit or None, min_id=min_id or 0)

            for i, msg in enumerate(all_msgs):
                if not msg.document:
                    continue

                filename = None
                for attr in msg.document.attributes:
                    if hasattr(attr, "file_name"):
                        filename = attr.file_name
                        break

                if not filename:
                    continue

                ext = Path(filename).suffix.lower()
                if ext not in EBOOK_EXTENSIONS:
                    continue

                date_iso = msg.date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                # Texto da própria mensagem (caption)
                text = msg.text or msg.raw_text or ""

                # Lê a mensagem ANTERIOR na lista como possível fonte de tags
                # (get_messages retorna do mais novo para o mais antigo,
                #  então a "próxima" mensagem no canal é índice anterior na lista)
                extra_tags = ""
                if i > 0:
                    prev = all_msgs[i - 1]
                    if prev and not prev.document:
                        candidate = (prev.text or prev.raw_text or "").strip()
                        diff = abs((prev.date - msg.date).total_seconds())
                        if diff < 300 and candidate:
                            only_hashtags = bool(re.match(r"^(#\\w+\\s*)+$", candidate))
                            if only_hashtags:
                                # Só hashtags → adiciona como tags extras
                                extra_tags = candidate
                                log.debug("Tags extras da mensagem seguinte: %s", candidate)
                            elif not text:
                                # Texto completo e arquivo sem caption → usa como metadados
                                text = candidate
                                log.debug("Usando texto da mensagem seguinte para: %s", filename)

                meta = extract_metadata(text, filename)

                # Adiciona tags extras da mensagem seguinte
                if extra_tags:
                    extra = ",".join(re.findall(r"#(\\w+)", extra_tags))
                    if meta["tags"]:
                        meta["tags"] = meta["tags"] + "," + extra
                    else:
                        meta["tags"] = extra
                size = msg.document.size if msg.document else 0

                # Busca capa automaticamente
                cover_url = fetch_cover_url(meta["title"], meta.get("author", ""))
                if cover_url:
                    log.info("[capa] %s", meta["title"][:50])

                saved = save_book(
                    msg_id=msg.id, meta=meta, filename=filename,
                    ext=ext, size=size, date=date_iso,
                    channel=channel, cover_url=cover_url,
                )

                if saved:
                    count_new += 1
                    log.info("[+] %s — %s", meta["title"][:60], filename)

            log.info("Coleta concluída: %d novos livros", count_new)
            return count_new

    async def verify(self, channel: str, delete: bool = False):
        """
        Verifica se cada livro do banco ainda existe no canal.
        Livros deletados do Telegram retornam mensagem vazia ou sem documento.
        delete=True: remove do banco os livros apagados.
        """
        async with self.client:
            if BOT_TOKEN:
                await self.client.start(bot_token=BOT_TOKEN)
            else:
                await self.client.start(phone=PHONE)

            with sqlite3.connect(DB_PATH) as conn:
                books = conn.execute(
                    "SELECT id, msg_id, title, filename FROM books WHERE channel=?",
                    (channel,)
                ).fetchall()

            if not books:
                log.info("Nenhum livro encontrado no banco para o canal %s", channel)
                return

            log.info("Verificando %d livros do canal %s...", len(books), channel)

            ok = 0
            ausentes = []
            erros = []

            # Busca todas as mensagens em lotes de 100 (limite do Telegram)
            total = len(books)
            for i in range(0, total, 100):
                lote = books[i:i+100]
                ids  = [b[1] for b in lote]  # msg_ids

                try:
                    msgs = await self.client.get_messages(channel, ids=ids)
                except Exception as e:
                    log.warning("Erro ao buscar lote %d-%d: %s", i, i+len(lote), e)
                    erros.extend(lote)
                    continue

                # get_messages retorna na mesma ordem dos ids
                for book, msg in zip(lote, msgs):
                    book_id, msg_id, title, filename = book

                    if msg is None or not hasattr(msg, "document") or not msg.document:
                        # Mensagem deletada ou sem documento
                        ausentes.append(book)
                        log.warning("  ✗ AUSENTE  msg_id=%-8d  %s", msg_id, title[:55])
                    else:
                        ok += 1
                        log.info("  ✓ ok       msg_id=%-8d  %s", msg_id, title[:55])

                # Pequena pausa para evitar flood
                await asyncio.sleep(0.3)

            # Relatório
            print(f"\n{'─'*55}")
            print(f"  Total verificado : {total}")
            print(f"  Ainda existem    : {ok}")
            print(f"  Ausentes/deletados: {len(ausentes)}")
            print(f"  Erros de rede    : {len(erros)}")
            print(f"{'─'*55}")

            if ausentes:
                print("\nLivros ausentes no Telegram:")
                for b in ausentes:
                    print(f"  msg_id={b[1]}  {b[3]}")

            if delete and ausentes:
                ids_deletar = [b[0] for b in ausentes]
                placeholders = ",".join("?" * len(ids_deletar))
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        f"DELETE FROM books WHERE id IN ({placeholders})", ids_deletar
                    )
                    # Remove cache local se existir
                    for b in ausentes:
                        row = conn.execute(
                            "SELECT cached_path FROM books WHERE id=?", (b[0],)
                        ).fetchone()
                        if row and row[0] and os.path.exists(row[0]):
                            os.remove(row[0])
                            log.info("Cache removido: %s", row[0])

                print(f"\n{len(ausentes)} livro(s) removido(s) do banco.")
            elif ausentes and not delete:
                print("\nPara remover os ausentes do banco rode com --delete:")
                print(f"  python collector.py --verify --delete --channel {channel}")

            return {"ok": ok, "ausentes": len(ausentes), "erros": len(erros)}

    async def download_file(self, msg_id: int, channel: str):
        async with self.client:
            if BOT_TOKEN:
                await self.client.start(bot_token=BOT_TOKEN)
            else:
                await self.client.start(phone=PHONE)
            msg = await self.client.get_messages(channel, ids=msg_id)
            if not msg or not msg.document:
                return None
            log.info("Baixando msg_id=%d do canal %s...", msg_id, channel)
            return await self.client.download_media(msg, file=bytes)


class TelegramDownloader:
    def download_file_sync(self, msg_id: int, channel: str):
        collector = TelegramCollector()
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(collector.download_file(msg_id, channel))
        finally:
            loop.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(description="Coletor Telegram → OPDS")
    parser.add_argument("--channel", "-c", help="Canal (@username ou link)")
    parser.add_argument("--sync",    "-s", action="store_true")
    parser.add_argument("--limit",   "-l", type=int, default=0)
    parser.add_argument("--list",    action="store_true")
    parser.add_argument("--clear-cache", action="store_true")
    parser.add_argument("--verify",       action="store_true",
                        help="Verifica quais livros ainda existem no Telegram")
    parser.add_argument("--delete",       action="store_true",
                        help="Usado com --verify: remove do banco os livros ausentes")
    parser.add_argument("--fix-titles",    action="store_true",
                        help="Limpa títulos sujos e separa autor colado")
    parser.add_argument("--update-covers", action="store_true",
                        help="Busca capas para livros sem capa")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.list:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT channel, COUNT(*) as n, MAX(date) as last FROM books GROUP BY channel"
            ).fetchall()
        if not rows:
            print("Nenhum canal coletado ainda.")
        else:
            print(f"{'Canal':<30} {'Livros':>8}  {'Último'}")
            print("-" * 55)
            for r in rows:
                print(f"{r[0]:<30} {r[1]:>8}  {r[2] or '-'}")
        return

    if args.clear_cache:
        import shutil
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE books SET cached_path=NULL")
        print("Cache limpo.")
        return

    if args.verify:
        if not args.channel:
            print("ERRO: --verify requer --channel")
            sys.exit(1)
        if not API_ID or not API_HASH:
            print("ERRO: Configure TG_API_ID e TG_API_HASH no arquivo .env")
            sys.exit(1)
        collector = TelegramCollector()
        asyncio.run(collector.verify(channel=args.channel, delete=args.delete))
        return

    if args.fix_titles:
        fix_titles()
        return

    if args.update_covers:
        update_all_covers()
        return

    if not args.channel:
        parser.print_help()
        sys.exit(1)

    if not API_ID or not API_HASH:
        print("ERRO: Configure TG_API_ID e TG_API_HASH no arquivo .env")
        sys.exit(1)

    os.makedirs(BASE_DIR + "/data", exist_ok=True)
    collector = TelegramCollector()
    asyncio.run(collector.collect(
        channel=args.channel,
        limit=args.limit,
        sync_new=args.sync,
    ))


if __name__ == "__main__":
    cli()


def fix_titles():
    """
    Limpa títulos sujos no banco:
    - Separa 'Título   Autor' em título e autor
    - Remove lixo como (2025), [KCC], grupos de scan
    - Remove entradas sem título válido (só hashtags, só números etc)
    """
    import sqlite3 as _sql

    with _sql.connect(DB_PATH) as conn:
        books = conn.execute("SELECT id, title, author FROM books").fetchall()

    print(f"Verificando {len(books)} livros...")
    fixed = 0
    deleted = 0

    for book_id, title, author in books:
        new_title = title
        new_author = author or ""

        # Remove entradas lixo (só hashtags ou só números)
        if re.match(r"^(#\w+\s*)+$", title.strip()) or re.match(r"^\d+$", title.strip()):
            with _sql.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM books WHERE id=?", (book_id,))
            print(f"  ✗ Removido: '{title}'")
            deleted += 1
            continue

        # Separa título e autor colados por 2+ espaços: "Título   Autor"
        if not new_author and "   " in new_title:
            parts = re.split(r"\s{2,}", new_title, maxsplit=1)
            if len(parts) == 2 and len(parts[1]) < 80:
                new_title  = parts[0].strip()
                new_author = parts[1].strip()

        # Limpa título com clean_title_for_search
        cleaned = clean_title_for_search(new_title)
        if cleaned and cleaned != new_title:
            new_title = cleaned

        # Atualiza se mudou algo
        if new_title != title or new_author != (author or ""):
            with _sql.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE books SET title=?, author=? WHERE id=?",
                    (new_title, new_author, book_id)
                )
            print(f"  ✓ id={book_id}: '{title}' → '{new_title}' | autor: '{new_author}'")
            fixed += 1

    print(f"\nCorrigidos: {fixed} | Removidos: {deleted} | Sem alteração: {len(books)-fixed-deleted}")
