/* Photo Sorter browser interface. Talks only to the local server it was loaded from. */
'use strict';

const T = new URLSearchParams(location.search).get('t') || sessionStorage.getItem('ps_t') || '';
if (T) { try { sessionStorage.setItem('ps_t', T); } catch (e) { /* private mode */ } }
if (location.search) history.replaceState(null, '', '/');

const $ = (sel, root = document) => root.querySelector(sel);
const app = $('#app');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n) => Number(n || 0).toLocaleString();
const thumb = (id) => `/thumb/${id}?t=${encodeURIComponent(T)}`;
const preview = (id) => `/preview/${id}?t=${encodeURIComponent(T)}`;

async function api(path, body) {
  const opts = { headers: { 'X-Token': T } };
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok || data.error) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $('#toast');
  t.innerHTML = `<div class="${isErr ? 'err' : ''}">${esc(msg)}</div>`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.innerHTML = ''; }, isErr ? 6000 : 2500);
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); return null; }
}

/* ---------------- state ---------------- */
let S = null;
const ui = {
  step: 1, colKey: null, detail: null, sel: new Set(), anchor: null,
  f: { day: '', fmt: { RAW: true, JPG: true, HEIC: true, OTHER: true }, cam: '', gps: '', rating: '', del: '' },
  plan: null, result: null, confirmMove: false, confirmUndo: false,
};

async function loadState() { S = await api('/api/state'); return S; }

/* ---------------- steps nav ---------------- */
function renderSteps() {
  const has = S && S.collections.length > 0;
  const steps = [[1, 'Choose photos'], [2, 'Name, rate, tag'], [3, 'Move']];
  if (ui.result) steps.push([4, 'Done']);
  $('#steps').innerHTML = steps.map(([n, label]) => {
    const enabled = n === 1 || (n === 4 && ui.result) || (n < 4 && has);
    const done = n < ui.step ? ' ✓' : '';
    return `<button type="button" data-step="${n}" ${ui.step === n ? 'aria-current="step"' : ''} ${enabled ? '' : 'disabled'}>${n} ${label}${done}</button>`;
  }).join('');
  $('#where').textContent = S && S.source ? `${S.source} · ${num(S.summary.photos)} photos` : 'Local only · offline';
}
$('#steps').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-step]');
  if (b && !b.disabled) go(Number(b.dataset.step));
});

async function go(step) {
  ui.step = step; ui.confirmMove = false; ui.confirmUndo = false;
  window.scrollTo(0, 0);
  await render();
}

async function render() {
  renderSteps();
  if (ui.step === 1) return renderSource();
  if (ui.step === 2) return renderGroups();
  if (ui.step === 3) return renderMove();
  if (ui.step === 4) return renderDone();
}

/* ---------------- helpers ---------------- */
function fmtOffset(sec) {
  if (!sec) return '+0:00';
  const sign = sec < 0 ? '−' : '+';
  let s = Math.abs(sec);
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return `${sign}${h}:${String(m).padStart(2, '0')}${s ? ':' + String(s).padStart(2, '0') : ''}`;
}
function parseOffset(txt) {
  const t = String(txt).trim().replace('−', '-').replace(/\s/g, '');
  if (!t) return 0;
  const m = t.match(/^([+-]?)(\d{1,3})(?::(\d{1,2}))?(?::(\d{1,2}))?$/);
  if (!m) throw new Error('Clock correction: type hours:minutes, for example +1:00 or -0:03');
  const v = Number(m[2]) * 3600 + Number(m[3] || 0) * 60 + Number(m[4] || 0);
  return m[1] === '-' ? -v : v;
}
async function pick(inputId, prompt) {
  const r = await guard(() => api('/api/pick-folder', { prompt }));
  if (r && r.path) { $('#' + inputId).value = r.path; $('#' + inputId).dispatchEvent(new Event('change')); }
}

/* ---------------- step 1: source ---------------- */
function renderSource() {
  const st = S.settings;
  const scanned = S.summary.photos > 0;
  const running = S.scan.running;
  const home = st.home ? st.home.join(', ') : '';
  const det = S.detected_home ? S.detected_home.map((x) => x.toFixed(3)).join(', ') : null;
  app.innerHTML = `
  <div class="page">
    <div>
      <h1>Where are the photos to sort?</h1>
      <p class="muted">Nothing is changed on disk until you press Move in step 3.</p>
    </div>
    <section class="card">
      <label for="src"><b>Source folder</b> <span class="muted small">(unsorted photos)</span></label>
      <div class="row">
        <input id="src" type="text" value="${esc(S.source)}" placeholder="/Users/you/Pictures/Unsorted" autocomplete="off">
        <button type="button" class="btn" id="pickSrc">Choose…</button>
      </div>
      <label class="check"><input type="checkbox" id="rec" ${S.recursive ? 'checked' : ''}>Include subfolders</label>
      <label for="dst"><b>Destination folder</b> <span class="muted small">(where sorted folders go; used to spot folders that already exist, can be changed in step 3)</span></label>
      <div class="row">
        <input id="dst" type="text" value="${esc(S.dest)}" placeholder="/Users/you/Pictures/Photos" autocomplete="off">
        <button type="button" class="btn" id="pickDst">Choose…</button>
      </div>
    </section>

    <section class="card">
      <h2>Grouping</h2>
      <div class="grid3">
        <div class="field"><label for="gap">New collection after a gap of</label>
          <div class="row"><input id="gap" type="number" min="0.1" step="0.5" value="${esc(st.gap_days)}" style="width:90px"><span class="muted">days</span></div></div>
        <div class="field"><label for="km">…or a location jump of</label>
          <div class="row"><input id="km" type="number" min="0" step="1" value="${esc(st.km)}" style="width:90px"><span class="muted">km (when GPS exists)</span></div></div>
        <div class="field"><label for="home">Home location (optional)</label>
          <input id="home" type="text" value="${esc(home)}" placeholder="${det ? 'Detected: ' + esc(det) : 'latitude, longitude'}"></div>
      </div>
      <label class="check"><input type="checkbox" id="trips" ${st.keep_trips_together ? 'checked' : ''}>Keep trips together: a location jump only splits when leaving or returning home</label>
      ${scanned ? `<div class="row"><button type="button" class="btn small" id="regroup" ${running ? 'disabled' : ''}>Update groups with these settings</button></div>` : ''}
    </section>

    <div id="scanArea">${scanArea()}</div>
    ${scanned && !running ? camerasCard() : ''}

    <div class="actions">
      <button type="button" class="btn ${scanned ? 'ghost' : 'primary'}" id="scan" ${running ? 'disabled' : ''}>${scanned ? 'Scan again' : 'Scan and group'}</button>
      ${scanned && !running ? '<button type="button" class="btn primary" id="toGroups">Review collections →</button>' : ''}
    </div>
  </div>`;

  $('#pickSrc').onclick = () => pick('src', 'Choose the folder with the photos to sort');
  $('#pickDst').onclick = () => pick('dst', 'Choose the destination folder');
  $('#scan').onclick = startScan;
  if ($('#toGroups')) $('#toGroups').onclick = () => go(2);
  if ($('#regroup')) $('#regroup').onclick = () => guard(async () => {
    await api('/api/settings', settingsBody());
    await loadState(); toast('Groups updated'); renderSource();
  });
  app.querySelectorAll('input[data-cam]').forEach((inp) => {
    inp.onchange = () => guard(async () => {
      await api('/api/camera-offset', { key: inp.dataset.cam, seconds: parseOffset(inp.value) });
      await loadState(); toast('Clock corrected, groups updated'); renderSource();
    });
  });
  if ($('#lineup')) $('#lineup').onclick = () => openLineup();
  if (running) pollScan();
}

