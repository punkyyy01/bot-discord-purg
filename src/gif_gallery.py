GIF_GALLERY_HTML: str = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PURG4TORY — GIF VAULT</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:      #0d0d0f;
      --bg2:     #111118;
      --bg3:     #1a1a26;
      --red:     #8b0000;
      --red2:    #c41230;
      --purple:  #6b21a8;
      --purple2: #9333ea;
      --text:    #e8e0f0;
      --muted:   #7a6f90;
      --border:  #2a1a3a;
      --ok:      #22c55e;
      --err:     #ef4444;
      --warn:    #f59e0b;
    }

    html { height: 100%; }

    body {
      min-height: 100%;
      background-color: var(--bg);
      background-image:
        radial-gradient(ellipse 110% 45% at 50% 0%, rgba(107,33,168,0.13) 0%, transparent 60%),
        repeating-linear-gradient(
          0deg,
          transparent,
          transparent 3px,
          rgba(107,33,168,0.02) 3px,
          rgba(107,33,168,0.02) 4px
        );
      color: var(--text);
      font-family: 'Space Mono', monospace;
      font-size: 14px;
    }

    /* ── HEADER ───────────────────────────────────── */
    header {
      position: relative;
      text-align: center;
      padding: 2.6rem 1rem 1.8rem;
      border-bottom: 1px solid var(--border);
    }
    header::after {
      content: '';
      position: absolute;
      left: 0; right: 0; bottom: -1px;
      height: 1px;
      background: linear-gradient(90deg,
        transparent 0%,
        var(--purple) 25%,
        var(--red2) 50%,
        var(--purple) 75%,
        transparent 100%);
    }
    h1 {
      font-family: 'Bebas Neue', sans-serif;
      font-size: clamp(2.4rem, 9vw, 5.5rem);
      letter-spacing: 0.1em;
      line-height: 1;
      text-shadow:
        0 0 24px rgba(147,51,234,0.55),
        0 0 60px rgba(139,0,0,0.3),
        0 2px 6px rgba(0,0,0,0.9);
    }
    .sk { color: var(--red2); }
    #counter {
      margin-top: 0.6rem;
      font-size: 0.68rem;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: var(--muted);
    }
    #counter em { font-style: normal; font-weight: 700; color: var(--purple2); }

    /* ── TOOLBAR ──────────────────────────────────── */
    .toolbar {
      max-width: 700px;
      margin: 1.6rem auto 0;
      padding: 0 1rem;
    }
    .add-form {
      display: flex;
      gap: 0.5rem;
    }
    .add-form input {
      flex: 1;
      min-width: 0;
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 3px;
      color: var(--text);
      font-family: 'Space Mono', monospace;
      font-size: 0.78rem;
      padding: 0.58rem 0.8rem;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .add-form input:focus {
      border-color: var(--purple);
      box-shadow: 0 0 0 2px rgba(107,33,168,0.22);
    }
    .add-form input::placeholder { color: var(--muted); }
    .add-form button {
      background: var(--red);
      border: none;
      border-radius: 3px;
      color: var(--text);
      cursor: pointer;
      font-family: 'Bebas Neue', sans-serif;
      font-size: 1.05rem;
      letter-spacing: 0.12em;
      padding: 0.58rem 1.1rem;
      transition: background 0.2s, box-shadow 0.2s;
      white-space: nowrap;
    }
    .add-form button:hover {
      background: var(--red2);
      box-shadow: 0 0 14px rgba(196,18,48,0.45);
    }
    #add-status {
      margin-top: 0.4rem;
      min-height: 1.3em;
      font-size: 0.72rem;
      letter-spacing: 0.04em;
    }
    #add-status.ok   { color: var(--ok); }
    #add-status.err  { color: var(--err); }
    #add-status.warn { color: var(--warn); }
    #add-status.info { color: var(--muted); }

    /* ── GRID ─────────────────────────────────────── */
    .grid-section {
      max-width: 1400px;
      margin: 1.6rem auto 0;
      padding: 0 1rem 5rem;
    }
    #grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 0.65rem;
    }

    /* ── CARD ─────────────────────────────────────── */
    .card {
      position: relative;
      border-radius: 5px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: var(--bg2);
      aspect-ratio: 4 / 3;
      transition:
        transform 0.2s ease,
        box-shadow 0.2s ease,
        border-color 0.2s ease,
        opacity 0.3s ease;
    }
    .card:hover {
      transform: translateY(-3px);
      box-shadow: 0 8px 28px rgba(107,33,168,0.3);
      border-color: rgba(147,51,234,0.5);
    }
    .card.out {
      opacity: 0;
      transform: scale(0.88);
      pointer-events: none;
    }
    .card img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .card.broken img { visibility: hidden; }
    .card.broken::before {
      content: 'VOID';
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: 'Bebas Neue', sans-serif;
      font-size: 1.8rem;
      color: var(--border);
      letter-spacing: 0.15em;
    }

    /* ── OVERLAY ──────────────────────────────────── */
    .overlay {
      position: absolute;
      inset: 0;
      background: rgba(13,13,15,0.72);
      display: flex;
      align-items: flex-start;
      justify-content: flex-end;
      padding: 0.4rem;
      opacity: 0;
      transition: opacity 0.18s ease;
    }
    .card:hover .overlay,
    .overlay.pin { opacity: 1; }

    .del-btn {
      background: rgba(139,0,0,0.82);
      border: 1px solid var(--red2);
      border-radius: 3px;
      color: #ffd0d0;
      cursor: pointer;
      font-size: 0.8rem;
      line-height: 1;
      padding: 0.28rem 0.5rem;
      transition: background 0.15s;
    }
    .del-btn:hover { background: var(--red2); }

    .confirm-row {
      display: flex;
      align-items: center;
      gap: 0.3rem;
      background: rgba(10,8,18,0.93);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.28rem 0.5rem;
      font-size: 0.68rem;
      white-space: nowrap;
    }
    .confirm-row span { color: var(--text); }
    .c-yes, .c-no {
      border: none;
      border-radius: 2px;
      cursor: pointer;
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.12rem 0.38rem;
    }
    .c-yes { background: var(--red); color: #ffd0d0; }
    .c-yes:hover { background: var(--red2); }
    .c-no  { background: var(--bg3); color: var(--muted); }
    .c-no:hover { color: var(--text); }

    /* ── LOAD MORE ────────────────────────────────── */
    .more-wrap { text-align: center; margin-top: 1.8rem; }
    #btn-more {
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 3px;
      color: var(--muted);
      cursor: pointer;
      display: none;
      font-family: 'Bebas Neue', sans-serif;
      font-size: 1rem;
      letter-spacing: 0.15em;
      padding: 0.55rem 2.2rem;
      transition: border-color 0.2s, color 0.2s, box-shadow 0.2s;
    }
    #btn-more:hover {
      border-color: var(--purple);
      color: var(--purple2);
      box-shadow: 0 0 14px rgba(107,33,168,0.22);
    }

    /* ── EMPTY / SPINNER ──────────────────────────── */
    .empty {
      grid-column: 1 / -1;
      padding: 4rem 1rem;
      text-align: center;
    }
    .empty-title {
      font-family: 'Bebas Neue', sans-serif;
      font-size: 2.5rem;
      color: var(--border);
      letter-spacing: 0.2em;
    }
    .empty p { margin-top: 0.5rem; color: var(--muted); font-size: 0.75rem; }

    .spinner {
      width: 36px;
      height: 36px;
      border: 3px solid var(--border);
      border-top-color: var(--purple2);
      border-radius: 50%;
      animation: spin 0.75s linear infinite;
      margin: 3rem auto;
      grid-column: 1 / -1;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── TOAST ────────────────────────────────────── */
    #toast {
      position: fixed;
      bottom: 1.2rem;
      right: 1.2rem;
      max-width: 280px;
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 5px;
      color: var(--text);
      font-size: 0.75rem;
      padding: 0.55rem 0.9rem;
      pointer-events: none;
      opacity: 0;
      transform: translateY(6px);
      transition: opacity 0.22s, transform 0.22s;
      z-index: 9999;
    }
    #toast.show      { opacity: 1; transform: none; }
    #toast.show.err  { border-color: var(--err); }
    #toast.show.ok   { border-color: var(--ok); }
    #toast.show.warn { border-color: var(--warn); }

    /* ── RESPONSIVE ───────────────────────────────── */
    @media (max-width: 500px) {
      #grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
      .add-form { flex-direction: column; }
    }
  </style>
