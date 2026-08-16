/* ============================================================
   SPACE LAIR - Living AI Factory (FASE 8C)
   Frontend conectado al AUTOPILOT EDITORIAL REAL.
   Fuente de verdad: core.autopilot / BookJobStore (backend).
   El frontend SOLO representa el estado recibido. Sin mock data.
   Estados job: PENDING/RUNNING/FAILED/COMPLETED/CANCELLED.
   Estados fase: PENDING/RUNNING/RETRY/PASS/FAIL.
   ============================================================ */
const API_BASE = '';
const USE_MOCK = false;

// Pipeline editorial REAL — espejo exacto de core.autopilot.AUTOPILOT_PHASES (10 fases).
const AUTOPILOT_PHASES = [
  { id: 'planner',      capability: 'create_book_plan',      label: 'PLANIFICADOR',      tool: 'plan' },
  { id: 'research',     capability: 'research_web',          label: 'INVESTIGACIÓN',          tool: 'search' },
  { id: 'outline',      capability: 'create_book_plan',      label: 'ESQUEMA',           tool: 'plan' },
  { id: 'writer',       capability: 'write_chapter_es',      label: 'REDACCIÓN',    tool: 'write' },
  { id: 'fact_check',   capability: 'fact_check_chapter',    label: 'VERIFICACIÓN',        tool: 'check' },
  { id: 'editor',       capability: 'edit_chapter',          label: 'EDITOR',            tool: 'edit' },
  { id: 'image_plan',   capability: 'create_chapter_image_plan', label: 'PLAN DE IMÁGENES',    tool: 'image' },
  { id: 'image_gen',    capability: 'generate_chapter_images',   label: 'GENERACIÓN DE IMÁGENES', tool: 'image' },
  { id: 'quality_gate', capability: 'final_quality_control', label: 'CONTROL DE CALIDAD',    tool: 'gate' },
  { id: 'docx',         capability: 'build_book_docx',       label: 'GENERADOR DE DOCUMENTO',  tool: 'doc' },
];
const JOB_STATUS_LABEL = {
  PENDING: 'ESPERANDO', RUNNING: 'EN EJECUCIÓN', CANCELLED: 'CANCELADO',
  COMPLETED: 'COMPLETADO', FAILED: 'FALLIDO',
};
const PHASE_STATUS_LABEL = {
  PENDING: 'ESPERANDO', RUNNING: 'EN EJECUCIÓN', RETRY: 'REINTENTO',
  PASS: 'COMPLETADO', FAIL: 'FALLIDO',
};
// fase.status -> clase CSS en .module-station
const PHASE_CSS = { PENDING: 'idle', RUNNING: 'running', RETRY: 'retry', PASS: 'completed', FAIL: 'error' };
const CAPABILITY_LABELS = {
  count_words: 'Contar palabras', summarize_text: 'Resumir texto', reverse_text: 'Invertir texto',
  external_tool: 'Herramienta externa', create_book_plan: 'Crear plan editorial',
  write_chapter_es: 'Escribir capítulo ES', write_chapter_en: 'Escribir capítulo EN',
  fact_check_chapter: 'Comprobar datos', edit_chapter: 'Editar capítulo',
  research_web: 'Investigar en la web', fetch_url: 'Obtener URL', extract_text: 'Extraer texto',
  build_book_docx: 'Crear documento Word', build_book_pdf: 'Crear PDF',
  final_quality_control: 'Control de calidad', generate_image: 'Generar imagen',
  create_chapter_image_plan: 'Planificar imágenes', generate_chapter_images: 'Generar imágenes',
};

const state = {
  books: [], selectedBookId: null,
  currentBookDetail: null, currentStats: {},
  modules: [], tasks: [], jobs: [], logs: [], feed: [],
  autopilot: null, autopilotBookId: null, autopilotLoading: false,
  connected: false, isLoading: false,
  _sseWelcomed: false, _sseLostWelcomed: false,
};

// ---------- API ----------
function apiFetch(url, opts = {}) {
  if (USE_MOCK) return Promise.resolve([]);
  return fetch(API_BASE + url, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  }).then(r => {
    if (!r.ok) {
      return r.json().catch(() => null).then(err => {
        const e = new Error((err && err.error) || r.statusText || ('HTTP ' + r.status));
        e.status = r.status; e.body = err;
        throw e;
      });
    }
    return r.json();
  });
}
// GET opcional: devuelve null en 404 (ej. no hay job). Nunca inventa.
function apiFetchOrNull(url) {
  return apiFetch(url).catch(e => (e.status === 404 ? null : Promise.reject(e)));
}

// ---------- Utils ----------
function esc(s) {
  const d = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => d[c]);
}
function trunc(s, n) {
  const t = String(s == null ? '' : s);
  return t.length > n ? t.slice(0, n) + '…' : t;
}
function fmtTime(d) { return new Date(d).toLocaleTimeString('es-ES', { hour12: false }); }
function phaseLabel(id) {
  const p = AUTOPILOT_PHASES.find(x => x.id === id);
  return p ? p.label : (id || '—');
}
function phaseTool(id) {
  const p = AUTOPILOT_PHASES.find(x => x.id === id);
  return p ? p.tool : 'doc';
}
function phaseById(id) {
  return state.autopilot ? state.autopilot.phases.find(p => p.id === id) : null;
}
function realPhase(id) {
  const p = phaseById(id);
  if (p) return p;
  return { id, label: phaseLabel(id), capability: '', status: 'PENDING', attempts: 0,
           duration: null, error: null, metrics: {}, module: null, task_id: null,
           started_at: null, completed_at: null };
}
function jobStatusClass(status) {
  return ({ PENDING: 'pending', RUNNING: 'running', COMPLETED: 'done', FAILED: 'error', CANCELLED: 'cancelled' }[status] || 'pending');
}
function _showError(msg) {
  const cmd = document.getElementById('cmd-status-text');
  if (cmd) cmd.textContent = 'ERROR: ' + msg;
  setTimeout(() => { if (cmd) cmd.textContent = 'SISTEMA LISTO'; }, 4500);
}
function setCmdStatus(text, mode) {
  const dot = document.getElementById('cmd-status-dot');
  const label = document.getElementById('cmd-status-text');
  if (dot) dot.className = 'cmd-status-dot ' + (mode || '');
  if (label) label.textContent = text;
}
function setButton(id, handler, enabled) {
  const el = document.getElementById(id);
  if (!el) return;
  el.disabled = !enabled;
  el.classList.toggle('disabled', !enabled);
  el.onclick = enabled ? handler : null;
}

