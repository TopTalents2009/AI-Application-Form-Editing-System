'use strict';
var currentDetail = null;
var pollTimer = null;
var expandedAll = false;
var currentBatch = null;
var batchTimer = null;
function scheduleBatchOff() {}
var SHOW_LIMIT = 4;
var currentSiblings = null;
var currentPage = 0;

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

/* ---------- config & engine ---------- */
function loadConfig() {
  fetch('/api/config').then(function (r) { return r.json(); }).then(function (cfg) {
  try {
    var row = $('engineRow');
    if (cfg.engines.length === 1) {
      var e = cfg.engines[0];
      var sel = document.createElement('select');
      sel.id = 'engine'; sel.style.display = 'none';
      var opt = document.createElement('option');
      opt.value = e.id; opt.textContent = e.label;
      sel.appendChild(opt);
      row.innerHTML = '<div class="engtag"><span class="edot"></span>' + esc(e.label) + '</div>';
      row.appendChild(sel);
    } else {
      var s = '<select id="engine">';
      cfg.engines.forEach(function (e2) { s += '<option value="' + e2.id + '">' + esc(e2.label) + '</option>'; });
      row.innerHTML = s + '</select>';
    }
  } catch (err) { console.error(err); }
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
function chip(name, bad, hint) {
    var cls = 'chip' + (bad ? ' bad' : '');
    return '<span class="' + cls + '" title="' + esc(name) + '">' + (bad ? '⚠ ' : '') + esc(name) + (hint ? '<em> ' + hint + '</em>' : '') + '</span>';
  }
function isDocx(name) { return /\.docx$/i.test(String(name || '')); }
function auditAppFiles(fileList) {
  var good = [], badNames = [];
  fileList.forEach(function (f) {
    if (isDocx(f.name)) {
      good.push(f);
      if (/意见/.test(f.name)) badNames.push(f.name + '（名称含“意见”，疑似选到了修改意见文档）');
    } else {
      badNames.push(f.name + '（非 .docx）');
    }
  });
  return { good: good, badNames: badNames };
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
function renderRow(row, tb) {
  var tr = document.createElement('tr');
  var extra = row.count > 1 ? '<span class="tcount">' + row.count + ' 本</span>' : '';
  tr.innerHTML =
    '<td style="white-space:nowrap">' + row.createdAt + '</td>' +
    '<td><span class="tname" title="' + esc(row.title) + '">' + esc(row.name) + '</span>' + extra + '</td>' +
    '<td><span class="badge st-' + row.status + '">' + statusText(row.status) + '</span></td>' +
    '<td><button class="mini">查看</button></td>';
  tr.querySelector('button').onclick = function () { openListRow(row); };
  tb.appendChild(tr);
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
      name: (apps[0] || '批次') + (apps.length > 1 ? ' 等' + apps.length + '本' : ''),
      title: apps.join('、'),
      count: apps.length,
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
      name: (first.app && first.app.name) + (sibs.length > 1 ? ' 等' + sibs.length + '本' : ''),
      title: sibs.map(function (x) { return x.app && x.app.name; }).join('、'),
      count: sibs.length,
      sibs: sibs,
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
    tb.innerHTML = '';
    var n = rows.length;
    $('emptyHint').style.display = n ? 'none' : 'block';
    var shown = expandedAll ? rows : rows.slice(0, SHOW_LIMIT);
    shown.forEach(function (row) { renderRow(row, tb); });
    var btn = $('toggleAll');
    if (expandedAll || n > SHOW_LIMIT) {
      btn.classList.remove('hidden');
      btn.textContent = expandedAll ? '▴ 收起列表' : '▾ 展开全部（共 ' + n + ' 条）';
    } else {
      btn.classList.add('hidden');
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
  if (msg.indexOf('✅') === 0) return 'ln-ok';
  if (msg.indexOf('⚠️') === 0) return 'ln-warn';
  if (msg.indexOf('🔧') === 0) return 'ln-tool';
  if (msg.indexOf('💬') === 0) return 'ln-say';
  if (msg.indexOf('🚀') === 0) return 'ln-go';
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
    var ic = o.name.endsWith('.docx') ? '📘' : (o.name.endsWith('.md') ? '📝' : '🐍');
    var li = document.createElement('li');
    li.innerHTML =
      '<span class="fic">' + ic + '</span>' +
      '<span class="fi"><span class="fn" title="' + esc(o.name) + '">' + esc(o.name) + '</span>' +
      '<span class="fs">' + Math.round(o.size / 1024) + ' KB' + (o.verify ? ' · ' + esc(o.verify) : '') + '</span></span>' +
      '<a class="dl" href="/api/tasks/' + t.id + '/files?dir=output&name=' + encodeURIComponent(o.name) + '">下载</a>';
    ul.appendChild(li);
  });
  if (!shown.length) ul.innerHTML = '<li style="border:none;background:none;padding-left:0;color:#98a1b3;font-size:13px">暂无产出</li>';

  var report = shown.find(function (o) { return o.name.indexOf('对照表') >= 0; });
  if (report && (t.status === 'done' || t.status === 'failed')) {
    $('reportHead').classList.remove('hidden');
    $('reportBox').classList.remove('hidden');
    fetch('/api/tasks/' + t.id + '/files?dir=output&name=' + encodeURIComponent(report.name))
      .then(function (r) { return r.text(); })
      .then(function (txt) {
        var key = 'md:' + txt.length + ':' + (t.status || '');
        if ($('reportBox').getAttribute('data-key') !== key) {
          $('reportBox').setAttribute('data-key', key);
          $('reportBox').innerHTML = renderMarkdown(txt);
        }
      });
  } else {
    $('reportHead').classList.add('hidden');
    $('reportBox').classList.add('hidden');
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
      if (m) m.innerHTML = '<span style="color:#c5221f;font-weight:700">⚠ 服务无响应</span> —— 请确认 start.cmd 窗口是否还开着（关闭窗口＝停止服务）；恢复后本页会自动继续。';
    }
  });
}

/* ---------- events ---------- */
$('submitBtn').onclick = function () {
  var errEl = $('formErr'); errEl.textContent = '';
  var rawApps = Array.from($('appFile').files || []);
  var audit = auditAppFiles(rawApps);
  if (audit.badNames.length) { errEl.textContent = '申报书栏存在无效选择：' + audit.badNames.join('；'); return; }
  var appFs = audit.good;
  var opFs = Array.from($('opFiles').files || []);
  if (!appFs.length) { errEl.textContent = '请选择申报书 .docx'; return; }
  if (!opFs.length) { errEl.textContent = '请至少选择一份修改意见文档'; return; }
  var multi = appFs.length > 1;
  var engine = $('engine').value || 'api';
  $('submitBtn').disabled = true; $('submitBtn').textContent = '上传中…';
  var reads = appFs.map(fileToB64).concat(opFs.map(fileToB64));
  Promise.all(reads).then(function (all) {
    var apps = appFs.map(function (f, i) { return { name: f.name, dataB64: all[i] }; });
    var opinions = opFs.map(function (f, i) { return { name: f.name, dataB64: all[appFs.length + i] }; });
    if (!multi) {
      return fetch('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ engine: engine, app: apps[0], opinions: opinions }) });
    }
    return fetch('/api/batches', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ engine: engine, apps: apps, opinions: opinions }) });
  }).then(readJson).then(function (res) {
    $('appFile').value = ''; $('opFiles').value = '';
    $('appName').innerHTML = ''; $('opNames').innerHTML = '';
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
    $('submitBtn').disabled = false; $('submitBtn').textContent = '🚀 提交任务';
  });
};
function resetForm() {
  $('appFile').value = ''; $('opFiles').value = '';
  $('appName').innerHTML = ''; $('opNames').innerHTML = '';
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
    var h = '<div class="mbook"><div class="mbook-h"><span>📦 批次成果（每本书一份修改稿）</span><span class="cnt">' + sum.length + ' 本</span></div><div class="mlist">';
    sum.forEach(function (ts) {
      var badge = '<span class="badge st-' + ts.status + '">' + statusText(ts.status) + '</span>';
      h += '<div class="mrow" style="border-top:none"><span class="fic">📘</span><span class="mtxt"><b>' + esc(ts.app) + '</b>　' + badge;
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
    html += '<div class="mbook"><div class="mbook-h"><span>📘 ' + esc(bk.file) + '</span><span class="cnt">' + bk.matched.length + ' 块</span></div><div class="mlist">';
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
    sh += '<div class="mbook"><div class="mbook-h"><span>🔗 ' + esc(sm.head) + '</span><span class="cnt">命中 ' + (sm.books || []).length + ' 本</span></div><div class="mlist">';
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
  $('startBatchBtn').textContent = '🚀 开始执行';
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
    .finally(function () { $('startBatchBtn').disabled = false; $('startBatchBtn').textContent = '🚀 开始执行'; });
};

$('appFile').addEventListener('change', function () {
  try {
  var audit = auditAppFiles(Array.from(this.files || []));
  var parts = audit.good.map(function (f) { return chip(f.name); });
  audit.badNames.forEach(function (n) { parts.push(chip(n, true)); });
  $('appName').innerHTML = parts.join('');
  } catch (err) { console.error(err); }
});
$('opFiles').addEventListener('change', function () {
  try {
  var list = Array.from(this.files || []);
  $('opNames').innerHTML = list.map(function (f) { return chip(f.name); }).join('');
  } catch (err) { console.error(err); }
});
$('toggleAll').onclick = function () {
  expandedAll = !expandedAll;
  refreshList();
};
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

loadConfig();
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
    curPlanData.push({
      find: e.find || '', replace: e.replace || '', clause: e.clause || '',
      opinion: e.opinion || e.clause || '', opName: e.opName || '', clauseId: e.clauseId || '',
      section: e._sec || e.section || '其他', appNo: e.appNo || appNo
    });
  });
  var loLines = (plan.leftovers || []).join('\n');

  var poolSum = (plan && plan.pool && plan.pool.summary) || (t.poolHit && (t.poolHit.talent || t.poolHit.enterprise) && ('人才 ' + (t.poolHit.talent || '无') + '；企业 ' + (t.poolHit.enterprise || '无'))) || '';
  var h = '<div class="pnote">🤖 源文件申报书编号 <b class="pno">' + esc(appNo || '未识别') + '</b>　' + esc(t.app && t.app.name || '') +
    (poolSum ? '<br>📚 库内检索：' + esc(poolSum) : '') +
    '<br>模型已生成 <b>' + curPlanData.length + '</b> 条编辑。请逐条核对：<b>意见条款</b>为短摘要，<b>修改意见</b>为意见文件原文摘录；<b>修改前</b>为定位锚点（只读），<b>修改后</b>可直接改写；取消勾选＝放弃该条。全部确认后点击下方按钮才会写入文件。</div>';
  h += '<div class="ptable-wrap"><table class="ptable"><thead><tr><th style="width:34px">用</th><th style="width:88px">编号</th><th style="width:70px">章节</th><th>意见条款</th><th style="width:18%">修改前（只读锚点）</th><th style="width:22%">修改意见</th><th style="width:24%">修改后（可编辑）</th><th style="width:36px"></th></tr></thead><tbody id="planRows"></tbody></table></div>';
  h += '<button class="mini addrow" id="addRowBtn">➕ 新增一行</button>';
  h += '<h3><span class="ic">📝</span>遗留事项（每行一条，可编辑）</h3><textarea id="loTa" class="lo-ta"></textarea>';
  h += '<div class="actions"><button id="applyBtn" class="primary">✅ 确认无误，写入文件</button><button id="replanBtn" class="ghost">🔄 重新生成计划</button><span id="planErr" class="err"></span></div>';
  el.innerHTML = h;

  var tb = el.querySelector('#planRows');
  curPlanData.forEach(function (e, i) { tb.appendChild(buildRow(e, i)); });
  el.querySelector('#loTa').value = loLines;
  el.oninput = function (ev) {
    if (ev.target && ev.target.matches && ev.target.matches('.ta-find,.ta-rep,.ta-op,.ta-clause')) fitTa(ev.target);
  };
  requestAnimationFrame(function () { fitPlanFields(tb); });

  el.querySelector('#addRowBtn').onclick = function () {
    var ne = { find: '', replace: '', clause: '（人工新增）', opinion: '', opName: '', clauseId: '', section: '其他', appNo: appNo };
    var i2 = curPlanData.push(ne) - 1;
    tb.appendChild(buildRow(ne, i2));
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
      var taF = tr.querySelector('.ta-find'), taR = tr.querySelector('.ta-rep'), taO = tr.querySelector('.ta-op');
      var taC = tr.querySelector('.ta-clause');
      if (!cb.checked) return;
      var fv = taF.value.trim();
      if (!fv) { missName.push('#' + (oi + 1)); return; }
      var src = curPlanData[oi] || {};
      var opinion = (taO && taO.value) || src.opinion || '';
      out.push({
        find: taF.value, replace: taR.value,
        clause: (taC && taC.value) || src.clause || '',
        opinion: opinion,
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
        btn.textContent = '✅ 确认无误，写入文件';
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
  var list = root.querySelectorAll ? root.querySelectorAll('.ta-find,.ta-rep,.ta-op,.ta-clause') : [];
  for (var i = 0; i < list.length; i++) fitTa(list[i]);
}

function buildRow(e, oi) {
  var tr = document.createElement('tr');
  tr.setAttribute('data-oi', oi);
  var opinion = e.opinion || e.clause || '';
  var opTip = e.opName ? ' title="' + esc(e.opName) + '"' : '';
  tr.innerHTML =
    '<td><input type="checkbox" checked></td>' +
    '<td><span class="pno">' + esc(e.appNo || '—') + '</span></td>' +
    '<td><span class="tag">' + esc(e.section) + '</span></td>' +
    '<td><textarea class="ta-clause" rows="1">' + escHtml(e.clause) + '</textarea></td>' +
    '<td><textarea class="ta-find" rows="1">' + escHtml(e.find) + '</textarea></td>' +
    '<td><textarea class="ta-op" rows="1"' + opTip + '>' + escHtml(opinion) + '</textarea></td>' +
    '<td><textarea class="ta-rep" rows="1">' + escHtml(e.replace) + '</textarea></td>' +
    '<td><button class="delbtn">✕</button></td>';
  tr.querySelector('.delbtn').onclick = function () { tr.parentNode.removeChild(tr); };
  return tr;
}