</head>
<body>

<header>
  <h1><span class="sk">☠</span> PURG4TORY GIF VAULT <span class="sk">☠</span></h1>
  <div id="counter"><em id="total">—</em> GIFs IN THE VOID</div>
</header>

<div class="toolbar">
  <form class="add-form" id="add-form" autocomplete="off">
    <input type="text" id="gif-url"
           placeholder="URL del GIF (tenor.com, giphy.com …)"
           spellcheck="false">
    <button type="submit">⊕ SUMMON</button>
  </form>
  <div id="add-status"></div>
</div>

<div class="grid-section">
  <div id="grid"><div class="spinner"></div></div>
  <div class="more-wrap"><button id="btn-more">CARGAR MÁS</button></div>
</div>

<div id="toast"></div>

<script>
'use strict';

const PAGE = 30;
let pool = [];

const $ = id => document.getElementById(id);

// ── Counter ────────────────────────────────────────
function setTotal(n) { $('total').textContent = n; }

// ── Toast ──────────────────────────────────────────
let _tt = null;
function showToast(msg, type) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'show ' + (type || '');
  clearTimeout(_tt);
  _tt = setTimeout(() => { el.className = ''; }, 3200);
}

// ── Load-more button ───────────────────────────────
function syncMore() {
  const rendered = $('grid').querySelectorAll('.card').length;
  const left = pool.length - rendered;
  const btn = $('btn-more');
  if (left > 0) {
    btn.textContent = 'CARGAR MÁS (' + left + ' restantes)';
    btn.style.display = 'inline-block';
  } else {
    btn.style.display = 'none';
  }
}