// ---------- Mensajería real (feed + logs) ----------
function addFeed(type, message) {
    state.feed.unshift({ type, message, time: Date.now() });
  if (state.feed.length > 100) state.feed.pop();
  renderFeed(document.getElementById('feed-content'));
  renderActivityFull();
}
function addLog(level, message) {
  state.logs.unshift({ level, message, time: Date.now() });
  if (state.logs.length > 200) state.logs.pop();
  renderLogs();
}
function renderFeed(container) {
  if (!container) return;
  if (!state.feed.length) {
    container.innerHTML = '<div class="feed-empty">Sin actividad todavía. Los eventos reales aparecerán aquí.</div>';
    return;
  }
  const icon = { system: '[S]', started: '[>]', completed: '[OK]', failed: '[X]', retry: '[R]', fallback: '[!]' };
  container.innerHTML = state.feed.map(item =>
    '<div class="feed-item feed-' + item.type + '">' +
      '<span class="feed-time">' + fmtTime(item.time) + '</span>' +
      '<span class="feed-icon">' + (icon[item.type] || '[*]') + '</span>' +
      '<span class="feed-message">' + esc(item.message) + '</span>' +
    '</div>'
  ).join('');
}

// ---------- Carga de datos ----------
async function loadBooks() {
  state.books = await apiFetch('/api/books').catch(() => state.books || []);
}
async function loadModules() {
  state.modules = await apiFetch('/api/modules').catch(() => state.modules || []);
}
async function loadTasks() {
  state.tasks = await apiFetch('/api/tasks').catch(() => state.tasks || []);
}
async function loadJobs() {
  state.jobs = await apiFetch('/api/autopilot').catch(() => state.jobs || []);
}
async function loadStats() {
  state.currentStats = await apiFetch('/api/stats').catch(() => ({}));
}
// state.autopilot = única fuente de verdad, reconstruible vía GET.
async function loadAutopilot() {
  if (state.selectedBookId) {
    state.autopilot = await apiFetchOrNull('/api/books/' + state.selectedBookId + '/autopilot');
    state.autopilotBookId = state.autopilot ? state.autopilot.book_id : state.selectedBookId;
  } else {
    const active = state.jobs.find(j => j.status === 'PENDING' || j.status === 'RUNNING');
    state.autopilot = active || (state.jobs.length ? state.jobs[0] : null);
    state.autopilotBookId = state.autopilot ? state.autopilot.book_id : null;
  }
}
async function loadCurrentBookDetail() {
  const bid = state.selectedBookId || state.autopilotBookId;
  state.currentBookDetail = bid ? await apiFetchOrNull('/api/books/' + bid + '/load') : null;
}
async function refreshData() {
  state.isLoading = true;
  setCmdStatus('ACTUALIZANDO...', 'busy');
  try {
    await Promise.all([loadBooks(), loadModules(), loadTasks(), loadJobs(), loadStats()]);
    await loadAutopilot();
    await loadCurrentBookDetail();
    state.connected = true;
    renderAll();
  } catch (e) {
    _showError('Error en datos: ' + e.message);
  } finally {
    state.isLoading = false;
    setCmdStatus('SISTEMA LISTO', 'online');
  }
}
// Reconstruye la vista del autopilot (ligera) tras un evento SSE.
async function refreshAutopilotView() {
  await loadAutopilot();
  await loadCurrentBookDetail();
  renderLivingPipeline();
  renderCurrentBook();
  renderBookReady();
  renderAutopilotControls();
  renderActivityFull();
  updateStatusIndicator();
  renderMetrics();
}
// Orquesta todos los renders.
function renderAll() {
  renderBookSelector();
  renderBooks();
  renderMetrics();
  renderModules();
  renderTasks();
  renderLogs();
  renderLivingPipeline();
  renderCurrentBook();
  renderBookReady();
  renderAutopilotControls();
  renderActivityFull();
  updateStatusIndicator();
}