function settingsBody() {
  return {
    gap_days: Number($('#gap').value || 7), km: Number($('#km').value || 0),
    home: $('#home').value.trim(), keep_trips_together: $('#trips').checked,
  };
}

function scanArea() {
  const sc = S.scan;
  if (sc.running) {
    const pct = sc.total ? Math.round(100 * sc.done / sc.total) : 5;
    return `<section class="card"><b>${esc(sc.phase)}…</b>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <span class="muted small">${sc.total ? `${num(sc.done)} of ${num(sc.total)}` : ''}</span></section>`;
  }
  if (sc.error) return `<div class="note">The scan stopped: ${esc(sc.error)}</div>`;
  const s = S.summary;
  if (!s.photos) return '';
  const f = s.formats;
  const parts = [['RAW', f.raw], ['DNG', f.dng], ['JPEG', f.jpeg], ['HEIC', f.heic], ['other', f.other]]
    .filter(([, n]) => n).map(([k, n]) => `${k} ${num(n)}`).join(' · ');
  return `<section class="stats" aria-label="Scan result">
    <div><b>${num(s.photos)}</b><span>photos</span></div>
    <div><b>${num(s.collections)}</b><span>collections</span></div>
    <div><b>${num(s.cameras)}</b><span>cameras</span></div>
    <div><b>${num(s.gps)}</b><span>with GPS</span></div>
    <div><b>${num(s.existing_folders)}</b><span>existing folders in destination</span></div>
    <div style="color:#b4d6bf;font-size:13px">${parts}${s.pairs ? ` · ${num(s.pairs)} RAW+JPEG pairs` : ''}</div>
  </section>
  ${s.no_date ? `<div class="note">${num(s.no_date)} photo(s) have no date inside the file; the file date was used instead.</div>` : ''}
  ${s.other_files ? `<p class="muted small">${num(s.other_files)} other file(s) (videos, documents) were found and will not be touched.</p>` : ''}`;
}

function camerasCard() {
  const rows = S.cameras.map((c) => `
    <tr><td>${esc(c.name)}</td><td class="muted">${num(c.count)}</td>
      <td>${c.tz && !c.offset ? `<span class="muted">Phone time zone in file</span><br>` : ''}
        <label><span class="muted small" style="display:none">${esc(c.name)} correction</span>
        <input type="text" data-cam="${esc(c.key)}" value="${esc(fmtOffset(c.offset))}" aria-label="${esc(c.name)} clock correction"></label></td>
      <td class="muted">${esc(c.first)}</td></tr>`).join('');
  return `<section class="card" aria-label="Camera clocks">
    <div class="row" style="justify-content:space-between"><h2>Camera clocks</h2>
      <span class="muted small">Corrections are remembered per camera (model and serial number)</span></div>
    <div style="overflow-x:auto"><table class="cams">
      <thead><tr><th>Camera</th><th>Photos</th><th>Clock correction (h:mm)</th><th>First photo after correction</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="row"><button type="button" class="btn small" id="lineup">Line up cameras…</button>
      <span class="muted small">Shows the photos of one day from all cameras in time order, so you can match the same moment</span></div>
  </section>`;
}

async function startScan() {
  const body = { source: $('#src').value.trim(), dest: $('#dst').value.trim(), recursive: $('#rec').checked, ...settingsBody() };
  if (!body.source) return toast('Choose the source folder first', true);
  const r = await guard(() => api('/api/scan', body));
  if (!r) return;
  ui.colKey = null; ui.sel.clear();
  await loadState();
  renderSource();
}

async function pollScan() {
  clearTimeout(pollScan.t);
  pollScan.t = setTimeout(async () => {
    await guard(loadState);
    if (ui.step !== 1) return;
    if (S.scan.running) { $('#scanArea').innerHTML = scanArea(); pollScan(); } else { renderSteps(); renderSource(); }
  }, 600);
}

