"""
Interface web para gerenciar o catálogo OPDS do BiblioGram.
Roda na porta 8082 (separado do servidor OPDS na 8081).

Uso:
  python webapp.py
  Acesse: http://SEU_IP:8082
"""

PAGE_SIZE = 20
from flask import Flask, render_template_string, request, jsonify, redirect, send_file
import sqlite3, os, subprocess, sys, json, logging, ast
from datetime import datetime

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data", "books.db")

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_stats():
    with get_db() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        formats = dict(conn.execute("SELECT ext, COUNT(*) FROM books GROUP BY ext").fetchall())
        no_cover= conn.execute("SELECT COUNT(*) FROM books WHERE cover_url IS NULL OR cover_url=''").fetchone()[0]
        no_author=conn.execute("SELECT COUNT(*) FROM books WHERE author IS NULL OR author=''").fetchone()[0]
        channels= conn.execute("SELECT DISTINCT channel FROM books").fetchall()
    return {
        "total": total,
        "formats": formats,
        "no_cover": no_cover,
        "no_author": no_author,
        "channels": [r[0] for r in channels],
    }

# ── HTML template ─────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, viewport-fit=cover">
<title>BiblioGram</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:       #0f0e11;
    --surface:  #18161c;
    --border:   #2a2730;
    --accent:   #a78bfa;
    --accent2:  #34d399;
    --danger:   #f87171;
    --text:     #e8e3f0;
    --muted:    #6b6478;
    --card:     #1c1a22;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif; min-height: 100vh; padding-bottom: 60px; }

  /* grain overlay */
  body::before {
    content: ''; position: fixed; inset: 0; pointer-events: none;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
    z-index: 999; opacity: 0.35;
  }

  header {
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 1rem;
    position: sticky; top: 0; background: var(--bg); z-index: 50;
  }
  .logo { font-family: 'Playfair Display', serif; font-size: 1.3rem; color: var(--accent); letter-spacing: -0.02em; white-space: nowrap; }
  .logo span { color: var(--accent2); }
  
  nav { margin-left: auto; display: flex; gap: 0.3rem; }
  nav button {
    background: transparent; border: 1px solid var(--border); color: var(--muted);
    padding: 0.35rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.75rem;
    font-family: 'DM Sans', sans-serif; transition: all 0.15s; white-space: nowrap;
  }
  nav button:hover, nav button.active { border-color: var(--accent); color: var(--accent); background: rgba(167,139,250,0.08); }

  main { padding: 1rem 1.25rem; max-width: 1200px; margin: 0 auto; }

  /* Stats */
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 0.75rem; margin-bottom: 1.5rem; }
  .stat {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem; position: relative; overflow: hidden;
  }
  .stat::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--accent); }
  .stat.green::before { background: var(--accent2); }
  .stat.red::before { background: var(--danger); }
  .stat-val { font-family: 'Playfair Display', serif; font-size: 1.5rem; color: var(--text); }
  .stat-label { font-size: 0.7rem; color: var(--muted); margin-top: 0.25rem; text-transform: uppercase; letter-spacing: 0.08em; }

  /* Toolbar */
  .toolbar { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center; }
  .search-box {
    flex: 1; min-width: 200px; background: var(--surface); border: 1px solid var(--border);
    color: var(--text); padding: 0.5rem 0.8rem; border-radius: 8px; font-size: 0.85rem;
    font-family: 'DM Sans', sans-serif; outline: none; transition: border 0.15s;
  }
  .search-box:focus { border-color: var(--accent); }
  .btn {
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 0.5rem 1rem; border-radius: 8px; cursor: pointer; font-size: 0.8rem;
    font-family: 'DM Sans', sans-serif; transition: all 0.15s; white-space: nowrap;
  }
  .btn:hover { border-color: var(--accent); color: var(--accent); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #0f0e11; font-weight: 500; }
  .btn.primary:hover { background: #c4b5fd; border-color: #c4b5fd; }
  .btn.success { background: var(--accent2); border-color: var(--accent2); color: #0f0e11; font-weight: 500; }
  .btn.danger { border-color: var(--danger); color: var(--danger); }
  .btn.danger:hover { background: rgba(248,113,113,0.1); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Desktop table, Mobile cards */
  .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; }
  thead tr { border-bottom: 1px solid var(--border); }
  th { padding: 0.75rem 1rem; text-align: left; font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; font-weight: 500; background: var(--surface); }
  tbody tr { border-bottom: 1px solid var(--border); transition: background 0.1s; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: rgba(167,139,250,0.04); }
  td { padding: 0.75rem 1rem; font-size: 0.875rem; vertical-align: middle; }
  
  .cover-thumb { width: 36px; height: 52px; object-fit: cover; border-radius: 4px; background: var(--border); display: block; }
  .cover-missing { width: 36px; height: 52px; border-radius: 4px; background: var(--border); display: flex; align-items: center; justify-content: center; font-size: 1.2rem; }
  .tag { display: inline-block; background: rgba(167,139,250,0.15); color: var(--accent); border-radius: 4px; padding: 2px 7px; font-size: 0.7rem; margin: 1px; font-family: 'DM Mono', monospace; }
  .ext { font-family: 'DM Mono', monospace; font-size: 0.78rem; color: var(--accent2); }
  .no-author { color: var(--muted); font-style: italic; }
  .actions { display: flex; gap: 0.4rem; }
  .icon-btn { background: transparent; border: 1px solid var(--border); color: var(--muted); width: 28px; height: 28px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
  .icon-btn:hover { border-color: var(--accent); color: var(--accent); }
  .icon-btn.del:hover { border-color: var(--danger); color: var(--danger); }

  /* Pagination */
  .pagination { display: flex; gap: 0.5rem; margin-top: 1.25rem; align-items: center; justify-content: center; flex-wrap: wrap; }
  .page-btn { background: var(--surface); border: 1px solid var(--border); color: var(--muted); width: 32px; height: 32px; border-radius: 8px; cursor: pointer; font-size: 0.8rem; transition: all 0.15s; }
  .page-btn:hover, .page-btn.active { border-color: var(--accent); color: var(--accent); }
  .page-info { font-size: 0.75rem; color: var(--muted); padding: 0 0.5rem; }

  /* Modal */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
  .modal-overlay.open { display: flex; }
  .modal { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 1.5rem; width: 90vw; max-width: 480px; max-height: 90vh; overflow-y: auto; }
  .modal h2 { font-family: 'Playfair Display', serif; font-size: 1.2rem; margin-bottom: 1.5rem; color: var(--accent); }
  .field { margin-bottom: 1rem; }
  .field label { display: block; font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.4rem; }
  .field input, .field textarea {
    width: 100%; background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 0.6rem 0.9rem; border-radius: 8px; font-size: 0.875rem; font-family: 'DM Sans', sans-serif;
    outline: none; transition: border 0.15s; resize: vertical;
  }
  .field input:focus, .field textarea:focus { border-color: var(--accent); }
  .modal-actions { display: flex; gap: 0.75rem; margin-top: 1.5rem; justify-content: flex-end; }
  .cover-preview { width: 60px; height: 86px; object-fit: cover; border-radius: 6px; margin-bottom: 0.75rem; }

  /* Toast */
  #toast { position: fixed; bottom: 2rem; right: 1rem; left: 1rem; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem 1.25rem; font-size: 0.875rem; z-index: 200; opacity: 0; transform: translateY(8px); transition: all 0.2s; pointer-events: none; }
  #toast.show { opacity: 1; transform: translateY(0); }
  #toast.ok { border-color: var(--accent2); color: var(--accent2); }
  #toast.err { border-color: var(--danger); color: var(--danger); }

  /* Sync log */
  .log-box { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; font-family: 'DM Mono', monospace; font-size: 0.75rem; color: var(--muted); height: 150px; overflow-y: auto; white-space: pre-wrap; }

  /* Hidden sections */
  section { display: none; }
  section.active { display: block; }

  /* Mobile responsive */
  @media (max-width: 768px) {
    header { padding: 0.75rem 1rem; }
    .logo { font-size: 1.1rem; }
    nav button { padding: 0.3rem 0.6rem; font-size: 0.7rem; }
    main { padding: 0.75rem; }
    .stats { gap: 0.5rem; }
    .stat { padding: 0.75rem; }
    .stat-val { font-size: 1.25rem; }
    .stat-label { font-size: 0.65rem; }
    .toolbar { gap: 0.4rem; }
    .search-box { min-width: 150px; padding: 0.4rem 0.6rem; font-size: 0.8rem; }
    .btn { padding: 0.4rem 0.8rem; font-size: 0.75rem; }
    
    /* Cards para mobile */
    .table-wrap { border: none; border-radius: 0; }
    table { font-size: 0.8rem; }
    td { padding: 0.5rem 0.75rem; }
    th { padding: 0.5rem 0.75rem; font-size: 0.7rem; }
    
    .modal { width: 95vw; padding: 1.25rem; border-radius: 12px; }
    .modal h2 { font-size: 1.1rem; }
    #toast { bottom: 1rem; right: 0.75rem; left: 0.75rem; }
  }

  @media (max-width: 480px) {
    header { padding: 0.6rem 0.75rem; }
    .logo { font-size: 1rem; }
    nav button { padding: 0.25rem 0.5rem; font-size: 0.65rem; }
    main { padding: 0.5rem; }
    .toolbar { gap: 0.3rem; }
    .btn { padding: 0.35rem 0.7rem; font-size: 0.7rem; }
    table { font-size: 0.75rem; }
    .cover-thumb { width: 28px; height: 40px; }
    .tag { font-size: 0.65rem; padding: 1px 5px; }
    .pagination { gap: 0.3rem; }
    .page-btn { width: 28px; height: 28px; font-size: 0.75rem; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">BiblioGram</div>
  <nav>
    <button class="nav-btn active" data-section="books">Livros</button>
    <button class="nav-btn" data-section="sync">Sync</button>
    <button class="nav-btn" data-section="tools">Ferramentas</button>
  </nav>
</header>

<main>
  <!-- LIVROS -->
  <section id="books" class="active">
    <h2 id="books-title" style="font-size: 1.1rem; margin-bottom: 1rem; color: var(--accent); font-family: 'Playfair Display', serif;">Todos os livros</h2>
    
    <div class="stats" id="stats"></div>

    <div class="toolbar">
      <input type="text" class="search-box" id="search" placeholder="Buscar título, autor...">
      <select class="search-box" id="format-filter" style="flex: 0.8;">
        <option value="">Todos os formatos</option>
      </select>
      <select class="search-box" id="tag-filter" style="flex: 0.8;">
        <option value="">Todos os tags</option>
      </select>
      <button class="btn" id="load-btn">Carregar</button>
      <button class="btn" id="add-btn">+ Livro</button>
    </div>

    <div class="table-wrap">
      <table id="books-table">
        <thead>
          <tr>
            <th>Capa</th>
            <th>Título</th>
            <th>Autor</th>
            <th>Formato</th>
            <th>Tags</th>
            <th>Ações</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>

    <div class="pagination" id="pagination"></div>
  </section>

  <!-- SINCRONIZAÇÃO -->
  <section id="sync">
    <div class="toolbar" style="margin-bottom: 1rem;">
      <input type="text" id="channel-input" class="search-box" placeholder="Canal Telegram (@nome)">
      <button class="btn primary" id="sync-btn">Sincronizar</button>
      <button class="btn" id="verify-btn">Verificar</button>
    </div>
    <div class="log-box" id="sync-log"></div>
  </section>

  <!-- FERRAMENTAS -->
  <section id="tools">
    <div class="toolbar">
      <button class="btn" id="fix-titles-btn">Limpar títulos</button>
      <button class="btn" id="update-covers-btn">Atualizar capas</button>
    </div>
    <div class="log-box" id="tool-log"></div>
  </section>
</main>

<!-- MODAL -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h2 id="modal-title">Editar livro</h2>
    <form id="modal-form">
      <div class="field">
        <label>Título</label>
        <input type="text" name="title">
      </div>
      <div class="field">
        <label>Autor</label>
        <input type="text" name="author">
      </div>
      <div class="field">
        <label>Descrição</label>
        <textarea name="description" rows="3"></textarea>
      </div>
      <div class="field">
        <label>Tags (separadas por vírgula)</label>
        <input type="text" name="tags">
      </div>
      <div class="field">
        <label>URL da capa</label>
        <input type="text" name="cover_url">
      </div>
      <img id="cover-preview" class="cover-preview" style="display:none;">
      <div class="modal-actions">
        <button type="button" class="btn" id="close-modal">Cancelar</button>
        <button type="submit" class="btn primary">Salvar</button>
      </div>
    </form>
  </div>
</div>

<div id="toast"></div>

<script>
let currentPage = 0, currentFilters = {};

function showSection(name) {
  document.querySelectorAll('section').forEach(s => s.classList.remove('active'));
  document.getElementById(name).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-section="${name}"]`).classList.add('active');
}

function toast(msg, type = 'ok') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show ' + type;
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  const h = `
    <div class="stat green"><div class="stat-val">${d.total}</div><div class="stat-label">Total</div></div>
    ${Object.entries(d.formats).map(([fmt, cnt]) => `<div class="stat"><div class="stat-val">${cnt}</div><div class="stat-label">${fmt}</div></div>`).join('')}
    <div class="stat red"><div class="stat-val">${d.no_cover}</div><div class="stat-label">Sem capa</div></div>
  `;
  document.getElementById('stats').innerHTML = h;
}

async function loadBooks(page = 0) {
  const q = document.getElementById('search').value;
  const ext = document.getElementById('format-filter').value;
  const tag = document.getElementById('tag-filter').value;
  const url = '/api/books?page=' + page + (q ? '&q=' + encodeURIComponent(q) : '') + (ext ? '&ext=' + ext : '') + (tag ? '&tag=' + tag : '');
  const r = await fetch(url);
  const d = await r.json();
  
  // Atualizar título baseado no filtro
  let title = 'Todos os livros';
  if (q) title = `Busca: "${q}"`;
  else if (tag) title = `Tag: ${tag}`;
  else if (ext) title = `Formato: ${ext}`;
  document.getElementById('books-title').textContent = title;
  
  const tbody = document.querySelector('#books-table tbody');
  tbody.innerHTML = d.books.map(b => `
    <tr>
      <td>${b.cover_url ? `<img src="${b.cover_url}" class="cover-thumb">` : '<div class="cover-missing">📕</div>'}</td>
      <td><strong>${b.title}</strong></td>
      <td>${b.author || '<span class="no-author">—</span>'}</td>
      <td><span class="ext">${b.ext}</span></td>
      <td>${(b.tags || '').split(',').map(t => t.trim()).filter(t => t).map(t => `<span class="tag">${t}</span>`).join('')}</td>
      <td class="actions">
        <button class="icon-btn" onclick="editBook(${b.id})">✎</button>
        <button class="icon-btn del" onclick="deleteBook(${b.id})">✕</button>
      </td>
    </tr>
  `).join('');
  
  document.getElementById('format-filter').innerHTML = '<option value="">Todos</option>' + d.all_exts.map(e => `<option value="${e}">${e}</option>`).join('');
  document.getElementById('tag-filter').innerHTML = '<option value="">Todos</option>' + d.all_tags.map(t => `<option value="${t}">${t}</option>`).join('');
  
  const pag = document.getElementById('pagination');
  const pages = Math.ceil(d.total / 20);
  pag.innerHTML = `<span class="page-info">Página ${page + 1} de ${pages}</span>` +
    (page > 0 ? `<button class="page-btn" onclick="loadBooks(${page-1})">←</button>` : '') +
    (page < pages - 1 ? `<button class="page-btn" onclick="loadBooks(${page+1})">→</button>` : '');
  
  currentPage = page;
  loadStats();
}

function editBook(id) {
  document.getElementById('modal').classList.add('open');
  document.getElementById('modal-form').onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData);
    await fetch(`/api/books/${id}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
    toast('Livro atualizado!', 'ok');
    document.getElementById('modal').classList.remove('open');
    loadBooks(currentPage);
  };
  
  // Preview de capa em tempo real
  const coverInput = document.querySelector('input[name="cover_url"]');
  const preview = document.getElementById('cover-preview');
  coverInput.addEventListener('input', (e) => {
    if (e.target.value) {
      preview.src = e.target.value;
      preview.style.display = 'block';
      preview.onerror = () => {
        preview.style.display = 'none';
        toast('URL de capa inválida', 'err');
      };
    } else {
      preview.style.display = 'none';
    }
  });
}

async function deleteBook(id) {
  if (!confirm('Tem certeza?')) return;
  await fetch(`/api/books/${id}`, { method: 'DELETE' });
  toast('Livro removido!', 'ok');
  loadBooks(currentPage);
}

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', (e) => showSection(e.target.dataset.section));
});

// Filtros em tempo real
document.getElementById('search').addEventListener('input', () => loadBooks(0));
document.getElementById('format-filter').addEventListener('change', () => loadBooks(0));
document.getElementById('tag-filter').addEventListener('change', () => loadBooks(0));

document.getElementById('load-btn').addEventListener('click', () => loadBooks(0));
document.getElementById('close-modal').addEventListener('click', () => document.getElementById('modal').classList.remove('open'));

document.getElementById('sync-btn').addEventListener('click', async () => {
  const channel = document.getElementById('channel-input').value;
  if (!channel) { toast('Informe o canal', 'err'); return; }
  const log = document.getElementById('sync-log');
  log.textContent = 'Sincronizando...';
  const r = await fetch('/api/sync', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ channel, mode: 'sync' }) });
  const d = await r.json();
  log.textContent = d.output || d.error || 'Concluído.';
  toast(d.error ? 'Erro' : 'Concluído!', d.error ? 'err' : 'ok');
  loadBooks(0);
});