// ---------- Worker SVG (puramente visual, no representa progreso) ----------
function workerToolSvg(tool, css) {
  const tools = {
    search: '<circle cx="35" cy="20" r="5.5" fill="none" stroke="currentColor" stroke-width="2"/><line x1="39" y1="24" x2="44" y2="29" stroke="currentColor" stroke-width="2"/>',
    write:  '<line x1="30" y1="26" x2="41" y2="26" stroke="currentColor" stroke-width="2"/><path d="M41 24l4 4-4 4" fill="none" stroke="currentColor" stroke-width="2"/>',
    edit:   '<rect x="30" y="20" width="11" height="12" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="32" y1="23" x2="39" y2="23" stroke="currentColor"/><line x1="32" y1="26" x2="37" y2="26" stroke="currentColor"/>',
    check:  '<path d="M30 26l4 4 9-10" fill="none" stroke="currentColor" stroke-width="2.2"/>',
    gate:   '<path d="M33 17l8 6-8 6-8-6z" fill="none" stroke="currentColor" stroke-width="1.6"/><line x1="33" y1="17" x2="33" y2="29" stroke="currentColor" stroke-width="1.4"/>',
    doc:    '<rect x="30" y="18" width="9" height="14" fill="none" stroke="currentColor" stroke-width="1.6"/><line x1="32" y1="22" x2="37" y2="22" stroke="currentColor"/>',
    plan:   '<rect x="29" y="18" width="12" height="8" fill="none" stroke="currentColor" stroke-width="1.6"/><line x1="31" y1="21" x2="39" y2="21" stroke="currentColor"/>',
    image:  '<rect x="28" y="18" width="11" height="12" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M28 29l4-5 3 4 5-6v5H28z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="37" cy="23" r="1.3" fill="currentColor"/>',
  };
  const svg = tools[tool] || tools.doc;
  return '<svg class="worker-svg" viewBox="0 0 48 48" aria-hidden="true">'
    + '<g class="worker-g ' + css + '">'
      + '<line x1="21" y1="40" x2="18" y2="47" class="worker-leg"/>'
      + '<line x1="27" y1="40" x2="30" y2="47" class="worker-leg"/>'
      + '<rect x="18" y="26" width="12" height="15" rx="2" class="worker-torso"/>'
      + '<polygon points="24,11 31,15 31,25 24,29 17,25 17,15" class="worker-head"/>'
      + '<rect x="20" y="16" width="8" height="4" rx="1" class="worker-visor"/>'
      + '<circle cx="24" cy="31" r="1.8" class="worker-led"/>'
      + '<line x1="18" y1="28" x2="12" y2="22" class="worker-arm"/>'
      + '<line x1="30" y1="28" x2="38" y2="24" class="worker-arm arm-tool"/>'
      + '<g class="worker-tool">' + svg + '</g>'
    + '</g></svg>';
}

// ---------- Selector de libro ----------
function renderBookSelector() {
  const sel = document.getElementById('book-selector');
  if (!sel) return;
  const cur = state.selectedBookId;
  let html = '<option value="">Seleccionar libro…</option>';
  state.books.forEach(b => {
    html += '<option value="' + b.id + '"' + (String(b.id) === String(cur) ? ' selected' : '') + '>'
      + esc(b.title || ('Libro ' + b.id)) + '</option>';
  });
  sel.innerHTML = html;
  sel.onchange = (e) => { if (e.target.value) onSelectBook(Number(e.target.value)); };
}
function onSelectBook(bookId) {
  state.selectedBookId = bookId;
  refreshData().then(() => showView('control-room'));
}

// ---------- Biblioteca ----------
function renderBooks() {
  const section = document.getElementById('books-section');
  if (!section) return;
  if (!state.books.length) {
    section.innerHTML = '<div class="empty-state">No hay libros disponibles</div>';
    return;
  }
  let html = '<table class="book-table"><thead><tr>'
    + '<th onclick="sortBooks(\'title\')">Título</th>'
    + '<th onclick="sortBooks(\'status\')">Estado</th>'
    + '<th onclick="sortBooks(\'chapters\')">Capítulos</th>'
    + '<th>Progreso</th><th>Autopilot</th><th>DOCX</th></tr></thead><tbody>';
  html += state.books.map(b => {
    const job = findJobForBook(b.id);
    return '<tr onclick="onSelectBook(' + b.id + ')">'
      + '<td><span class="book-title">' + esc(b.title || ('Libro ' + b.id)) + '</span></td>'
      + '<td><span class="book-status ' + (b.status || 'pending') + '">' + esc(b.status || 'pendiente') + '</span></td>'
      + '<td>' + (b.chapter_count || 0) + '</td>'
      + '<td><div class="progress-bar-bg"><div class="progress-bar-fill'
      + ((b.progress || 0) >= 100 ? '' : (b.progress > 0 ? ' partial' : ' pending'))
      + '" style="width:' + Math.min(b.progress || 0, 100) + '%"></div></div></td>'
      + '<td><span class="book-status ' + jobStatusClass(job && job.status) + '">'
      + esc(job ? JOB_STATUS_LABEL[job.status] : '—') + '</span></td>'
      + '<td>' + (b.has_docx ? '<span style="color:var(--success-green)">✓</span>' : '—') + '</td>'
      + '</tr>';
  }).join('');
  html += '</tbody></table>';
  section.innerHTML = html;
}
function sortBooks(field) {
  if (!state.books.length) return;
  state.books.sort((a, b) =>
    field === 'progress'
      ? (b.progress || 0) - (a.progress || 0)
      : String(a[field] || '').localeCompare(String(b[field] || ''))
  );
  renderBooks();
}
function findJobForBook(bid) {
  return (state.jobs || []).find(j => j.book_id === bid) ||
    (state.autopilot && state.autopilot.book_id === bid ? state.autopilot : null);
}

