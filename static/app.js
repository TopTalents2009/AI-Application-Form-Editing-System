'use strict';
var currentDetail = null;
var pollTimer = null;
var currentBatch = null;
var batchTimer = null;
function scheduleBatchOff() {}
var currentSiblings = null;
var currentPage = 0;
var listExpanded = {};

function $(id) { return document.getElementById(id); }
function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
function escAttr(s) { return esc(s).replace(/"/g, '&quot;'); }
function apiMsg(res, fallback) {
  if (!res) return fallback || '请求失败';
  if (res.error) return String(res.error);
  var d = res.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]) return d[0].msg || d[0].message || JSON.stringify(d[0]);
  if (d) return JSON.stringify(d);
  return fallback || '请求失败';
}
function readJson(r) {
  return r.json().then(function (res) {
    if (!r.ok) throw new Error(apiMsg(res, 'HTTP ' + r.status));
    return res;
  });
}
function ensurePoll() {
  if (!currentDetail) return;
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollDetail, 1500);
}

/* ---------- model config ---------- */
var currentCfg = null;
var cfgDraft = { grok: {}, gemini: {}, doubao: {}, classifyModel: '' };
var cfgEditId = '';
var OP_EMPTY = '（该模型未给出对应修改）';
var OP_LEGACY = '（该任务生成时尚未做多模型对照）';
function modelList(cfg) {
  var e = cfg && cfg.edit;
  var rows = [];
  if (e && e.grok) rows.push(e.grok);
  if (e && e.gemini) rows.push(e.gemini);
  if (e && e.doubao) rows.push(e.doubao);
  if (rows.length) return rows;
  if (cfg && cfg.models && cfg.models.length) return cfg.models;
  return (cfg && cfg.engines) || [];
}
function modelFamily(id) {
  var s = String(id || '');
  if (/gemini/i.test(s)) return 'gemini';
  if (/doubao|volc|arkcn|seed-2-0/i.test(s)) return 'doubao';
  return 'grok';
}
function modelLabel(m) {
  if (!m || !(m.label || m.id)) return '请选择模型';
  var name = m.label || m.id;
  if (m.ready === false) return name + '（未配置）';
  return name;
}
function setCfgMsg(ok, text) {
  var el = $('cfgMsg');
  if (!el) return;
  el.className = ok ? 'err ok' : 'err';
  el.textContent = text || '';
}
function closeAllMenus() {
  ['engineMenu', 'defaultMenu'].forEach(function (id) {
    var el = $(id);
    if (el) el.hidden = true;
  });
}
function updateLive() {
  var live = $('liveStatus');
  if (live) live.textContent = '服务运行中';
}
function cfgField(id, label, value, extra) {
  extra = extra || {};
  var cls = 'cfg-field' + (extra.span2 ? ' span2' : '');
  if (extra.type === 'select') {
    var opts = (extra.options || []).map(function (o) {
      return '<option value="' + escAttr(o[0]) + '"' + (String(value) === String(o[0]) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('');
    return '<div class="' + cls + '"><label>' + esc(label) + '</label><select id="' + id + '">' + opts + '</select></div>';
  }
  if (extra.type === 'checkbox') {
    return '<label class="cfg-check"><input type="checkbox" id="' + id + '"' + (value ? ' checked' : '') + '> ' + esc(label) + '</label>';
  }
  var ph = extra.placeholder ? ' placeholder="' + escAttr(extra.placeholder) + '"' : '';
  return '<div class="' + cls + '"><label>' + esc(label) + '</label><input id="' + id + '" type="' + (extra.type || 'text') + '" autocomplete="off" spellcheck="false"' + ph + ' value="' + escAttr(value == null ? '' : value) + '"></div>';
}
function hydrateDraft(cfg) {
  var e = (cfg && cfg.edit) || {};
  cfgDraft.grok = Object.assign({ id: 'grok-4.6', label: 'Grok', timeoutSec: 900, stream: false }, e.grok || {});
  cfgDraft.gemini = Object.assign({ id: 'gemini-3.7-flash', label: 'Gemini', timeoutSec: 300, stream: true }, e.gemini || {});
  cfgDraft.doubao = Object.assign({
    id: 'doubao-seed-2-0-mini-260428',
    label: '火山',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    timeoutSec: 300,
    stream: true
  }, e.doubao || {});
  cfgDraft.classifyModel = e.classifyModel || (e.grok && e.grok.id) || 'grok-4.6';
  if (!cfgEditId) cfgEditId = cfgDraft.classifyModel || cfgDraft.grok.id;
}
function readFormIntoDraft() {
  if (!$('cfgId')) return;
  var fam = modelFamily(cfgEditId);
  var d = Object.assign({}, cfgDraft[fam] || {});
  d.id = ($('cfgId').value || '').trim();
  d.baseUrl = ($('cfgBase').value || '').trim();
  var key = ($('cfgKey') && $('cfgKey').value || '').trim();
  if (key) d.apiKey = key;
  else delete d.apiKey;
  d.timeoutSec = Number($('cfgTimeout') && $('cfgTimeout').value) || d.timeoutSec || 0;
  d.stream = !!( $('cfgStream') && $('cfgStream').checked );
  if ($('cfgEffort')) d.reasoningEffort = $('cfgEffort').value;
  cfgDraft[fam] = d;
  cfgEditId = d.id || cfgEditId;
}
function renderCfgForm() {
  var form = $('cfgForm');
  if (!form) return;
  var fam = modelFamily(cfgEditId);
  var d = cfgDraft[fam] || {};
  var title = d.label || (fam === 'gemini' ? 'Gemini' : (fam === 'doubao' ? '火山' : 'Grok'));
  var html = '<div class="cfg-block' + (d.ready ? '' : ' warn') + '"><h3>' + esc(title) + ' 接入参数</h3><div class="cfg-grid">';
  html += cfgField('cfgId', '模型 id', d.id || '');
  if (fam === 'grok') {
    html += cfgField('cfgEffort', '思考长度', d.reasoningEffort || 'medium', {
      type: 'select',
      options: [['low', '短（low）'], ['medium', '中（medium）'], ['high', '长（high）']]
    });
  } else {
    html += cfgField('cfgTimeout', '超时（秒）', d.timeoutSec || 300, { type: 'number' });
  }
  html += cfgField('cfgBase', '请求地址', d.baseUrl || '', { span2: true });
  html += cfgField('cfgKey', '密钥', '', {
    span2: true,
    type: 'password',
    placeholder: d.hasKey ? '已配置，留空则保持不变' : '未配置，请填写密钥'
  });
  if (fam === 'grok') html += cfgField('cfgTimeout', '超时（秒）', d.timeoutSec || 900, { type: 'number' });
  html += cfgField('cfgStream', '流式', !!d.stream, { type: 'checkbox' });
  html += '</div><div class="cfg-status ' + (d.ready ? 'ok' : 'bad') + '">' +
    (d.ready ? '已就绪' : '未配置：保存前请填写请求地址和密钥') + '</div></div>';
  form.innerHTML = html;
}
function bindDropdown(rowId, wrapId, btnId, menuId, hidId, pickId, onPick) {
  var row = $(rowId);
  if (!row) return;
  var models = modelList(currentCfg);
  var cur = models.filter(function (m) { return m.id === pickId; })[0] || models[0] || {};
  var html = '<div class="model-dd" id="' + wrapId + '">';
  html += '<button type="button" class="model-dd-btn" id="' + btnId + '">' + esc(modelLabel(cur)) + '</button>';
  html += '<input type="hidden" id="' + hidId + '" value="' + escAttr(cur.id || '') + '">';
  html += '<div class="model-dd-menu" id="' + menuId + '" hidden>';
  if (!models.length) html += '<div class="model-dd-empty">暂无模型，请检查 config.json</div>';
  else models.forEach(function (m) {
    html += '<button type="button" class="model-dd-item' + (m.id === (cur.id || '') ? ' active' : '') + '" data-id="' + escAttr(m.id) + '">' + esc(modelLabel(m)) + '</button>';
  });
  html += '</div></div>';
  row.innerHTML = html;
  var btn = $(btnId), menu = $(menuId), hid = $(hidId);
  if (btn && menu) {
    btn.onclick = function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var open = menu.hidden;
      closeAllMenus();
      menu.hidden = !open;
    };
    menu.onclick = function (ev) {
      ev.stopPropagation();
      var it = ev.target && ev.target.closest ? ev.target.closest('.model-dd-item') : null;
      if (!it) return;
      hid.value = it.getAttribute('data-id') || '';
      btn.textContent = it.textContent;
      var items = menu.querySelectorAll('.model-dd-item');
      for (var i = 0; i < items.length; i++) items[i].classList.toggle('active', items[i] === it);
      menu.hidden = true;
      onPick(hid.value);
    };
  }
}
function renderConfigUi(cfg) {
  currentCfg = cfg;
  hydrateDraft(cfg);
  bindDropdown('engineRow', 'engineWrap', 'engineBtn', 'engineMenu', 'cfgTarget', cfgEditId, function (id) {
    readFormIntoDraft();
    cfgEditId = id;
    renderCfgForm();
    updateLive();
  });
  bindDropdown('defaultRow', 'defaultWrap', 'defaultBtn', 'defaultMenu', 'engine', cfgDraft.classifyModel, function (id) {
    cfgDraft.classifyModel = id;
    persistClassify(id);
    updateLive();
  });
  renderCfgForm();
  updateLive();
  if (cfg && cfg.configError) setCfgMsg(false, cfg.configError);
}
function persistClassify(id) {
  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ classifyModel: id })
  }).then(readJson).then(function (cfg) {
    currentCfg = cfg;
    if (cfg && cfg.edit) cfgDraft.classifyModel = cfg.edit.classifyModel || id;
    setCfgMsg(true, '已保存默认采用模型');
    updateLive();
  }).catch(function (err) {
    setCfgMsg(false, '保存默认失败：' + (err && err.message || err));
  });
}
function collectSaveBody() {
  readFormIntoDraft();
  var fam = modelFamily(cfgEditId);
  var body = { classifyModel: cfgDraft.classifyModel || ($('engine') && $('engine').value) || '' };
  var row = Object.assign({}, cfgDraft[fam]);
  delete row.hasKey;
  if (!row.apiKey) delete row.apiKey;
  body[fam] = row;
  return body;
}
function postCfg(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : '{}'
  }).then(readJson);
}
function saveCfg(asDefault) {
  setCfgMsg(false, '');
  var body = collectSaveBody();
  var keepEdit = cfgEditId;
  body.saveAsDefault = !!asDefault;
  postCfg('/api/config', body).then(function (cfg) {
    cfgEditId = keepEdit;
    renderConfigUi(cfg);
    setCfgMsg(true, asDefault ? '已保存，并更新默认配置' : '已保存当前模型配置');
  }).catch(function (err) {
    setCfgMsg(false, '保存失败：' + (err && err.message || err));
  });
}
function restoreCfg() {
  if (!confirm('用默认配置覆盖当前 config.json？')) return;
  setCfgMsg(false, '');
  postCfg('/api/config/restore-default').then(function (cfg) {
    cfgEditId = '';
    renderConfigUi(cfg);
    setCfgMsg(true, '已恢复默认配置');
  }).catch(function (err) {
    setCfgMsg(false, '恢复失败：' + (err && err.message || err));
  });
}
function renderProbe(res) {
  var box = $('probeBox');
  if (!box) return;
  var rows = (res && res.results) || [];
  if (!rows.length) {
    box.hidden = false;
    box.innerHTML = '<div class="p-row p-bad">' + esc((res && res.error) || '没有可检测的模型') + '</div>';
    return;
  }
  box.hidden = false;
  box.innerHTML = rows.map(function (r) {
    var ms = r.ms ? (' · ' + (r.ms / 1000).toFixed(1) + 's') : '';
    if (r.ok) return '<div class="p-row p-ok">● ' + esc(r.label || r.id) + ' 已连通' + ms + (r.detail ? '（' + esc(r.detail) + '）' : '') + '</div>';
    return '<div class="p-row p-bad">● ' + esc(r.label || r.id) + ' 失败' + ms + '：' + esc(r.error || '未知错误') + '</div>';
  }).join('');
}
function probeModels() {
  var btn = $('cfgProbeBtn');
  var box = $('probeBox');
  if (box) { box.hidden = false; box.innerHTML = '<div class="p-row">正在检测 Grok / Gemini / 火山…</div>'; }
  if (btn) btn.disabled = true;
  setCfgMsg(false, '');
  fetch('/api/config/probe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}'
  }).then(readJson).then(function (res) {
    renderProbe(res);
    var n = ((res && res.results) || []).filter(function (r) { return r.ok; }).length;
    var tot = ((res && res.results) || []).length;
    setCfgMsg(!!(res && res.ok), n + '/' + tot + ' 个模型连通' + (res && res.ok ? '' : '（未通的模型提交任务会失败）'));
  }).catch(function (err) {
    if (box) { box.hidden = false; box.innerHTML = '<div class="p-row p-bad">' + esc(err && err.message || err) + '</div>'; }
    setCfgMsg(false, '检测失败：' + (err && err.message || err));
  }).then(function () { if (btn) btn.disabled = false; });
}
function bindCfgButtons() {
  if (window._cfgBound) return;
  window._cfgBound = true;
  var s = $('cfgSaveBtn'), d = $('cfgDefaultBtn'), r = $('cfgRestoreBtn'), p = $('cfgProbeBtn');
  if (s) s.onclick = function () { saveCfg(false); };
  if (d) d.onclick = function () { saveCfg(true); };
  if (r) r.onclick = function () { restoreCfg(); };
  if (p) p.onclick = function () { probeModels(); };
}
if (!window._engineMenuBound) {
  window._engineMenuBound = true;
  document.addEventListener('click', function (ev) {
    var wraps = ['engineWrap', 'defaultWrap'];
    var hit = wraps.some(function (id) {
      var el = $(id);
      return el && el.contains(ev.target);
    });
    if (!hit) closeAllMenus();
  });
}
function loadConfig() {
  bindCfgButtons();
  fetch('/api/config').then(function (r) { return r.json(); }).then(function (cfg) {
    renderConfigUi(cfg);
  }).catch(function (err) {
    var form = $('cfgForm');
    if (form) form.innerHTML = '<div class="cfg-block warn">读取配置失败：' + esc(err && err.message || err) + '</div>';
  });
}