document.getElementById('verify-btn').addEventListener('click', async () => {
  const channel = document.getElementById('channel-input').value;
  if (!channel) { toast('Informe o canal', 'err'); return; }
  const log = document.getElementById('sync-log');
  log.textContent = 'Verificando...';
  const r = await fetch('/api/sync', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ channel, mode: 'verify' }) });
  const d = await r.json();
  log.textContent = d.output || d.error || 'Concluído.';
  toast(d.error ? 'Erro' : 'Concluído!', d.error ? 'err' : 'ok');
  loadBooks(0);
});

document.getElementById('fix-titles-btn').addEventListener('click', async () => {
  const log = document.getElementById('tool-log');
  log.textContent = 'Limpando títulos...';
  const r = await fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'fix-titles' }) });
  const d = await r.json();
  log.textContent = d.output || d.error || 'Concluído.';
  toast(d.error ? 'Erro' : 'Concluído!', d.error ? 'err' : 'ok');
  loadBooks(0);
});

document.getElementById('update-covers-btn').addEventListener('click', async () => {
  const log = document.getElementById('tool-log');
  log.textContent = 'Atualizando capas...';
  const r = await fetch('/api/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'update-covers' }) });
  const d = await r.json();
  log.textContent = d.output || d.error || 'Concluído.';
  toast(d.error ? 'Erro' : 'Concluído!', d.error ? 'err' : 'ok');
  loadBooks(0);
});

loadBooks(0);
</script>
</body>
</html>"""

# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/stats")
def api_stats():
    return jsonify(db_stats())

@app.route("/api/books")
def api_books():
    page = int(request.args.get("page", 0))
    q    = request.args.get("q", "").strip()
    ext  = request.args.get("ext", "").strip()
    tag  = request.args.get("tag", "").strip()

    conditions = []
    params = []

    if q:
        conditions.append("(title LIKE ? OR author LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if ext:
        conditions.append("ext = ?")
        params.append(ext)
    if tag:
        conditions.append("lower(tags) LIKE ?")
        params.append(f"%{tag.lower()}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM books {where}", params).fetchone()[0]
        books = conn.execute(
            f"SELECT * FROM books {where} ORDER BY date DESC LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, page * PAGE_SIZE]
        ).fetchall()

        all_exts = [r[0] for r in conn.execute("SELECT DISTINCT ext FROM books ORDER BY ext").fetchall()]
        tag_rows = conn.execute("SELECT tags FROM books WHERE tags IS NOT NULL AND tags != ''").fetchall()

    from collections import Counter
    tag_counter = Counter()
    for row in tag_rows:
        for t in row[0].split(","):
            t = t.strip().lower()
            if t:
                tag_counter[t] += 1

    return jsonify({
        "total":    total,
        "books":    [dict(b) for b in books],
        "all_exts": all_exts,
        "all_tags": [t for t, _ in tag_counter.most_common(50)],
    })

@app.route("/api/books/<int:book_id>", methods=["PUT"])
def api_update_book(book_id):
    data = request.get_json()
    with get_db() as conn:
        conn.execute("""
            UPDATE books SET title=?, author=?, tags=?, description=?, cover_url=?
            WHERE id=?
        """, (data.get("title"), data.get("author"), data.get("tags"),
              data.get("description"), data.get("cover_url"), book_id))
    return jsonify({"ok": True})

@app.route("/api/books/<int:book_id>", methods=["DELETE"])
def api_delete_book(book_id):
    with get_db() as conn:
        conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    return jsonify({"ok": True})

@app.route("/api/sync", methods=["POST"])
def api_sync():
    data    = request.get_json()
    channel = data.get("channel", "")
    mode    = data.get("mode", "sync")

    if not channel:
        return jsonify({"error": "Canal não informado"}), 400

    cmd = [sys.executable, os.path.join(BASE_DIR, "collector.py"), "--channel", channel]
    if mode == "sync":
        cmd.append("--sync")
    elif mode == "verify":
        cmd += ["--verify", "--delete"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = (result.stdout + result.stderr).strip()
        return jsonify({"output": output})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout — canal muito grande. Use o terminal."})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/action", methods=["POST"])
def api_action():
    data   = request.get_json()
    action = data.get("action", "")

    valid = {"fix-titles", "update-covers"}
    if action not in valid:
        return jsonify({"error": "Ação inválida"}), 400

    cmd = [sys.executable, os.path.join(BASE_DIR, "collector.py"), f"--{action}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = (result.stdout + result.stderr).strip()
        return jsonify({"output": output})
    except Exception as e:
        return jsonify({"error": str(e)})

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("WEBAPP_PORT", 8082))
    log.info("Interface web disponível em http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