// ---------- Living AI Factory — pipeline de 8 fases reales ----------
function renderLivingPipeline() {
  const container = document.getElementById('living-pipeline');
  if (!container) return;
  let flow = '';
  AUTOPILOT_PHASES.forEach((ph, i) => {
    const real = realPhase(ph.id);
    const status = real.status;                       // PENDING/RUNNING/RETRY/PASS/FAIL
    const css = PHASE_CSS[status] || 'idle';
    const label = PHASE_STATUS_LABEL[status] || status;
    const capLabel = CAPABILITY_LABELS[ph.capability] || ph.capability;

    let detail = '<span class="station-status">' + label + '</span>';
    if (real.attempts && real.attempts > 0) detail += '<span class="station-attempt">intento ' + real.attempts + '</span>';
    if (real.duration != null) detail += '<span class="station-duration">' + Number(real.duration).toFixed(2) + 's</span>';
    if (real.module) detail += '<span class="station-module">' + esc(real.module) + '</span>';
    if (status === 'PASS' && real.metrics) {
      const m = real.metrics;
      const bits = [];
      if (m.word_count != null) bits.push('palabras ' + m.word_count);
      if (m.chapter_count != null) bits.push('capítulos ' + m.chapter_count);
      if (m.placeholder_detected != null) bits.push('placeholders ' + (m.placeholder_detected ? 'SÍ' : 'NO'));
      if (m.quality_status) bits.push('QC ' + esc(m.quality_status));
      if (bits.length) detail += '<span class="station-metrics">' + bits.join(' · ') + '</span>';
    }
    if (real.error) detail += '<span class="station-error" title="' + esc(real.error) + '">' + esc(trunc(real.error, 80)) + '</span>';

    const taskText = real.task_id ? ('Tarea #' + real.task_id) : capLabel;
    flow += '<div class="module-station ' + css + '" data-phase="' + ph.id + '" role="button" tabindex="0" aria-label="' + ph.label + '">'
      + '<div class="station-glow"></div>'
      + '<div class="station-core">'
        + '<div class="station-header"><span class="station-name">' + ph.label + '</span><span class="station-led"></span></div>'
        + '<div class="worker-agent">' + workerToolSvg(ph.tool, css) + '</div>'
        + '<div class="worker-task">' + esc(taskText) + '</div>'
        + detail
        + '<div class="station-activity"><span class="activity-fill"></span></div>'
      + '</div>'
    + '</div>';
    if (i < AUTOPILOT_PHASES.length - 1) {
      const nextCss = PHASE_CSS[(realPhase(AUTOPILOT_PHASES[i + 1].id)).status] || 'idle';
      const active = (css === 'running' || css === 'retry' || nextCss === 'running' || nextCss === 'retry');
      flow += '<div class="factory-connector ' + (active ? 'active' : '') + '"><span class="flow-dot"></span></div>';
    }
  });
  container.innerHTML = '<div class="factory-flow">' + flow + '</div>';
}

// ---------- Current Book (conectado a /api/stats + job real) ----------
function renderCurrentBook() {
  const container = document.getElementById('current-book-body');
  if (!container) return;
  const book = state.currentBookDetail ? state.currentBookDetail.book : (state.currentStats.current_book || null);
  const job = state.autopilot;
  if (!book && !job) {
    container.innerHTML = '<div class="current-book-empty"><div class="cb-status-line"><span class="cb-flag"></span><span>NINGÚN LIBRO ACTIVO</span></div>'
      + '<div class="cb-sub">Selecciona o crea un libro y pulsa INICIAR AUTOPILOT.</div></div>';
    return;
  }
  const title = (book && book.title) || 'Sin título';
  const bookId = (book && book.id) || (job && job.book_id) || '--';
  const jobStatus = job ? job.status : null;
  const statusText = jobStatus ? (JOB_STATUS_LABEL[jobStatus] || jobStatus) : ((book && book.status) || 'pendiente');

  const cp = (state.currentStats.current_stats || {});
  const currentPhaseId = job ? job.current_phase : cp.current_phase;
  const currentPhaseLabel = currentPhaseId ? phaseLabel(currentPhaseId) : '—';

  // Conteos reales de fases del job
  let counts = { pending: 0, running: 0, pass: 0, fail: 0, retry: 0 };
  if (job && job.phases) {
    job.phases.forEach(p => {
      const s = (p.status || 'PENDING').toLowerCase();
      if (counts.hasOwnProperty(s)) counts[s]++; else counts.pending++;
    });
  }
  const totalPhases = AUTOPILOT_PHASES.length;
  const donePhases = counts.pass;

  // capítulos / palabras (reales)
  let chapterCount = (state.currentBookDetail && state.currentBookDetail.stats.total_chapters) || 0;
  let wordCount = (state.currentBookDetail && state.currentBookDetail.stats.total_words) || 0;
  const qg = job && job.phases && job.phases.find(p => p.id === 'quality_gate');
  if (!wordCount && qg && qg.metrics && qg.metrics.word_count != null) wordCount = qg.metrics.word_count;
  const docx = job ? job.docx_path : null;
  const attempts = job ? (job.attempts || 0) : 0;
  const duration = job ? (job.duration || 0) : 0;
  const err = job && job.error ? esc(job.error) : '';

  let html = '<div class="current-book-title">' + esc(title) + '</div>'
    + '<div class="current-book-meta">'
    + '<span class="cb-id">Libro #' + esc(bookId) + '</span>'
    + '<span class="cb-status ' + (job ? jobStatusClass(job.status) : 'pending') + '"> · ' + esc(statusText) + '</span>'
    + '</div>'
    + '<div class="cb-phase-line"><span class="cb-label">FASE ACTUAL:</span> ' + esc(currentPhaseLabel) + '</div>'
    + '<div class="current-book-progress">'
    + '<div class="current-book-bar"><div class="current-book-fill' + (donePhases ? '' : ' pending') + '" style="width:' + Math.round((donePhases / totalPhases) * 100) + '%"></div></div>'
    + '<div class="current-book-pct">' + donePhases + '/' + totalPhases + ' fases</div>'
    + '</div>'
    + '<div class="book-metrics">'
    + '<span>Capítulos: ' + chapterCount + '</span>'
    + '<span class="metric-active">Palabras: ' + wordCount + '</span>'
    + '<span>Intentos job: ' + attempts + '</span>'
    + '<span>Duración: ' + Number(duration).toFixed(2) + 's</span>'
    + '</div>'
    + '<div class="phase-counts">'
    + '<span class="c-pending">' + PHASE_STATUS_LABEL.PENDING + ': ' + counts.pending + '</span>'
    + '<span class="c-running">' + PHASE_STATUS_LABEL.RUNNING + ': ' + counts.running + '</span>'
    + '<span class="c-retry">' + PHASE_STATUS_LABEL.RETRY + ': ' + counts.retry + '</span>'
    + '<span class="c-pass">' + PHASE_STATUS_LABEL.PASS + ': ' + counts.pass + '</span>'
    + '<span class="c-fail">' + PHASE_STATUS_LABEL.FAIL + ': ' + counts.fail + '</span>'
    + '</div>';
  if (err) html += '<div class="station-error">' + err + '</div>';
  if (docx) html += '<div class="cb-docx"><span class="cb-label">DOCX:</span> <code class="docx-path">' + esc(docx) + '</code></div>';
  container.innerHTML = html;
}