// ── Card factory ───────────────────────────────────
function mkCard(gif) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.gid = gif.id;

  const img = document.createElement('img');
  img.loading = 'lazy';
  img.src = gif.url;
  img.alt = '';
  img.addEventListener('error', () => card.classList.add('broken'));

  const ov = document.createElement('div');
  ov.className = 'overlay';
  attachDelBtn(ov, gif.id, card);

  card.appendChild(img);
  card.appendChild(ov);
  return card;
}

function attachDelBtn(ov, id, card) {
  ov.innerHTML = '';
  ov.classList.remove('pin');
  const btn = document.createElement('button');
  btn.className = 'del-btn';
  btn.textContent = '✕';
  btn.title = 'Eliminar GIF';
  btn.addEventListener('click', () => askConfirm(ov, id, card));
  ov.appendChild(btn);
}

function askConfirm(ov, id, card) {
  ov.classList.add('pin');
  const row = document.createElement('div');
  row.className = 'confirm-row';

  const lbl = document.createElement('span');
  lbl.textContent = '¿seguro?';

  const yes = document.createElement('button');
  yes.className = 'c-yes';
  yes.textContent = '✓';
  yes.addEventListener('click', () => execDelete(id, card, ov));

  const no = document.createElement('button');
  no.className = 'c-no';
  no.textContent = '✗';
  no.addEventListener('click', () => attachDelBtn(ov, id, card));

  row.appendChild(lbl);
  row.appendChild(yes);
  row.appendChild(no);
  ov.innerHTML = '';
  ov.appendChild(row);
}

async function execDelete(id, card, ov) {
  try {
    const res = await fetch('/api/gifs/' + id, { method: 'DELETE' });
    if (res.status === 429) {
      showToast('Rate limit — espera antes de borrar más', 'warn');
      attachDelBtn(ov, id, card);
      return;
    }
    if (!res.ok) {
      showToast('Error ' + res.status, 'err');
      attachDelBtn(ov, id, card);
      return;
    }
    const data = await res.json();
    if (data.deleted) {
      card.classList.add('out');
      pool = pool.filter(g => g.id !== id);
      setTotal(pool.length);
      setTimeout(() => { card.remove(); syncMore(); }, 320);
    } else {
      showToast('No encontrado', 'warn');
      attachDelBtn(ov, id, card);
    }
  } catch (e) {
    showToast('Error de red: ' + e.message, 'err');
    attachDelBtn(ov, id, card);
  }
}

// ── Render batch ───────────────────────────────────
function renderBatch() {
  const grid = $('grid');
  const from = grid.querySelectorAll('.card').length;
  const batch = pool.slice(from, from + PAGE);
  const frag = document.createDocumentFragment();
  batch.forEach(g => frag.appendChild(mkCard(g)));
  grid.appendChild(frag);
  syncMore();
}

// ── Initial fetch ──────────────────────────────────
async function loadGifs() {
  const grid = $('grid');
  grid.innerHTML = '<div class="spinner"></div>';
  try {
    const res = await fetch('/api/gifs');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    pool = data.gifs;
    setTotal(data.total);
    grid.innerHTML = '';
    if (pool.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.innerHTML = '<div class="empty-title">VAULT VACÍO</div><p>Agrega un GIF usando el formulario.</p>';
      grid.appendChild(empty);
      $('btn-more').style.display = 'none';
      return;
    }
    renderBatch();
  } catch (e) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.innerHTML = '<p>Error cargando GIFs: ' + e.message + '</p>';
    grid.innerHTML = '';
    grid.appendChild(empty);
    showToast('No se pudo conectar con la API', 'err');
  }
}

// ── Add form ───────────────────────────────────────
$('add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const inp = $('gif-url');
  const url = inp.value.trim();
  const st  = $('add-status');
  if (!url) return;

  st.textContent = 'Invocando…';
  st.className   = 'info';

  try {
    const res = await fetch('/api/gifs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();

    if (res.status === 429) {
      st.textContent = '⚠ Rate limit: espera antes de agregar más';
      st.className   = 'warn';
      return;
    }
    if (!res.ok) {
      st.textContent = '✕ ' + (data.error || 'Error ' + res.status);
      st.className   = 'err';
      return;
    }
    if (data.inserted) {
      st.textContent = '✓ GIF summonado al vault (total: ' + data.total + ')';
      st.className   = 'ok';
      inp.value = '';
      await loadGifs();
    } else {
      st.textContent = '⚠ Ese GIF ya habita en el vault';
      st.className   = 'warn';
    }
  } catch (err) {
    st.textContent = '✕ Error de red: ' + err.message;
    st.className   = 'err';
  }
});

$('btn-more').addEventListener('click', renderBatch);

loadGifs();
</script>

</body>
</html>
"""