/* ---------- upload ---------- */
function fileToB64(file) {
  return new Promise(function (resolve, reject) {
    var r = new FileReader();
    r.onload = function () { resolve(r.result.slice(r.result.indexOf(',') + 1)); };
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
function chip(name, bad, hint, kind, idx) {
    var cls = 'chip' + (bad ? ' bad' : '');
    var x = (kind != null && idx != null)
      ? '<button type="button" class="chip-x" data-kind="' + kind + '" data-i="' + idx + '" aria-label="移除" title="移除">×</button>'
      : '';
    return '<span class="' + cls + '" title="' + escAttr(name) + '"><span class="chip-n">' + (bad ? '! ' : '') + esc(name) + (hint ? '<em> ' + hint + '</em>' : '') + '</span>' + x + '</span>';
  }
function isDocx(name) { return /\.docx$/i.test(String(name || '')); }
function isPdf(name) { return /\.pdf$/i.test(String(name || '')); }
function isAppFile(name) { return isDocx(name) || isPdf(name); }
function opExtKind(name) {
  var n = String(name || '');
  if (/\.(jpe?g|png|webp|gif|tiff?|bmp)$/i.test(n)) return 'image';
  if (/\.(xlsx|xlsm|xls|csv)$/i.test(n)) return 'excel';
  if (/\.(docx|wps)$/i.test(n)) return 'word';
  if (/\.(txt|md)$/i.test(n)) return 'text';
  return '';
}
function isOpFile(name) { return !!opExtKind(name); }
function auditAppFiles(fileList) {
  var good = [], badNames = [];
  fileList.forEach(function (f) {
    if (isAppFile(f.name)) {
      good.push(f);
      if (/意见/.test(f.name)) badNames.push(f.name + '（名称含“意见”，疑似选到了修改意见文档）');
    } else {
      badNames.push(f.name + '（非 .docx / 数字 PDF）');
    }
  });
  return { good: good, badNames: badNames };
}

var pickedApps = [];
var pickedOps = [];
function fileKey(f) {
  return [f.name, f.size, f.lastModified].join('\t');
}
function mergeFiles(store, incoming) {
  var seen = {};
  store.forEach(function (f) { seen[fileKey(f)] = true; });
  incoming.forEach(function (f) {
    var k = fileKey(f);
    if (!seen[k]) { seen[k] = true; store.push(f); }
  });
  return store;
}
function renderAppChips() {
  var el = $('appName');
  if (!el) return;
  el.innerHTML = pickedApps.map(function (f, i) {
    var bad = !isAppFile(f.name);
    var warnOp = /意见/.test(f.name);
    var hint = bad ? '非 .docx/.pdf' : (isPdf(f.name) ? '数字PDF·转Word后修改' : (warnOp ? '名称含“意见”' : ''));
    return chip(f.name, bad || warnOp, hint, 'app', i);
  }).join('');
}
function renderOpChips() {
  var el = $('opNames');
  if (!el) return;
  el.innerHTML = pickedOps.map(function (f, i) {
    var k = opExtKind(f.name);
    var hint = !k ? '不支持' : (k === 'image' ? '图片·Gemini识字' : (k === 'excel' ? 'Excel' : ''));
    return chip(f.name, !k, hint, 'op', i);
  }).join('');
}
function clearPicked() {
  pickedApps = [];
  pickedOps = [];
  if ($('appFile')) $('appFile').value = '';
  if ($('opFiles')) $('opFiles').value = '';
  renderAppChips();
  renderOpChips();
}

/* ---------- list ---------- */

/* ---------- 极简 Markdown 渲染（标题/表格/引用/列表/加粗/代码/hr） ---------- */
function escHtml(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function inlineMd(s) {
  return escHtml(s)
    .replace(/\`([^\`]+)\`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}
function isMdTableLine(s) {
  return /^\s*\|/.test(s) || /^\s*:?-{2,}[\s:|]*\|[\s:|-]*$/.test(s);
}
function isMdBreak(s) {
  return isMdTableLine(s) || /^(#{1,4})\s+/.test(s) || /^\s*---+\s*$/.test(s) ||
    /^\s*&gt;|^\s*>/.test(s) || /^\s*[-*]\s+/.test(s) || /^\s*\d+\.\s+/.test(s);
}
function renderMarkdown(md) {
  var lines = String(md || '').split('\n');
  var html = '', i = 0, inTable = false, tableRows = [];
  function flushTable() {
    if (!inTable) return;
    // 第一行为表头；分隔行(---)剔除
    var body = tableRows.filter(function (r) { return !/^\s*\|?\s*:?-{2,}[\s:\-|]*$/.test(r); });
    if (body.length) {
      var cellsOf = function (r) { return r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|'); };
      var head = cellsOf(body[0]);
      html += '<table><thead><tr>';
      head.forEach(function (c) { html += '<th>' + inlineMd(c.trim()) + '</th>'; });
      html += '</tr></thead><tbody>';
      for (var k = 1; k < body.length; k++) {
        var cs = cellsOf(body[k]);
        html += '<tr>';
        head.forEach(function (_, ci) { html += '<td>' + inlineMd((cs[ci] || '').trim()) + '</td>'; });
        html += '</tr>';
      }
      html += '</tbody></table>';
    }
    tableRows = []; inTable = false;
  }
  while (i < lines.length) {
    var line = lines[i];
    var start = i;
    if (isMdTableLine(line)) { inTable = true; tableRows.push(line); i++; continue; }
    flushTable();
    var mH = line.match(/^(#{1,4})\s+(.*)$/);
    if (mH) { var lv = mH[1].length; html += '<h' + lv + '>' + inlineMd(mH[2]) + '</h' + lv + '>'; i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { html += '<hr>'; i++; continue; }
    if (/^\s*&gt;|^\s*>/.test(line)) {
      var qs = [];
      while (i < lines.length && /^\s*&gt;|^\s*>/.test(lines[i])) { qs.push(lines[i].replace(/^\s*&gt;?\s?/, '')); i++; }
      html += '<blockquote><p>' + qs.map(inlineMd).join('<br>') + '</p></blockquote>';
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      html += '<ul>';
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        html += '<li>' + inlineMd(lines[i].replace(/^\s*[-*]\s+/, '')) + '</li>'; i++;
      }
      html += '</ul>';
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      html += '<ol>';
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        html += '<li>' + inlineMd(lines[i].replace(/^\s*\d+\.\s+/, '')) + '</li>'; i++;
      }
      html += '</ol>';
      continue;
    }
    if (line.trim() === '') { i++; continue; }
    var para = [];
    while (i < lines.length && lines[i].trim() !== '' && !isMdBreak(lines[i])) {
      para.push(inlineMd(lines[i])); i++;
    }
    if (para.length) html += '<p>' + para.join('<br>') + '</p>';
    else if (i === start) { html += '<p>' + inlineMd(line) + '</p>'; i++; }
  }
  flushTable();
  return html;
}

function statusText(s) {
  return {
    queued: '排队中', preparing: '预处理', running: 'Agent 执行中', planned: '待人工确认',
    done: '已完成', failed: '失败', extracting: '提取中', matching: '配对中',
    arbitrating: '仲裁中', ready: '待确认配对', started: '执行中'
  }[s] || s;
}
function createdKey(s) {
  var m = String(s || '').match(/(\d+)\D+(\d+)\D+(\d+)(?:\D+(\d+)\D+(\d+)\D+(\d+))?/);
  if (!m) return '';
  function p(x, n) { x = String(x || '0'); while (x.length < n) x = '0' + x; return x; }
  return p(m[1], 4) + p(m[2], 2) + p(m[3], 2) + p(m[4], 2) + p(m[5], 2) + p(m[6], 2);
}
function aggregateStatus(list) {
  var st = (list || []).map(function (t) { return t.status; });
  if (st.some(function (s) { return s === 'running' || s === 'queued' || s === 'preparing'; })) return 'running';
  if (st.some(function (s) { return s === 'planned'; })) return 'planned';
  if (st.length && st.every(function (s) { return s === 'done'; })) return 'done';
  if (st.length && st.every(function (s) { return s === 'failed'; })) return 'failed';
  if (st.some(function (s) { return s === 'failed'; })) return 'failed';
  return st[0] || 'queued';
}
function asSib(t) {
  return {
    id: t.id,
    status: t.status,
    appName: (typeof t.app === 'string' ? t.app : (t.app && t.app.name) || t.appName || '')
  };
}
function rowKey(row) {
  if (row.batchId) return 'b:' + row.batchId;
  return 't:' + (row.id || '');
}
function booksOf(row) {
  if (row && row.books && row.books.length) return row.books;
  if (row && row.sibs && row.sibs.length) return row.sibs.map(asSib);
  if (row && row.kind === 'task') return [{ id: row.id, appName: row.name, status: row.status }];
  return [];
}
function paintNameCell(tr, row) {
  var td = tr.querySelector('.col-name');
  if (!td) return;
  var open = !!(row.count > 1 && listExpanded[rowKey(row)]);
  var books = booksOf(row);
  var extra = '';
  if (row.count > 1) {
    extra = '<button type="button" class="tcount tcount-btn" title="展开查看全部申报书">' + row.count + ' 本 ' + (open ? '▴' : '▾') + '</button>';
  }
  var lis = '';
  if (row.count > 1) {
    lis = '<ul class="tbooks' + (open ? '' : ' hidden') + '">';
    books.forEach(function (b, i) {
      lis += '<li data-i="' + i + '"><span class="tbook-name" title="' + escAttr(b.appName || '') + '">' + esc(b.appName || '未命名') + '</span>';
      if (b.status) lis += '<span class="badge st-' + escAttr(b.status) + '">' + esc(statusText(b.status)) + '</span>';
      lis += '</li>';
    });
    lis += '</ul>';
  }
  td.innerHTML = '<div class="namecell' + (open ? ' open' : '') + '"><span class="tname" title="' + escAttr(row.title || row.name || '') + '">' + esc(row.name || '') + '</span>' + extra + lis + '</div>';
  var btn = td.querySelector('.tcount-btn');
  var tname = td.querySelector('.tname');
  function toggle(ev) {
    if (ev) ev.stopPropagation();
    if (!row || row.count < 2) return;
    var k = rowKey(row);
    listExpanded[k] = !listExpanded[k];
    paintNameCell(tr, tr._row || row);
  }
  if (btn) btn.onclick = toggle;
  if (tname && row.count > 1) {
    tname.style.cursor = 'pointer';
    tname.onclick = toggle;
  }
  var ul = td.querySelector('.tbooks');
  if (ul) {
    ul.onclick = function (ev) {
      ev.stopPropagation();
      var li = ev.target.closest ? ev.target.closest('li') : null;
      if (!li) return;
      openBookFromRow(tr._row || row, parseInt(li.getAttribute('data-i'), 10));
    };
  }
}
function openBookFromRow(row, i) {
  if (!row) return;
  if (row.kind === 'batch-pending') {
    currentSiblings = null; currentPage = 0;
    $('detailCard').classList.add('hidden');
    pollBatch(row.batchId);
    return;
  }
  var books = booksOf(row);
  var idx = isFinite(i) ? i : 0;
  if (idx < 0 || idx >= books.length) idx = 0;
  if (row.kind === 'batch' && books.length) {
    currentSiblings = books.map(asSib);
    currentPage = idx;
    var hit = books[idx] || books[0];
    if (hit && hit.id) showDetail(hit.id);
    return;
  }
  currentSiblings = null; currentPage = 0;
  updatePager();
  if (row.id) showDetail(row.id);
}
function renderRow(row, tb) {
  var tr = document.createElement('tr');
  tr.setAttribute('data-key', rowKey(row));
  tr._row = row;
  tr.innerHTML =
    '<td class="col-time">' + esc(row.createdAt) + '</td>' +
    '<td class="col-name"></td>' +
    '<td class="col-st"><span class="badge st-' + escAttr(row.status) + '">' + esc(statusText(row.status)) + '</span></td>' +
    '<td class="col-act"><button type="button" class="mini">查看</button></td>';
  paintNameCell(tr, row);
  tr.querySelector('.col-act button').onclick = function () { openListRow(tr._row); };
  if (tb) tb.appendChild(tr);
  return tr;
}
function updateRow(tr, row) {
  tr._row = row;
  tr.setAttribute('data-key', rowKey(row));
  var timeTd = tr.querySelector('.col-time');
  var at = String(row.createdAt || '');
  if (timeTd && timeTd.textContent !== at) timeTd.textContent = at;
  paintNameCell(tr, row);
  var badge = tr.querySelector('.col-st .badge');
  if (badge) {
    var cls = 'badge st-' + (row.status || '');
    var tx = statusText(row.status);
    if (badge.className !== cls) badge.className = cls;
    if (badge.textContent !== tx) badge.textContent = tx;
  }
}
function patchList(tb, rows) {
  var existing = {};
  Array.prototype.forEach.call(tb.querySelectorAll('tr'), function (tr) {
    existing[tr.getAttribute('data-key') || ''] = tr;
  });
  var used = {};
  var ordered = [];
  rows.forEach(function (row) {
    var key = rowKey(row);
    used[key] = true;
    var tr = existing[key];
    if (tr) updateRow(tr, row);
    else tr = renderRow(row, null);
    ordered.push(tr);
  });
  Object.keys(existing).forEach(function (k) {
    if (!used[k] && existing[k] && existing[k].parentNode) existing[k].parentNode.removeChild(existing[k]);
  });
  ordered.forEach(function (tr, i) {
    if (tb.children[i] !== tr) tb.insertBefore(tr, tb.children[i] || null);
  });
}
function openListRow(row) {
  if (row.kind === 'batch-pending') {
    currentSiblings = null; currentPage = 0;
    $('detailCard').classList.add('hidden');
    pollBatch(row.batchId);
    return;
  }
  if (row.kind === 'batch' && row.sibs && row.sibs.length) {
    currentSiblings = row.sibs.map(asSib);
    currentPage = 0;
    showDetail(currentSiblings[0].id);
    return;
  }
  currentSiblings = null; currentPage = 0;
  updatePager();
  showDetail(row.id);
}
function buildListRows(tasks, batches) {
  var rows = [];
  var grouped = {};
  var usedTask = {};
  (tasks || []).forEach(function (t) {
    if (t.batchId) {
      if (!grouped[t.batchId]) grouped[t.batchId] = [];
      grouped[t.batchId].push(t);
    }
  });
  (batches || []).forEach(function (b) {
    var ids = b.taskIds || [];
    if (b.status === 'started' && ids.length && !(grouped[b.id] && grouped[b.id].length)) {
      grouped[b.id] = ids.map(function (id) {
        return (tasks || []).filter(function (t) { return t.id === id; })[0];
      }).filter(Boolean);
    }
  });
  (batches || []).forEach(function (b) {
    if (grouped[b.id] && grouped[b.id].length) return;
    var apps = b.apps || [];
    rows.push({
      kind: 'batch-pending',
      createdAt: b.createdAt,
      status: b.status,
      name: apps[0] || '批次',
      title: apps.join('、'),
      count: apps.length,
      books: apps.map(function (n) { return { id: '', appName: n, status: b.status }; }),
      batchId: b.id
    });
  });
  Object.keys(grouped).forEach(function (bid) {
    var sibs = grouped[bid].slice().sort(function (a, b) {
      return createdKey(a.createdAt) < createdKey(b.createdAt) ? -1 : 1;
    });
    if (!sibs.length) return;
    sibs.forEach(function (t) { usedTask[t.id] = true; });
    var newest = sibs[0].createdAt;
    sibs.forEach(function (t) { if (createdKey(t.createdAt) > createdKey(newest)) newest = t.createdAt; });
    var first = sibs[0];
    rows.push({
      kind: 'batch',
      createdAt: newest,
      status: aggregateStatus(sibs),
      name: (first.app && first.app.name) || '',
      title: sibs.map(function (x) { return x.app && x.app.name; }).join('、'),
      count: sibs.length,
      books: sibs.map(asSib),
      sibs: sibs,
      batchId: bid,
      id: first.id
    });
  });
  (tasks || []).forEach(function (t) {
    if (usedTask[t.id]) return;
    rows.push({
      kind: 'task',
      createdAt: t.createdAt,
      status: t.status,
      name: t.app && t.app.name,
      title: t.app && t.app.name,
      count: 1,
      id: t.id
    });
  });
  rows.sort(function (a, b) { return createdKey(a.createdAt) > createdKey(b.createdAt) ? -1 : 1; });
  return rows;
}
function refreshList() {
  Promise.all([
    fetch('/api/tasks').then(function (r) { return r.json(); }),
    fetch('/api/batches').then(function (r) { return r.json(); }).catch(function () { return { batches: [] }; })
  ]).then(function (pair) {
    var rows = buildListRows((pair[0] && pair[0].tasks) || [], (pair[1] && pair[1].batches) || []);
    var tb = $('taskTable').querySelector('tbody');
    var n = rows.length;
    $('emptyHint').style.display = n ? 'none' : 'block';
    patchList(tb, rows);
    var pill = $('listCount');
    if (pill) {
      if (n) { pill.textContent = n + ' 条'; pill.classList.remove('hidden'); }
      else { pill.textContent = ''; pill.classList.add('hidden'); }
    }
  });
}

/* ---------- detail ---------- */
var STEPS = [
  { key: 'queued',    label: '排队' },
  { key: 'preparing', label: '预处理' },
  { key: 'running',   label: '生成计划' },
  { key: 'planned',   label: '人工确认' },
  { key: 'apply',     label: '写入文件' },
];
function renderStepper(status) {
  var idxMap = { queued: 0, preparing: 1, running: 2, planned: 3, done: 4 };
  var idx = idxMap[status];
  var html = '';
  for (var i = 0; i < STEPS.length; i++) {
    var cls = 'step';
    if (status === 'failed') {
      if (i <= 2) cls += ' done';
      else if (i === 3) cls += ' failed';
    } else if (status === 'done') {
      cls += ' done';
    } else if (i < idx) {
      cls += ' done';
    } else if (i === idx) {
      cls += (status === 'planned' ? ' active warn' : ' active');
    }
    html += '<div class="' + cls + '"><span class="ball">' + (cls.indexOf('done') >= 0 ? '✓' : i + 1) + '</span>' + STEPS[i].label + '</div>';
    if (i < STEPS.length - 1) html += '<div class="step-line"></div>';
  }
  $('stepper').innerHTML = html;
}
function lnClass(msg) {
  msg = String(msg || '');
  if (/(失败|跳过|警告|未命中|丢弃|超长|未达标|未检出|中断|不可用)/.test(msg)) return 'ln-warn';
  if (/(完成|就绪|已改|已生成|返回 \d+)/.test(msg)) return 'ln-ok';
  if (/(同时提交|直连模式|开始按章)/.test(msg)) return 'ln-go';
  if (/(检索|提取|分类|仲裁)/.test(msg)) return 'ln-tool';
  return '';
}
function renderDetail(t) {
  if (currentSiblings) {
    currentSiblings.forEach(function (s) { if (s.id === t.id) { s.status = t.status; if (t.app && t.app.name) s.appName = t.app.name; } });
    var idx = -1;
    currentSiblings.forEach(function (s, i) { if (s.id === t.id) idx = i; });
    if (idx >= 0) currentPage = idx;
    updatePager();
  }
  renderStepper(t.status);
  $('detailId').textContent = t.id;
  var errLine = t.error ? '<br>错误：<span style="color:#c5221f">' + esc(t.error) + '</span>' : '';
  $('detailMeta').innerHTML =
    '状态：<b>' + statusText(t.status) + '</b>　·　创建：' + t.createdAt +
    (t.model ? '　·　模型：' + esc(t.modelLabel || t.model) : '') +
    (t.finishedAt ? '　·　结束：' + t.finishedAt : '') + errLine;

  renderPlanSection(t);
  var box = $('logBox');
  var atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  var html = '';
  (t.log || []).forEach(function (l) {
    html += '<div class="ln ' + lnClass(l.msg) + '"><span class="ln-time">[' + l.t + ']</span>' + esc(l.msg) + '</div>';
  });
  box.innerHTML = html || '<div class="ln">等待事件…</div>';
  if (atBottom) box.scrollTop = box.scrollHeight;

  var shown = (t.deliverables && t.deliverables.length) ? t.deliverables : (t.outputs || []);
  var ul = $('outList');
  ul.innerHTML = '';
  shown.forEach(function (o) {
    var ic = '';
    var li = document.createElement('li');
    li.innerHTML =
      '<span class="fic">' + ic + '</span>' +
      '<span class="fi"><span class="fn" title="' + escAttr(o.name) + '">' + esc(o.name) + '</span>' +
      '<span class="fs">' + Math.round(o.size / 1024) + ' KB' + (o.verify ? ' · ' + esc(o.verify) : '') + '</span></span>' +
      '<a class="dl" href="/api/tasks/' + t.id + '/files?dir=output&name=' + encodeURIComponent(o.name) + '">下载</a>';
    ul.appendChild(li);
  });
  if (!shown.length) ul.innerHTML = '<li style="border:none;background:none;padding-left:0;color:#98a1b3;font-size:13px">暂无产出</li>';

  var reportMd = shown.find(function (o) { return o.name === '修改对照表.md' || (o.name.indexOf('对照表') >= 0 && /\.md$/i.test(o.name)); });
  var reportDocx = shown.find(function (o) { return /对照表\.docx$/i.test(o.name) && o.name.indexOf('_修改后') < 0; });
  var docxLink = $('reportDocx');
  if ((reportMd || reportDocx) && (t.status === 'done' || t.status === 'failed')) {
    $('reportHead').classList.remove('hidden');
    if (docxLink) {
      if (reportDocx) {
        docxLink.classList.remove('hidden');
        docxLink.href = '/api/tasks/' + t.id + '/files?dir=output&name=' + encodeURIComponent(reportDocx.name);
      } else {
        docxLink.classList.add('hidden');
        docxLink.removeAttribute('href');
      }
    }
    if (reportMd) {
      $('reportBox').classList.remove('hidden');
      fetch('/api/tasks/' + t.id + '/files?dir=output&name=' + encodeURIComponent(reportMd.name))
        .then(function (r) { return r.text(); })
        .then(function (txt) {
          var key = 'md:' + txt.length + ':' + (t.status || '');
          if ($('reportBox').getAttribute('data-key') !== key) {
            $('reportBox').setAttribute('data-key', key);
            $('reportBox').innerHTML = renderMarkdown(txt);
          }
        });
    } else {
      $('reportBox').classList.add('hidden');
    }
  } else {
    $('reportHead').classList.add('hidden');
    $('reportBox').classList.add('hidden');
    if (docxLink) docxLink.classList.add('hidden');
  }
  if (t.status === 'done' || t.status === 'failed') {
    refreshList();
    var keep = currentSiblings && currentSiblings.some(function (s) { return s.status !== 'done' && s.status !== 'failed'; });
    if (!keep) { clearInterval(pollTimer); pollTimer = null; }
  }
}
function resetPlanView() {
  var pe = document.getElementById('planEditor');
  if (pe) { pe.setAttribute('data-tid', ''); pe.innerHTML = ''; pe.classList.add('hidden'); }
  curPlanTaskId = null;
  planFetchInFlight = null;
  var rb = $('reportBox');
  if (rb) rb.setAttribute('data-key', '');
}
function updatePager() {
  var el = $('bookPager');
  if (!el) return;
  if (!currentSiblings || currentSiblings.length < 2) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  var i = currentPage;
  var s = currentSiblings[i] || {};
  $('pageTitle').textContent = s.appName || s.id || '';
  $('pageInfo').textContent = '第 ' + (i + 1) + ' / ' + currentSiblings.length + ' 本　' + statusText(s.status);
  $('pagePrev').disabled = i <= 0;
  $('pageNext').disabled = i >= currentSiblings.length - 1;
}
function switchBook(i) {
  if (!currentSiblings || i < 0 || i >= currentSiblings.length) return;
  if (currentSiblings[i].id === currentDetail) { currentPage = i; updatePager(); return; }
  currentPage = i;
  resetPlanView();
  showDetail(currentSiblings[i].id, false);
}
function showDetail(id, scroll) {
  currentDetail = id;
  $('matchCard').classList.add('hidden');
  $('detailCard').classList.remove('hidden');
  updatePager();
  if (pollTimer) clearInterval(pollTimer);
  pollDetail();
  pollTimer = setInterval(pollDetail, 1500);
  if (scroll !== false) $('detailCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
var failStreak = 0;
function pollDetail() {
  if (!currentDetail) return;
  fetch('/api/tasks/' + currentDetail).then(readJson).then(function (t) { failStreak = 0; renderDetail(t); }).catch(function () {
    failStreak++;
    if (failStreak === 2) {
      var m = document.getElementById('detailMeta');
      if (m) m.innerHTML = '<span style="color:#c5221f;font-weight:700">服务无响应</span> —— 请确认 start.cmd 窗口是否还开着（关闭窗口＝停止服务）；恢复后本页会自动继续。';
    }
  });
}

/* ---------- events ---------- */
$('submitBtn').onclick = function () {
  var errEl = $('formErr'); errEl.textContent = '';
  var rawApps = pickedApps.slice();
  var audit = auditAppFiles(rawApps);
  if (audit.badNames.length) { errEl.textContent = '申报书栏存在无效选择：' + audit.badNames.join('；'); return; }
  var appFs = audit.good;
  var opFs = pickedOps.slice();
  if (!appFs.length) { errEl.textContent = '请选择申报书 .docx 或数字版 PDF'; return; }
  if (!opFs.length) { errEl.textContent = '请至少选择一份修改意见文档'; return; }
  var badOps = opFs.filter(function (f) { return !isOpFile(f.name); }).map(function (f) { return f.name; });
  if (badOps.length) { errEl.textContent = '意见栏存在不支持的类型：' + badOps.join('；'); return; }
  var multi = appFs.length > 1;
  $('submitBtn').disabled = true; $('submitBtn').textContent = '上传中…';
  var reads = appFs.map(fileToB64).concat(opFs.map(fileToB64));
  Promise.all(reads).then(function (all) {
    var apps = appFs.map(function (f, i) { return { name: f.name, dataB64: all[i] }; });
    var opinions = opFs.map(function (f, i) { return { name: f.name, dataB64: all[appFs.length + i] }; });
    var body = { engine: 'api', model: ($('engine') && $('engine').value) || '' };
    if (!multi) {
      body.app = apps[0]; body.opinions = opinions;
      return fetch('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    }
    body.apps = apps; body.opinions = opinions;
    return fetch('/api/batches', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }).then(readJson).then(function (res) {
    clearPicked();
    refreshList();
    if (multi) {
      currentSiblings = null; currentPage = 0;
      pollBatch(res.id);
      $('matchCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      currentSiblings = null; currentPage = 0;
      showDetail(res.id);
    }
  }).catch(function (e) {
    errEl.textContent = '提交失败: ' + e;
  }).finally(function () {
    $('submitBtn').disabled = false; $('submitBtn').textContent = '提交任务';
  });
};
function resetForm() {
  clearPicked();
}
function hideSharedMatch() {
  var h = $('sharedHead'); if (h) h.classList.add('hidden');
  var l = $('sharedList'); if (l) l.innerHTML = '';
}
function pollBatch(id) {
  currentBatch = id;
  $('matchCard').classList.remove('hidden');
  $('detailCard').classList.add('hidden');
  $('batchId').textContent = id;
  $('matchErr').textContent = '';
  fetch('/api/batches/' + id).then(readJson).then(renderMatch).catch(function(){});
}
var batchTimer = null;
function scheduleBatch() { if (batchTimer) clearTimeout(batchTimer); batchTimer = setTimeout(function () { batchTimer = null; if (currentBatch && !$('matchCard').classList.contains('hidden')) pollBatch(currentBatch); }, 1800); }

function renderMatch(b) {
  $('matchStats').innerHTML = '状态：<b>' + (b.status === 'started' ? '执行中/已完成' : b.status) + '</b>　·　创建：' + b.createdAt +
    (b.error ? '<br><span style="color:#c5221f">' + esc(b.error) + '</span>' : '');
  if (b.status === 'failed') { hideSharedMatch(); scheduleBatchOff(); return; }
  if (b.status === 'started') {
    hideSharedMatch();
    scheduleBatchOff();
    var sum = b.taskSummaries || [];
    var h = '<div class="mbook"><div class="mbook-h"><span>批次成果（每本书一份修改稿）</span><span class="cnt">' + sum.length + ' 本</span></div><div class="mlist">';
    sum.forEach(function (ts) {
      var badge = '<span class="badge st-' + ts.status + '">' + statusText(ts.status) + '</span>';
      h += '<div class="mrow" style="border-top:none"><span class="mtxt"><b>' + esc(ts.app) + '</b>　' + badge;
      (ts.deliverables || []).forEach(function (o) {
        if (o.name.endsWith('_修改后.docx') || o.name.indexOf('对照表') >= 0 || o.name.indexOf('遗留') >= 0) {
          h += ' <a href="/api/tasks/' + ts.id + '/files?dir=output&name=' + encodeURIComponent(o.name) + '" style="color:#1462ae">' + esc(o.name) + '</a>';
        }
      });
      h += '</span></div>';
    });
    h += '</div></div>';
    $('matchBooks').innerHTML = h;
    var busy = sum.some(function (s3) { return s3.status !== 'done' && s3.status !== 'failed'; });
    if (busy) scheduleBatch();
    return;
  }
  if (b.status !== 'ready') { $('matchBooks').innerHTML = '<div class="empty">正在提取与匹配…</div>'; hideSharedMatch(); scheduleBatch(); return; }
  scheduleBatchOff();
  var books = (b.match && b.match.books) || [];
  var html = '';
  var totalBlocks = 0;
  books.forEach(function (bk) {
    if (!bk.matched.length) return;
    totalBlocks += bk.matched.length;
    html += '<div class="mbook"><div class="mbook-h"><span>' + esc(bk.file) + '</span><span class="cnt">' + bk.matched.length + ' 块</span></div><div class="mlist">';
    bk.matched.forEach(function (mm) {
      html += '<label class="mrow"><input type="checkbox" checked data-kind="match" data-file="' + escAttr(bk.file) + '" data-op="' + escAttr(mm.opName) + '" data-idx="' + mm.blockIdx + '">' +
        '<span class="mtxt">' + esc(mm.head) + '<br><span class="msrc">' + esc(mm.opName) + '#' + mm.blockIdx + ' · 证据: ' + esc(mm.evidence) + ' · ' + mm.score + '分</span></span></label>';
    });
    html += '</div></div>';
  });
  if (!totalBlocks) html = '<div class="empty">没有任何自动配对结果</div>';
  $('matchBooks').innerHTML = html;
  var shared = (b.match && b.match.shared) || [];
  var sh = '';
  shared.forEach(function (sm) {
    sh += '<div class="mbook"><div class="mbook-h"><span>' + esc(sm.head) + '</span><span class="cnt">命中 ' + (sm.books || []).length + ' 本</span></div><div class="mlist">';
    sh += '<div class="mrow" style="border-top:none"><span class="mtxt"><span class="msrc">' + esc(sm.opName) + '#' + sm.blockIdx + ' · ' + esc(sm.excerpt || '') + '</span></span></div>';
    (sm.books || []).forEach(function (fn) {
      sh += '<label class="mrow"><input type="checkbox" checked data-kind="shared" data-file="' + escAttr(fn) + '" data-op="' + escAttr(sm.opName) + '" data-idx="' + sm.blockIdx + '">' +
        '<span class="mtxt">写入　<b>' + esc(fn) + '</b></span></label>';
    });
    sh += '</div></div>';
  });
  $('sharedList').innerHTML = sh;
  if (shared.length) $('sharedHead').classList.remove('hidden');
  else $('sharedHead').classList.add('hidden');
  var unl = $('unmatchedList'); unl.innerHTML = '';
  ((b.match && b.match.unmatched) || []).forEach(function (u) {
    var li = document.createElement('li');
    li.textContent = u.head + '　[' + u.opName + '#' + u.blockIdx + ']';
    unl.appendChild(li);
  });
  $('startBatchBtn').textContent = '开始执行';
  $('startBatchBtn').disabled = false;
}
function scheduleBatchOff() { if (batchTimer) { clearTimeout(batchTimer); batchTimer = null; } }

$('startBatchBtn').onclick = function () {
  var errEl = $('matchErr'); errEl.textContent = '';
  if (!currentBatch) return;
  var picks = [];
  Array.prototype.slice.call(document.querySelectorAll('#matchBooks input[data-kind=match]:checked')).forEach(function (cb) {
    picks.push({ bookFile: cb.getAttribute('data-file'), opName: cb.getAttribute('data-op'), blockIdx: parseInt(cb.getAttribute('data-idx'), 10) });
  });
  var sharedMap = {};
  Array.prototype.slice.call(document.querySelectorAll('#sharedList input[data-kind=shared]:checked')).forEach(function (cb) {
    var k = cb.getAttribute('data-op') + '\t' + cb.getAttribute('data-idx');
    if (!sharedMap[k]) sharedMap[k] = { opName: cb.getAttribute('data-op'), blockIdx: parseInt(cb.getAttribute('data-idx'), 10), books: [] };
    sharedMap[k].books.push(cb.getAttribute('data-file'));
  });
  var shared = Object.keys(sharedMap).map(function (k) { return sharedMap[k]; });
  $('startBatchBtn').disabled = true; $('startBatchBtn').textContent = '创建中…';
  fetch('/api/batches/' + currentBatch + '/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ includeGeneric: $('genericToggle').checked, picks: picks, shared: shared }) })
    .then(readJson)
    .then(function (res) {
      refreshList();
      var ids = res.taskIds || [];
      if (!ids.length) { errEl.textContent = '没有可执行的配对任务'; return; }
      fetch('/api/batches/' + currentBatch).then(readJson).then(function (b) {
        var sum = b.taskSummaries || [];
        currentSiblings = (sum.length ? sum : ids.map(function (id) { return { id: id, app: '', status: 'queued' }; })).map(asSib);
        currentPage = 0;
        $('matchCard').classList.add('hidden');
        showDetail(currentSiblings[0].id);
      }).catch(function () {
        currentSiblings = ids.map(function (id) { return { id: id, status: 'queued', appName: '' }; });
        currentPage = 0;
        $('matchCard').classList.add('hidden');
        showDetail(ids[0]);
      });
    })
    .catch(function (e) { errEl.textContent = e.message || String(e); })
    .finally(function () { $('startBatchBtn').disabled = false; $('startBatchBtn').textContent = '开始执行'; });
};

$('appFile').addEventListener('change', function () {
  try {
    mergeFiles(pickedApps, Array.from(this.files || []));
    this.value = '';
    renderAppChips();
  } catch (err) { console.error(err); }
});
$('opFiles').addEventListener('change', function () {
  try {
    mergeFiles(pickedOps, Array.from(this.files || []));
    this.value = '';
    renderOpChips();
  } catch (err) { console.error(err); }
});
function onChipRemove(ev) {
  var btn = ev.target.closest ? ev.target.closest('.chip-x') : null;
  if (!btn) return;
  ev.preventDefault();
  ev.stopPropagation();
  var kind = btn.getAttribute('data-kind');
  var i = parseInt(btn.getAttribute('data-i'), 10);
  if (isNaN(i) || i < 0) return;
  if (kind === 'app') {
    pickedApps.splice(i, 1);
    renderAppChips();
  } else if (kind === 'op') {
    pickedOps.splice(i, 1);
    renderOpChips();
  }
}
$('appName').addEventListener('click', onChipRemove);
$('opNames').addEventListener('click', onChipRemove);
$('closeDetail').onclick = function () {
  $('detailCard').classList.add('hidden');
  currentDetail = null;
  currentSiblings = null;
  currentPage = 0;
  updatePager();
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
};
$('pagePrev').onclick = function () { switchBook(currentPage - 1); };
$('pageNext').onclick = function () { switchBook(currentPage + 1); };

// 会话过期时全局跳转登录页
(function () {
  var _fetch = window.fetch;
  window.fetch = function () {
    return _fetch.apply(this, arguments).then(function (res) {
      if (res.status === 401 && location.pathname !== '/login') {
        location.href = '/login';
      }
      return res;
    });
  };
})();

function initUserBox() {
  var box = $('userBox');
  if (!box) return;
  fetch('/api/auth/me').then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
    var u = j && j.user;
    if (!u) { location.href = '/login'; return; }
    $('userName').textContent = u.realName + (u.department ? ' · ' + u.department : '');
    if (u.role === 'admin') $('adminLink').style.display = '';
    box.style.display = '';
    var lb = $('logoutBtn');
    if (lb) lb.onclick = function () {
      fetch('/api/auth/logout', { method: 'POST' }).then(function () { location.href = '/login'; });
    };
  });
}

/* ---------- 意见反馈 ---------- */
var fbFiles = [];
function fbStatusLabel(st) {
  if (st === 'done') return '已处理';
  if (st === 'read') return '已读';
  return '待处理';
}
function setFbMsg(ok, text) {
  var el = $('fbMsg');
  if (!el) return;
  el.className = ok ? 'err ok' : 'err';
  el.textContent = text || '';
}
function revokeFbUrls() {
  fbFiles.forEach(function (x) { if (x.url) URL.revokeObjectURL(x.url); });
}
function renderFbThumbs() {
  var box = $('fbThumbs');
  if (!box) return;
  box.innerHTML = fbFiles.map(function (f, i) {
    return '<div class="fb-thumb"><img src="' + escAttr(f.url) + '" alt=""><button type="button" class="chip-x" data-i="' + i + '" title="移除">×</button></div>';
  }).join('');
}
function openFb() {
  var mask = $('fbMask');
  if (!mask) return;
  mask.hidden = false;
  setFbMsg(false, '');
  loadMyFeedback();
}
function closeFb() {
  var mask = $('fbMask');
  if (mask) mask.hidden = true;
}
function loadMyFeedback() {
  var box = $('fbMine');
  if (!box) return;
  fetch('/api/feedback').then(readJson).then(function (res) {
    var items = (res && res.items) || [];
    if (!items.length) { box.textContent = '暂无反馈'; return; }
    box.innerHTML = items.map(function (it) {
      var imgs = (it.files || []).map(function (f) {
        return '<a href="' + escAttr(f.url) + '" target="_blank" rel="noopener"><img src="' + escAttr(f.url) + '" alt="' + escAttr(f.name) + '"></a>';
      }).join('');
      return '<div class="fb-item"><div class="fb-meta"><span class="fb-st ' + escAttr(it.status || 'new') + '">' + esc(fbStatusLabel(it.status)) + '</span><span>' + esc(it.createdAt) + '</span></div>' +
        (it.content ? '<div class="fb-body">' + esc(it.content) + '</div>' : '') +
        (imgs ? '<div class="fb-imgs">' + imgs + '</div>' : '') +
        (it.reply
          ? '<div class="fb-reply"><span class="fb-rp-meta">管理员回复' + (it.replyAt ? ' · ' + esc(it.replyAt) : '') + '</span><span class="fb-rp-text">' + esc(it.reply) + '</span></div>'
          : '') +
        '</div>';
    }).join('');
  }).catch(function (err) {
    box.textContent = '加载失败：' + (err && err.message || err);
  });
}
function submitFeedback() {
  var btn = $('fbSubmitBtn');
  var text = ($('fbContent') && $('fbContent').value || '').trim();
  if (!text && !fbFiles.length) { setFbMsg(false, '请填写内容或上传截图'); return; }
  if (btn) btn.disabled = true;
  setFbMsg(false, '提交中…');
  var fd = new FormData();
  fd.append('content', text);
  fbFiles.forEach(function (x) { fd.append('files', x.file, x.file.name); });
  fetch('/api/feedback', { method: 'POST', body: fd }).then(readJson).then(function () {
    if ($('fbContent')) $('fbContent').value = '';
    revokeFbUrls();
    fbFiles = [];
    renderFbThumbs();
    if ($('fbFiles')) $('fbFiles').value = '';
    setFbMsg(true, '已提交，管理员可在后台查看');
    loadMyFeedback();
  }).catch(function (err) {
    setFbMsg(false, '提交失败：' + (err && err.message || err));
  }).then(function () { if (btn) btn.disabled = false; });
}
function initFeedback() {
  var open = $('fbOpenBtn'), mask = $('fbMask'), close = $('fbCloseBtn');
  var input = $('fbFiles'), thumbs = $('fbThumbs'), sub = $('fbSubmitBtn');
  if (!open || !mask) return;
  open.onclick = function () { openFb(); };
  if (close) close.onclick = closeFb;
  mask.addEventListener('click', function (ev) { if (ev.target === mask) closeFb(); });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && mask && !mask.hidden) closeFb();
  });
  if (input) input.addEventListener('change', function () {
    var added = Array.prototype.slice.call(this.files || []);
    this.value = '';
    added.forEach(function (f) {
      if (!/^image\//i.test(f.type) && !/\.(jpe?g|png|gif|webp)$/i.test(f.name)) return;
      if (f.size > 8 * 1024 * 1024) { setFbMsg(false, '超过 8MB：' + f.name); return; }
      var key = f.name + ':' + f.size + ':' + f.lastModified;
      if (fbFiles.some(function (x) { return x.key === key; })) return;
      if (fbFiles.length >= 8) { setFbMsg(false, '最多 8 张图片'); return; }
      fbFiles.push({ file: f, key: key, url: URL.createObjectURL(f) });
    });
    renderFbThumbs();
  });
  if (thumbs) thumbs.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('.chip-x') : null;
    if (!btn) return;
    var i = parseInt(btn.getAttribute('data-i'), 10);
    if (isNaN(i) || !fbFiles[i]) return;
    if (fbFiles[i].url) URL.revokeObjectURL(fbFiles[i].url);
    fbFiles.splice(i, 1);
    renderFbThumbs();
  });
  if (sub) sub.onclick = submitFeedback;
}

loadConfig();
initUserBox();
initFeedback();
refreshList();
setInterval(refreshList, 3000);
/* ---------- 计划编辑器（模型出计划 → 人工修订 → 确认写入） ---------- */
var curPlanTaskId = null;
var curPlanData = [];
var planFetchInFlight = null;

function renderPlanSection(t) {
  var el = document.getElementById('planEditor');
  if (!el) {
    el = document.createElement('div');
    el.id = 'planEditor';
    var meta = document.getElementById('detailMeta');
    meta.parentNode.insertBefore(el, meta.nextSibling);
  }
  if (t.status !== 'planned' && t.status !== 'failed') {
    el.setAttribute('data-tid', '');
    el.classList.add('hidden'); el.innerHTML = ''; curPlanTaskId = null;
    planFetchInFlight = null;
    return;
  }
  el.classList.remove('hidden');
  if (el.getAttribute('data-tid') === String(t.id) && el.querySelector('#planRows')) return; // 保留人工输入
  if (planFetchInFlight === String(t.id)) return;
  curPlanTaskId = t.id;
  planFetchInFlight = String(t.id);
  el.innerHTML = '<div class="empty">正在载入编辑计划…</div>';
  fetch('/api/tasks/' + t.id + '/plan').then(function (r) {
    if (!r.ok) throw new Error(r.status === 404 ? '尚未生成' : 'HTTP ' + r.status);
    return r.json();
  }).then(function (plan) {
    if (curPlanTaskId !== String(t.id)) return;
    buildPlanEditor(el, t, plan);
    el.setAttribute('data-tid', String(t.id));
  }).catch(function (e) {
    if (curPlanTaskId !== String(t.id)) return;
    el.setAttribute('data-tid', '');
    if (t.status === 'failed' && String(e.message || '').indexOf('尚未生成') >= 0) {
      el.classList.add('hidden'); el.innerHTML = ''; curPlanTaskId = null;
      return;
    }
    el.innerHTML = '<div class="empty">计划载入失败：' + esc(e.message) + '</div>';
  }).finally(function () {
    if (planFetchInFlight === String(t.id)) planFetchInFlight = null;
  });
}

function appNoOf(name) {
  var nums = [], m, re = /\d{4,6}/g;
  name = String(name || '');
  while ((m = re.exec(name))) {
    if (/^2[56]\d{4}$/.test(m[0])) continue;
    nums.push(m[0]);
  }
  return nums.join('/');
}

function buildPlanEditor(el, t, plan) {
  curPlanData = [];
  var appNo = (plan && plan.appNo) || (t.app && t.app.no) || appNoOf(t.app && t.app.name) || '';
  (plan.edits || []).forEach(function (e) {
    var hasCompare = Object.prototype.hasOwnProperty.call(e, 'opinionGrok') || Object.prototype.hasOwnProperty.call(e, 'opinionGemini') || Object.prototype.hasOwnProperty.call(e, 'opinionDoubao');
    curPlanData.push({
      find: e.find || '', replace: e.replace || '', clause: e.clause || '',
      opinion: e.opinion || e.clause || '',
      opinionGrok: e.opinionGrok || '',
      opinionGemini: e.opinionGemini || '',
      opinionDoubao: e.opinionDoubao || '',
      hasCompare: hasCompare,
      hasDoubao: Object.prototype.hasOwnProperty.call(e, 'opinionDoubao'),
      opName: e.opName || '', clauseId: e.clauseId || '',
      section: e._sec || e.section || '其他', appNo: e.appNo || appNo
    });
  });
  var loLines = (plan.leftovers || []).join('\n');

  var poolSum = (plan && plan.pool && plan.pool.summary) || (t.poolHit && (t.poolHit.talent || t.poolHit.enterprise) && ('人才 ' + (t.poolHit.talent || '无') + '；企业 ' + (t.poolHit.enterprise || '无'))) || '';
  var att = (plan && plan.attachments) || {};
  var attItems = att.items || [];
  var attSum = att.summary || (t.attachHit && t.attachHit.summary) || '';
  var h = '<div class="pnote">源文件申报书编号 <b class="pno">' + esc(appNo || '未识别') + '</b>　' + esc(t.app && t.app.name || '') +
    (poolSum ? '<br>库内检索：' + esc(poolSum) : '') +
    (attSum ? '<br>缺附件检索：' + esc(attSum) : '') +
    '<br>Grok、Gemini、火山已分别生成修改意见，共 <b>' + curPlanData.length + '</b> 条。请逐条核对：<b>意见条款</b>为短摘要；三列修改意见为各模型给出的改写；<b>修改前</b>为定位锚点（只读），<b>修改后</b>为实际写入内容（默认可点「采用」或直接改写）。取消勾选＝放弃该条。全部确认后才会写入文件。</div>';
  if (attItems.length) {
    h += '<div class="att-box"><div class="att-h">已检索到的附件（点击下载）</div><ul class="att-ul">';
    attItems.forEach(function (it) {
      var src = it.source === 'papers' ? '论文系统' : '人才库';
      h += '<li><span class="att-k">' + esc(it.kind || '附件') + '</span> <a class="dl" href="' + escAttr(it.download || '') + '">' + esc(it.filename || it.title || '下载') + '</a> <em>' + esc(src) + (it.title && it.title !== it.filename ? ' · ' + it.title : '') + '</em></li>';
    });
    h += '</ul></div>';
  }
  h += '<div class="ptable-wrap"><table class="ptable"><thead><tr><th style="width:34px">用</th><th style="width:88px">编号</th><th style="width:70px">章节</th><th>意见条款</th><th style="width:12%">修改前（定位用，勿改）</th><th style="width:14%">Grok修改意见</th><th style="width:14%">Gemini修改意见</th><th style="width:14%">火山修改意见</th><th style="width:16%">修改后（可编辑）</th><th style="width:36px"></th></tr></thead><tbody id="planRows"></tbody></table></div>';
  h += '<button class="mini addrow" id="addRowBtn">新增一行</button>';
  h += '<h3>遗留事项（每行一条，可编辑）</h3><textarea id="loTa" class="lo-ta"></textarea>';
  h += '<div class="actions"><button id="applyBtn" class="primary">确认无误，写入文件</button><button id="replanBtn" class="ghost">重新生成计划</button><span id="planErr" class="err"></span></div>';
  el.innerHTML = h;

  var tb = el.querySelector('#planRows');
  curPlanData.forEach(function (e, i) { tb.appendChild(buildRow(e, i)); });
  el.querySelector('#loTa').value = loLines;
  el.oninput = function (ev) {
    if (ev.target && ev.target.matches && ev.target.matches('.ta-find,.ta-rep,.ta-op,.ta-op-grok,.ta-op-gemini,.ta-op-doubao,.ta-clause')) fitTa(ev.target);
  };
  requestAnimationFrame(function () { fitPlanFields(tb); });

  el.querySelector('#addRowBtn').onclick = function () {
    var ne = { find: '', replace: '', clause: '（人工新增）', opinion: '', opinionGrok: '', opinionGemini: '', opinionDoubao: '', hasDoubao: true, opName: '', clauseId: '', section: '其他', appNo: appNo };
    var i2 = curPlanData.push(ne) - 1;
    tb.appendChild(buildRow(ne, i2, { editableFind: true }));
    requestAnimationFrame(function () { fitPlanFields(tb.lastChild); });
  };

  el.querySelector('#replanBtn').onclick = function () {
    if (!confirm('重新生成将丢弃当前所有人工修改，确定？')) return;
    fetch('/api/tasks/' + t.id + '/replan', { method: 'POST' }).then(readJson).then(function () {
      el.setAttribute('data-tid', '');
      ensurePoll();
      pollDetail();
    }).catch(function (e2) { el.querySelector('#planErr').textContent = e2.message || String(e2); });
  };

  el.querySelector('#applyBtn').onclick = function () {
    var errEl = el.querySelector('#planErr'); errEl.textContent = '';
    var rows = Array.prototype.slice.call(tb.querySelectorAll('tr'));
    var out = [], missName = [];
    rows.forEach(function (tr) {
      var oi = parseInt(tr.getAttribute('data-oi'), 10);
      var cb = tr.querySelector('input[type=checkbox]');
      var taF = tr.querySelector('.ta-find'), taR = tr.querySelector('.ta-rep');
      var taC = tr.querySelector('.ta-clause');
      var taG = tr.querySelector('.ta-op-grok'), taM = tr.querySelector('.ta-op-gemini'), taD = tr.querySelector('.ta-op-doubao');
      if (!cb.checked) return;
      var fv = taF.value.trim();
      if (!fv) { missName.push('#' + (oi + 1)); return; }
      var src = curPlanData[oi] || {};
      out.push({
        find: taF.value, replace: taR.value,
        clause: (taC && taC.value) || src.clause || '',
        opinion: src.opinion || '',
        opinionGrok: realOp(taG),
        opinionGemini: realOp(taM),
        opinionDoubao: realOp(taD),
        opName: src.opName || '',
        clauseId: src.clauseId || '',
        _sec: src.section || '其他',
        appNo: src.appNo || appNo
      });
    });
    if (missName.length) { errEl.textContent = '以下行缺少锚点：' + missName.join('、'); return; }
    var lo = $('loTa').value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
    if (!out.length && !lo.length) { errEl.textContent = '没有任何要应用的编辑或遗留事项'; return; }
    var btn = this;
    btn.disabled = true; btn.textContent = '写入中…';
    fetch('/api/tasks/' + t.id + '/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ edits: out, leftovers: lo }) })
      .then(readJson)
      .then(function () { ensurePoll(); pollDetail(); })
      .catch(function (e2) { if (errEl) errEl.textContent = e2.message || String(e2); })
      .finally(function () {
        if (!btn || !document.body.contains(btn)) return;
        btn.disabled = false;
        btn.textContent = '确认无误，写入文件';
      });
  };
}

function fitTa(el) {
  if (!el) return;
  el.style.height = '0';
  el.style.height = Math.max(el.scrollHeight, 42) + 'px';
}

function fitPlanFields(root) {
  if (!root) return;
  var list = root.querySelectorAll ? root.querySelectorAll('.ta-find,.ta-rep,.ta-op,.ta-op-grok,.ta-op-gemini,.ta-op-doubao,.ta-clause') : [];
  for (var i = 0; i < list.length; i++) fitTa(list[i]);
}

function realOp(ta) {
  if (!ta) return '';
  var v = String(ta.value || '');
  if (!v || v === OP_EMPTY || v === OP_LEGACY) return '';
  return v;
}
function buildRow(e, oi, opts) {
  var tr = document.createElement('tr');
  tr.setAttribute('data-oi', oi);
  var grokOp = e.opinionGrok || '';
  var geminiOp = e.opinionGemini || '';
  var doubaoOp = e.opinionDoubao || '';
  var emptyHint = (e.hasCompare === false) ? OP_LEGACY : OP_EMPTY;
  var grokEmpty = grokOp ? '' : emptyHint;
  var geminiEmpty = geminiOp ? '' : emptyHint;
  var doubaoEmpty = doubaoOp ? '' : (e.hasDoubao === false ? OP_LEGACY : emptyHint);
  var clauseTip = e.opinion ? ' title="' + escAttr(e.opinion) + '"' : (e.opName ? ' title="' + escAttr(e.opName) + '"' : '');
  var findRo = (opts && opts.editableFind) ? '' : ' readonly';
  tr.innerHTML =
    '<td><input type="checkbox" checked></td>' +
    '<td><span class="pno">' + esc(e.appNo || '—') + '</span></td>' +
    '<td><span class="tag">' + esc(e.section) + '</span></td>' +
    '<td><textarea class="ta-clause" rows="1"' + clauseTip + '>' + escHtml(e.clause) + '</textarea></td>' +
    '<td><textarea class="ta-find" rows="1"' + findRo + '>' + escHtml(e.find) + '</textarea></td>' +
    '<td><div class="op-cell">' +
      '<textarea class="ta-op ta-op-grok" rows="1" readonly>' + escHtml(grokOp || grokEmpty) + '</textarea>' +
      '<button type="button" class="use-op" data-src="grok"' + (grokOp ? '' : ' disabled') + '>采用</button>' +
    '</div></td>' +
    '<td><div class="op-cell">' +
      '<textarea class="ta-op ta-op-gemini" rows="1" readonly>' + escHtml(geminiOp || geminiEmpty) + '</textarea>' +
      '<button type="button" class="use-op" data-src="gemini"' + (geminiOp ? '' : ' disabled') + '>采用</button>' +
    '</div></td>' +
    '<td><div class="op-cell">' +
      '<textarea class="ta-op ta-op-doubao" rows="1" readonly>' + escHtml(doubaoOp || doubaoEmpty) + '</textarea>' +
      '<button type="button" class="use-op" data-src="doubao"' + (doubaoOp ? '' : ' disabled') + '>采用</button>' +
    '</div></td>' +
    '<td><textarea class="ta-rep" rows="1">' + escHtml(e.replace) + '</textarea></td>' +
    '<td><button class="delbtn">✕</button></td>';
  tr.querySelector('.delbtn').onclick = function () { tr.parentNode.removeChild(tr); };
  var opSel = { grok: '.ta-op-grok', gemini: '.ta-op-gemini', doubao: '.ta-op-doubao' };
  Array.prototype.slice.call(tr.querySelectorAll('.use-op')).forEach(function (btn) {
    btn.onclick = function () {
      var src = btn.getAttribute('data-src');
      var ta = tr.querySelector(opSel[src] || '.ta-op-grok');
      var rep = tr.querySelector('.ta-rep');
      if (!ta || !rep || btn.disabled) return;
      var val = realOp(ta);
      if (!val) return;
      rep.value = val;
      fitTa(rep);
    };
  });
  return tr;
}