// ---------- BOOK READY ----------
// Solo cuando el backend lo confirma: status COMPLETED + docx_path válido.
function renderBookReady() {
  const banner = document.getElementById('book-ready-banner');
  if (!banner) return;
  const job = state.autopilot;

  // Nunca inferir COMPLETED por el porcentaje de fases: dato real del job.
  const isReady = !!(job && job.status === 'COMPLETED' && job.docx_path);
  if (!isReady) {
    banner.style.display = 'none';
    banner.innerHTML = '';
    return;
  }

  const book = state.currentBookDetail
    ? (state.currentBookDetail.book || {})
    : (state.currentStats.current_book || {});
  const stats = (state.currentBookDetail && state.currentBookDetail.stats) || {};

  const title = book.title || 'Sin título';
  const chapters = stats.total_chapters != null ? stats.total_chapters
    : (Array.isArray(book.chapters) ? book.chapters.length : '—');
  const words = stats.total_words != null ? stats.total_words : '—';

  // Quality Gate: solo el estado real de la fase (backend es autoridad).
  let qgLabel = '—';
  if (job.phases) {
    const qg = job.phases.find(p => p.id === 'quality_gate');
    if (qg) {
      qgLabel = qg.status || '—';
      if (qg.metrics && qg.metrics.quality_gate) qgLabel = qg.metrics.quality_gate;
    }
  }

  const duration = Number(job.duration || 0).toFixed(2);
  const ts = job.updated_at || job.completed_at || job.created_at || '';
  const dateTime = ts ? new Date(ts.replace(' ', 'T')).toLocaleString('es-ES') : '—';
  const downloadUrl = '/api/books/' + encodeURIComponent(job.book_id) + '/docx';

  banner.style.display = '';
  banner.innerHTML = '<div class="book-ready-card">'
    + '<div class="book-ready-title">📦 LIBRO LISTO</div>'
    + '<div class="book-ready-meta">Libro #' + esc(job.book_id) + ' · ' + esc(JOB_STATUS_LABEL.COMPLETED) + '</div>'
    + '<div class="book-ready-grid">'
      + '<div><span class="book-ready-label">Título</span><div class="book-ready-value">' + esc(title) + '</div></div>'
      + '<div><span class="book-ready-label">Capítulos</span><div class="book-ready-value">' + esc(chapters) + '</div></div>'
      + '<div><span class="book-ready-label">Palabras</span><div class="book-ready-value">' + esc(words) + '</div></div>'
      + '<div><span class="book-ready-label">Control de calidad</span><div class="book-ready-value">' + esc(qgLabel) + '</div></div>'
      + '<div><span class="book-ready-label">Duración</span><div class="book-ready-value">' + esc(duration) + 's</div></div>'
      + '<div><span class="book-ready-label">Fecha/Hora</span><div class="book-ready-value">' + esc(dateTime) + '</div></div>'
    + '</div>'
    + '<div class="book-ready-docx"><span class="book-ready-label">DOCX real</span>'
    + '<code class="docx-path">' + esc(job.docx_path) + '</code></div>'
    + '<div class="book-ready-actions">'
      + '<a class="btn btn-success" href="' + esc(downloadUrl) + '" download>⬇ ABRIR / DESCARGAR DOCX</a>'
    + '</div>'
  + '</div>';
}

function downloadDocx() {
  const job = state.autopilot;
  if (!job || !job.docx_path || !job.book_id) return;
  // El DOCX lo sirve el backend real al hacer GET; navego sin falsificar nada.
  window.location.href = '/api/books/' + encodeURIComponent(job.book_id) + '/docx';
}