/* ---------------- camera line-up ---------------- */
const CAM_COLORS = ['#5aa9ff', '#f2b84b', '#6fcf8f', '#e88ad1', '#b39dff', '#f07a5e'];
async function openLineup(date) {
  const L = await guard(() => api('/api/lineup' + (date ? `?date=${date}` : '')));
  if (!L) return;
  const ov = $('#overlay');
  if (!L.days.length) {
    ov.innerHTML = `<div class="ov-top"><b>Line up cameras</b><button type="button" class="btn small" id="ovClose">Close</button></div>
      <div class="ov-body"><p class="muted">No day has photos from more than one camera.</p></div>`;
  } else {
    const camKeys = [...new Set(L.photos.map((p) => p.camera_key))];
    const color = (k) => CAM_COLORS[camKeys.indexOf(k) % CAM_COLORS.length];
    const cams = S.cameras.filter((c) => camKeys.includes(c.key));
    ov.innerHTML = `
      <div class="ov-top">
        <div class="row"><b>Line up cameras</b>
          <label class="muted small" for="luDay">Day</label>
          <select id="luDay">${L.days.map((d) => `<option value="${d.date}" ${d.date === L.date ? 'selected' : ''}>${esc(d.label)} · ${d.cameras} cameras</option>`).join('')}</select>
        </div>
        <button type="button" class="btn small" id="ovClose">Close</button>
      </div>
      <div class="row" style="padding:0 18px 12px">
        ${cams.map((c) => `<label class="row" style="gap:6px;flex-wrap:nowrap"><span style="width:12px;height:12px;border-radius:3px;background:${color(c.key)}"></span>${esc(c.name)}
          <input type="text" data-lucam="${esc(c.key)}" value="${esc(fmtOffset(c.offset))}" style="width:100px;flex:none" aria-label="${esc(c.name)} clock correction"></label>`).join('')}
        <span class="muted small">Change a correction until the same moments sit next to each other. Photos already sorted into the destination are included.</span>
      </div>
      <div class="strip">${L.photos.map((p) => `
        <figure style="border-color:${color(p.camera_key)}"><img loading="lazy" src="${thumb(p.id)}" alt="${esc(p.name)}">
          <figcaption><span>${esc(p.time)}</span><span class="muted">${esc(p.camera)}${p.sorted ? ' · already in ' + esc(p.folder) : ''}</span></figcaption></figure>`).join('')}</div>`;
    $('#luDay').onchange = (e) => openLineup(e.target.value);
    ov.querySelectorAll('input[data-lucam]').forEach((inp) => {
      inp.onchange = () => guard(async () => {
        await api('/api/camera-offset', { key: inp.dataset.lucam, seconds: parseOffset(inp.value) });
        await loadState(); openLineup(L.date);
      });
    });
  }
  ov.hidden = false;
  $('#ovClose').onclick = closeOverlay;
  $('#ovClose').focus();
}
async function closeOverlay() {
  const ov = $('#overlay');
  const wasLineup = !!$('#luDay', ov) || ov.textContent.includes('Line up');
  ov.hidden = true; ov.innerHTML = '';
  ui.previewIdx = null;
  if (wasLineup && ui.step === 1) { await loadState(); renderSource(); }
}

/* ---------------- step 2: collections ---------------- */
async function renderGroups() {
  if (!S.collections.length) return go(1);
  if (!ui.colKey || !S.collections.find((c) => c.key === ui.colKey)) ui.colKey = S.collections[0].key;
  const d = await guard(() => api('/api/collection/' + ui.colKey));
  if (!d) return;
  if (!ui.detail || ui.detail.key !== d.key) { ui.sel.clear(); ui.anchor = null; ui.f.day = ''; }
  ui.detail = d;
  const idx = S.collections.findIndex((c) => c.key === d.key);
  app.innerHTML = `
  <div class="work">
    <aside class="side" aria-label="Collections">
      <h2>Collections · ${S.collections.length}</h2>
      ${S.collections.map((c) => `
        <button type="button" class="colbtn" data-col="${c.key}" ${c.key === d.key ? 'aria-current="true"' : ''}>
          <span class="n"><span class="nm ${c.unnamed ? 'un' : ''}">${c.unnamed ? esc(c.prefix + '-') + 'add a name' : esc(c.target)}</span><span class="muted small">${num(c.count)}</span></span>
          <span class="meta"><span class="badge ${c.kind === 'Holiday' ? 'hol' : ''}">${esc(c.kind)}</span>${c.existing_target ? '<span class="badge ex">existing</span>' : ''}${esc(c.dates)}</span>
        </button>`).join('')}
    </aside>
    <section class="main" aria-label="Collection">
      ${nameEditor(d, idx)}
      ${filtersBar(d)}
      <div id="days"></div>
    </section>
    <aside class="inspector" aria-label="Selected photos"><div class="sticky" id="insp"></div></aside>
  </div>
  <footer class="foot">
    <span id="footNote">${footText()}</span>
    <button type="button" class="btn primary" id="toMove">Next: destination →</button>
  </footer>`;
  bindGroups(d, idx);
  renderTiles();
  renderInspector();
}

function footText() {
  const unnamed = S.collections.filter((c) => c.unnamed).length;
  const del = S.collections.reduce((a, c) => a + c.to_delete, 0);
  return `<span class="${unnamed || del ? 'warn' : 'muted'} small">${unnamed ? `${unnamed} collection(s) have no name yet` : 'All collections have a name'}${del ? ` · ${num(del)} photo(s) marked for deletion` : ''}</span>`;
}

function nameEditor(d, idx) {
  const m = d.match;
  const usingExisting = m && d.use_existing;
  let notice = '';
  if (usingExisting) {
    notice = `<div class="info row" style="justify-content:space-between">
      <span style="flex:1 1 320px">The folder <b class="mono">${esc(m.name)}</b> already exists in the destination (${num(m.count)} photos${m.dates ? ', ' + esc(m.dates) : ''}). These photos match its dates and will be added to it.</span>
      <span class="row"><button type="button" class="btn small primary" aria-pressed="true">Add to existing folder</button>
      <button type="button" class="btn small ghost" id="ownName">Use a different name</button></span>
      ${m.keywords.length ? `<label class="check" style="flex-basis:100%"><input type="checkbox" id="copyKw" ${d.copy_keywords ? 'checked' : ''}>Also give these photos its keywords: ${esc(m.keywords.join(', '))}</label>` : ''}
    </div>`;
  } else if (m) {
    notice = `<div class="info row" style="justify-content:space-between"><span>These dates match the existing folder <b class="mono">${esc(m.name)}</b>.</span>
      <button type="button" class="btn small" id="useExisting">Add to it instead</button></div>`;
  } else if (d.existing_target) {
    notice = `<div class="info">A folder named <b class="mono">${esc(d.target)}</b> already exists in the destination. These photos will be added to it.</div>`;
  }
  const noDate = d.days.reduce((a, day) => a + day.photos.filter((p) => p.time_source === 'file').length, 0);
  return `<div class="field">
    <label for="cname" class="muted small">Folder name</label>
    <div class="row">
      ${usingExisting
        ? `<div class="namebox"><span class="prefix" style="flex:1">${esc(m.name)}</span></div>`
        : `<div class="namebox"><span class="prefix">${esc(d.prefix)}-</span><input id="cname" type="text" value="${esc(d.name)}" placeholder="add a name, e.g. Sardinia" autocomplete="off"></div>`}
      <button type="button" class="btn" id="merge" ${idx === 0 ? 'disabled' : ''}>Merge with previous</button>
      <button type="button" class="btn" id="split" title="Starts a new collection at the first selected photo">Split at selection</button>
    </div>
    <p class="muted small">${esc(d.dates)} · ${num(d.count)} photos · ${esc(d.cameras.join(', '))} ·
      <button type="button" class="badge ${d.kind === 'Holiday' ? 'hol' : ''}" id="kind" title="Click to change">${esc(d.kind)}</button>
      · name starts with year and month of the first photo${noDate ? ` · <span class="warn">${noDate} without a date in the file</span>` : ''}</p>
    ${notice}
  </div>`;
}

