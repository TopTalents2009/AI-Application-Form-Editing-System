/* 管理后台：入库看板 */
(function (global) {
  function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
  function escAttr(s) { return esc(s).replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function say(ok, t) {
    var msg = document.getElementById('msg');
    if (!msg) return;
    msg.className = 'msg ' + (ok ? 'ok' : 'bad');
    msg.textContent = t;
    setTimeout(function () { if (msg.textContent === t) msg.textContent = ''; }, 4000);
  }
  var STATUS = {
    done: ['st-done', '已完成'], planned: ['st-planned', '待确认'],
    running: ['st-running', '运行中'], pending: ['st-pending', '排队中'],
    failed: ['st-failed', '失败'], matching: ['st-running', '配对中']
  };
  function roleLabel(r) {
    return r === 'admin' ? '管理员' : (r === 'leader' ? '组长' : '普通用户');
  }
  function roleOptions(cur) {
    return [['user', '普通用户'], ['leader', '组长'], ['admin', '管理员']].map(function (v) {
      return '<option value="' + v[0] + '"' + (cur === v[0] ? ' selected' : '') + '>' + v[1] + '</option>';
    }).join('');
  }
  function taskOrigin(t) {
    if (!t) return '人工提交';
    var name = String(t.wecomSession || '').trim();
    if (name) return name;
    var src = String(t.source || '');
    if (src === 'wecom' || src === 'wecom-watch') return '会话';
    var apiName = String(t.apiKeyName || '').trim();
    if (!apiName) {
      var actor = String(t.apiActor || '');
      if (/^API:/i.test(actor)) apiName = actor.replace(/^API:/i, '').trim();
    }
    if (src === 'openapi' || t.apiKeyId || apiName) return apiName || '开放API';
    return '人工提交';
  }
  function parseStamp(s) {
    var m = String(s || '').match(/(\d{4})\D(\d{1,2})\D(\d{1,2})\D(\d{1,2})\D(\d{1,2})\D(\d{1,2})/);
    if (!m) return 0;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
  }
  function formatElapsed(ms) {
    if (!(ms >= 0)) ms = 0;
    var sec = Math.floor(ms / 1000);
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    function z(n) { return (n < 10 ? '0' : '') + n; }
    if (h > 0) return h + ':' + z(m) + ':' + z(s);
    return z(m) + ':' + z(s);
  }
  function runElapsed(since) {
    var t0 = parseStamp(since);
    if (!t0) return '';
    return formatElapsed(Date.now() - t0);
  }
  function badge(st, since) {
    var m = STATUS[st] || ['st-pending', st || '未知'];
    var label = m[1];
    if (st === 'running' && since) {
      var el = runElapsed(since);
      if (el) label += ' ' + el;
      return '<span class="badge ' + m[0] + '" data-run-since="' + escAttr(since) + '" data-run-base="' + escAttr(m[1]) + '">' + esc(label) + '</span>';
    }
    return '<span class="badge ' + m[0] + '">' + esc(label) + '</span>';
  }
  var AdminStats = global.AdminStats || { userCount: 0, totals: { tasks: '—', batches: '—' } };
  global.AdminStats = AdminStats;
  var ingestTimer = null;
  var ingestBound = false;
  var ingestCache = { groups: [], stats: {} };
  var ingestOpen = { groups: {}, apps: {} };
  var ingestPage = 1;
  var INGEST_PAGE_SIZE = 20;
  var IG_CHEV = '<svg class="ig-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
  function ingestAid(g) {
    return String((g && (g.attachId || g.title)) || '无编号');
  }
  function ingestAppKey(g, f) {
    return ingestAid(g) + ':' + String((f && (f.id || f.taskId || f.filename)) || '');
  }
  function rememberIngestOpen() {
    var box = document.getElementById('ingestBoard');
    if (!box) return ingestOpen;
    box.querySelectorAll('details.ingest-group[data-ig-id]').forEach(function (el) {
      var id = el.getAttribute('data-ig-id') || '';
      if (!id) return;
      if (el.open) ingestOpen.groups[id] = true;
      else delete ingestOpen.groups[id];
      el.querySelectorAll('details.ingest-app[data-ig-app]').forEach(function (app) {
        var k = app.getAttribute('data-ig-app') || '';
        if (!k) return;
        if (app.open) ingestOpen.apps[k] = true;
        else delete ingestOpen.apps[k];
      });
    });
    return ingestOpen;
  }
  function restoreIngestOpen() {
    var box = document.getElementById('ingestBoard');
    if (!box) return;
    box.querySelectorAll('details.ingest-group[data-ig-id]').forEach(function (el) {
      var id = el.getAttribute('data-ig-id') || '';
      if (ingestOpen.groups[id]) el.open = true;
      el.querySelectorAll('details.ingest-app[data-ig-app]').forEach(function (app) {
        var k = app.getAttribute('data-ig-app') || '';
        if (ingestOpen.apps[k]) app.open = true;
      });
    });
  }
  function renderIngestFileCells(f) {
    f = f || {};
    var st = f.ok ? '<span class="badge st-done">已入库</span>' : '<span class="badge st-failed">' + esc(f.status || '失败') + '</span>';
    var sess = f.sessionName || f.sessionId || '';
    var acts = [];
    if (f.source === 'app_edit' && f.id) acts.push('<button type="button" class="mini primary" data-ig-preview="' + esc(String(f.id)) + '">预览</button>');
    if (f.url) acts.push('<a class="mini" href="' + esc(f.url) + '">下载</a>');
    if (f.taskId) acts.push('<a class="mini" href="/t/' + encodeURIComponent(f.taskId) + '" target="_blank" rel="noopener">任务</a>');
    var kind = f.source === 'app_edit'
      ? (f.version ? '申报书 ' + f.version : (f.label || '申报书'))
      : (f.label || f.kind || '材料');
    return '<span class="kind">' + esc(kind) + '</span>' +
      '<span class="fn" title="' + esc(f.filename || '') + '">' + esc(f.filename || '未命名') + '</span>' +
      '<span>' + st + '</span>' +
      '<span class="meta">' + esc(f.sourceLabel || '') + (sess ? ' · ' + esc(sess) : '') + '</span>' +
      '<span class="meta">' + esc(f.sender || f.personName || '') + (f.time || f.at ? ' · ' + esc(f.time || f.at) : '') + '</span>' +
      '<span class="ingest-acts">' + acts.join('') + '</span>';
  }
  function ingestQuery() {
    var q = ((document.getElementById('igQ') || {}).value || '').trim();
    var source = ((document.getElementById('igSource') || {}).value || '').trim();
    var ok = ((document.getElementById('igOk') || {}).value || '1').trim();
    var p = [];
    if (q) p.push('q=' + encodeURIComponent(q));
    if (source) p.push('source=' + encodeURIComponent(source));
    if (ok) p.push('ok=' + encodeURIComponent(ok));
    return p.length ? ('?' + p.join('&')) : '';
  }
  function paintIngestStats(d) {
    d = d || {};
    var set = function (id, v) { var el = document.getElementById(id); if (el) el.textContent = v; };
    set('igStGroups', d.groupCount != null ? d.groupCount : '—');
    set('igStApps', d.appCount != null ? d.appCount : '—');
    set('igStWatch', d.watchCount != null ? d.watchCount : '—');
    set('igStSummary', d.summaryCount != null ? d.summaryCount : '—');
    set('igStLocal', d.localCount != null ? d.localCount : '—');
  }
  function renderIngestPage() {
    var box = document.getElementById('ingestBoard');
    var pager = document.getElementById('igPager');
    var info = document.getElementById('igPageInfo');
    var prev = document.getElementById('igPagePrev');
    var next = document.getElementById('igPageNext');
    if (!box) return;
    var groups = ingestCache.groups || [];
    var total = groups.length;
    var pages = Math.max(1, Math.ceil(total / INGEST_PAGE_SIZE));
    if (ingestPage > pages) ingestPage = pages;
    if (ingestPage < 1) ingestPage = 1;
    var start = (ingestPage - 1) * INGEST_PAGE_SIZE;
    var slice = groups.slice(start, start + INGEST_PAGE_SIZE);
    if (!total) {
      box.innerHTML = '<div class="wecom-empty">还没有入库记录</div>';
      if (pager) pager.hidden = true;
      return;
    }
    box.innerHTML = slice.map(function (g) {
      var aid = ingestAid(g);
      var files = g.files || [];
      var n = files.length;
      var head = '<summary class="ingest-group-hd">' + IG_CHEV + '<b>' + esc(aid) + '</b>' +
        (g.personName ? '<em>' + esc(g.personName) + '</em>' : '') +
        '<em>' + n + ' 份</em>' +
        (g.appCount ? '<em>申报书 ' + g.appCount + ' 版</em>' : '') +
        (g.opinionCount ? '<em>意见 ' + g.opinionCount + ' 条</em>' : '') +
        (g.latestAt ? '<em>最新 ' + esc(g.latestAt) + '</em>' : '') +
        (g.failCount ? '<em>失败 ' + g.failCount + '</em>' : '') + '</summary>';
      var rows = files.map(function (f) {
        if (f.source === 'app_edit') {
          return '<details class="ingest-app" data-ig-app="' + escAttr(ingestAppKey(g, f)) + '">' +
            '<summary class="ingest-app-hd">' + IG_CHEV + renderIngestFileCells(f) + '</summary>' +
            renderIngestOpinions(f) +
            '</details>';
        }
        return '<div class="ingest-file">' + renderIngestFileCells(f) + '</div>';
      }).join('');
      return '<details class="ingest-group" data-ig-id="' + escAttr(aid) + '">' + head + rows + '</details>';
    }).join('');
    restoreIngestOpen();
    if (pager) {
      pager.hidden = total <= INGEST_PAGE_SIZE;
      if (info) info.textContent = '第 ' + ingestPage + ' / ' + pages + ' 页 · 共 ' + total + ' 个编号';
      if (prev) prev.disabled = ingestPage <= 1;
      if (next) next.disabled = ingestPage >= pages;
    }
  }
  function renderIngestOpinions(f) {
    f = f || {};
    var ops = f.opinions || [];
    var clauses = f.clauses || [];
    var html = '<div class="ingest-ops">';
    if (f.appName) html += '<div class="ingest-op-app">来源申报书：' + esc(f.appName) + '</div>';
    if (!ops.length && !clauses.length) {
      html += '<div class="ingest-op-miss">未找到对应修改意见（任务已删或未保留原文）</div></div>';
      return html;
    }
    ops.forEach(function (op) {
      html += '<div class="ingest-op">' +
        '<span class="kind">修改意见</span>' +
        '<span class="fn" title="' + esc(op.name || '') + '">' + esc(op.name || '未命名') + '</span>' +
        (op.snippet ? '<span class="meta">' + esc(op.snippet) + '</span>' : '') +
        '</div>';
    });
    if (clauses.length) {
      html += '<ul class="ingest-op-clauses">' + clauses.slice(0, 8).map(function (c) {
        return '<li>' + esc(c.clause || c.opinion || '') + '</li>';
      }).join('') + (clauses.length > 8 ? '<li>共 ' + clauses.length + ' 条</li>' : '') + '</ul>';
    }
    return html + '</div>';
  }
  function paintIngestBoard(d, opts) {
    d = d || {};
    opts = opts || {};
    rememberIngestOpen();
    ingestCache.groups = d.groups || [];
    ingestCache.stats = d;
    if (!opts.keepPage) ingestPage = 1;
    paintIngestStats(d);
    renderIngestPage();
  }
  function loadIngestBoard(quiet) {
    var box = document.getElementById('ingestBoard');
    rememberIngestOpen();
    if (box && !quiet) box.innerHTML = '<div class="wecom-empty">加载入库记录…</div>';
    fetch('/api/admin/talent-ingest' + ingestQuery()).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (x) {
      if (!x.ok) throw new Error((x.j && (x.j.detail || x.j.message)) || '加载失败');
      paintIngestBoard(x.j, { keepPage: !!quiet });
    }).catch(function (e) {
      if (box) box.innerHTML = '<div class="wecom-empty">' + esc(e.message || '入库库不可用') + '</div>';
    });
    if (!ingestBound) {
      ingestBound = true;
      if (box) {
        box.addEventListener('toggle', function (e) {
          var t = e.target;
          if (!t || !t.getAttribute) return;
          if (t.classList.contains('ingest-group')) {
            var id = t.getAttribute('data-ig-id') || '';
            if (!id) return;
            if (t.open) ingestOpen.groups[id] = true;
            else delete ingestOpen.groups[id];
          } else if (t.classList.contains('ingest-app')) {
            var k = t.getAttribute('data-ig-app') || '';
            if (!k) return;
            if (t.open) ingestOpen.apps[k] = true;
            else delete ingestOpen.apps[k];
          }
        }, true);
      }
      var qEl = document.getElementById('igQ');
      var go = function () { ingestPage = 1; loadIngestBoard(true); };
      if (qEl) qEl.addEventListener('input', function () {
        clearTimeout(ingestTimer);
        ingestTimer = setTimeout(go, 280);
      });
      ['igSource', 'igOk'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', go);
      });
      var btn = document.getElementById('igSearch');
      if (btn) btn.addEventListener('click', go);
      var rel = document.getElementById('igReload');
      if (rel) rel.addEventListener('click', function () { loadIngestBoard(false); });
      var prev = document.getElementById('igPagePrev');
      var next = document.getElementById('igPageNext');
      if (prev) prev.addEventListener('click', function () {
        if (ingestPage <= 1) return;
        ingestPage--;
        renderIngestPage();
      });
      if (next) next.addEventListener('click', function () {
        var pages = Math.max(1, Math.ceil((ingestCache.groups || []).length / INGEST_PAGE_SIZE));
        if (ingestPage >= pages) return;
        ingestPage++;
        renderIngestPage();
      });
    }
  }
  window.loadIngestBoard = loadIngestBoard;

  function closeIgPreview() {
    var modal = document.getElementById('igPreviewModal');
    if (modal) modal.hidden = true;
  }
  function igIsMasked(s) {
    s = String(s == null ? '' : s).trim();
    if (!s) return false;
    if (s === '***' || s === '***@***.***') return true;
    if (s.indexOf('*') < 0) return false;
    var stars = (s.match(/\*/g) || []).length;
    return stars >= 3 || (stars >= 2 && s.indexOf('@') >= 0);
  }
  function igMaskFits(masked, plain) {
    masked = String(masked || '');
    plain = String(plain || '');
    if (!masked || !plain || igIsMasked(plain)) return false;
    if (masked === '***' || masked === '***@***.***') return true;
    var parts = masked.split(/\*+/).filter(Boolean).map(function (x) {
      return x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    });
    if (!parts.length) return true;
    try { return new RegExp(parts.join('.*'), 'i').test(plain); } catch (e) { return false; }
  }
  function igUnmaskObj(obj) {
    if (!obj || typeof obj !== 'object') return obj;
    if (Array.isArray(obj)) {
      obj.forEach(igUnmaskObj);
      return obj;
    }
    var alias = { '电子邮件': ['电子邮箱'], '电子邮箱': ['电子邮件'], '手机号': ['手机', '联系电话'], '证件号码': ['护照号码'] };
    Object.keys(obj).forEach(function (k) {
      var v = obj[k];
      if (v && typeof v === 'object') { igUnmaskObj(v); return; }
      if (!igIsMasked(String(v == null ? '' : v))) return;
      var cands = (alias[k] || []).map(function (a) { return obj[a]; });
      if (k === '电子邮件' || k === '电子邮箱') {
        Object.keys(obj).forEach(function (ok) {
          var ov = obj[ok];
          if (typeof ov === 'string' && ov.indexOf('@') > 0) cands.push(ov);
        });
      }
      for (var i = 0; i < cands.length; i++) {
        var s = String(cands[i] == null ? '' : cands[i]).trim();
        if (s && !igIsMasked(s) && igMaskFits(String(v), s)) { obj[k] = s; return; }
      }
    });
    return obj;
  }
  function igPreviewText(s) {
    s = String(s == null ? '' : s);
    if (!s) return '';
    return esc(s).replace(/\r\n/g, '\n').replace(/\n/g, '<br>');
  }
  function igSkipKey(k) {
    k = String(k || '');
    return !k || k.charAt(0) === '_' || k === '_mode_normalized';
  }
  function igPreviewKvDeep(obj) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return '';
    var cells = [];
    Object.keys(obj).forEach(function (k) {
      if (igSkipKey(k)) return;
      var v = obj[k];
      if (v && typeof v === 'object') return;
      var s = String(v == null ? '' : v);
      if (!s) return;
      cells.push('<span>' + esc(k) + '</span><span>' + igPreviewText(s) + '</span>');
    });
    return cells.length ? '<div class="ig-preview-kv">' + cells.join('') + '</div>' : '';
  }
  function igPreviewAny(v, title, depth) {
    depth = depth || 0;
    if (v == null || v === '') return '';
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      var t = igPreviewText(v);
      return t ? '<div class="ig-preview-plain">' + t + '</div>' : '';
    }
    if (Array.isArray(v)) {
      if (!v.length) return '';
      var allObj = v.every(function (x) { return x && typeof x === 'object' && !Array.isArray(x); });
      if (allObj) {
        return v.map(function (item, i) {
          return '<div class="ig-preview-cardlet"><div class="ig-preview-sub">' + esc(title || '条目') + ' ' + (i + 1) + '</div>' +
            igPreviewAny(item, title, depth + 1) + '</div>';
        }).join('');
      }
      return '<ul class="ig-preview-list">' + v.map(function (x) {
        return '<li>' + (x && typeof x === 'object' ? igPreviewAny(x, '', depth + 1) : igPreviewText(x)) + '</li>';
      }).join('') + '</ul>';
    }
    if (typeof v === 'object') {
      var keys = Object.keys(v).filter(function (k) { return !igSkipKey(k); });
      var nested = keys.filter(function (k) { var x = v[k]; return x && typeof x === 'object' && !(Array.isArray(x) && !x.length); });
      var html = igPreviewKvDeep(v);
      nested.forEach(function (k) {
        var x = v[k];
        if (x == null || x === '' || (Array.isArray(x) && !x.length)) return;
        html += '<div class="ig-preview-sec"><h4>' + esc(k) + '</h4>' + igPreviewAny(x, k, depth + 1) + '</div>';
      });
      return html;
    }
    return '';
  }
  function igPreviewOpinions(pack) {
    pack = pack || {};
    var ops = pack.opinions || [];
    var clauses = pack.clauses || [];
    if (!ops.length && !clauses.length && !pack.appName) return '';
    var h = '<div class="ig-preview-ops"><h4>对应修改意见</h4>';
    if (pack.appName) h += '<div class="ig-preview-sub">来源申报书 ' + esc(pack.appName) + '</div>';
    ops.forEach(function (op) {
      h += '<div class="ig-preview-cardlet"><div class="ig-preview-sub">' + esc(op.name || '修改意见') + '</div>' +
        (op.snippet ? '<div class="ig-preview-plain">' + igPreviewText(op.snippet) + '</div>' : '') +
        '</div>';
    });
    if (clauses.length) {
      h += '<ul class="ig-preview-list">' + clauses.map(function (c) {
        var t = c.clause || '';
        if (c.opinion && c.opinion !== t) t = (t ? t + '：' : '') + c.opinion;
        return '<li>' + esc(t) + '</li>';
      }).join('') + '</ul>';
    }
    if (!ops.length && !clauses.length) h += '<div class="ig-preview-plain">未找到对应修改意见</div>';
    return h + '</div>';
  }
  function paintIgPreview(item, exp, taskOp) {
    item = item || {};
    try { exp = JSON.parse(JSON.stringify(exp || {})); } catch (e) { exp = exp || {}; }
    igUnmaskObj(exp);
    var talent = exp.talent && typeof exp.talent === 'object' ? exp.talent : exp;
    var payload = talent.payload && typeof talent.payload === 'object' ? talent.payload : {};
    var basic0 = payload['申报人基本信息'] && typeof payload['申报人基本信息'] === 'object' ? payload['申报人基本信息'] : {};
    if (igIsMasked(String(talent.id_number_masked || '')) && basic0['证件号码'] && !igIsMasked(basic0['证件号码'])) talent.id_number_masked = basic0['证件号码'];
    if (igIsMasked(String(talent.email_masked || ''))) {
      var em0 = basic0['电子邮箱'] || basic0['电子邮件'] || '';
      if (em0 && !igIsMasked(em0)) talent.email_masked = em0;
    }
    if (igIsMasked(String(talent.phone_masked || '')) && basic0['手机号'] && !igIsMasked(basic0['手机号'])) talent.phone_masked = basic0['手机号'];
    var last = talent.last_application || payload.last_application || {};
    var title = document.getElementById('igPreviewTitle');
    var meta = document.getElementById('igPreviewMeta');
    var body = document.getElementById('igPreviewBody');
    var ver = item.version || exp.version || talent.version || '';
    var aid = item.attachId || exp.attach_id || talent.attach_id || '';
    var name = item.personName || talent.name || '';
    if (title) title.textContent = '申报书 ' + (ver || '预览') + (aid ? ' · ' + aid : '');
    if (meta) {
      meta.textContent = [name, talent.mode || item.mode || '', talent.source_year || '', item.createdAt || ''].filter(Boolean).join(' · ');
    }
    var bits = [];
    var opHtml = igPreviewOpinions(taskOp || {});
    if (opHtml) bits.push(opHtml);
    if (talent.profile_summary) {
      bits.push('<div class="ig-preview-sec"><h4>简介</h4><div class="ig-preview-plain">' + igPreviewText(talent.profile_summary) + '</div></div>');
    }
    Object.keys(payload).forEach(function (k) {
      if (igSkipKey(k) || k === 'last_application') return;
      var block = igPreviewAny(payload[k], k);
      if (block) bits.push('<div class="ig-preview-sec"><h4>' + esc(k) + '</h4>' + block + '</div>');
    });
    if (last && typeof last === 'object' && Object.keys(last).length) {
      bits.push('<div class="ig-preview-sec"><h4>上一版申报</h4>' + igPreviewAny(last, '上一版申报') + '</div>');
    }
    try {
      bits.push('<details class="ig-preview-raw"><summary>完整 JSON</summary><pre class="ig-preview-json">' +
        esc(JSON.stringify(exp, null, 2)) + '</pre></details>');
    } catch (e) {}
    if (body) body.innerHTML = bits.join('') || '没有可预览的内容';
  }
  function openIgPreview(fid) {
    var modal = document.getElementById('igPreviewModal');
    var body = document.getElementById('igPreviewBody');
    var title = document.getElementById('igPreviewTitle');
    var meta = document.getElementById('igPreviewMeta');
    if (!modal) return;
    modal.hidden = false;
    if (title) title.textContent = '申报书预览';
    if (meta) meta.textContent = '';
    if (body) body.textContent = '加载中…';
    fetch('/api/talent-files/' + encodeURIComponent(fid)).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (x) {
      if (!x.ok) throw new Error((x.j && (x.j.detail || x.j.message)) || '加载失败');
      paintIgPreview(x.j.item || {}, x.j.export || {}, x.j.taskOpinions || {});
    }).catch(function (e) {
      if (body) body.textContent = e.message || '预览失败';
    });
  }
  if (!window._igPreviewBound) {
    window._igPreviewBound = true;
    document.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('[data-ig-preview]');
      if (btn) {
        e.preventDefault();
        e.stopPropagation();
        openIgPreview(btn.getAttribute('data-ig-preview'));
        return;
      }
      if (e.target && e.target.id === 'igPreviewClose') {
        closeIgPreview();
        return;
      }
      var modal = document.getElementById('igPreviewModal');
      if (modal && !modal.hidden && e.target === modal) closeIgPreview();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeIgPreview();
    });
  }

  var ingestTalentBound = false;
  function ingestTalentMsg(text, ok) {
    var el = document.getElementById('ingestTalentMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'wecom-watch-panel-msg' + (text ? (ok ? ' ok' : ' bad') : '');
    if (text && ok) setTimeout(function () {
      if (el.textContent === text) { el.textContent = ''; el.className = 'wecom-watch-panel-msg'; }
    }, 4000);
  }
  function paintIngestTalent(cfg, st) {
    cfg = cfg || {};
    st = st || {};
    var on = document.getElementById('igTalentUpload');
    var maxEl = document.getElementById('igTalentUploadMax');
    if (on) on.checked = cfg.talentUploadEnabled !== false;
    if (maxEl) maxEl.value = cfg.talentUploadMax != null ? cfg.talentUploadMax : 10;
    var box = document.getElementById('ingestTalentStatus');
    if (!box) return;
    var bits = [];
    bits.push(cfg.talentUploadEnabled !== false ? '入库已开启' : '入库已关闭');
    bits.push('每会话最多 ' + (cfg.talentUploadMax != null ? cfg.talentUploadMax : 10) + ' 份');
    if (st.enabled === false) bits.push('会话扫描未开，入库要等扫描跑起来');
    else if (st.lastNote) bits.push(st.lastNote);
    box.textContent = bits.join(' · ');
  }
  function loadIngestTalent() {
    var box = document.getElementById('ingestTalentStatus');
    if (box && !box.textContent) box.textContent = '加载配置…';
    fetch('/api/wecom/watch-config').then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (x) {
      if (!x.ok) throw new Error((x.j && x.j.detail) || '加载失败');
      paintIngestTalent((x.j && x.j.config) || {}, (x.j && x.j.status) || {});
    }).catch(function (e) {
      if (box) box.textContent = e.message || '加载失败';
    });
    if (!ingestTalentBound) {
      ingestTalentBound = true;
      var btn = document.getElementById('igTalentSave');
      if (btn) btn.addEventListener('click', function () {
        btn.disabled = true;
        ingestTalentMsg('保存中…', true);
        fetch('/api/wecom/watch-config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            watch: {
              talentUploadEnabled: !!(document.getElementById('igTalentUpload') || {}).checked,
              talentUploadMax: parseInt((document.getElementById('igTalentUploadMax') || {}).value, 10) || 10
            }
          })
        }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (x) {
            if (!x.ok) throw new Error((x.j && x.j.detail) || '保存失败');
            paintIngestTalent((x.j && x.j.config) || {}, (x.j && x.j.status) || {});
            ingestTalentMsg('已保存', true);
          }).catch(function (e) {
            ingestTalentMsg(e.message || '保存失败', false);
          }).finally(function () { btn.disabled = false; });
      });
    }
  }
  global.loadIngestBoard = loadIngestBoard;
  global.loadIngestTalent = loadIngestTalent;
})(window);