// ---------- Autopilot controls (Iniciar / Cancelar / Reintentar) ----------
function bindAutopilotButtons() {
  const j = state.autopilot;
  const sel = state.selectedBookId;
  const loading = state.autopilotLoading;
  setButton('autopilot-start', startAutopilot, !!sel && !loading && (!j || j.status === 'CANCELLED'));
  setButton('autopilot-cancel', cancelAutopilot, !!sel && !loading && !!j && (j.status === 'PENDING' || j.status === 'RUNNING'));
  setButton('autopilot-retry', retryAutopilot, !!sel && !loading && !!j && j.status === 'FAILED');
  setButton('autopilot-view', viewBookDetail, !!j && j.status === 'COMPLETED' && !loading);
}
function renderAutopilotControls() {
  const box = document.getElementById('autopilot-actions');
  if (!box) return;
  const job = state.autopilot;
  const sel = state.selectedBookId;
  const loading = state.autopilotLoading;
  if (!sel) {
    box.innerHTML = '<span class="autopilot-hint">Selecciona o crea un libro para iniciar el autopilot.</span>';
    return;
  }
  if (loading) {
    box.innerHTML = '<span class="autopilot-chip running">PROCESANDO…</span>';
    return;
  }
  let html = '';
  if (!job) {
    html = '<button type="button" id="autopilot-start" class="btn btn-command-primary">INICIAR AUTOPILOT</button>';
  } else if (job.status === 'PENDING' || job.status === 'RUNNING') {
    html = '<span class="autopilot-chip running">' + esc(JOB_STATUS_LABEL[job.status]) + '</span>'
      + '<button type="button" id="autopilot-cancel" class="btn btn-danger">CANCELAR</button>'
      + '<span class="autopilot-sub">fase: ' + esc(phaseLabel(job.current_phase)) + '</span>';
  } else if (job.status === 'FAILED') {
    html = '<span class="autopilot-chip error">' + esc(JOB_STATUS_LABEL[job.status]) + '</span>'
      + '<button type="button" id="autopilot-retry" class="btn btn-primary">REINTENTAR</button>'
      + (job.error ? '<span class="autopilot-error" title="' + esc(job.error) + '">' + esc(trunc(job.error, 100)) + '</span>' : '');
  } else if (job.status === 'CANCELLED') {
    html = '<span class="autopilot-chip idle">CANCELADO</span>'
      + '<button type="button" id="autopilot-start" class="btn btn-command-primary">REINICIAR AUTOPILOT</button>';
  } else if (job.status === 'COMPLETED') {
    html = '<span class="autopilot-chip success">' + esc(JOB_STATUS_LABEL[job.status]) + '</span>'
      + '<button type="button" id="autopilot-view" class="btn btn-success">VER LIBRO</button>';
  }
  box.innerHTML = html;
  bindAutopilotButtons();
}

// ---------- Modules ----------
function renderModules() {
  const section = document.getElementById('modules-section');
  if (!section) return;
  if (!state.modules.length) {
    section.innerHTML = '<div class="empty-state">No hay módulos</div>';
    return;
  }
  section.innerHTML = state.modules.map(m =>
    '<div class="module-card"><h4 class="module-name">' + esc(m.name) + '</h4>'
    + '<p class="module-desc">' + esc(m.description || m.capability || '') + '</p>'
    + '<span class="module-status ' + (m.status || 'idle') + '">' + esc(m.status || 'idle') + '</span>'
    + '</div>'
  ).join('');
}

// ---------- Tasks ----------
function renderTasks() {
  const container = document.getElementById('tasks-list');
  if (!container) return;
  if (!state.tasks.length) {
    container.innerHTML = '<div class="empty-state">No hay tareas pendientes</div>';
    return;
  }
  container.innerHTML = state.tasks.map(t =>
    '<div class="task-item ' + (t.status || 'pending') + '">'
    + '<span class="task-id">#' + t.id + '</span>'
    + '<span class="task-name">' + esc(t.name || t.module) + '</span>'
    + '<span class="task-status ' + jobStatusClass(t.status) + '">' + esc(JOB_STATUS_LABEL[t.status] || t.status) + '</span>'
    + '<span class="task-time">' + (t.created_at ? fmtTime(t.created_at) : '') + '</span>'
    + '</div>'
  ).join('');
}

// ---------- Logs ----------
function renderLogs() {
  const container = document.getElementById('logs-list');
  if (!container) return;
  if (!state.logs.length) {
    container.innerHTML = '<div class="log-empty">Sin logs todavía.</div>';
    return;
  }
  const levels = { system: 'LOG', info: 'INFO', warn: 'WARN', error: 'ERR' };
  container.innerHTML = state.logs.map(l =>
    '<div class="log-line log-' + (l.level || 'info') + '">'
    + '<span class="log-time">' + fmtTime(l.time) + '</span>'
    + '<span class="log-level">' + esc(levels[l.level] || 'INFO') + '</span>'
    + '<span class="log-message">' + esc(l.message) + '</span>'
    + '</div>'
  ).join('');
}

// ---------- Metrics ----------
function renderMetrics() {
  const container = document.getElementById('metrics-content');
  if (!container) return;
  const job = state.autopilot;
  if (!job) { container.innerHTML = '<div class="empty-state">Selecciona / inicia un libro</div>'; return; }
  const cp = job.phases ? job.phases[job.phases.length - 1] : null;
  const lastMetrics = (job.latest_metrics || (cp && cp.metrics) || {});
  const stat = state.currentStats;
  const rows = [
    { label: 'Estado del job', value: JOB_STATUS_LABEL[job.status] || job.status, cls: 'running' },
    { label: 'Duración (s)', value: Number(job.duration || 0).toFixed(2), cls: '' },
    { label: 'Intentos', value: job.attempts || 0, cls: '' },
    { label: 'Fase actual', value: phaseLabel(job.current_phase), cls: '' },
    { label: 'Última métrica word_count', value: lastMetrics.word_count != null ? lastMetrics.word_count : '—', cls: '' },
    { label: 'Estado de calidad', value: lastMetrics.quality_status ? String(lastMetrics.quality_status) : '—', cls: (lastMetrics.quality_status === 'FAIL' ? 'error' : '') },
    { label: 'marcadores detectados', value: lastMetrics.placeholder_detected != null ? (lastMetrics.placeholder_detected ? 'SÍ' : 'NO') : '—', cls: (lastMetrics.placeholder_detected ? 'error' : '') },
    { label: 'Libros totales', value: stat.total_books != null ? stat.total_books : '—', cls: '' },
  ];
  container.innerHTML = rows.map(r =>
    '<div class="metric-row"><span class="metric-label">' + esc(r.label) + '</span>'
    + '<span class="metric-value ' + (r.cls || '') + '">' + esc(r.value) + '</span></div>'
  ).join('');
}

