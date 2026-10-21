/* 管理后台：任务总览 */
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
  var TASK_PAGE_SIZE = 20;
  var taskPage = 1;
  var taskCache = [];
  function renderTaskTable() {
    var tb = document.getElementById('taskBody');
    var pager = document.getElementById('taskPager');
    var info = document.getElementById('taskPageInfo');
    var prev = document.getElementById('taskPagePrev');
    var next = document.getElementById('taskPageNext');
    var total = taskCache.length;
    var pages = Math.max(1, Math.ceil(total / TASK_PAGE_SIZE));
    if (taskPage > pages) taskPage = pages;
    if (taskPage < 1) taskPage = 1;
    var start = (taskPage - 1) * TASK_PAGE_SIZE;
    var slice = taskCache.slice(start, start + TASK_PAGE_SIZE);
    tb.innerHTML = '';
    var openId = tb.getAttribute('data-open') || '';
    if (openId) {
      var openIdx = -1;
      for (var i = 0; i < taskCache.length; i++) {
        if (taskCache[i].id === openId) { openIdx = i; break; }
      }
      if (openIdx >= 0) {
        var openPage = Math.floor(openIdx / TASK_PAGE_SIZE) + 1;
        if (openPage !== taskPage) {
          taskPage = openPage;
          start = (taskPage - 1) * TASK_PAGE_SIZE;
          slice = taskCache.slice(start, start + TASK_PAGE_SIZE);
        }
      }
    }
    slice.forEach(function (t) {
      var tr = document.createElement('tr');
      tr.className = 'task-row' + (t.id === openId ? ' open' : '');
      tr.setAttribute('data-id', t.id);
      var nOut = (t.deliverables || t.outputs || []).length;
      tr.innerHTML =
        '<td class="td-clip" title="' + esc(t.createdAt) + '">' + esc(t.createdAt) + '</td>' +
        '<td class="td-name" title="' + esc((t.app && t.app.name) || '') + '">' + (function () {
          var m = String(t.app && t.app.mode || '').toUpperCase();
          var tag = (m === 'QM' || m === 'HJ') ? '<span class="mode-tag mode-' + m + '">' + m + '</span> ' : '';
          var name = esc((t.app && t.app.name) || '未命名');
          return tag + '<a class="task-name-link" href="/t/' + encodeURIComponent(t.id) + '" target="_blank" rel="noopener" title="打开任务界面">' + name + '</a>';
        })() + '</td>' +
        '<td class="td-clip" title="' + esc(taskOrigin(t)) + '">' + esc(taskOrigin(t)) + '</td>' +
        '<td class="mono td-clip" title="' + esc(t.app && t.app.no) + '">' + esc(t.app && t.app.no) + '</td>' +
        '<td class="td-clip" title="' + esc(t.owner || '（历史）') + '">' + esc(t.owner || '（历史）') + '</td>' +
        '<td>' + badge(t.status, t.status === 'running' ? (t.runningSince || t.startedAt || '') : '') + '</td>' +
        '<td class="mono">' + (t.batchId ? '√' : '—') + '</td>' +
        '<td><button type="button" class="mini" data-open="' + esc(t.id) + '">' + (t.id === openId ? '收起' : (nOut ? '查看产出 · ' + nOut : '查看产出')) + '</button></td>';
      tb.appendChild(tr);
      if (t.id === openId) {
        var fr = document.createElement('tr');
        fr.className = 'task-files';
        fr.setAttribute('data-for', t.id);
        fr.innerHTML = '<td colspan="8"><div class="files-box"><div class="files-muted">加载产出…</div></div></td>';
        tb.appendChild(fr);
        fillTaskFiles(fr, t.id);
      }
    });
    if (!slice.length) tb.innerHTML = '<tr><td colspan="8" style="color:#98a2b6">暂无任务</td></tr>';
    if (pager) {
      pager.hidden = total <= TASK_PAGE_SIZE;
      if (info) info.textContent = '第 ' + taskPage + ' / ' + pages + ' 页 · 共 ' + total + ' 条';
      if (prev) prev.disabled = taskPage <= 1;
      if (next) next.disabled = taskPage >= pages;
    }
  }
  function loadOverview() {
    Promise.all([
      fetch('/api/tasks').then(function (r) { return r.json(); }),
      fetch('/api/batches').then(function (r) { return r.json(); })
    ]).then(function (res) {
      var tasks = res[0].tasks || [];
      var batches = res[1].batches || [];
      var done = 0, failed = 0, running = 0;
      tasks.forEach(function (t) {
        if (t.status === 'done') done++;
        else if (t.status === 'failed') failed++;
        else running++;
      });
      document.getElementById('stTotal').textContent = tasks.length;
      document.getElementById('stBatch').textContent = batches.length;
      document.getElementById('stRunning').textContent = running;
      document.getElementById('stDone').textContent = done;
      document.getElementById('stFailed').textContent = failed;
      AdminStats.totals.tasks = tasks.length;
      AdminStats.totals.batches = batches.length;
      if (global.renderDataCard) global.renderDataCard();
      taskCache = tasks;
      renderTaskTable();
    }).catch(function (e) { console.warn('overview', e); });
  }

  function fmtSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }
  function fillTaskFiles(fr, tid) {
    fetch('/api/tasks/' + encodeURIComponent(tid)).then(function (r) { return r.json(); }).then(function (t) {
      var files = t.outputs || t.deliverables || [];
      var box = fr.querySelector('.files-box');
      if (!box) return;
      if (!files.length) {
        box.innerHTML = '<div class="fh">产出文件</div><div class="files-muted">该任务尚无产出（未完成写入或校验未通过）</div>';
        return;
      }
      var lis = files.map(function (o) {
        var href = '/api/tasks/' + encodeURIComponent(tid) + '/files?dir=output&name=' + encodeURIComponent(o.name);
        return '<li><span class="fn" title="' + esc(o.name) + '">' + esc(o.name) + '</span>' +
          '<span class="fs">' + fmtSize(o.size) + '</span>' +
          '<a class="dl" href="' + href + '" download><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:12px;height:12px;vertical-align:-2px;margin-right:3px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>下载</a></li>';
      }).join('');
      var tf = t.talentAppFile || {};
      var extra = '';
      if (tf.id && tf.version) {
        extra = '<div class="files-muted" style="margin-top:8px">已入库人才库字段 JSON ' +
          esc(tf.attachId || '') + ' · <b>' + esc(tf.version) + '</b>' +
          '<span class="ingest-acts">' +
          '<button type="button" class="mini primary" data-ig-preview="' + esc(String(tf.id)) + '">预览</button>' +
          '<a class="mini" href="/api/talent-files/' + encodeURIComponent(tf.id) + '/file">下载</a>' +
          '</span></div>';
      }
      box.innerHTML = '<div class="fh">产出文件 · ' + files.length + ' 个</div><ul>' + lis + '</ul>' + extra;
    }).catch(function (e) {
      var box = fr.querySelector('.files-box');
      if (box) box.innerHTML = '<div class="files-muted">读取失败：' + esc(e.message || '') + '</div>';
    });
  }
  function toggleTaskOpen(tid) {
    var tb = document.getElementById('taskBody');
    var cur = tb.getAttribute('data-open') || '';
    tb.setAttribute('data-open', cur === tid ? '' : tid);
    renderTaskTable();
  }
  document.getElementById('taskPagePrev').addEventListener('click', function () {
    if (taskPage <= 1) return;
    taskPage--;
    var tb = document.getElementById('taskBody');
    if (tb) tb.setAttribute('data-open', '');
    renderTaskTable();
  });
  document.getElementById('taskPageNext').addEventListener('click', function () {
    var pages = Math.max(1, Math.ceil(taskCache.length / TASK_PAGE_SIZE));
    if (taskPage >= pages) return;
    taskPage++;
    var tb = document.getElementById('taskBody');
    if (tb) tb.setAttribute('data-open', '');
    renderTaskTable();
  });
  document.getElementById('taskBody').addEventListener('click', function (e) {
    if (e.target.closest && (e.target.closest('a.dl') || e.target.closest('a.task-name-link'))) return;
    var btn = e.target.closest ? e.target.closest('button[data-open]') : null;
    if (btn) {
      e.preventDefault();
      toggleTaskOpen(btn.getAttribute('data-open'));
      return;
    }
    var tr = e.target.closest ? e.target.closest('tr.task-row') : null;
    if (tr) toggleTaskOpen(tr.getAttribute('data-id'));
  });
  global.loadOverview = loadOverview;
})(window);
