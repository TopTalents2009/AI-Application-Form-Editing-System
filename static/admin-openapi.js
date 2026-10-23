/* 管理后台：开放 API */
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
  var apiKeysCache = [];
  var apiLogPage = 1;
  var API_LOG_PAGE = 50;
  var apiKeyMsg = document.getElementById('apiKeyMsg');
  function sayApi(ok, t) {
    if (!apiKeyMsg) return;
    apiKeyMsg.className = 'msg ' + (ok ? 'ok' : 'bad');
    apiKeyMsg.textContent = t;
    setTimeout(function () { if (apiKeyMsg.textContent === t) apiKeyMsg.textContent = ''; }, 5000);
  }
  function apiHttp(path, opt) {
    return fetch(path, opt || {}).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, data: j }; });
    });
  }
  function fillApiOwnerSelect(users) {
    var html = '<option value="">自动（api:名称）</option>';
    (users || []).forEach(function (u) {
      html += '<option value="' + esc(u.username) + '">' + esc(u.realName || u.username) + '（' + esc(u.username) + '）</option>';
    });
    ['apiKeyOwner', 'apiKeyEditOwner'].forEach(function (id) {
      var sel = document.getElementById(id);
      if (!sel) return;
      var cur = sel.value;
      sel.innerHTML = html;
      if (cur) sel.value = cur;
    });
  }
  function mdInline(s) {
    s = esc(s);
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    return s;
  }
  function splitRow(line) {
    var t = String(line || '').trim();
    if (t.charAt(0) === '|') t = t.slice(1);
    if (t.charAt(t.length - 1) === '|') t = t.slice(0, -1);
    return t.split('|').map(function (c) { return c.trim(); });
  }
  function isSepRow(line) {
    return /^\s*\|?\s*:?-{3,}/.test(line || '');
  }
  function mdToHtml(src) {
    src = String(src || '').replace(/\r\n/g, '\n');
    var lines = src.split('\n');
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/^```/.test(line)) {
        var buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        if (i < lines.length) i++;
        out.push('<pre><code>' + esc(buf.join('\n')) + '</code></pre>');
        continue;
      }
      if (/^\s*\|/.test(line) && i + 1 < lines.length && isSepRow(lines[i + 1])) {
        var heads = splitRow(line);
        i += 2;
        var rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i]) && !isSepRow(lines[i])) {
          rows.push(splitRow(lines[i]));
          i++;
        }
        var h = '<table><thead><tr>' + heads.map(function (c) { return '<th>' + mdInline(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
        rows.forEach(function (r) {
          h += '<tr>' + r.map(function (c) { return '<td>' + mdInline(c) + '</td>'; }).join('') + '</tr>';
        });
        out.push(h + '</tbody></table>');
        continue;
      }
      if (/^\s*---+\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
      var hm = /^(#{1,3})\s+(.+)$/.exec(line);
      if (hm) {
        out.push('<h' + hm[1].length + '>' + mdInline(hm[2]) + '</h' + hm[1].length + '>');
        i++; continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        var items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push('<li>' + mdInline(lines[i].replace(/^\s*[-*]\s+/, '')) + '</li>');
          i++;
        }
        out.push('<ul>' + items.join('') + '</ul>');
        continue;
      }
      if (!String(line).trim()) { i++; continue; }
      var para = [line];
      i++;
      while (i < lines.length && String(lines[i]).trim() && !/^(#{1,3}\s|```|\s*\||\s*---+\s*$|\s*[-*]\s+)/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      out.push('<p>' + mdInline(para.join(' ')) + '</p>');
    }
    return out.join('');
  }
  var apiDocMd = '';
  function paintApiDocs(md) {
    var el = document.getElementById('apiDocs');
    if (!el) return;
    var live = location.origin;
    var text = String(md || '').replace(/http:\/\/127\.0\.0\.1:3777/g, live);
    el.innerHTML =
      '<div class="kv"><span class="k">当前 Base URL</span><span class="v"><code>' + esc(live) + '/api/v1</code></span></div>' +
      mdToHtml(text);
  }
  function renderApiDocs() {
    var el = document.getElementById('apiDocs');
    if (!el) return;
    if (apiDocMd) { paintApiDocs(apiDocMd); return; }
    fetch('/api/admin/api-docs').then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.data || !res.data.markdown) {
          el.innerHTML = '<p>' + esc((res.data && res.data.detail) || '文档加载失败') + '</p>';
          return;
        }
        apiDocMd = res.data.markdown;
        paintApiDocs(apiDocMd);
      }).catch(function (e) {
        el.innerHTML = '<p>' + esc(e.message || '文档加载失败') + '</p>';
      });
  }
  function showApiSecret(secret) {
    var modal = document.getElementById('apiKeyModal');
    var text = document.getElementById('apiKeySecretText');
    if (text) text.textContent = secret || '';
    if (modal) modal.hidden = false;
  }
  function hideApiSecret() {
    var modal = document.getElementById('apiKeyModal');
    if (modal) modal.hidden = true;
  }
  function sayApiEdit(ok, t) {
    var el = document.getElementById('apiKeyEditMsg');
    if (!el) return;
    el.className = 'msg ' + (ok ? 'ok' : 'bad');
    el.textContent = t || '';
  }
  function hideApiKeyEdit() {
    var modal = document.getElementById('apiKeyEditModal');
    if (modal) modal.hidden = true;
    sayApiEdit(true, '');
  }
  function showApiKeyEdit(k) {
    k = k || {};
    var modal = document.getElementById('apiKeyEditModal');
    var idEl = document.getElementById('apiKeyEditId');
    var nameEl = document.getElementById('apiKeyEditName');
    var noteEl = document.getElementById('apiKeyEditNote');
    var ownerEl = document.getElementById('apiKeyEditOwner');
    var expEl = document.getElementById('apiKeyEditExpire');
    if (idEl) idEl.value = k.id || '';
    if (nameEl) nameEl.value = k.name || '';
    if (noteEl) noteEl.value = k.note || '';
    if (ownerEl) {
      var owner = k.ownerUsername || '';
      var has = false;
      for (var i = 0; i < ownerEl.options.length; i++) {
        if (ownerEl.options[i].value === owner) has = true;
      }
      if (owner && !has) {
        var opt = document.createElement('option');
        opt.value = owner;
        opt.textContent = owner;
        ownerEl.appendChild(opt);
      }
      ownerEl.value = owner;
    }
    if (expEl) expEl.value = (k.expireAt || '').slice(0, 10);
    sayApiEdit(true, '');
    if (modal) modal.hidden = false;
    if (nameEl) nameEl.focus();
  }
  function renderApiKeys(keys) {
    apiKeysCache = keys || [];
    var tbody = document.getElementById('apiKeyBody');
    var logSel = document.getElementById('apiLogKey');
    var logCur = logSel ? logSel.value : '';
    if (logSel) {
      var oh = '<option value="">全部密钥</option>';
      apiKeysCache.forEach(function (k) {
        oh += '<option value="' + k.id + '">' + esc(k.name) + '（' + esc(k.display) + '）</option>';
      });
      logSel.innerHTML = oh;
      logSel.value = logCur;
    }
    if (!tbody) return;
    if (!apiKeysCache.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="files-muted">还没有密钥。填写名称后点「生成密钥」。</td></tr>';
      return;
    }
    tbody.innerHTML = '';
    apiKeysCache.forEach(function (k) {
      var tr = document.createElement('tr');
      var st = k.status === 'active' ? '<span class="badge st-done">启用</span>' : '<span class="badge st-failed">停用</span>';
      var toggle = k.status === 'active' ? '停用' : '启用';
      var toggleTo = k.status === 'active' ? 'disabled' : 'active';
      var keyCell = k.secret
        ? '<code class="api-secret" title="' + esc(k.secret) + '">' + esc(k.secret) + '</code>' +
          '<button type="button" class="mini" data-api-act="copy" data-id="' + k.id + '" style="margin-left:6px">复制</button>'
        : '<span class="mono">' + esc(k.display) + '</span><div style="font-size:11.5px;color:#98a2b6;margin-top:2px">旧密钥未保存明文，请删除后重新生成</div>';
      tr.innerHTML =
        '<td class="td-name" title="' + esc(k.note || k.name) + '">' + esc(k.name) +
          (k.note ? '<div style="font-size:11.5px;color:#98a2b6;margin-top:2px">' + esc(k.note) + '</div>' : '') + '</td>' +
        '<td class="td-name">' + keyCell + '</td>' +
        '<td>' + esc(k.ownerUsername || 'api:' + k.name) + '</td>' +
        '<td>' + st + '</td>' +
        '<td>' + (k.callCount || 0) + '</td>' +
        '<td>' + esc(k.lastUsedAt || '—') + '</td>' +
        '<td>' + esc((k.expireAt || '').slice(0, 10) || '永久') + '</td>' +
        '<td class="ops">' +
          '<button type="button" class="mini" data-api-act="edit" data-id="' + k.id + '">编辑</button>' +
          '<button type="button" class="mini" data-api-act="toggle" data-id="' + k.id + '" data-to="' + toggleTo + '">' + toggle + '</button>' +
          '<button type="button" class="mini" data-api-act="del" data-id="' + k.id + '">删除</button>' +
        '</td>';
      tbody.appendChild(tr);
    });
  }
  function httpBadge(code) {
    var n = parseInt(code, 10) || 0;
    var cls = n >= 500 ? 'st-5xx' : (n >= 400 ? 'st-4xx' : 'st-2xx');
    return '<span class="badge ' + cls + '">' + n + '</span>';
  }
  function renderApiLogs(j) {
    var tbody = document.getElementById('apiLogBody');
    var items = (j && j.items) || [];
    var st = (j && j.stats) || {};
    var keysEl = document.getElementById('apiStKeys');
    var todayEl = document.getElementById('apiStToday');
    var errEl = document.getElementById('apiStErr');
    if (keysEl) keysEl.textContent = st.keysActive != null ? st.keysActive : '—';
    if (todayEl) todayEl.textContent = st.today != null ? st.today : '—';
    if (errEl) errEl.textContent = st.errorsToday != null ? st.errorsToday : '—';
    setApiReqBadge(st.pendingRequests);
    if (!tbody) return;
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="files-muted">暂无调用记录</td></tr>';
    } else {
      tbody.innerHTML = items.map(function (it) {
        var tid = it.taskId
          ? '<a href="/t/' + encodeURIComponent(it.taskId) + '" target="_blank" rel="noopener">' + esc(it.taskId) + '</a>'
          : '—';
        return '<tr>' +
          '<td>' + esc(it.createdAt) + '</td>' +
          '<td title="' + esc(it.keyPrefix) + '">' + esc(it.keyName || it.keyPrefix || '—') + '</td>' +
          '<td class="mono">' + esc(it.method) + '</td>' +
          '<td class="td-clip" title="' + esc(it.path) + '">' + esc(it.path) + '</td>' +
          '<td>' + httpBadge(it.statusCode) + '</td>' +
          '<td>' + (it.durationMs || 0) + 'ms</td>' +
          '<td class="mono">' + esc(it.ip || '—') + '</td>' +
          '<td class="td-clip">' + tid + '</td>' +
          '<td class="td-clip" title="' + esc(it.error) + '">' + esc(it.error || '') + '</td>' +
        '</tr>';
      }).join('');
    }
    var total = (j && j.total) || 0;
    var pages = Math.max(1, Math.ceil(total / API_LOG_PAGE));
    if (apiLogPage > pages) apiLogPage = pages;
    var pager = document.getElementById('apiLogPager');
    var info = document.getElementById('apiLogInfo');
    var prev = document.getElementById('apiLogPrev');
    var next = document.getElementById('apiLogNext');
    if (pager) pager.hidden = pages <= 1 && total <= API_LOG_PAGE;
    if (info) info.textContent = '第 ' + apiLogPage + ' / ' + pages + ' 页 · 共 ' + total + ' 条';
    if (prev) prev.disabled = apiLogPage <= 1;
    if (next) next.disabled = apiLogPage >= pages;
  }
  function loadApiRequests() {
    return apiHttp('/api/admin/api-requests').then(function (res) {
      var box = document.getElementById('apiReqList');
      if (!res.ok) {
        if (box) box.innerHTML = '<div class="files-muted">' + esc((res.data && res.data.detail) || '加载失败') + '</div>';
        return;
      }
      setApiReqBadge(res.data.pending);
      var items = res.data.requests || [];
      if (!box) return;
      if (!items.length) {
        box.innerHTML = '<div class="files-muted">暂无用户申请。用户可在主界面用户名下拉菜单中提交。</div>';
        return;
      }
      box.innerHTML = items.map(function (it) {
        var st = it.status === 'approved' ? '<span class="badge st-done">已通过</span>'
          : (it.status === 'rejected' ? '<span class="badge st-failed">已拒绝</span>'
            : (it.status === 'revoked' ? '<span class="badge st-failed">密钥已停用</span>' : '<span class="badge st-planned">待审核</span>'));
        var ops = it.status === 'pending'
          ? '<button type="button" class="mini" data-req-act="approve" data-id="' + it.id + '">通过</button>' +
            '<button type="button" class="mini" data-req-act="reject" data-id="' + it.id + '">拒绝</button>'
          : '';
        var extra = it.rejectReason ? '<div style="font-size:12px;color:#c5221f;margin-top:4px">拒绝：' + esc(it.rejectReason) + '</div>' : '';
        return '<div style="display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px dashed #eef1f6">' +
          '<div style="flex:1;min-width:0"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px">' +
          st + '<b>' + esc(it.realName || it.username) + '</b><span style="font-size:12px;color:#8a93a6">' +
          esc(it.department || '') + (it.username ? ' · ' + esc(it.username) : '') + ' · ' + esc(it.createdAt) + '</span></div>' +
          '<div style="font-size:13px;line-height:1.6;white-space:pre-wrap">' + esc(it.purpose) + '</div>' + extra + '</div>' +
          '<div class="ops">' + ops + '</div></div>';
      }).join('');
    }).catch(function (e) {
      var box = document.getElementById('apiReqList');
      if (box) box.innerHTML = '<div class="files-muted">' + esc(e.message || '加载失败') + '</div>';
    });
  }
  function setApiReqBadge(n) {
    var b = document.getElementById('apiReqBadge');
    if (!b) return;
    n = parseInt(n, 10) || 0;
    if (n > 0) { b.hidden = false; b.textContent = n > 99 ? '99+' : String(n); }
    else { b.hidden = true; b.textContent = ''; }
  }
  function refreshApiReqBadge() {
    fetch('/api/admin/api-requests').then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.ok) setApiReqBadge(j.pending);
    }).catch(function () {});
  }
  function loadApiKeys() {
    return apiHttp('/api/admin/apikeys').then(function (res) {
      if (!res.ok) { sayApi(false, (res.data && res.data.detail) || '密钥列表加载失败'); return; }
      renderApiKeys(res.data.keys || []);
    }).catch(function (e) { sayApi(false, '密钥列表加载失败：' + e.message); });
  }
  function loadApiLogs() {
    var keyId = (document.getElementById('apiLogKey') || {}).value || '';
    var status = (document.getElementById('apiLogStatus') || {}).value || '';
    var q = (document.getElementById('apiLogQ') || {}).value || '';
    var since = (document.getElementById('apiLogSince') || {}).value || '';
    var until = (document.getElementById('apiLogUntil') || {}).value || '';
    var qs = 'limit=' + API_LOG_PAGE + '&offset=' + ((apiLogPage - 1) * API_LOG_PAGE);
    if (keyId) qs += '&keyId=' + encodeURIComponent(keyId);
    if (status) qs += '&status=' + encodeURIComponent(status);
    if (q) qs += '&q=' + encodeURIComponent(q);
    if (since) qs += '&since=' + encodeURIComponent(since);
    if (until) qs += '&until=' + encodeURIComponent(until);
    return apiHttp('/api/admin/api-logs?' + qs).then(function (res) {
      if (!res.ok) {
        document.getElementById('apiLogBody').innerHTML = '<tr><td colspan="9">' + esc((res.data && res.data.detail) || '加载失败') + '</td></tr>';
        return;
      }
      renderApiLogs(res.data);
    }).catch(function (e) {
      var tbody = document.getElementById('apiLogBody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="9">' + esc(e.message || '加载失败') + '</td></tr>';
    });
  }
  function loadOpenApi(quiet) {
    renderApiDocs();
    fetch('/api/admin/users').then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.ok) fillApiOwnerSelect(j.users || []);
    }).catch(function () {});
    loadApiKeys();
    loadApiRequests();
    if (!quiet) apiLogPage = apiLogPage || 1;
    loadApiLogs();
  }
  document.getElementById('apiKeyCreate').addEventListener('click', function () {
    var name = (document.getElementById('apiKeyName').value || '').trim();
    if (!name) { sayApi(false, '请填写密钥名称'); return; }
    var btn = document.getElementById('apiKeyCreate');
    btn.disabled = true;
    apiHttp('/api/admin/apikeys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        note: (document.getElementById('apiKeyNote').value || '').trim(),
        ownerUsername: document.getElementById('apiKeyOwner').value || '',
        expireAt: document.getElementById('apiKeyExpire').value || ''
      })
    }).then(function (res) {
      if (res.ok && res.data.ok) {
        sayApi(true, '已生成密钥');
        document.getElementById('apiKeyName').value = '';
        document.getElementById('apiKeyNote').value = '';
        document.getElementById('apiKeyExpire').value = '';
        showApiSecret((res.data.key || {}).secret || '');
        loadApiKeys();
      } else sayApi(false, (res.data && res.data.detail) || '创建失败');
    }).catch(function (e) { sayApi(false, e.message); })
    .finally(function () { btn.disabled = false; });
  });
  document.getElementById('apiKeyBody').addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('button[data-api-act]') : null;
    if (!btn) return;
    var id = btn.getAttribute('data-id');
    var act = btn.getAttribute('data-api-act');
    if (act === 'toggle') {
      var to = btn.getAttribute('data-to');
      apiHttp('/api/admin/apikeys/' + id, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: to })
      }).then(function (res) {
        if (res.ok && res.data.ok) { sayApi(true, to === 'active' ? '已启用' : '已停用'); loadApiKeys(); loadApiLogs(); }
        else sayApi(false, (res.data && res.data.detail) || '更新失败');
      });
    } else if (act === 'edit') {
      var found = null;
      (apiKeysCache || []).forEach(function (k) {
        if (String(k.id) === String(id)) found = k;
      });
      if (!found) { sayApi(false, '密钥不存在'); return; }
      showApiKeyEdit(found);
    } else if (act === 'copy') {
      var secret = '';
      (apiKeysCache || []).forEach(function (k) {
        if (String(k.id) === String(id)) secret = k.secret || '';
      });
      if (!secret) { sayApi(false, '没有可复制的明文'); return; }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(secret).then(function () { sayApi(true, '已复制 API Key'); }).catch(function () {});
      }
    } else if (act === 'del') {
      if (!confirm('删除该密钥后，使用它的调用将立即失败。确定删除？')) return;
      apiHttp('/api/admin/apikeys/' + id, { method: 'DELETE' }).then(function (res) {
        if (res.ok && res.data.ok) { sayApi(true, '已删除'); loadApiKeys(); }
        else sayApi(false, (res.data && res.data.detail) || '删除失败');
      });
    }
  });
  document.getElementById('apiLogReload').addEventListener('click', function () { apiLogPage = 1; loadApiLogs(); });
  document.getElementById('apiLogKey').addEventListener('change', function () { apiLogPage = 1; loadApiLogs(); });
  document.getElementById('apiLogStatus').addEventListener('change', function () { apiLogPage = 1; loadApiLogs(); });
  document.getElementById('apiLogQ').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { apiLogPage = 1; loadApiLogs(); }
  });
  document.getElementById('apiLogPrev').addEventListener('click', function () { if (apiLogPage > 1) { apiLogPage--; loadApiLogs(); } });
  document.getElementById('apiLogNext').addEventListener('click', function () { apiLogPage++; loadApiLogs(); });
  document.getElementById('apiLogPurge').addEventListener('click', function () {
    if (!confirm('清除 30 天前的调用历史？此操作不可恢复。')) return;
    apiHttp('/api/admin/api-logs?days=30', { method: 'DELETE' }).then(function (res) {
      if (res.ok && res.data.ok) { sayApi(true, '已删除 ' + (res.data.deleted || 0) + ' 条'); apiLogPage = 1; loadApiLogs(); }
      else sayApi(false, (res.data && res.data.detail) || '清理失败');
    });
  });
  document.getElementById('apiKeyCopy').addEventListener('click', function () {
    var t = (document.getElementById('apiKeySecretText') || {}).textContent || '';
    if (!t) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(function () { sayApi(true, '已复制到剪贴板'); }).catch(function () {});
    }
  });
  var apiDocDl = document.getElementById('apiDocDownload');
  if (apiDocDl) apiDocDl.addEventListener('click', function (e) {
    e.preventDefault();
    fetch('/api/openapi/guide').then(function (r) {
      if (!r.ok) throw new Error('下载失败');
      return r.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = '申报书开放API文档.md';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
    }).catch(function (err) { sayApi(false, err.message || '下载失败'); });
  });
  document.getElementById('apiKeyModalClose').addEventListener('click', hideApiSecret);
  document.getElementById('apiKeyModal').addEventListener('click', function (e) {
    if (e.target === this) hideApiSecret();
  });
  document.getElementById('apiKeyEditCancel').addEventListener('click', hideApiKeyEdit);
  document.getElementById('apiKeyEditModal').addEventListener('click', function (e) {
    if (e.target === this) hideApiKeyEdit();
  });
  document.getElementById('apiKeyEditSave').addEventListener('click', function () {
    var id = (document.getElementById('apiKeyEditId') || {}).value || '';
    var name = ((document.getElementById('apiKeyEditName') || {}).value || '').trim();
    if (!id) { sayApiEdit(false, '缺少密钥编号'); return; }
    if (!name) { sayApiEdit(false, '请填写密钥名称'); return; }
    var btn = document.getElementById('apiKeyEditSave');
    btn.disabled = true;
    sayApiEdit(true, '保存中…');
    apiHttp('/api/admin/apikeys/' + encodeURIComponent(id), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        note: ((document.getElementById('apiKeyEditNote') || {}).value || '').trim(),
        ownerUsername: (document.getElementById('apiKeyEditOwner') || {}).value || '',
        expireAt: (document.getElementById('apiKeyEditExpire') || {}).value || ''
      })
    }).then(function (res) {
      if (res.ok && res.data.ok) {
        hideApiKeyEdit();
        sayApi(true, '已保存密钥');
        loadApiKeys();
      } else sayApiEdit(false, (res.data && res.data.detail) || '保存失败');
    }).catch(function (e) { sayApiEdit(false, e.message || '保存失败'); })
    .finally(function () { btn.disabled = false; });
  });
  document.getElementById('apiReqList').addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('button[data-req-act]') : null;
    if (!btn) return;
    var id = btn.getAttribute('data-id');
    var act = btn.getAttribute('data-req-act');
    var body = { action: act };
    if (act === 'reject') {
      var reason = window.prompt('拒绝原因（可留空）', '') || '';
      body.reason = reason;
    } else if (!confirm('通过后将生成 API Key，并在密钥列表中可见。确定通过？')) {
      return;
    }
    apiHttp('/api/admin/api-requests/' + id + '/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (res) {
      if (!res.ok) { sayApi(false, (res.data && res.data.detail) || '处理失败'); return; }
      sayApi(true, act === 'approve' ? '已通过并生成密钥' : '已拒绝');
      if (act === 'approve' && res.data.key && res.data.key.secret) showApiSecret(res.data.key.secret);
      loadApiRequests();
      loadApiKeys();
    });
  });
  global.fillApiOwnerSelect = fillApiOwnerSelect;
  global.loadOpenApi = loadOpenApi;
  global.refreshApiReqBadge = refreshApiReqBadge;
})(window);