// ---------- Activity Full (feed en vista de actividad) ----------
function renderActivityFull() {
  const container = document.getElementById('activity-full');
  if (!container) return;
  if (!state.feed.length) {
    container.innerHTML = '<div class="feed-empty">Sin actividad reciente.</div>';
    return;
  }
  const icon = { system: '[S]', started: '[>]', completed: '[OK]', failed: '[X]', retry: '[R]', fallback: '[!]' };
  container.innerHTML = state.feed.map(item =>
    '<div class="feed-item feed-' + item.type + '">'
    + '<span class="feed-time">' + fmtTime(item.time) + '</span>'
    + '<span class="feed-icon">' + (icon[item.type] || '[*]') + '</span>'
    + '<span class="feed-message">' + esc(item.message) + '</span>'
    + '</div>'
  ).join('');
}

// ---------- Indicador de conexión ----------
function updateStatusIndicator() {
  const sseDot = document.getElementById('sse-dot');
  if (sseDot) sseDot.classList.toggle('online', state.connected);
  const feedStatus = document.getElementById('feed-status');
  if (feedStatus) feedStatus.textContent = state.connected ? '● CONECTADO' : '● DESCONECTADO';
  const live = document.getElementById('live-modules');
  if (live) {
    const phasePass = state.autopilot ? state.autopilot.phases.filter(p => p.status === 'PASS').length : 0;
    live.textContent = 'LIBROS ' + state.books.length + ' · FASES OK ' + phasePass + '/' + AUTOPILOT_PHASES.length;
  }
}

// ---------- Navegación de vistas ----------
function showView(name) {
  document.querySelectorAll('.view-section').forEach(s => s.classList.toggle('active', s.dataset.view === name));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  if (name === 'control-room') refreshAutopilotView();
}
function setupNavigation() {
  document.querySelectorAll('.nav-item').forEach(b => {
    b.addEventListener('click', () => showView(b.dataset.view));
  });
}

// ---------- Acciones del autopilot (endpoints reales) ----------
function startAutopilot() {
  if (!state.selectedBookId || state.autopilotLoading) return;
  state.autopilotLoading = true;
  renderAutopilotControls();
  addFeed('system', 'Iniciando autopilot en libro #' + state.selectedBookId);
  apiFetch('/api/books/' + state.selectedBookId + '/autopilot/start', { method: 'POST' })
    .then(job => { state.autopilot = job; addFeed('started', 'Autopilot iniciado (job ' + (job.job_id || '') + ')'); refreshData(); })
    .catch(e => _showError('No se pudo iniciar: ' + e.message))
    .finally(() => { state.autopilotLoading = false; renderAutopilotControls(); });
}
function cancelAutopilot() {
  if (!state.selectedBookId || state.autopilotLoading) return;
  state.autopilotLoading = true;
  renderAutopilotControls();
  apiFetch('/api/books/' + state.selectedBookId + '/autopilot/cancel', { method: 'POST' })
    .then(job => { state.autopilot = job; addFeed('failed', 'Autopilot cancelado (job ' + (job.job_id || '') + ')'); refreshData(); })
    .catch(e => _showError('No se pudo cancelar: ' + e.message))
    .finally(() => { state.autopilotLoading = false; renderAutopilotControls(); });
}
function retryAutopilot() {
  if (!state.selectedBookId || state.autopilotLoading) return;
  state.autopilotLoading = true;
  renderAutopilotControls();
  apiFetch('/api/books/' + state.selectedBookId + '/autopilot/retry', { method: 'POST' })
    .then(job => { state.autopilot = job; addFeed('retry', 'Reintentando autopilot (job ' + (job.job_id || '') + ')'); refreshData(); })
    .catch(e => _showError('No se pudo reintentar: ' + e.message))
    .finally(() => { state.autopilotLoading = false; renderAutopilotControls(); });
}

function viewBookDetail() {
  if (state.autopilot && state.autopilot.docx_path) addFeed('completed', 'Libro listo: ' + state.autopilot.docx_path);
  showView('control-room');
}
function openCreateBook() {
  const modal = document.getElementById('book-create-modal');
  if (modal) modal.style.display = 'flex';
}
function closeCreateBook() {
  const modal = document.getElementById('book-create-modal');
  if (modal) modal.style.display = 'none';
}
async function createNewBook() {
  const title = ((document.getElementById('new-book-title') || {}).value || '').trim();
  const chapters = parseInt((document.getElementById('new-book-chapters') || {}).value || '1', 10);
  const idea = (document.getElementById('new-book-idea') || {}).value || '';
  const author = ((document.getElementById('new-book-author') || {}).value || '').trim();
  const genre = ((document.getElementById('new-book-genre') || {}).value || '').trim();
  const target_audience = ((document.getElementById('new-book-audience') || {}).value || '').trim();
  const imageCountRadio = document.querySelector('input[name="images_per_chapter"]:checked');
  const image_count = imageCountRadio ? parseInt(imageCountRadio.value, 10) : 3;
  const layout_preset = ((document.getElementById('layout-preset') || {}).value || 'editorial').trim();
  const layout_font = ((document.getElementById('layout-font') || {}).value || 'Georgia').trim();
  const layout_color = ((document.getElementById('layout-heading-color') || {}).value || '#1F3A5F').trim();
  const layout_alignment = ((document.getElementById('layout-alignment') || {}).value || 'justify').trim();
  const layout_config = {
    preset: layout_preset,
    overrides: {
      font_family: layout_font,
      heading_color: layout_color,
      body_alignment: layout_alignment,
    },
  };
  if (!title) { _showError('Indica un título'); return; }
  try {
    const book = await apiFetch('/api/books', { method: 'POST', body: JSON.stringify({ title, target_chapters: chapters || 1, idea, author, genre, target_audience, image_count, layout_config }) });
    const id = book.book_id || book.id;
    closeCreateBook();
    state.selectedBookId = id;
    addFeed('system', 'Libro creado: ' + title);
    await refreshData();
    showView('control-room');
  } catch (e) { _showError('Error creando libro: ' + e.message); }
}

