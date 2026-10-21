/* 管理后台：系统信息与告警 */
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
  function kv(k, v) { return '<div class="kv"><span class="k">' + k + '</span><span class="v">' + v + '</span></div>'; }
  function loadSystem() {
    fetch('/api/config').then(function (r) { return r.json(); }).then(function (cfg) {
      var ed = cfg.edit || {};
      var okCnt = ['grok', 'gemini', 'doubao'].filter(function (f) { return ed[f] && ed[f].hasKey; }).length;
      var mysql = ed.mysql || {};
      var portal = ed.portal || {};
      var wecom = ed.wecomChat || {};
      document.getElementById('sysBasic').innerHTML =
        kv('系统版本', 'v' + esc(cfg.version || '')) +
        kv('部署方式', '本地单机 · 端口 3777') +
        kv('模型接入', '<span class="' + (okCnt ? 'dot-ok' : 'dot-bad') + '">●</span> ' + okCnt + ' / 3 已配置') +
        kv('配置状态', cfg.configured ? '<span class="dot-ok">●</span> 正常' : '<span class="dot-bad">●</span> 未配置') +
        kv('数据库', mysql.configured ? '<span class="dot-ok">●</span> ' + esc((mysql.host || '') + '/' + (mysql.database || '')) : '<span class="dot-bad">●</span> 未配置') +
        kv('企微解析', wecom.configured ? '<span class="dot-ok">●</span> 已配置' : '<span class="dot-bad">●</span> 未配置') +
        kv('协同门户', portal.configured ? '<span class="dot-ok">●</span> 已配置' : '<span class="dot-bad">●</span> 未配置');
      var rows = '';
      ['grok', 'gemini', 'doubao'].forEach(function (f) {
        var m = ed[f] || {};
        rows += '<div class="model-row">' +
          '<span class="mn">' + esc(m.label || f) + '</span>' +
          '<span class="mid" title="' + esc(m.id || '') + '">' + esc(m.id || '') + '</span>' +
          '<span class="' + (m.hasKey ? 'dot-ok' : 'dot-bad') + '">● ' + (m.hasKey ? '密钥已配置' : '未配置密钥') + '</span>' +
        '</div>';
      });
      document.getElementById('sysModels').innerHTML = rows;
      renderDataCard();
    }).catch(function (e) {
      document.getElementById('sysBasic').innerHTML = '<div class="kv"><span class="k">加载失败</span><span class="v">' + esc(e.message || '') + '</span></div>';
    });
  }

  function renderDataCard() {
    var el = document.getElementById('sysData');
    if (!el) return;
    el.innerHTML =
      kv('用户数据库', '<span class="dot-ok">●</span> MySQL 已连接') +
      kv('账号总数', AdminStats.userCount + ' 人') +
      kv('任务存储', AdminStats.totals.tasks + ' 个任务') +
      kv('批次存储', AdminStats.totals.batches + ' 个批次');
  }

  var alertLogFilter = '';
  function alertKindLabel(k) {
    return ({ task_failed: '任务失败', feedback: '用户反馈', watch_pending: '值班待审', group_issue: '群内重要问题', test: '试发' }[k] || k || '');
  }
  function alertFormDirty() {
    var w = document.getElementById('alertWebhook');
    if (w && (w.value || '').trim()) return true;
    var sec = document.getElementById('sec-alert');
    var a = document.activeElement;
    return !!(sec && a && sec.contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName || ''));
  }
  function renderAlertLeaders(rows) {
    var box = document.getElementById('alertLeaders');
    if (!box) return;
    var list = (rows && rows.length) ? rows : [{ department: '*', name: '', userid: '' }];
    box.innerHTML = list.map(function (row) {
      return '<div class="leader-row">' +
        '<div class="f-row" style="margin:0"><label>部门</label><div class="inp"><input data-k="department" value="' + escAttr(row.department || '') + '" placeholder="如 申报部 或 *"></div></div>' +
        '<div class="f-row" style="margin:0"><label>姓名</label><div class="inp"><input data-k="name" value="' + escAttr(row.name || '') + '" placeholder="显示用"></div></div>' +
        '<div class="f-row" style="margin:0"><label>userid</label><div class="inp"><input data-k="userid" value="' + escAttr(row.userid || '') + '" placeholder="企微账号"></div></div>' +
        '<button type="button" class="mini alert-leader-del">删除</button>' +
        '</div>';
    }).join('');
  }
  var ISSUE_WATCH_DEFAULTS = ['低级错误','怎么回事','什么时候','？','到底','尽快','急','人呢？','今天能交/给我吗','涉嫌造假','重复','效率差'];
  function fillIssueWatch(d) {
    d = d || {};
    var en = document.getElementById('issueWatchEnabled');
    var ta = document.getElementById('issueWatchKeywords');
    if (en) en.checked = d.enabled !== false;
    if (ta) {
      var kws = (d.keywords && d.keywords.length) ? d.keywords : (d.defaults || ISSUE_WATCH_DEFAULTS);
      ta.value = kws.join('\n');
    }
  }
  function collectIssueWatch() {
    return {
      enabled: !!(document.getElementById('issueWatchEnabled') || {}).checked,
      level: 'P0',
      keywords: ((document.getElementById('issueWatchKeywords') || {}).value || '').trim()
    };
  }
  function collectAlertLeaders() {
    var box = document.getElementById('alertLeaders');
    var out = [];
    if (!box) return out;
    box.querySelectorAll('.leader-row').forEach(function (row) {
      out.push({
        department: ((row.querySelector('[data-k="department"]') || {}).value || '').trim(),
        name: ((row.querySelector('[data-k="name"]') || {}).value || '').trim(),
        userid: ((row.querySelector('[data-k="userid"]') || {}).value || '').trim()
      });
    });
    return out;
  }
  function setDueBcDot(d, parent) {
    var el = document.getElementById('dueBcDot');
    if (!el) return;
    var hookOk = !!(d && d.webhookReady) || !!(parent && (parent.hasWebhook || parent.configured));
    if (!hookOk) { el.style.color = 'var(--bad)'; el.textContent = '● 未配置 Webhook'; return; }
    if (!d || !d.enabled) { el.style.color = '#d97706'; el.textContent = '● 已关闭（Webhook 可用）'; return; }
    el.style.color = 'var(--ok)'; el.textContent = '● 定时开启 · ' + ((d.times || []).join('、') || '—');
  }
  function fillDueBroadcast(d, quiet, parent) {
    d = d || {};
    if (parent && (parent.hasWebhook || parent.configured)) d.webhookReady = true;
    setDueBcDot(d, parent);
    var en = document.getElementById('dueBcEnabled');
    var tm = document.getElementById('dueBcTimes');
    var sla = document.getElementById('dueBcSla');
    var rec = document.getElementById('dueBcRecent');
    var mx = document.getElementById('dueBcMax');
    var wk = document.getElementById('dueBcSkipWeekend');
    var wo = document.getElementById('dueBcWecomOnly');
    var em = document.getElementById('dueBcSendEmpty');
    var pre = document.getElementById('dueBcPreview');
    var hint = document.getElementById('dueBcHint');
    if (!quiet) {
      if (en) en.checked = !!d.enabled;
      if (tm) tm.value = (d.times || []).join(',');
      if (sla) sla.value = d.slaWorkdays == null ? 1 : d.slaWorkdays;
      if (rec) rec.value = d.recentDays == null ? 14 : d.recentDays;
      if (mx) mx.value = d.maxLines == null ? 20 : d.maxLines;
      if (wk) wk.checked = d.skipWeekend !== false;
      if (wo) wo.checked = !!d.wecomOnly;
      if (em) em.checked = !!d.sendIfEmpty;
    }
    if (pre) pre.textContent = d.preview || '（暂无预览）';
    if (hint) {
      var bits = [];
      if (d.lastRunAt) bits.push('上次 ' + d.lastRunAt);
      if (d.lastNote) bits.push(d.lastNote);
      if (d.lastError) bits.push('错误：' + d.lastError);
      bits.push('待交付 ' + (d.pending || 0) + ' 份');
      hint.textContent = bits.join(' · ');
    }
  }
  function collectDueBroadcast() {
    return {
      enabled: !!(document.getElementById('dueBcEnabled') || {}).checked,
      times: ((document.getElementById('dueBcTimes') || {}).value || '').trim(),
      slaWorkdays: Number((document.getElementById('dueBcSla') || {}).value || 1),
      recentDays: Number((document.getElementById('dueBcRecent') || {}).value || 14),
      maxLines: Number((document.getElementById('dueBcMax') || {}).value || 20),
      skipWeekend: !!(document.getElementById('dueBcSkipWeekend') || {}).checked,
      wecomOnly: !!(document.getElementById('dueBcWecomOnly') || {}).checked,
      sendIfEmpty: !!(document.getElementById('dueBcSendEmpty') || {}).checked
    };
  }
  function setAlertDot(j) {
    var el = document.getElementById('alertStatusDot');
    if (!el) return;
    if (!j || !j.configured) { el.style.color = 'var(--bad)'; el.textContent = '● 未配置 Webhook'; return; }
    if (!j.enabled) { el.style.color = '#d97706'; el.textContent = '● 已配置但关闭'; return; }
    el.style.color = 'var(--ok)'; el.textContent = '● 推送开启';
  }
  function renderAlertLogs(logs) {
    var body = document.getElementById('alertLogBody');
    if (!body) return;
    if (!logs || !logs.length) {
      body.innerHTML = '<tr><td colspan="5" style="color:#8a93a6">尚无发送记录</td></tr>';
      return;
    }
    body.innerHTML = logs.map(function (row) {
      var ok = !!row.ok;
      var tags = [];
      if (row.mentionedMobile && row.mentionedMobile.length) {
        tags = tags.concat(row.mentionedMobile.map(function (m) { return '@' + m; }));
      }
      if (row.mentioned && row.mentioned.length) {
        tags = tags.concat(row.mentioned.map(function (m) { return '@' + m; }));
      }
      var extra = ok ? (tags.length ? tags.join(' ') : '—') : esc(row.error || '失败');
      var bodyTxt = esc(row.body || '');
      return '<tr>' +
        '<td>' + esc(row.at || '') + '</td>' +
        '<td><strong>' + esc(row.level || '') + '</strong><div class="alert-kind">' + esc(alertKindLabel(row.kind)) + '</div></td>' +
        '<td><div>' + esc(row.title || '') + '</div>' + (bodyTxt ? '<div class="alert-body">' + bodyTxt + '</div>' : '') + '</td>' +
        '<td>' + (ok ? '<span class="dot-ok">成功</span>' : '<span class="dot-bad">失败</span>') + '</td>' +
        '<td>' + extra + '</td>' +
        '</tr>';
    }).join('');
  }
  function loadAlertPanel(quiet) {
    fetch('/api/admin/alert', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j || !j.ok) throw new Error((j && j.detail) || '无法读取告警配置');
      setAlertDot(j);
      if (!quiet || !alertFormDirty()) {
        var en = document.getElementById('alertEnabled');
        var lv = document.getElementById('alertMinLevel');
        var du = document.getElementById('alertDedupe');
        var pb = document.getElementById('alertPublicBase');
        var hint = document.getElementById('alertWebhookHint');
        var hook = document.getElementById('alertWebhook');
        if (en) en.checked = !!j.enabled;
        if (lv) lv.value = j.minLevel || 'P1';
        if (du) du.value = j.dedupeMinutes == null ? 15 : j.dedupeMinutes;
        if (pb) pb.value = j.publicBaseUrl || '';
        if (hint) hint.textContent = j.configured
          ? ('当前密钥尾号 ' + (j.webhookHint || '已配置') + ' · 留空保存不修改')
          : '群「… → 消息推送」里复制地址';
        if (hook) hook.placeholder = j.configured ? '已配置，留空不修改' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…';
        renderAlertLeaders(j.leaders);
      }
      if (!quiet || !alertFormDirty()) fillIssueWatch(j.issueWatch);
      fillDueBroadcast(j.dueBroadcast, quiet, j);
      var logs = j.logs || [];
      if (alertLogFilter === 'ok') logs = logs.filter(function (x) { return x.ok; });
      if (alertLogFilter === 'fail') logs = logs.filter(function (x) { return !x.ok; });
      renderAlertLogs(logs);
    }).catch(function (e) {
      setAlertDot({ configured: false });
      var body = document.getElementById('alertLogBody');
      if (body && !quiet) body.innerHTML = '<tr><td colspan="5" class="dot-bad">' + esc(e.message || '加载失败') + '</td></tr>';
    });
  }
  var alertSaveBtn = document.getElementById('alertSaveBtn');
  if (alertSaveBtn) {
    alertSaveBtn.addEventListener('click', function () {
      var msg = document.getElementById('alertMsg');
      var payload = {
        enabled: !!(document.getElementById('alertEnabled') || {}).checked,
        minLevel: (document.getElementById('alertMinLevel') || {}).value || 'P1',
        dedupeMinutes: Number((document.getElementById('alertDedupe') || {}).value || 15),
        publicBaseUrl: ((document.getElementById('alertPublicBase') || {}).value || '').trim(),
        leaders: collectAlertLeaders(),
        issueWatch: collectIssueWatch()
      };
      var hook = ((document.getElementById('alertWebhook') || {}).value || '').trim();
      if (hook) payload.webhook = hook;
      alertSaveBtn.disabled = true;
      postConfig({ alert: payload }).then(function (res) {
        if (msg) {
          msg.className = 'msg ' + (res.ok ? 'ok' : 'bad');
          msg.textContent = res.ok ? '已保存' : ((res.data && res.data.detail) || '保存失败');
        }
        if (res.ok) {
          var w = document.getElementById('alertWebhook');
          if (w) w.value = '';
          loadAlertPanel();
        }
      }).catch(function (e) {
        if (msg) { msg.className = 'msg bad'; msg.textContent = e.message || '保存失败'; }
      }).finally(function () { alertSaveBtn.disabled = false; });
    });
  }
  var alertTestBtn = document.getElementById('alertTestBtn');
  if (alertTestBtn) {
    alertTestBtn.addEventListener('click', function () {
      var msg = document.getElementById('alertMsg');
      alertTestBtn.disabled = true;
      fetch('/api/admin/alert/test', { method: 'POST' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          if (msg) {
            msg.className = 'msg ' + (res.ok ? 'ok' : 'bad');
            msg.textContent = res.ok ? '已发到告警群' : ((res.data && res.data.detail) || '发送失败');
          }
          loadAlertPanel(true);
        })
        .catch(function (e) {
          if (msg) { msg.className = 'msg bad'; msg.textContent = e.message || '发送失败'; }
        })
        .finally(function () { alertTestBtn.disabled = false; });
    });
  }
  var issueWatchReset = document.getElementById('issueWatchReset');
  if (issueWatchReset) {
    issueWatchReset.addEventListener('click', function () {
      var ta = document.getElementById('issueWatchKeywords');
      var en = document.getElementById('issueWatchEnabled');
      if (ta) ta.value = ISSUE_WATCH_DEFAULTS.join('\n');
      if (en) en.checked = true;
    });
  }
  var dueBcSaveBtn = document.getElementById('dueBcSaveBtn');
  if (dueBcSaveBtn) {
    dueBcSaveBtn.addEventListener('click', function () {
      var msg = document.getElementById('dueBcMsg');
      dueBcSaveBtn.disabled = true;
      postConfig({ alert: { dueBroadcast: collectDueBroadcast() } }).then(function (res) {
        if (msg) {
          msg.className = 'msg ' + (res.ok ? 'ok' : 'bad');
          msg.textContent = res.ok ? '已保存' : ((res.data && res.data.detail) || '保存失败');
        }
        if (res.ok) loadAlertPanel();
      }).catch(function (e) {
        if (msg) { msg.className = 'msg bad'; msg.textContent = e.message || '保存失败'; }
      }).finally(function () { dueBcSaveBtn.disabled = false; });
    });
  }
  function dueBcPost(url, btn, okText) {
    var msg = document.getElementById('dueBcMsg');
    if (btn) btn.disabled = true;
    fetch(url, { method: 'POST' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        if (msg) {
          msg.className = 'msg ' + (res.ok ? 'ok' : 'bad');
          msg.textContent = res.ok
            ? (okText + (res.data && res.data.skipped === 'empty' ? '（列表为空未发送）' : ''))
            : ((res.data && res.data.detail) || '发送失败');
        }
        loadAlertPanel(true);
      })
      .catch(function (e) {
        if (msg) { msg.className = 'msg bad'; msg.textContent = e.message || '发送失败'; }
      })
      .finally(function () { if (btn) btn.disabled = false; });
  }
  var dueBcTestBtn = document.getElementById('dueBcTestBtn');
  if (dueBcTestBtn) dueBcTestBtn.addEventListener('click', function () {
    dueBcPost('/api/admin/due-broadcast/test', dueBcTestBtn, '已试发到告警群');
  });
  var dueBcRunBtn = document.getElementById('dueBcRunBtn');
  if (dueBcRunBtn) dueBcRunBtn.addEventListener('click', function () {
    if (!confirm('按当前规则立即发一条正式播报到告警群？')) return;
    dueBcPost('/api/admin/due-broadcast/run', dueBcRunBtn, '已播报到告警群');
  });
  var alertLeaderBox = document.getElementById('alertLeaders');
  if (alertLeaderBox) {
    alertLeaderBox.addEventListener('click', function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest('.alert-leader-del') : null;
      if (!btn) return;
      var row = btn.closest('.leader-row');
      if (row) row.remove();
      if (!alertLeaderBox.querySelector('.leader-row')) renderAlertLeaders([]);
    });
  }
  var alertLeaderAdd = document.getElementById('alertLeaderAdd');
  if (alertLeaderAdd) {
    alertLeaderAdd.addEventListener('click', function () {
      var box = document.getElementById('alertLeaders');
      if (!box) return;
      var wrap = document.createElement('div');
      wrap.innerHTML = '<div class="leader-row">' +
        '<div class="f-row" style="margin:0"><label>部门</label><div class="inp"><input data-k="department" placeholder="如 申报部 或 *"></div></div>' +
        '<div class="f-row" style="margin:0"><label>姓名</label><div class="inp"><input data-k="name" placeholder="显示用"></div></div>' +
        '<div class="f-row" style="margin:0"><label>userid</label><div class="inp"><input data-k="userid" placeholder="企微账号"></div></div>' +
        '<button type="button" class="mini alert-leader-del">删除</button>' +
        '</div>';
      box.appendChild(wrap.firstChild);
    });
  }
  document.querySelectorAll('.alert-log-filter').forEach(function (btn) {
    btn.addEventListener('click', function () {
      alertLogFilter = btn.getAttribute('data-st') || '';
      document.querySelectorAll('.alert-log-filter').forEach(function (b) { b.classList.toggle('active', b === btn); });
      loadAlertPanel(true);
    });
  });
  var alertLogRefresh = document.getElementById('alertLogRefresh');
  if (alertLogRefresh) alertLogRefresh.addEventListener('click', function () { loadAlertPanel(true); });
  var alertLogClear = document.getElementById('alertLogClear');
  if (alertLogClear) {
    alertLogClear.addEventListener('click', function () {
      if (!confirm('清空本地发送日志？不影响已发出的群消息。')) return;
      fetch('/api/admin/alert/logs', { method: 'DELETE' })
        .then(function (r) { return r.json(); })
        .then(function () { loadAlertPanel(true); })
        .catch(function () {});
    });
  }
  global.loadSystem = loadSystem;
  global.loadAlertPanel = loadAlertPanel;
  global.renderDataCard = renderDataCard;
})(window);