function allPhotos() { return ui.detail ? ui.detail.days.flatMap((d) => d.photos) : []; }
function fmtKey(p) {
  const parts = p.fmt.split('+');
  return parts.map((x) => (x === 'RAW' || x === 'DNG' ? 'RAW' : x === 'JPG' || x === 'JPEG' ? 'JPG' : x === 'HEIC' ? 'HEIC' : 'OTHER'));
}
function passes(p, day) {
  const f = ui.f;
  if (f.day && day.date !== f.day) return false;
  if (!fmtKey(p).some((k) => f.fmt[k])) return false;
  if (f.cam && p.camera !== f.cam) return false;
  if (f.gps === 'with' && p.lat == null) return false;
  if (f.gps === 'without' && p.lat != null) return false;
  if (f.rating === 'none' && p.rating) return false;
  if (f.rating && f.rating !== 'none' && p.rating < Number(f.rating)) return false;
  if (f.del === 'hide' && p.delete) return false;
  if (f.del === 'only' && !p.delete) return false;
  return true;
}
function visiblePhotos() {
  return ui.detail.days.flatMap((day) => day.photos.filter((p) => passes(p, day)));
}

function filtersBar(d) {
  const cams = [...new Set(allPhotos().map((p) => p.camera))];
  const f = ui.f;
  const chip = (k, label) => `<button type="button" class="chip" data-fmt="${k}" aria-pressed="${f.fmt[k]}">${label}</button>`;
  return `<div class="filters" role="group" aria-label="Filters">
    <span class="muted small">Filter</span>
    <select id="fDay" aria-label="Day"><option value="">All days</option>${d.days.map((day) => `<option value="${day.date}" ${f.day === day.date ? 'selected' : ''}>${esc(day.label)} (${day.photos.length})</option>`).join('')}</select>
    ${chip('RAW', 'RAW')}${chip('JPG', 'JPEG')}${chip('HEIC', 'HEIC')}${chip('OTHER', 'Other')}
    <select id="fCam" aria-label="Camera"><option value="">All cameras</option>${cams.map((c) => `<option ${f.cam === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
    <select id="fGps" aria-label="GPS"><option value="">GPS: any</option><option value="with" ${f.gps === 'with' ? 'selected' : ''}>With GPS</option><option value="without" ${f.gps === 'without' ? 'selected' : ''}>Without GPS</option></select>
    <select id="fRat" aria-label="Rating"><option value="">Rating: any</option><option value="none" ${f.rating === 'none' ? 'selected' : ''}>Unrated</option>${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${f.rating === String(n) ? 'selected' : ''}>${'★'.repeat(n)} or more</option>`).join('')}</select>
    <select id="fDel" aria-label="Deletion marks"><option value="">All photos</option><option value="hide" ${f.del === 'hide' ? 'selected' : ''}>Hide marked for deletion</option><option value="only" ${f.del === 'only' ? 'selected' : ''}>Only marked for deletion</option></select>
    <span style="flex-grow:1"></span>
    <button type="button" class="linkbtn" id="selAll">Select all shown</button>
    <span class="muted small" id="shown"></span>
  </div>`;
}

function bindGroups(d, idx) {
  app.querySelectorAll('[data-col]').forEach((b) => { b.onclick = () => { ui.colKey = b.dataset.col; renderGroups(); }; });
  $('#toMove').onclick = () => go(3);
  const nameInput = $('#cname');
  if (nameInput) {
    nameInput.onchange = () => guard(async () => {
      await api('/api/collection', { key: d.key, name: nameInput.value });
      await loadState(); renderGroups();
    });
    nameInput.onkeydown = (e) => { if (e.key === 'Enter') nameInput.blur(); };
  }
  if ($('#ownName')) $('#ownName').onclick = () => guard(async () => { await api('/api/collection', { key: d.key, use_existing: false }); await loadState(); renderGroups(); });
  if ($('#useExisting')) $('#useExisting').onclick = () => guard(async () => { await api('/api/collection', { key: d.key, use_existing: true }); await loadState(); renderGroups(); });
  if ($('#copyKw')) $('#copyKw').onchange = (e) => guard(async () => { await api('/api/collection', { key: d.key, copy_keywords: e.target.checked }); await loadState(); });
  $('#kind').onclick = () => guard(async () => {
    const next = d.kind === 'Holiday' ? 'Daily life' : 'Holiday';
    await api('/api/collection', { key: d.key, kind: next }); await loadState(); renderGroups();
  });
  $('#merge').onclick = () => guard(async () => {
    const prev = S.collections[idx - 1];
    await api('/api/merge', { key: d.key }); await loadState();
    ui.colKey = prev ? prev.key : null; renderGroups(); toast('Merged with the previous collection');
  });
  $('#split').onclick = () => guard(async () => {
    const order = allPhotos();
    const first = order.find((p) => ui.sel.has(p.id));
    if (!first) return toast('Select the photo where the new collection should start', true);
    if (first.id === order[0].id) return toast('That is already the first photo of this collection', true);
    await api('/api/split', { photo_id: first.id }); await loadState();
    ui.colKey = first.id; renderGroups(); toast('Split into a new collection');
  });
  const refilter = () => { renderTiles(); };
  $('#fDay').onchange = (e) => { ui.f.day = e.target.value; refilter(); };
  $('#fCam').onchange = (e) => { ui.f.cam = e.target.value; refilter(); };
  $('#fGps').onchange = (e) => { ui.f.gps = e.target.value; refilter(); };
  $('#fRat').onchange = (e) => { ui.f.rating = e.target.value; refilter(); };
  $('#fDel').onchange = (e) => { ui.f.del = e.target.value; refilter(); };
  app.querySelectorAll('[data-fmt]').forEach((b) => {
    b.onclick = () => { ui.f.fmt[b.dataset.fmt] = !ui.f.fmt[b.dataset.fmt]; b.setAttribute('aria-pressed', ui.f.fmt[b.dataset.fmt]); refilter(); };
  });
  $('#selAll').onclick = () => { ui.sel = new Set(visiblePhotos().map((p) => p.id)); renderTiles(); renderInspector(); };
}

function tileHtml(p) {
  const sel = ui.sel.has(p.id);
  return `<button type="button" class="tile ${sel ? 'sel' : ''} ${p.delete ? 'del' : ''}" data-id="${p.id}"
      aria-pressed="${sel}" aria-label="${esc(p.name)}${p.rating ? ', ' + p.rating + ' stars' : ''}${p.delete ? ', marked for deletion' : ''}">
    <span class="img"><img loading="lazy" src="${thumb(p.id)}" alt="">
      <span class="fmt">${esc(p.fmt)}</span>${p.delete ? '<span class="delb">DELETE</span>' : ''}</span>
    <span class="cap"><span>${esc(p.name)} · ${esc(p.time)}</span><span class="stars">${'★'.repeat(p.rating || 0)}</span></span>
  </button>`;
}

function renderTiles() {
  const box = $('#days');
  if (!box) return;
  let shown = 0;
  box.innerHTML = ui.detail.days.map((day) => {
    const ps = day.photos.filter((p) => passes(p, day));
    shown += ps.length;
    if (!ps.length) return '';
    return `<section class="day"><h3>${esc(day.label)} <span>${ps.length} photo(s)${ps.length !== day.photos.length ? ` of ${day.photos.length}` : ''}</span></h3>
      <div class="tiles">${ps.map(tileHtml).join('')}</div></section>`;
  }).join('') || '<p class="muted">No photo matches these filters.</p>';
  const all = allPhotos().length;
  $('#shown').textContent = `Showing ${shown} of ${all}`;
  box.querySelectorAll('.tile').forEach((t) => {
    t.onclick = (e) => clickTile(t.dataset.id, e);
    t.ondblclick = () => openPreview(t.dataset.id);
  });
}

function clickTile(id, e) {
  const vis = visiblePhotos().map((p) => p.id);
  if (e.shiftKey && ui.anchor && vis.includes(ui.anchor)) {
    const a = vis.indexOf(ui.anchor), b = vis.indexOf(id);
    const [lo, hi] = a < b ? [a, b] : [b, a];
    if (!(e.metaKey || e.ctrlKey)) ui.sel.clear();
    vis.slice(lo, hi + 1).forEach((x) => ui.sel.add(x));
  } else if (e.metaKey || e.ctrlKey) {
    ui.sel.has(id) ? ui.sel.delete(id) : ui.sel.add(id);
    ui.anchor = id;
  } else {
    ui.sel = new Set([id]); ui.anchor = id;
  }
  updateTileSelection();
  renderInspector();
}

function updateTileSelection() {
  app.querySelectorAll('.tile').forEach((t) => {
    const on = ui.sel.has(t.dataset.id);
    t.classList.toggle('sel', on); t.setAttribute('aria-pressed', on);
  });
}

function selectedPhotos() { return allPhotos().filter((p) => ui.sel.has(p.id)); }

function renderInspector() {
  const box = $('#insp');
  if (!box) return;
  const sel = selectedPhotos();
  const total = allPhotos().length;
  if (!sel.length) {
    box.innerHTML = `<div><h2>No photo selected</h2><p class="muted small" style="margin-top:6px">Click a photo to select it. Shift-click selects a range, ${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}-click adds or removes one. Double-click opens it large.</p></div>
      <div class="muted small">Keys: 0–5 set the rating, X marks for deletion, ${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+A selects all shown, Enter opens the photo.</div>
      ${recentHtml()}`;
    bindRecent(); return;
  }
  const ratings = new Set(sel.map((p) => p.rating || 0));
  const rating = ratings.size === 1 ? [...ratings][0] : null;
  const counts = {};
  sel.forEach((p) => p.keywords.forEach((k) => { counts[k] = (counts[k] || 0) + 1; }));
  const kws = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
  const allDel = sel.every((p) => p.delete);
  const one = sel.length === 1 ? sel[0] : null;
  box.innerHTML = `
    <div><h2>${sel.length === 1 ? '1 photo selected' : `${sel.length} photos selected`}</h2>
      ${rating === null ? '<p class="muted small">Different ratings</p>' : ''}</div>
    <div class="field"><b>Rating</b>
      <div class="row" role="group" aria-label="Rating" style="gap:4px">
        ${[1, 2, 3, 4, 5].map((n) => `<button type="button" class="starbtn ${rating !== null && n <= rating ? 'on' : ''}" data-star="${n}" aria-label="${n} star${n > 1 ? 's' : ''}">★</button>`).join('')}
      </div>
      <span class="muted small">Click the same star again to clear · keys 0–5</span></div>
    <div class="field"><label for="kwIn"><b>Keywords</b></label>
      <div class="row" style="gap:6px">${kws.map((k) => `<span class="kw ${counts[k] < sel.length ? 'part' : ''}" title="${counts[k] < sel.length ? `on ${counts[k]} of ${sel.length}` : ''}">${esc(k)}<button type="button" data-rmkw="${esc(k)}" aria-label="Remove ${esc(k)}">×</button></span>`).join('') || '<span class="muted small">None yet</span>'}</div>
      <input id="kwIn" type="text" placeholder="Add keyword, press Enter" autocomplete="off">
      ${recentHtml()}
      ${kws.length ? `<button type="button" class="btn small ghost" id="kwAll" style="color:var(--accent2)">Add these keywords to all ${num(total)} in this collection</button>` : ''}
    </div>
    <button type="button" class="btn danger" id="delBtn">${allDel ? 'Unmark for deletion (X)' : 'Mark for deletion (X)'}</button>
    ${one ? `<div class="muted small" style="border-top:1px solid var(--line);padding-top:12px;line-height:1.6">
      ${one.files.map(esc).join(' + ')}<br>${esc(one.camera)} · ${esc(one.time)}${one.time_source === 'file' ? ' (file date)' : ''}<br>
      ${one.lat != null ? `GPS ${one.lat.toFixed(4)}, ${one.lon.toFixed(4)}` : 'No GPS'}</div>` : ''}`;
  box.querySelectorAll('[data-star]').forEach((b) => {
    b.onclick = () => { const n = Number(b.dataset.star); setRating(rating === n ? 0 : n); };
  });
  box.querySelectorAll('[data-rmkw]').forEach((b) => { b.onclick = () => editKeywords([], [b.dataset.rmkw]); });
  const inp = $('#kwIn');
  inp.onkeydown = (e) => {
    if (e.key === 'Enter' && inp.value.trim()) {
      const ks = inp.value.split(',').map((s) => s.trim()).filter(Boolean);
      inp.value = ''; editKeywords(ks, [], true);
    }
  };
  bindRecent();
  if ($('#kwAll')) $('#kwAll').onclick = () => guard(async () => {
    const ids = allPhotos().map((p) => p.id);
    await api('/api/photos', { ids, add: kws });
    allPhotos().forEach((p) => kws.forEach((k) => { if (!p.keywords.includes(k)) p.keywords.push(k); }));
    toast(`Keywords added to ${ids.length} photos`); renderInspector();
  });
  $('#delBtn').onclick = toggleDelete;
}

function recentHtml() {
  const r = (S.recent_keywords || []);
  if (!r.length) return '';
  return `<div class="small muted">Recent: ${r.map((k) => `<button type="button" class="linkbtn" data-addkw="${esc(k)}">${esc(k)}</button>`).join(' · ')}</div>`;
}
function bindRecent() {
  app.querySelectorAll('[data-addkw]').forEach((b) => {
    b.onclick = () => { if (!ui.sel.size) return toast('Select photos first', true); editKeywords([b.dataset.addkw], []); };
  });
}

async function setRating(n) {
  const sel = selectedPhotos(); if (!sel.length) return;
  const ok = await guard(() => api('/api/photos', { ids: sel.map((p) => p.id), rating: n }));
  if (ok === null) return;
  sel.forEach((p) => { p.rating = n; });
  refreshTiles(sel); renderInspector();
}
async function editKeywords(add, remove, refocus) {
  const sel = selectedPhotos(); if (!sel.length) return;
  const ok = await guard(() => api('/api/photos', { ids: sel.map((p) => p.id), add, remove }));
  if (ok === null) return;
  sel.forEach((p) => {
    add.forEach((k) => { if (!p.keywords.includes(k)) p.keywords.push(k); });
    p.keywords = p.keywords.filter((k) => !remove.includes(k));
  });
  add.forEach((k) => { S.recent_keywords = [k, ...(S.recent_keywords || []).filter((x) => x !== k)].slice(0, 8); });
  renderInspector();
  if (refocus && $('#kwIn')) $('#kwIn').focus();
}
async function toggleDelete() {
  const sel = selectedPhotos(); if (!sel.length) return;
  const val = !sel.every((p) => p.delete);
  const ok = await guard(() => api('/api/photos', { ids: sel.map((p) => p.id), delete: val }));
  if (ok === null) return;
  sel.forEach((p) => { p.delete = val; });
  refreshTiles(sel); renderInspector();
  guard(async () => { await loadState(); const f = $('#footNote'); if (f) f.innerHTML = footText(); });
}
function refreshTiles(ps) {
  ps.forEach((p) => {
    const t = app.querySelector(`.tile[data-id="${p.id}"]`);
    if (!t) return;
    const tmp = document.createElement('div'); tmp.innerHTML = tileHtml(p);
    const n = tmp.firstElementChild;
    n.onclick = (e) => clickTile(p.id, e); n.ondblclick = () => openPreview(p.id);
    t.replaceWith(n);
  });
  const ov = $('#overlay');
  if (!ov.hidden && ui.previewIdx != null) showPreview();
}

/* ---------------- large preview ---------------- */
function openPreview(id) {
  const vis = visiblePhotos();
  ui.previewList = vis; ui.previewIdx = Math.max(0, vis.findIndex((p) => p.id === id));
  ui.sel = new Set([id]); ui.anchor = id; updateTileSelection(); renderInspector();
  $('#overlay').hidden = false; showPreview();
}
function showPreview() {
  const p = ui.previewList[ui.previewIdx];
  if (!p) return closeOverlay();
  const ov = $('#overlay');
  ov.innerHTML = `<div class="ov-top">
      <div class="row"><b>${esc(p.name)}</b><span class="muted small">${esc(p.camera)} · ${esc(p.time)} · ${ui.previewIdx + 1} of ${ui.previewList.length}</span>
        <span style="color:var(--star)">${'★'.repeat(p.rating || 0)}</span>${p.delete ? '<span class="badge" style="background:#e07b2a;color:#16181b">DELETE</span>' : ''}</div>
      <div class="row"><span class="muted small">← → browse · 0–5 rate · X delete · Esc close</span>
        <button type="button" class="btn small" id="pvPrev" aria-label="Previous">←</button><button type="button" class="btn small" id="pvNext" aria-label="Next">→</button>
        <button type="button" class="btn small" id="ovClose">Close</button></div></div>
    <div class="ov-body"><img src="${preview(p.id)}" alt="${esc(p.name)}"></div>`;
  $('#ovClose').onclick = closeOverlay;
  $('#pvPrev').onclick = () => movePreview(-1);
  $('#pvNext').onclick = () => movePreview(1);
}
function movePreview(dir) {
  const n = ui.previewIdx + dir;
  if (n < 0 || n >= ui.previewList.length) return;
  ui.previewIdx = n;
  const id = ui.previewList[n].id;
  ui.sel = new Set([id]); ui.anchor = id; updateTileSelection(); renderInspector(); showPreview();
}

document.addEventListener('keydown', (e) => {
  const ov = $('#overlay');
  const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName);
  if (e.key === 'Escape' && !ov.hidden) { e.preventDefault(); closeOverlay(); return; }
  if (ui.step !== 2 || typing) return;
  if (!ov.hidden && ui.previewIdx != null) {
    if (e.key === 'ArrowRight') { e.preventDefault(); movePreview(1); return; }
    if (e.key === 'ArrowLeft') { e.preventDefault(); movePreview(-1); return; }
  } else if (!ov.hidden) return;
  if (/^[0-5]$/.test(e.key) && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setRating(Number(e.key)); }
  else if ((e.key === 'x' || e.key === 'X') && !e.metaKey && !e.ctrlKey) { e.preventDefault(); toggleDelete(); }
  else if ((e.key === 'a' || e.key === 'A') && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ui.sel = new Set(visiblePhotos().map((p) => p.id)); updateTileSelection(); renderInspector(); }
  else if (e.key === 'Enter' && ui.sel.size && ov.hidden && !document.activeElement.closest('button:not(.tile)')) { e.preventDefault(); openPreview([...ui.sel][0]); }
  else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && ov.hidden && ui.anchor) {
    const vis = visiblePhotos(); const i = vis.findIndex((p) => p.id === ui.anchor);
    const n = vis[i + (e.key === 'ArrowRight' ? 1 : -1)];
    if (n) { e.preventDefault(); ui.sel = new Set([n.id]); ui.anchor = n.id; updateTileSelection(); renderInspector();
      const t = app.querySelector(`.tile[data-id="${n.id}"]`); if (t) { t.focus(); } }
  }
});