// ---------- SSE (eventos reales con nombre) ----------
function connectSSE() {
  if (USE_MOCK || typeof EventSource === 'undefined') return;
  const es = new EventSource('/api/stream');
  es.onopen = () => {
    const wasConnected = state.connected;
    state.connected = true;
    updateStatusIndicator();
    // Reconstruir desde GET tras (re)conexión; nunca falsar estado del job.
    refreshData();
    if (wasConnected === false) addFeed('system', 'SSE reconectado — estado reconstruido desde el backend');
  };
  es.onerror = () => {
    state.connected = false;
    updateStatusIndicator();
    addFeed('system', 'SSE desconectado — se reintentará sin alterar el estado del job');
  };
  es.addEventListener('job_started', (e) => {
    const d = JSON.parse(e.data);
    addFeed('started', 'Job ' + (d.status || 'RUNNING') + ' · libro #' + d.book_id + ' (' + (d.job_id || '') + ')');
    refreshAutopilotView();
  });
  es.addEventListener('phase_started', (e) => {
    const d = JSON.parse(e.data);
    addFeed('started', 'Fase INICIADA: ' + phaseLabel(d.phase) + ' (' + (d.label || '') + ') · intento ' + (d.attempt || 1));
    refreshAutopilotView();
  });
  es.addEventListener('phase_progress', (e) => {
    refreshAutopilotView(); // sin porcentajes inventados
  });
  es.addEventListener('phase_completed', (e) => {
    const d = JSON.parse(e.data);
    addFeed('completed', 'Fase COMPLETADA: ' + phaseLabel(d.phase) + ' · '
      + (d.duration != null ? Number(d.duration).toFixed(2) + 's' : '')
      + ' · módulo ' + (d.module || '—'));
    if (d.phase === 'docx' && d.metrics && d.metrics.docx_path) addFeed('completed', 'DOCX generado: ' + d.metrics.docx_path);
    refreshAutopilotView();
  });
  es.addEventListener('phase_failed', (e) => {
    const d = JSON.parse(e.data);
    addFeed(d.will_retry ? 'retry' : 'failed',
      'Fase FALLIDA: ' + phaseLabel(d.phase) + ' · ' + (d.error || 'error') + ' (intento ' + (d.attempt || 1) + '/' + (d.max_attempts || 1) + (d.will_retry ? ', reintentará' : '') + ')');
    refreshAutopilotView();
  });
  es.addEventListener('job_completed', (e) => {
    const d = JSON.parse(e.data);
    addFeed('completed', 'Job COMPLETADO · ' + (d.docx_path ? 'DOCX: ' + d.docx_path : 'sin DOCX'));
    refreshAutopilotView();
  });
  es.addEventListener('job_failed', (e) => {
    const d = JSON.parse(e.data);
    addFeed('failed', 'Job FALLIDO · fase ' + (d.current_phase || '') + ' · ' + (d.error || ''));
    refreshAutopilotView();
  });
  es.addEventListener('task_started', (e) => { const d = JSON.parse(e.data); addLog('info', 'Tarea #' + d.task_id + ' en ejecución (' + (d.module_id || '') + ')'); });
  es.addEventListener('task_completed', (e) => { const d = JSON.parse(e.data); addLog('info', 'Tarea #' + d.task_id + ' completada'); });
  es.addEventListener('task_failed', (e) => { const d = JSON.parse(e.data); addLog('error', 'Tarea #' + d.task_id + ' falló'); });
  es.addEventListener('central_ai_decision', (e) => { const d = JSON.parse(e.data); addLog('info', 'IA eligió ' + (d.module_id || '') + ' para #' + d.task_id); });
}

// ---------- Init ----------
function initApp() {
  setupNavigation();
  const btnNewProject = document.getElementById('btn-new-project');
  if (btnNewProject) btnNewProject.addEventListener('click', openCreateBook);
  const btnRefresh = document.getElementById('btn-refresh');
  if (btnRefresh) btnRefresh.addEventListener('click', () => refreshData());
  const btnOpenProject = document.getElementById('btn-open-project');
  if (btnOpenProject) btnOpenProject.addEventListener('click', () => showView('books'));
  const btnViewActivity = document.getElementById('btn-view-activity');
  if (btnViewActivity) btnViewActivity.addEventListener('click', () => showView('activity'));
  const btnViewSystem = document.getElementById('btn-view-system');
  if (btnViewSystem) btnViewSystem.addEventListener('click', () => showView('config'));
  const btnCreate = document.getElementById('btn-create-book');
  if (btnCreate) btnCreate.addEventListener('click', createNewBook);
  const btnCancelCreate = document.getElementById('btn-cancel-create');
  if (btnCancelCreate) btnCancelCreate.addEventListener('click', closeCreateBook);
  connectSSE();
  refreshData();
  setCmdStatus('SISTEMA LISTO', 'online');
}
document.addEventListener('DOMContentLoaded', initApp);