/* ---------------- step 3: destination and move ---------------- */
async function renderMove() {
  const plan = await guard(() => api('/api/plan'));
  if (!plan) return;
  ui.plan = plan;
  const s = plan.summary;
  const running = false;
  const destName = plan.dest ? plan.dest.split('/').filter(Boolean).pop() + '/' : '(destination)/';
  const lines = [esc(destName)];
  const fs = plan.folders;
  fs.forEach((f, i) => {
    const last = i === fs.length - 1;
    const tag = f.name === '_to_delete' ? '' : f.existing ? '<span class="ex">existing</span>' : '<span class="new">new</span>';
    const count = f.existing ? `+${num(f.adding)} → ${num(f.before + f.adding)}` : num(f.adding);
    const un = plan.unnamed.includes(f.name);
    lines.push(`${last ? '└─' : '├─'} <span class="${un ? 'un' : ''}">${esc(f.name)}</span>  ${tag} <span class="dim">${count}${f.skipped ? `, ${f.skipped} skipped` : ''}</span>`);
    const notes = f.samples.filter((x) => x.note);
    notes.slice(0, 4).forEach((x) => lines.push(`${last ? '   ' : '│  '}  ${esc(x.name)}  <span class="dim">${esc(x.note)}</span>`));
  });
  app.innerHTML = `
  <div class="page" style="max-width:1180px">
    <div><h1>Where should the photos go?</h1>
      <p class="muted">Each collection becomes one folder directly inside the destination, named yyyymm-name.</p></div>
    <div class="cols2">
      <div style="display:flex;flex-direction:column;gap:18px">
        <section class="card">
          <label for="dst3"><b>Destination folder</b></label>
          <div class="row"><input id="dst3" type="text" value="${esc(plan.dest)}" placeholder="/Users/you/Pictures/Photos" autocomplete="off">
            <button type="button" class="btn" id="pick3">Choose…</button></div>
          <details><summary class="muted small" style="cursor:pointer">HEIC keywords</summary>
            <div class="field" style="margin-top:8px"><label for="heic" class="small">Where to write keywords and ratings for HEIC photos</label>
              <select id="heic"><option value="both" ${S.heic_mode === 'both' ? 'selected' : ''}>Inside the file and in a .xmp sidecar (recommended until tested)</option>
                <option value="embedded" ${S.heic_mode === 'embedded' ? 'selected' : ''}>Inside the file only</option>
                <option value="sidecar" ${S.heic_mode === 'sidecar' ? 'selected' : ''}>In a .xmp sidecar only</option></select></div></details>
        </section>
        <section class="card" aria-label="What will happen"><h2>What will happen</h2>
          <ul class="plain">
            <li><b>${num(s.photos - s.to_delete)} photos</b> move into <b>${num(s.folders_new.length + s.folders_existing.length)} folder(s)</b>: ${num(s.folders_new.length)} new, ${num(s.folders_existing.length)} already existing (RAW+JPEG pairs stay together)</li>
            ${s.to_delete ? `<li><b>${num(s.to_delete)} photo${s.to_delete === 1 ? '' : 's'}</b> marked for deletion move to <span class="mono">_to_delete</span>; nothing is erased</li>` : ''}
            ${s.skipped.length ? `<li>${num(s.skipped.length)} photo(s) are already in the destination (identical file) and stay where they are</li>` : ''}
            ${s.renamed.length ? `<li>${num(s.renamed.length)} photo(s) get a number added because the name is taken, for example ${esc(s.renamed[0])}</li>` : ''}
            <li>Ratings and keywords are written for ${num(s.sidecars + s.embedded)} file(s): .xmp sidecars for RAW, inside the file for JPEG, DNG and HEIC</li>
            <li>Nothing is overwritten, and every step is logged so the move can be undone</li>
          </ul>
        </section>
        ${!plan.dest_ok ? '<div class="note" role="alert">Choose an existing destination folder.</div>' : ''}
        ${plan.same_as_source ? '<div class="note" role="alert">The destination must be different from the source folder.</div>' : ''}
        ${plan.unnamed.length ? `<div class="note" role="alert">${plan.unnamed.length} collection(s) have no name and will be called ${plan.unnamed.map((n) => `<span class="mono">${esc(n)}</span>`).join(', ')}. <a href="#" id="nameNow">Name them now</a></div>` : ''}
        ${plan.shared.length ? `<div class="info">More than one collection goes into ${plan.shared.map((n) => `<span class="mono">${esc(n)}</span>`).join(', ')}; their photos will share that folder.</div>` : ''}
      </div>
      <section class="card" aria-label="Folder preview"><h2>Preview</h2><div class="tree">${lines.join('\n')}</div></section>
    </div>
    <div id="moveProgress"></div>
    <div class="actions">
      <button type="button" class="btn ghost" id="back">← Back</button>
      <button type="button" class="btn primary" id="move" ${!plan.dest_ok || plan.same_as_source || !s.photos || running ? 'disabled' : ''}>${ui.confirmMove ? `Confirm: move ${num(s.photos)} photos now` : `Move ${num(s.photos)} photos`}</button>
    </div>
  </div>`;
  $('#back').onclick = () => go(2);
  $('#pick3').onclick = () => pick('dst3', 'Choose the destination folder');
  $('#dst3').onchange = () => guard(async () => { await api('/api/destination', { dest: $('#dst3').value.trim() }); await loadState(); renderMove(); });
  $('#heic').onchange = (e) => guard(async () => { await api('/api/settings', { heic_mode: e.target.value }); await loadState(); renderMove(); });
  if ($('#nameNow')) $('#nameNow').onclick = (e) => { e.preventDefault(); const c = S.collections.find((x) => x.unnamed); if (c) ui.colKey = c.key; go(2); };
  $('#move').onclick = async () => {
    if (!ui.confirmMove) {
      ui.confirmMove = true; renderMove();
      clearTimeout(ui.cmT); ui.cmT = setTimeout(() => { if (ui.step === 3 && ui.confirmMove) { ui.confirmMove = false; renderMove(); } }, 6000);
      return;
    }
    ui.confirmMove = false;
    const r = await guard(() => api('/api/apply', {}));
    if (r) pollApply();
  };
}

async function pollApply() {
  const a = await guard(() => api('/api/apply'));
  if (!a) return;
  const box = $('#moveProgress');
  if (a.running) {
    const pct = a.total ? Math.round(100 * a.done / a.total) : 3;
    if (box) {
      box.innerHTML = `<section class="card"><b>Moving photos… please keep this window open</b><div class="progress"><div style="width:${pct}%"></div></div>
        <span class="muted small">${num(a.done)} of ${num(a.total)}</span></section>`;
      app.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    }
    setTimeout(pollApply, 500);
    return;
  }
  if (a.error) { toast('Moving stopped: ' + a.error, true); renderMove(); return; }
  ui.result = a.result;
  await loadState();
  go(4);
}

/* ---------------- step 4: done ---------------- */
function renderDone() {
  const r = ui.result;
  if (!r) return go(1);
  const errs = r.errors || [];
  const mins = Math.floor((r.seconds || 0) / 60), secs = (r.seconds || 0) % 60;
  app.innerHTML = `
  <div class="page" style="max-width:820px">
    <div class="row" style="align-items:flex-start;gap:16px">
      <svg role="img" aria-label="Done" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="${errs.length ? '#f0a35e' : '#6fcf8f'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 12.5l2.5 2.5L16 9.5"/></svg>
      <div><h1>${num(r.photos)} photos moved</h1>
        <p class="muted">Into ${num(r.folders_new.length + r.folders_existing.length)} folder(s) (${num(r.folders_new.length)} new, ${num(r.folders_existing.length)} existing)${r.skipped.length ? ` · ${num(r.skipped.length)} identical file(s) skipped` : ''} · ${errs.length} problem(s) · took ${mins ? mins + ' min ' : ''}${secs} s</p></div>
    </div>
    <section class="card" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px">
      <div><b style="font-size:22px">${num(r.photos - r.to_delete)}</b><div class="muted small">in collections</div></div>
      <div><b style="font-size:22px">${num(r.to_delete)}</b><div class="muted small">in _to_delete</div></div>
      <div><b style="font-size:22px">${num(r.sidecars)}</b><div class="muted small">sidecars written</div></div>
      <div><b style="font-size:22px">${num(r.renamed.length)}</b><div class="muted small">renamed to avoid a clash</div></div>
    </section>
    ${errs.length ? `<div class="note" role="alert"><b>Problems</b><ul class="plain" style="margin-top:6px">${errs.slice(0, 20).map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
    <div class="row">
      <button type="button" class="btn primary" id="open">Open in Finder</button>
      <button type="button" class="btn ghost" id="undo">${ui.confirmUndo ? 'Confirm: put everything back' : 'Undo this move'}</button>
      <button type="button" class="btn ghost" id="again">Sort another folder</button>
    </div>
    <section class="card"><h2>Next, in Lightroom Classic</h2>
      <ol class="plain">
        <li>New photos: File › Import, choose the destination folder and use <b>Add</b> (files stay where they are).</li>
        <li>Folders already in the catalog: right-click the folder › Synchronize Folder, then select its photos › Metadata › Read Metadata from Files.</li>
        <li>Check <span class="mono">_to_delete</span>, then empty it yourself in Finder.</li>
      </ol></section>
    <p class="muted small">Log: <span class="mono">${esc(r.journal || '')}</span></p>
  </div>`;
  $('#open').onclick = () => guard(() => api('/api/open', {}));
  $('#again').onclick = () => { ui.result = null; go(1); };
  $('#undo').onclick = async () => {
    if (!ui.confirmUndo) { ui.confirmUndo = true; renderDone(); return; }
    ui.confirmUndo = false;
    const u = await guard(() => api('/api/undo', {}));
    if (!u) return;
    toast(`${num(u.files_moved_back)} files moved back${u.skipped ? `, ${u.skipped} could not be` : ''}`, !!u.skipped);
    ui.result = null;
    await loadState(); go(1);
  };
}

/* ---------------- start ---------------- */
$('#quit').onclick = async () => {
  try { await api('/api/quit', {}); } catch (e) { /* server already gone */ }
  document.body.innerHTML = '<div class="page"><h1>Photo Sorter stopped</h1><p class="muted">You can close this tab. To start again, double-click "Start Photo Sorter".</p></div>';
};

(async function init() {
  if (!T) {
    app.innerHTML = '<div class="page"><h1>Open the link from the Terminal window</h1><p class="muted">For safety, Photo Sorter only answers the link it printed when it started.</p></div>';
    return;
  }
  try { await loadState(); } catch (e) {
    app.innerHTML = `<div class="page"><h1>Cannot reach Photo Sorter</h1><p class="muted">${esc(e.message)}</p></div>`;
    return;
  }
  render();
})();
