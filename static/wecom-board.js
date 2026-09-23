/* 各群记录看板：主界面与管理后台共用 */
(function (global) {
  var STATUS = {
    done: ['st-done', '已完成'], planned: ['st-planned', '待确认'],
    running: ['st-running', '运行中'], pending: ['st-pending', '排队中'],
    failed: ['st-failed', '失败'], matching: ['st-running', '配对中']
  };
  function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
  function escAttr(s) { return esc(s).replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function badge(st) {
    var m = STATUS[st] || ['st-pending', st || '未知'];
    return '<span class="badge ' + m[0] + '">' + m[1] + '</span>';
  }
  function say(ok, t) {
    var msg = document.getElementById('wecomMsg') || document.getElementById('msg');
    if (!msg) return;
    msg.className = 'msg ' + (ok ? 'ok' : 'bad');
    msg.textContent = t;
    setTimeout(function () { if (msg.textContent === t) msg.textContent = ''; }, 4000);
  }

  /* ===== 各群记录看板 ===== */
  var wecomState = {
    sources: [], sourceId: '*', sourceKind: '', sessionId: '', sessionName: '',
    offset: 0, limit: 80, total: 0, fromEnd: 0, hasEarlier: false, searchMode: false, replicas: [], tail: true,
    sessionKey: '', msgKey: '', sourceKey: '', pageItems: [], pick: { app: null, opinions: [] },
    watchGroups: [], watchOnly: true
  };
  var wecomGroupTimer = null;
  var wecomMsgTimer = null;
  var wecomMsgReq = 0;
  var wecomAiLoading = false;
  var wecomAiTab = 'read';
  var wecomAiLastRead = null;

  function wecomKind() {
    var el = document.getElementById('wecomOnlyGroup');
    return (el && !el.checked) ? 'all' : 'group';
  }
  function wecomSourceParam() {
    var id = wecomState.sourceId || '*';
    return (id === '*' || id === 'all') ? '' : id;
  }
  function wecomCurrentSource() {
    var list = wecomState.sources || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === wecomState.sourceId) return list[i];
    }
    return {};
  }
  function wecomRemoteName() {
    var s = wecomCurrentSource();
    return s.operator_name || s.computer_name || wecomState.sourceId || '远端电脑';
  }
  function wecomReplicaNames(copies) {
    var names = [];
    var seen = {};
    function add(n) {
      n = String(n || '').trim();
      if (!n || seen[n]) return;
      seen[n] = 1;
      names.push(n);
    }
    (copies || []).forEach(function (c) { add(c.source_label || c.label || c.source_id); });
    (wecomState.replicas || []).forEach(function (r) { add(r.label || r.source_id); });
    if (!names.length && wecomSourceParam()) add(wecomRemoteName());
    return names.length ? names.join('、') : '各电脑';
  }
  function wecomIdleCacheHint(copies) {
    var n = (copies || []).length;
    if (!n) return '点击下载';
    var names = (copies || []).map(function (c) { return c.source_label || c.source_id; }).filter(Boolean);
    return '可查 ' + names.join(' · ') + ' · 点击下载';
  }
  function wecomParseJsonAttr(el, name) {
    try {
      return JSON.parse(decodeURIComponent(el.getAttribute(name) || '%5B%5D'));
    } catch (e) {
      return [];
    }
  }
  function wecomSetFileStatus(btn, text, cls) {
    var st = btn && btn.parentElement && btn.parentElement.querySelector('.st');
    if (!st) return;
    st.textContent = text;
    st.className = 'st' + (cls ? ' ' + cls : '');
    st.title = text;
  }
  function wecomQuery(path) {
    return fetch(path).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, data: j }; });
    });
  }
  function wecomFail(box, t) {
    if (!box) return;
    box.innerHTML = '<div class="wecom-empty">' + esc(t || '加载失败') + '</div>';
  }
  function loadWecomBoard(quiet) {
    var st = document.getElementById('wecomStatus');
    if (!quiet && st) st.textContent = '检测解析服务…';
    wecomQuery('/api/wecom/health').then(function (res) {
      var h = res.data || {};
      if (st) {
        var ok = !!h.ok;
        var svc = !!h.service_ok;
        var local = h.readMode === 'local';
        st.innerHTML = '<span class="' + (ok ? 'dot-ok' : (svc ? '' : 'dot-bad')) + '">●</span> ' +
          (ok ? (local
            ? ('本地直读 · ' + (h.source_count || 0) + ' 台电脑')
            : ('解析服务已连通 · ' + esc(h.baseUrl || '') + ' · ' + (h.source_count || 0) + ' 台电脑')) :
            (svc ? '服务可达，但 ' + esc(h.error || '未通过鉴权') : ('解析服务未就绪：' + esc(h.error || '无法连接')))) +
          (h.hint ? '<span style="color:#8a93a6">　' + esc(h.hint) + '</span>' : '');
      }
      if (!h.ok) {
        if (!wecomState.sources.length) {
          document.getElementById('wecomSources').innerHTML = '';
          wecomFail(document.getElementById('wecomSessions'), h.hint || h.error || '未连接');
        }
        return;
      }
      return wecomQuery('/api/wecom/sources').then(function (sr) {
        if (!sr.ok) {
          wecomFail(document.getElementById('wecomSessions'), (sr.data && (sr.data.detail || sr.data.error)) || '无法列出电脑');
          return;
        }
        wecomState.sources = sr.data.items || [];
        if (!wecomState.sourceId) wecomState.sourceId = '*';
        if (!quiet) renderWecomSources();
        else renderWecomSourcesIfChanged();
        loadWecomSessions(quiet);
        if (wecomState.sessionId) loadWecomMessages(quiet);
      });
    }).catch(function (e) {
      if (st) st.innerHTML = '<span class="dot-bad">●</span> 检测失败：' + esc(e.message || e);
    });
    loadWecomWatchGroups();
    loadWecomWatchStatus();
  }
  function loadWecomWatchGroups() {
    return wecomQuery('/api/wecom/watch-groups').then(function (res) {
      var j = res.data || {};
      if (!res.ok) return;
      wecomState.watchGroups = j.groups || [];
      wecomWatchHintUpdate();
    }).catch(function () {});
  }
  function wecomWatchHintUpdate() {
    var el = document.getElementById('wecomWatchHint');
    if (!el) return;
    var n = (wecomState.watchGroups || []).length;
    el.textContent = n ? ('已关注 ' + n + ' 条会话 · 点 ★ 切换') : '点会话左侧 ★ 关注（值班扫描，含单聊）';
  }
  function wecomGroupWatched(name, sid) {
    var groups = wecomState.watchGroups || [];
    if (!groups.length) return false;
    name = String(name || '');
    sid = String(sid || '');
    var blob = (name + ' ' + sid).toLowerCase();
    for (var i = 0; i < groups.length; i++) {
      var g = String(groups[i] || '');
      if (!g) continue;
    if (g === name || g === sid) return true;
    var gl = g.toLowerCase();
    // 只在「关注词出现在这条会话的名字或 id 里」时算已关注，与后端一致。
    // 短会话名若只是另一条更长关注名的前缀（如「申报一组组长-小唐」对上「…小唐1819…」），不能算已关注，否则取消时删不掉那条更长的记录，星标会一直亮。
    if (gl && blob.indexOf(gl) >= 0) return true;
    }
    return false;
  }
  function wecomToggleWatch(btn) {
    var wrap = btn.closest('.wecom-item-wrap');
    if (!wrap) return;
    var name = wrap.getAttribute('data-name') || '';
    var sid = wrap.getAttribute('data-id') || '';
    var watch = !wecomGroupWatched(name, sid);
    btn.disabled = true;
    fetch('/api/wecom/watch-groups/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: name, session_id: sid, watch: watch })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var j = x.j || {};
        if (!x.ok) {
          say(false, j.detail || '操作失败');
          return;
        }
        wecomState.watchGroups = j.groups || [];
        btn.classList.toggle('on', watch);
        wecomWatchHintUpdate();
        loadWecomWatchStatus();
        say(true, watch ? '已加入关注会话' : '已取消关注');
        if (wecomState.watchOnly && !watch) loadWecomSessions();
      }).catch(function (e) {
        say(false, e.message || '操作失败');
      }).finally(function () {
        btn.disabled = false;
      });
  }
  var wecomWatchTimer = null;
  var wecomWatchPanelBound = false;
  var wwSelectedLogId = '';

  var WW_STATUS_LABEL = {
    done: '完成',
    empty: '无消息',
    baseline: '记水位',
    idle: '无新消息',
    skip: '跳过',
    no_opinion: '无意见',
    error: '失败'
  };

  function wwSetBtnBusy(btn, busy, busyText) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.wwLabel) btn.dataset.wwLabel = btn.textContent;
      btn.disabled = true;
      btn.classList.add('busy');
      if (busyText) btn.textContent = busyText;
    } else {
      btn.disabled = false;
      btn.classList.remove('busy');
      if (btn.dataset.wwLabel) btn.textContent = btn.dataset.wwLabel;
    }
  }

  function wwFormatLogTime(at) {
    var s = String(at || '').trim();
    if (!s) return '—';
    var parts = s.split(' ');
    if (parts.length >= 2) {
      var d = parts[0].split('/');
      var today = new Date();
      var sameDay = d.length === 3 &&
        parseInt(d[0], 10) === today.getFullYear() &&
        parseInt(d[1], 10) === (today.getMonth() + 1) &&
        parseInt(d[2], 10) === today.getDate();
      return sameDay ? parts[1] : (d[1] + '/' + d[2] + ' ' + parts[1]);
    }
    return s;
  }

  function wwStatusLabel(status) {
    return WW_STATUS_LABEL[status] || status || '—';
  }

  function wwPanelMsg(text, ok) {
    var el = document.getElementById('wecomWatchPanelMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'wecom-watch-panel-msg' + (text ? (ok ? ' ok' : ' bad') : '');
    if (text && ok) setTimeout(function () { if (el.textContent === text) { el.textContent = ''; el.className = 'wecom-watch-panel-msg'; } }, 4000);
  }

  function wwOriginText(p) {
    p = p || {};
    var bits = [];
    var place = p.sessionName || p.sessionId || '';
    if (place) bits.push('会话「' + place + '」');
    if (p.sender && p.messageTime) bits.push(p.sender + ' 于 ' + p.messageTime + ' 发出');
    else if (p.sender) bits.push(p.sender + ' 发出');
    else if (p.messageTime) bits.push('消息时间 ' + p.messageTime);
    var srcs = p.sources || [];
    if (srcs.length) bits.push('电脑：' + srcs.join('、'));
    return bits.length ? bits.join(' · ') : '未记录原消息';
  }

  function wecomOpenSession(sid, name) {
    sid = String(sid || '');
    name = String(name || '');
    if (!sid && !name) return;
    var list = document.getElementById('wecomSessions');
    var wrap = null;
    if (list) {
      list.querySelectorAll('.wecom-item-wrap').forEach(function (el) {
        if (wrap) return;
        if ((sid && el.getAttribute('data-id') === sid) || (name && el.getAttribute('data-name') === name)) wrap = el;
      });
    }
    if (wrap) {
      var itemBtn = wrap.querySelector('.wecom-item');
      if (itemBtn) itemBtn.click();
      if (wrap.scrollIntoView) wrap.scrollIntoView({ block: 'nearest' });
    } else {
      wecomState.sessionId = sid;
      wecomState.sessionName = name;
      wecomState.replicas = [];
      wecomState.offset = 0;
      wecomState.fromEnd = 0;
      wecomState.tail = true;
      wecomState.searchMode = false;
      wecomState.msgKey = '';
      wecomMsgReq += 1;
      var title = document.getElementById('wecomChatTitle');
      if (title) title.textContent = name || sid || '未选择会话';
      var stream = document.getElementById('wecomStream');
      if (stream) stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
      loadWecomMessages();
    }
    var chat = document.querySelector('.wecom-chat');
    if (chat && chat.scrollIntoView) chat.scrollIntoView({ block: 'nearest' });
  }

  function wwRenderPending(rows) {
    var pendBox = document.getElementById('wecomWatchPending');
    if (!pendBox) return;
    rows = rows || [];
    if (!rows.length) {
      pendBox.hidden = true;
      pendBox.innerHTML = '';
      return;
    }
    pendBox.hidden = false;
    var shared = (rows[0] && rows[0].opinions) || [];
    var allSame = rows.every(function (p) {
      var ops = p.opinions || [];
      return !p.paired && ops.length === shared.length && ops.every(function (n, i) { return n === shared[i]; });
    });
    var headOps = (allSame && shared.length)
      ? '<div class="why">本会话识别到的修改意见：' + shared.map(function (n) { return esc(n); }).join('；') + '</div>'
      : '';
    pendBox.innerHTML = '<div class="hd">待人审 ' + rows.length + ' 条 · 上传后进入首页「待人工确认」' + headOps + '</div>' +
      rows.map(function (p) {
        var id = escAttr(p.caseId || '');
        var ops = p.opinions || [];
        var opLine = (p.paired && ops.length)
          ? ops.map(function (n) { return esc(n); }).join('、')
          : (ops.length ? '未配到这份申报书' : '未识别到修改意见');
        var sid = escAttr(p.sessionId || '');
        var sname = escAttr(p.sessionName || '');
        return '<div class="row" data-case="' + id + '"><b>申报书：' + esc(p.filename || p.caseId || '—') + '</b>' +
          '<div class="origin">出处：' + esc(wwOriginText(p)) + '</div>' +
          '<div>修改意见：' + opLine + '</div>' +
          (p.at ? '<div class="why">列入待审 ' + esc(p.at) + '</div>' : '') +
          '<div class="why">' + esc(p.reason || '') + '</div>' +
          '<div class="acts"><button type="button" class="mini primary" data-pend="upload" data-id="' + id + '">上传任务</button>' +
          '<button type="button" class="mini" data-pend="skip" data-id="' + id + '">不上传</button>' +
          '<button type="button" class="mini" data-pend="open" data-sid="' + sid + '" data-name="' + sname + '">打开会话</button></div></div>';
      }).join('');
  }

  function wwRenderPanelStatus(cfg, st) {
    var box = document.getElementById('wecomWatchPanelStatus');
    if (!box) return;
    cfg = cfg || {};
    st = st || {};
    var bits = [];
    bits.push(st.enabled ? '已开启' : '已关闭');
    if (st.enabled) {
      bits.push(st.mode === 'review' ? '待人审' : (st.mode === 'off' ? '关闭' : '自动提交'));
      bits.push('聊天同步后增量扫描');
      if (st.waitingSync && !st.running) bits.push('等待新同步');
      bits.push('回看 ' + (cfg.windowHours || st.windowHours || 48) + 'h');
    }
    bits.push('关注会话 ' + (st.groupCount || 0) + ' 条');
    if (cfg.model || st.model) bits.push('模型 ' + (cfg.model || st.model));
    if (!st.modelReady) bits.push('Gemini 未就绪');
    if (st.running) bits.push('正在扫描');
    else if (st.lastRunAt) bits.push('上次 ' + st.lastRunAt);
    if (st.lastNote) bits.push(st.lastNote);
    box.textContent = bits.join(' · ');
    wwRenderPending(st.pending || []);
    var evBox = document.getElementById('wecomWatchPanelEvents');
    if (evBox) {
      var evs = st.events || [];
      if (!evs.length) {
        evBox.hidden = true;
        evBox.innerHTML = '';
      } else {
        evBox.hidden = false;
        evBox.innerHTML = evs.map(function (e) {
          return '<div class="ev' + (e.kind === 'error' ? ' err' : '') + '">' + esc(e.at || '') + ' ' + esc(e.text || '') + '</div>';
        }).join('');
      }
    }
  }

  function wwFillPanelForm(cfg) {
    cfg = cfg || {};
    var el;
    el = document.getElementById('wwEnabled'); if (el) el.checked = !!cfg.enabled;
    el = document.getElementById('wwMode'); if (el) el.value = cfg.mode || 'auto';
    el = document.getElementById('wwWindow'); if (el) el.value = cfg.windowHours != null ? cfg.windowHours : 48;
    el = document.getElementById('wwTimeout'); if (el) el.value = cfg.timeoutSec != null ? cfg.timeoutSec : 60;
    el = document.getElementById('wwOpinionWait'); if (el) el.value = cfg.opinionWaitMin != null ? cfg.opinionWaitMin : 60;
    el = document.getElementById('wwOwner'); if (el) el.value = cfg.owner || '';
    el = document.getElementById('wwAllowNoOpinion'); if (el) el.checked = !!cfg.allowNoOpinion;
    el = document.getElementById('wwSkipDismissed'); if (el) el.checked = cfg.skipDismissed !== false;
  }

  function wwCollectPanelForm() {
    return {
      enabled: !!(document.getElementById('wwEnabled') || {}).checked,
      mode: (document.getElementById('wwMode') || {}).value || 'auto',
      windowHours: parseInt((document.getElementById('wwWindow') || {}).value, 10) || 48,
      timeoutSec: parseInt((document.getElementById('wwTimeout') || {}).value, 10) || 60,
      opinionWaitMin: parseInt((document.getElementById('wwOpinionWait') || {}).value, 10) || 0,
      owner: String(((document.getElementById('wwOwner') || {}).value) || '').trim(),
      allowNoOpinion: !!(document.getElementById('wwAllowNoOpinion') || {}).checked,
      skipDismissed: !!((document.getElementById('wwSkipDismissed') || {}).checked)
    };
  }

  function wwStatusTag(row) {
    if (!row || !row.ok) return '<span class="tag bad">失败</span>';
    if ((row.createdCount || 0) > 0) return '<span class="tag ok">提交 ' + row.createdCount + '</span>';
    if ((row.pendingCount || 0) > 0) return '<span class="tag warn">待审 ' + row.pendingCount + '</span>';
    return '<span class="tag">无提交</span>';
  }

  function wwGroupStatusTag(status) {
    var lab = wwStatusLabel(status);
    var cls = 'tag';
    if (status === 'error') cls += ' bad';
    else if (status === 'done') cls += ' ok';
    else if (status === 'no_opinion' || status === 'skip' || status === 'idle') cls += ' muted';
    return '<span class="' + cls + '">' + esc(lab) + '</span>';
  }

  function wwMarkLogRow(id) {
    var box = document.getElementById('wecomWatchLogs');
    if (!box) return;
    box.querySelectorAll('tr[data-id]').forEach(function (tr) {
      tr.classList.toggle('on', (tr.getAttribute('data-id') || '') === id);
    });
  }

  function wwCloseLogDetail() {
    wwSelectedLogId = '';
    wwMarkLogRow('');
    var box = document.getElementById('wecomWatchLogDetail');
    if (box) {
      box.hidden = true;
      box.className = 'wecom-watch-log-detail';
      box.innerHTML = '';
    }
  }

  function wwRenderScanLogs(items) {
    var box = document.getElementById('wecomWatchLogs');
    if (!box) return;
    box.classList.remove('loading');
    if (!items || !items.length) {
      box.innerHTML = '<div class="wecom-empty" style="padding:20px">暂无扫描记录</div>';
      return;
    }
    box.innerHTML = '<table><thead><tr><th>时间</th><th>触发</th><th>会话</th><th>结果</th><th>耗时</th><th>摘要</th></tr></thead><tbody>' +
      items.map(function (it) {
        var sum = it.summary || it.error || '—';
        var trig = it.trigger === 'manual' ? '手动' : (it.trigger === 'sync' ? '同步' : '自动');
        return '<tr data-id="' + escAttr(it.id || '') + '"' + (it.id === wwSelectedLogId ? ' class="on"' : '') + '>' +
          '<td class="ww-time" title="' + escAttr(it.at || '') + '">' + esc(wwFormatLogTime(it.at)) + '</td>' +
          '<td><span class="tag trig' + (it.trigger === 'manual' ? ' manual' : '') + '">' + esc(trig) + '</span></td>' +
          '<td>' + esc(it.scannedCount || 0) + '<span class="ww-muted">/' + esc(it.groupCount || 0) + '</span></td>' +
          '<td>' + wwStatusTag(it) + '</td>' +
          '<td class="ww-muted">' + esc(Math.round((it.durationMs || 0) / 1000)) + 's</td>' +
          '<td class="ww-sum" title="' + escAttr(sum) + '">' + esc(sum) + '</td></tr>';
      }).join('') + '</tbody></table>';
    box.querySelectorAll('tr[data-id]').forEach(function (tr) {
      tr.addEventListener('click', function () {
        var id = tr.getAttribute('data-id') || '';
        if (!id) return;
        if (id === wwSelectedLogId) {
          wwCloseLogDetail();
          return;
        }
        wwLoadScanLogDetail(id);
      });
    });
  }

  function wwRenderLogDetail(d) {
    var box = document.getElementById('wecomWatchLogDetail');
    if (!box) return;
    var groups = d.groups || [];
    var head = '<div class="wecom-watch-log-detail-hd">' +
      '<div class="ttl"><b>扫描详情</b> · ' + esc(d.at || '') +
      ' · <span class="tag trig' + (d.trigger === 'manual' ? ' manual' : '') + '">' + esc(d.trigger === 'manual' ? '手动' : (d.trigger === 'sync' ? '同步' : '自动')) + '</span></div>' +
      '<button type="button" class="mini ww-log-close" title="关闭">×</button></div>';
    head += '<div class="wecom-watch-log-meta">' +
      '会话 <b>' + esc(d.scannedCount || 0) + '/' + esc(d.groupCount || 0) + '</b>' +
      ' · 提交 <b>' + esc(d.createdCount || 0) + '</b>' +
      ' · 待审 <b>' + esc(d.pendingCount || 0) + '</b>' +
      ' · 耗时 <b>' + esc(Math.round((d.durationMs || 0) / 1000)) + 's</b></div>';
    if (d.summary) head += '<div class="wecom-watch-log-summary">' + esc(d.summary) + '</div>';
    if (d.error) head += '<div class="wecom-watch-log-summary bad">' + esc(d.error) + '</div>';
    var body = groups.length
      ? '<div class="wecom-watch-log-groups">' + groups.map(function (g) {
        var bits = [];
        if (g.messageCount) bits.push('消息 ' + g.messageCount);
        if (g.freshCount) bits.push('新 ' + g.freshCount);
        if (g.hasOpinion) bits.push('有意见');
        if (g.createdCount) bits.push('提交 ' + g.createdCount);
        if (g.pendingCount) bits.push('待审 ' + g.pendingCount);
        if (g.skippedCount) bits.push('跳过 ' + g.skippedCount);
        return '<div class="grp">' +
          '<div class="grp-hd"><span class="grp-name">' + esc(g.sessionName || g.sessionId || '—') + '</span>' +
          wwGroupStatusTag(g.status) + '</div>' +
          (bits.length ? '<div class="grp-bits">' + esc(bits.join(' · ')) + '</div>' : '') +
          (g.note ? '<div class="grp-note">' + esc(g.note) + '</div>' : '') +
          '</div>';
      }).join('') + '</div>'
      : '<div class="wecom-empty" style="padding:12px">本轮未扫描到各会话明细</div>';
    box.className = 'wecom-watch-log-detail';
    box.innerHTML = head + body;
    var closeBtn = box.querySelector('.ww-log-close');
    if (closeBtn) closeBtn.addEventListener('click', wwCloseLogDetail);
  }

  function wwLoadScanLogDetail(id) {
    var box = document.getElementById('wecomWatchLogDetail');
    if (!box || !id) return;
    wwSelectedLogId = id;
    wwMarkLogRow(id);
    box.hidden = false;
    box.className = 'wecom-watch-log-detail loading';
    box.innerHTML = '<div class="ww-loading"><span class="ww-spin"></span>加载详情…</div>';
    wecomQuery('/api/wecom/watch-logs/' + encodeURIComponent(id)).then(function (res) {
      if (!res.ok) {
        box.className = 'wecom-watch-log-detail bad';
        box.innerHTML = '<div class="wecom-watch-log-detail-hd"><div class="ttl bad">加载失败</div>' +
          '<button type="button" class="mini ww-log-close" title="关闭">×</button></div>' +
          '<div class="grp-note">' + esc((res.data && res.data.detail) || '加载失败') + '</div>';
        var closeBtn = box.querySelector('.ww-log-close');
        if (closeBtn) closeBtn.addEventListener('click', wwCloseLogDetail);
        return;
      }
      wwRenderLogDetail(res.data || {});
    }).catch(function (e) {
      box.className = 'wecom-watch-log-detail bad';
      box.innerHTML = '<div class="grp-note">' + esc(e.message || '加载失败') + '</div>';
    });
  }

  function loadWecomWatchLogs(opts) {
    opts = opts || {};
    var box = document.getElementById('wecomWatchLogs');
    var refreshBtn = document.getElementById('wwLogsRefresh');
    if (!box) return;
    if (!opts.quiet) {
      box.classList.add('loading');
      if (!opts.keep) box.innerHTML = '<div class="wecom-empty ww-loading"><span class="ww-spin"></span>加载中…</div>';
      wwSetBtnBusy(refreshBtn, true, '刷新中…');
    }
    return wecomQuery('/api/wecom/watch-logs?limit=50').then(function (res) {
      if (!res.ok) {
        box.classList.remove('loading');
        box.innerHTML = '<div class="wecom-empty bad">' + esc((res.data && res.data.detail) || '加载失败') + '</div>';
        if (opts.fromClick) wwPanelMsg('日志加载失败', false);
        return;
      }
      wwRenderScanLogs((res.data && res.data.items) || []);
      if (opts.fromClick) wwPanelMsg('日志已刷新', true);
    }).catch(function (e) {
      box.classList.remove('loading');
      box.innerHTML = '<div class="wecom-empty bad">' + esc(e.message || '加载失败') + '</div>';
      if (opts.fromClick) wwPanelMsg(e.message || '加载失败', false);
    }).finally(function () {
      wwSetBtnBusy(refreshBtn, false);
    });
  }

  function loadWecomWatchPanel() {
    var panel = document.getElementById('wecomWatchPanel');
    if (!panel) return;
    loadWecomWatchLogs({ quiet: true });
    wecomQuery('/api/wecom/watch-config').then(function (res) {
      if (!res.ok) {
        wwPanelMsg((res.data && res.data.detail) || '加载配置失败', false);
        return;
      }
      var d = res.data || {};
      wwFillPanelForm(d.config || {});
      wwRenderPanelStatus(d.config || {}, d.status || {});
    }).catch(function (e) { wwPanelMsg(e.message || '加载失败', false); });
  }

  function initWecomWatchPanel() {
    var panel = document.getElementById('wecomWatchPanel');
    if (!panel || wecomWatchPanelBound) return;
    wecomWatchPanelBound = true;
    loadWecomWatchPanel();
    var saveBtn = document.getElementById('wwSaveBtn');
    if (saveBtn) saveBtn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      wwSetBtnBusy(saveBtn, true, '保存中…');
      wwPanelMsg('保存中…', true);
      say(true, '值班配置保存中…');
      fetch('/api/wecom/watch-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ watch: wwCollectPanelForm() })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (x) {
          if (!x.ok) {
            var fail = (x.j && x.j.detail) || '保存失败';
            wwPanelMsg(fail, false);
            say(false, fail);
            return;
          }
          var d = x.j || {};
          wwFillPanelForm(d.config || {});
          wwRenderPanelStatus(d.config || {}, d.status || {});
          wwPanelMsg('已保存', true);
          say(true, '值班配置已保存');
          loadWecomWatchStatus();
        }).catch(function (e) {
          wwPanelMsg(e.message || '保存失败', false);
          say(false, e.message || '保存失败');
        })
        .finally(function () { wwSetBtnBusy(saveBtn, false); });
    });
    var probeBtn = document.getElementById('wwProbeBtn');
    if (probeBtn) probeBtn.addEventListener('click', function () {
      wwSetBtnBusy(probeBtn, true, '检测中…');
      wwPanelMsg('检测 Gemini…', true);
      fetch('/api/wecom/watch-probe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.ok) wwPanelMsg('✓ ' + (j.detail || '已连通') + ' · ' + (j.model || ''), true);
          else wwPanelMsg('✗ ' + (j.error || j.detail || '失败'), false);
        }).catch(function (e) { wwPanelMsg(e.message || '检测失败', false); })
        .finally(function () { wwSetBtnBusy(probeBtn, false); });
    });
    var runBtn = document.getElementById('wwRunBtn');
    if (runBtn) runBtn.addEventListener('click', function () {
      wwSetBtnBusy(runBtn, true, '扫描中…');
      wwPanelMsg('扫描中，请稍候…', true);
      fetch('/api/wecom/watch-run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (x) {
          var j = x.j || {};
          if (x.ok) {
            wwPanelMsg('✓ 本轮完成' + (j.created && j.created.length ? '，提交 ' + j.created.length + ' 条' : ''), true);
            loadWecomWatchPanel();
            loadWecomWatchStatus();
            if (j.logId) wwLoadScanLogDetail(j.logId);
          } else wwPanelMsg('✗ ' + (j.detail || '扫描未执行'), false);
        }).catch(function (e) { wwPanelMsg(e.message || '扫描失败', false); })
        .finally(function () { wwSetBtnBusy(runBtn, false); });
    });
    var logsRefresh = document.getElementById('wwLogsRefresh');
    if (logsRefresh) logsRefresh.addEventListener('click', function () {
      loadWecomWatchLogs({ fromClick: true, keep: true });
    });
    wwBindPending();
  }
  function wwBindPending() {
    var pendBox = document.getElementById('wecomWatchPending');
    if (pendBox && !pendBox.dataset.bound) {
      pendBox.dataset.bound = '1';
      pendBox.addEventListener('click', function (ev) {
        var btn = ev.target.closest('[data-pend]');
        if (!btn || btn.disabled) return;
        var action = btn.getAttribute('data-pend') || '';
        if (action === 'open') {
          wecomOpenSession(btn.getAttribute('data-sid') || '', btn.getAttribute('data-name') || '');
          return;
        }
        var caseId = btn.getAttribute('data-id') || '';
        if (!caseId || (action !== 'upload' && action !== 'skip')) return;
        var row = btn.closest('.row');
        var buttons = row ? row.querySelectorAll('button') : [btn];
        buttons.forEach(function (b) { b.disabled = true; });
        btn.textContent = action === 'upload' ? '上传中…' : '处理中…';
        fetch('/api/wecom/watch-pending', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: action, caseId: caseId })
        }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (x) {
            var j = x.j || {};
            if (!x.ok) {
              wwPanelMsg(j.detail || '操作失败', false);
              say(false, j.detail || '操作失败');
              buttons.forEach(function (b) { b.disabled = false; });
              btn.textContent = action === 'upload' ? '上传任务' : '不上传';
              return;
            }
            if (action === 'upload') {
              var created = (j.created || [])[0] || {};
              wwPanelMsg('已上传，到首页任务列表确认写入', true);
              say(true, '已提交待确认任务' + (created.filename ? '：' + created.filename : ''));
            } else {
              wwPanelMsg('已标记不上传', true);
              say(true, '已从待人审移除');
            }
            if (document.getElementById('wecomWatchPanel')) loadWecomWatchPanel();
            else loadWecomWatchStatus();
          }).catch(function (e) {
            wwPanelMsg(e.message || '操作失败', false);
            buttons.forEach(function (b) { b.disabled = false; });
            btn.textContent = action === 'upload' ? '上传任务' : '不上传';
          });
      });
    }
  }

  function loadWecomWatchStatus() {
    var el = document.getElementById('wecomWatchStatus');
    if (!el) return;
    wecomQuery('/api/wecom/watch').then(function (res) {
      var j = res.data || {};
      if (!res.ok) {
        el.textContent = '';
        return;
      }
      var bits = [];
      if (!j.enabled) bits.push('值班关闭');
      else if (j.needGroups) bits.push('值班已开，请在左侧点 ★ 关注会话');
      else if (!j.modelReady) bits.push('值班未配置 Gemini');
      else bits.push('值班 ' + (j.mode === 'review' ? '待人审' : '自动提交待确认') + ' · ' + esc(j.model || ''));
      if (j.running) bits.push('正在扫描');
      else if (j.lastRunAt) bits.push('上次 ' + esc(j.lastRunAt));
      if (j.lastNote) bits.push(esc(j.lastNote));
      el.innerHTML = '<span class="' + (j.enabled && j.modelReady && !j.needGroups ? 'dot-ok' : '') + '">●</span> ' + bits.join(' · ');
      if (document.getElementById('wecomUserReview')) wwRenderPending(j.pending || []);
    }).catch(function () {});
    if (!wecomWatchTimer) wecomWatchTimer = setInterval(function () {
      loadWecomWatchStatus();
      if (document.getElementById('wecomUserReview')) loadWecomWatchLogs({ quiet: true });
    }, 30000);
  }
  var wwUserRunTimer = 0;
  var WW_USER_RUN_MS = 60000;
  var WW_USER_RUN_KEY = 'wwUserRunUntil';
  function wwUserRunLeft() {
    var until = 0;
    try { until = parseInt(sessionStorage.getItem(WW_USER_RUN_KEY) || '0', 10) || 0; } catch (e) { until = 0; }
    return Math.max(0, until - Date.now());
  }
  function wwSyncUserRun(btn) {
    if (!btn || btn.dataset.scanning === '1') return;
    var left = wwUserRunLeft();
    if (left <= 0) {
      btn.disabled = false;
      btn.classList.remove('busy');
      btn.textContent = '立即扫描';
      try { sessionStorage.removeItem(WW_USER_RUN_KEY); } catch (e) {}
      if (wwUserRunTimer) { clearInterval(wwUserRunTimer); wwUserRunTimer = 0; }
      return;
    }
    btn.disabled = true;
    btn.textContent = '冷却 ' + Math.ceil(left / 1000) + ' 秒';
    if (!wwUserRunTimer) wwUserRunTimer = setInterval(function () { wwSyncUserRun(btn); }, 250);
  }
  function wwArmUserRun(btn, ms) {
    try { sessionStorage.setItem(WW_USER_RUN_KEY, String(Date.now() + Math.max(0, ms))); } catch (e) {}
    wwSyncUserRun(btn);
  }
  function initWecomUserReview() {
    var box = document.getElementById('wecomUserReview');
    if (!box || box.dataset.bound) return;
    box.dataset.bound = '1';
    wwBindPending();
    var logsRefresh = document.getElementById('wwLogsRefresh');
    if (logsRefresh) logsRefresh.addEventListener('click', function () {
      loadWecomWatchLogs({ fromClick: true, keep: true });
    });
    var runBtn = document.getElementById('wwUserRunBtn');
    if (runBtn) {
      wwSyncUserRun(runBtn);
      runBtn.addEventListener('click', function () {
        if (runBtn.disabled || runBtn.dataset.scanning === '1' || wwUserRunLeft() > 0) return;
        runBtn.dataset.scanning = '1';
        wwSetBtnBusy(runBtn, true, '扫描中…');
        say(true, '正在扫描关注会话…');
        wwArmUserRun(runBtn, WW_USER_RUN_MS);
        fetch('/api/wecom/watch-run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); })
          .then(function (x) {
            var j = x.j || {};
            var retry = parseInt(j.retryAfter, 10);
            if (retry > 0) wwArmUserRun(runBtn, retry * 1000);
            if (x.ok && j.ok !== false) {
              var extra = (j.created && j.created.length) ? '，提交 ' + j.created.length + ' 条' : '';
              say(true, '本轮扫描完成' + extra);
              loadWecomWatchStatus();
              loadWecomWatchLogs({ quiet: true });
            } else {
              say(false, (typeof j.detail === 'string' && j.detail) || '扫描未执行');
            }
          }).catch(function (e) {
            say(false, e.message || '扫描失败');
          }).finally(function () {
            runBtn.dataset.scanning = '';
            wwSetBtnBusy(runBtn, false);
            wwSyncUserRun(runBtn);
          });
      });
    }
    loadWecomWatchLogs({ quiet: true });
    loadWecomWatchStatus();
  }
  function renderWecomSourcesIfChanged() {
    var key = (wecomState.sources || []).map(function (s) {
      return [s.id || '', s.operator_name || '', s.computer_name || '', s.kind || ''].join('\t');
    }).join('\n') + '\n' + (wecomState.sourceId || '*');
    if (wecomState.sourceKey === key && document.getElementById('wecomSources') && document.getElementById('wecomSources').children.length) return;
    wecomState.sourceKey = key;
    renderWecomSources();
  }
  function renderWecomSources() {
    var box = document.getElementById('wecomSources');
    if (!box) return;
    var allOn = !wecomState.sourceId || wecomState.sourceId === '*';
    var html = '<button type="button" class="wecom-chip' + (allOn ? ' on' : '') + '" data-id="*" data-kind="">全部电脑<span class="tag">合并</span></button>';
    html += wecomState.sources.map(function (s) {
      var id = s.id || '';
      var label = s.operator_name || s.computer_name || id;
      var tag = (s.kind === 'local' || id === 'local') ? '本机' : '远端';
      return '<button type="button" class="wecom-chip' + (id === wecomState.sourceId ? ' on' : '') + '" data-id="' + escAttr(id) + '" data-kind="' + escAttr(s.kind || '') + '">' + esc(label) + '<span class="tag">' + tag + '</span></button>';
    }).join('');
    box.innerHTML = html;
    wecomState.sourceKey = (wecomState.sources || []).map(function (s) {
      return [s.id || '', s.operator_name || '', s.computer_name || '', s.kind || ''].join('\t');
    }).join('\n') + '\n' + (wecomState.sourceId || '*');
    box.querySelectorAll('.wecom-chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        wecomState.sourceId = btn.getAttribute('data-id') || '*';
        wecomState.sourceKind = btn.getAttribute('data-kind') || '';
        wecomState.sessionId = '';
        wecomState.replicas = [];
        wecomState.offset = 0;
        wecomState.fromEnd = 0;
        wecomState.tail = true;
        wecomState.sessionKey = '';
        wecomState.msgKey = '';
        wecomPickReset(true);
        renderWecomSources();
        loadWecomSessions();
        document.getElementById('wecomChatTitle').textContent = '未选择会话';
        document.getElementById('wecomChatSub').textContent = '从左侧点开会话；同一会话在多台电脑上已合并';
        document.getElementById('wecomStream').innerHTML = '<div class="wecom-empty">选择会话后显示消息。</div>';
        document.getElementById('wecomPager').hidden = true;
      });
    });
  }
  function wecomSessionKey(items) {
    return (items || []).map(function (it) {
      return [it.username || '', it.display_name || '', it.msg_count || '', it.last_time || '', it.synced_at || '', (it.replicas || []).length].join('\t');
    }).join('\n');
  }
  function wecomMsgKey(items) {
    return (items || []).map(function (m) {
      return [m.message_id || '', m.time_text || '', m.sender || '', (m.text || '').length, m.attachment_name || '', m.media_url || ''].join('\t');
    }).join('\n');
  }
  function loadWecomSessions(quiet) {
    var list = document.getElementById('wecomSessions');
    var q = (document.getElementById('wecomGroupQ') || {}).value || '';
    var url = '/api/wecom/groups?kind=' + encodeURIComponent(wecomKind()) + '&q=' + encodeURIComponent(q);
    if (wecomSourceParam()) url += '&source_id=' + encodeURIComponent(wecomSourceParam());
    if (wecomState.watchOnly) url += '&watch_only=1&limit=1000';
    var scroll = list ? list.scrollTop : 0;
    wecomQuery(url).then(function (res) {
      if (!res.ok) {
        if (!quiet) wecomFail(list, (res.data && res.data.detail) || '加载会话失败');
        return;
      }
      if (Array.isArray(res.data.watchGroups)) wecomState.watchGroups = res.data.watchGroups;
      wecomWatchHintUpdate();
      var items = res.data.items || [];
      if (wecomState.watchOnly && (wecomState.watchGroups || []).length) {
        items = items.filter(function (it) {
          return wecomGroupWatched(it.display_name || '', it.username || '');
        });
      }
      items.sort(function (a, b) {
        var as = String(a.synced_at || '');
        var bs = String(b.synced_at || '');
        if (as !== bs) return as < bs ? 1 : -1;
        var at = String(a.last_time || '');
        var bt = String(b.last_time || '');
        if (at === bt) return 0;
        return at < bt ? 1 : -1;
      });
      if (!items.length) {
        if (!quiet) {
          wecomFail(list, wecomState.watchOnly
            ? '没有已关注的会话。点会话左侧 ★ 关注，或取消「仅关注会话」。'
            : (q ? '没有匹配的会话' : '没有会话。可取消「仅群聊」，或先在解析助手里勾选并同步。'));
        }
        return;
      }
      var key = wecomSessionKey(items) + '\n' + (wecomState.watchGroups || []).join('\n') + '\n' + (wecomState.watchOnly ? '1' : '0');
      if (quiet && wecomState.sessionKey === key) return;
      wecomState.sessionKey = key;
      list.innerHTML = items.map(function (it) {
        var sid = it.username || '';
        var name = it.display_name || sid || '未命名会话';
        var reps = it.replicas || [];
        var nrep = reps.length;
        var labels = reps.map(function (r) { return r.label || r.source_id; }).filter(Boolean).join('、');
        var meta = (it.synced_at ? '同步 ' + it.synced_at : '未同步') +
          (it.msg_count ? ' · ' + it.msg_count + ' 条' : '') +
          (it.last_time ? ' · 消息 ' + it.last_time : '');
        if (nrep > 1 && labels) meta += ' · ' + labels;
        var watched = wecomGroupWatched(name, sid);
        var starTitle = watched ? '取消关注（值班不再扫描）' : '加入关注会话（群聊和单聊都会扫描）';
        return '<div class="wecom-item-wrap' + (sid === wecomState.sessionId ? ' on' : '') + '" data-id="' + escAttr(sid) + '" data-name="' + escAttr(name) + '">' +
          '<button type="button" class="wecom-star' + (watched ? ' on' : '') + '" title="' + escAttr(starTitle) + '">★</button>' +
          '<button type="button" class="wecom-item" data-replicas="' + encodeURIComponent(JSON.stringify(reps)) + '">' +
          '<b>' + esc(name) + '</b><span>' + esc(meta || sid) + '</span></button></div>';
      }).join('');
      list.querySelectorAll('.wecom-star').forEach(function (btn) {
        btn.addEventListener('click', function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          wecomToggleWatch(btn);
        });
      });
      list.querySelectorAll('.wecom-item').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var wrap = btn.closest('.wecom-item-wrap');
          wecomState.sessionId = (wrap && wrap.getAttribute('data-id')) || '';
          wecomState.sessionName = (wrap && wrap.getAttribute('data-name')) || '';
          wecomState.replicas = wecomParseJsonAttr(btn, 'data-replicas');
          wecomState.offset = 0;
          wecomState.fromEnd = 0;
          wecomState.tail = true;
          wecomState.searchMode = false;
          wecomState.msgKey = '';
          wecomMsgReq += 1;
          wecomPickReset(true);
          wecomAiReset();
          list.querySelectorAll('.wecom-item-wrap').forEach(function (x) { x.classList.toggle('on', x === wrap); });
          var stream = document.getElementById('wecomStream');
          if (stream) stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
          loadWecomMessages();
        });
      });
      if (quiet) list.scrollTop = scroll;
    }).catch(function (e) { if (!quiet) wecomFail(list, e.message || '加载失败'); });
  }
  function wecomMsgAlive(reqId, sid) {
    return reqId === wecomMsgReq && sid === wecomState.sessionId;
  }
  function loadWecomMessages(quiet) {
    var stream = document.getElementById('wecomStream');
    var title = document.getElementById('wecomChatTitle');
    var sub = document.getElementById('wecomChatSub');
    var pager = document.getElementById('wecomPager');
    if (!wecomState.sessionId) {
      if (!quiet) {
        wecomFail(stream, '选择会话后显示消息。');
        if (pager) pager.hidden = true;
      }
      return;
    }
    var reqId = ++wecomMsgReq;
    var sid = wecomState.sessionId;
    if (title) title.textContent = wecomState.sessionName || wecomState.sessionId;
    var q = ((document.getElementById('wecomMsgQ') || {}).value || '').trim();
    var hasMsgs = !!(stream && stream.querySelector('.wecom-msg'));
    if (!quiet) {
      if (stream) stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
    } else if (!hasMsgs && stream) {
      stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
    }
    if (q) {
      wecomState.searchMode = true;
      var surl = '/api/wecom/search?q=' + encodeURIComponent(q) + '&session_id=' + encodeURIComponent(sid) + '&limit=80';
      if (wecomSourceParam()) surl += '&source_id=' + encodeURIComponent(wecomSourceParam());
      wecomQuery(surl).then(function (res) {
        if (!wecomMsgAlive(reqId, sid)) return;
        if (pager) pager.hidden = true;
        if (!res.ok) { if (!quiet) wecomFail(stream, (res.data && res.data.detail) || '搜索失败'); return; }
        var items = res.data.items || [];
        if (sub) sub.textContent = '关键词「' + q + '」命中 ' + items.length + ' 条 · 来自 ' + wecomReplicaNames();
        var key = wecomMsgKey(items);
        if (!(quiet && wecomState.msgKey === key)) {
          wecomState.msgKey = key;
          renderWecomMsgs(stream, items, true, quiet);
        }
      }).catch(function (e) {
        if (!wecomMsgAlive(reqId, sid)) return;
        if (!quiet) wecomFail(stream, e.message || '搜索失败');
      });
      return;
    }
    wecomState.searchMode = false;
    var start = (document.getElementById('wecomStart') || {}).value || '';
    var end = (document.getElementById('wecomEnd') || {}).value || '';
    var url = '/api/wecom/groups/' + encodeURIComponent(sid) + '/messages?limit=' + wecomState.limit +
      '&tail=1&from_end=' + (wecomState.fromEnd || 0);
    if (wecomSourceParam()) url += '&source_id=' + encodeURIComponent(wecomSourceParam());
    if (start) url += '&start_date=' + encodeURIComponent(start);
    if (end) url += '&end_date=' + encodeURIComponent(end);
    wecomQuery(url).then(function (res) {
      if (!wecomMsgAlive(reqId, sid)) return;
      if (!res.ok) {
        if (!quiet) { wecomFail(stream, (res.data && res.data.detail) || '加载消息失败'); if (pager) pager.hidden = true; }
        return;
      }
      var d = res.data || {};
      wecomState.total = d.total || 0;
      wecomState.offset = d.offset || 0;
      wecomState.fromEnd = d.fromEnd || 0;
      wecomState.hasEarlier = !!d.hasEarlier;
      wecomState.tail = true;
      if (d.replicas && d.replicas.length) wecomState.replicas = d.replicas;
      if (d.display_name && !wecomState.sessionName) wecomState.sessionName = d.display_name;
      if (title && wecomState.sessionName) title.textContent = wecomState.sessionName;
      var items = d.items || [];
      var names = wecomReplicaNames();
      var pageNote = wecomState.fromEnd ? ('已跳过最新 ' + wecomState.fromEnd + ' 条') : '最新';
      if (sub) sub.textContent = '本页 ' + items.length + ' 条（重复已合并）· ' + pageNote +
        (d.readMode === 'local' ? ' · 本地直读' : '') +
        (start || end ? ' · 已按日期筛选' : '') + ' · ' + names;
      var key = wecomMsgKey(items);
      if (!(quiet && wecomState.msgKey === key)) {
        wecomState.msgKey = key;
        renderWecomMsgs(stream, items, false, quiet);
      }
      if (pager) {
        pager.hidden = !wecomState.hasEarlier && wecomState.fromEnd <= 0;
        document.getElementById('wecomPageInfo').textContent = wecomState.fromEnd
          ? ('更早 ' + wecomState.fromEnd + ' 条之前 · 本页 ' + items.length)
          : ('最新 ' + items.length + ' 条');
        document.getElementById('wecomPrev').disabled = !wecomState.hasEarlier;
        document.getElementById('wecomNext').disabled = wecomState.fromEnd <= 0;
      }
    }).catch(function (e) {
      if (!wecomMsgAlive(reqId, sid)) return;
      if (!quiet) wecomFail(stream, e.message || '加载失败');
    });
  }
  function wecomFileHref(copy) {
    if (!copy || !copy.source_id || !copy.message_id) return '';
    var url = '/api/wecom/sources/' + encodeURIComponent(copy.source_id) + '/files/' + encodeURIComponent(copy.message_id);
    var cid = copy.session_id || wecomState.sessionId || '';
    if (cid) url += '?session_id=' + encodeURIComponent(cid);
    return url;
  }
  function wecomIsLocalCopy(c) {
    return !!(c && (c.source_id === 'local' || c.kind === 'local'));
  }
  function wecomSortCopies(copies) {
    return (copies || []).slice().sort(function (a, b) {
      return (wecomIsLocalCopy(a) ? 0 : 1) - (wecomIsLocalCopy(b) ? 0 : 1);
    });
  }
  function wecomCopiesOf(m) {
    var copies = [];
    if (m && Array.isArray(m.copies) && m.copies.length) {
      copies = m.copies.map(function (c) {
        return {
          source_id: c.source_id,
          source_label: c.source_label || c.label || '',
          kind: c.kind || '',
          message_id: Number(c.message_id || 0),
          session_id: c.session_id || wecomState.sessionId || ''
        };
      }).filter(function (c) { return c.source_id && c.message_id; });
    } else {
      var mid = Number((m && m.message_id) || 0);
      if (mid) {
        var sid = (m && m.source_id) || wecomSourceParam();
        if (sid) {
          copies = [{ source_id: sid, message_id: mid, session_id: wecomState.sessionId, source_label: wecomRemoteName(), kind: wecomState.sourceKind || '' }];
        } else {
          copies = (wecomState.replicas || []).map(function (r) {
            return { source_id: r.source_id, message_id: mid, session_id: wecomState.sessionId, source_label: r.label || '', kind: r.kind || '' };
          }).filter(function (c) { return c.source_id && c.message_id; });
        }
      }
    }
    return wecomSortCopies(copies.filter(function (c, i, arr) {
      return arr.findIndex(function (x) { return x.source_id === c.source_id; }) === i;
    }));
  }
  var WECOM_MEDIA_URL = /https?:\/\/\S*(?:wework\.qpic\.cn|wx\.qlogo\.cn|mmbiz\.qpic\.cn|pic\.weixin\.qq\.com|imunion\.weixin\.qq\.com|weixin\.qq\.com\/cgi-bin\/mmae-bin)\S*/gi;
  function wecomStripMediaUrls(s) {
    return String(s || '').replace(WECOM_MEDIA_URL, ' ').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  }
  function wecomExtractFileName(text) {
    var s = wecomStripMediaUrls(text);
    var hit = s.match(/\[?(?:文件|file)?\]?\s*([^\n[\]/\\]+?\.(?:zip|rar|7z|pdf|xlsx?|docx?|pptx?|png|jpe?g|gif|webp|bmp|mp4|txt|md))/i);
    return hit ? String(hit[1] || '').trim() : '';
  }
  function wecomHasFileUrl(text) {
    return /imunion\.weixin\.qq\.com|tpdownloadmedia/i.test(String(text || ''));
  }
  function wecomCaption(m, fname, hasMedia) {
    var text = wecomStripMediaUrls(m.text || m.snippet || '');
    if (hasMedia) {
      text = text.replace(/^\[.*?\]\s*/, '');
      if (fname) text = text.split(fname).join('').trim();
      if (!text || text === fname) return '';
    }
    return text;
  }
  function wecomIsImage(m) {
    var t = Number(m.msg_type || 0);
    var name = String(m.attachment_name || m.text || '');
    if (t === 4 || t === 15) return true;
    if (m.media_url) return true;
    return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
  }
  function wecomFileName(m) {
    if (m.attachment_name) return String(m.attachment_name);
    var fromText = wecomExtractFileName(m.text || m.snippet || '');
    if (fromText) return fromText;
    var label = String(m.msg_type_label || '').trim();
    if (label && label !== '文本' && label !== '系统消息' && !/^text$/i.test(label)) return label;
    return '聊天文件';
  }
  function wecomFileExt(name) {
    var s = String(name || '');
    var i = s.lastIndexOf('.');
    if (i < 0 || i === s.length - 1) return 'FILE';
    return s.slice(i + 1).toUpperCase().slice(0, 4);
  }
  function wecomExtOf(name) {
    var s = String(name || '');
    var i = s.lastIndexOf('.');
    return i >= 0 ? s.slice(i).toLowerCase() : '';
  }
  function wecomCanAppFile(name) {
    var e = wecomExtOf(name);
    return !e || /\.(docx|docm|wps|xlsx|xlsm|xls|pdf)$/i.test(e);
  }
  function wecomCanOpinionFile(name) {
    var e = wecomExtOf(name);
    if (/\.pdf$/i.test(e)) return false;
    return !e || /\.(docx|docm|wps|txt|md|xlsx|xlsm|xls|csv|jpe?g|png|webp|gif|tif|tiff|bmp|m4a|mp3|wav|aac|ogg|flac|amr|wma|webm)$/i.test(e);
  }
  function wecomPickMid(m) {
    var copies = wecomCopiesOf(m);
    return String((m && m.message_id) || (copies[0] && copies[0].message_id) || '');
  }
  function wecomPickIsApp(m) {
    var a = wecomState.pick && wecomState.pick.app;
    return !!(a && String(a.message_id) === wecomPickMid(m));
  }
  function wecomPickIsOpFile(m) {
    var mid = wecomPickMid(m);
    return ((wecomState.pick && wecomState.pick.opinions) || []).some(function (o) {
      return o && o.kind !== 'text' && String(o.message_id) === mid;
    });
  }
  function wecomPickIsOpText(m) {
    var mid = wecomPickMid(m);
    return ((wecomState.pick && wecomState.pick.opinions) || []).some(function (o) {
      return o && o.kind === 'text' && String(o.message_id) === mid;
    });
  }
  function wecomHasFile(m) {
    var t = Number(m.msg_type || 0);
    if (m.attachment_name) return true;
    if (t === 14 || t === 16 || t === 20) return true;
    if (t === 4 || t === 15) return !m.media_url;
    if (wecomHasFileUrl(m.text || m.snippet || '') && wecomExtractFileName(m.text || m.snippet || '')) return true;
    return !!(m.has_attachment && m.message_id && !wecomIsImage(m));
  }
  function wecomPreview(src) {
    var box = document.getElementById('wecomPreview');
    if (!box) {
      box = document.createElement('div');
      box.id = 'wecomPreview';
      box.className = 'wecom-preview';
      box.hidden = true;
      box.innerHTML = '<img alt="预览">';
      box.addEventListener('click', function () {
        box.hidden = true;
        box.querySelector('img').removeAttribute('src');
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !box.hidden) box.click();
      });
      document.body.appendChild(box);
    }
    box.querySelector('img').src = src;
    box.hidden = false;
  }
  function wecomPcsHtml(copies) {
    return '<div class="wecom-pcs">' + copies.map(function (c, i) {
      return '<span class="wecom-pc" data-pc="' + i + '">' + esc(c.source_label || c.source_id || '') + '</span>';
    }).join('') + '</div>';
  }
  function wecomSetPcState(wrap, idx, cls) {
    if (!wrap) return;
    var chips = wrap.querySelectorAll('.wecom-pc');
    if (chips[idx]) chips[idx].className = 'wecom-pc' + (cls ? ' ' + cls : '');
  }
  function renderWecomMsgs(stream, items, fromSearch, quiet) {
    if (!items.length) {
      if (!quiet || !stream.querySelector('.wecom-msg')) {
        wecomFail(stream, fromSearch ? '没有命中的消息' : '该时间范围内没有消息');
      }
      return;
    }
    var keepScroll = stream.scrollTop;
    var nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80;
    stream.innerHTML = items.map(function (m, i) {
      var fname = wecomFileName(m);
      var copies = wecomCopiesOf(m);
      var mid = m.message_id || (copies[0] && copies[0].message_id) || '';
      var hasThumb = !!(wecomIsImage(m) && m.media_url);
      var showCard = wecomHasFile(m);
      var text = wecomCaption(m, fname, hasThumb || showCard);
      var who = m.sender || '未知';
      var when = m.time_text || '';
      var typ = m.msg_type_label || '';
      if (showCard && /系统消息/.test(typ)) typ = '文件';
      var extra = '';
      if (hasThumb) {
        var src = '/api/wecom/media?url=' + encodeURIComponent(m.media_url);
        extra += '<img class="wecom-thumb" data-preview="' + escAttr(src) + '" src="' + escAttr(src) + '" alt="' + escAttr(fname) + '" loading="lazy" referrerpolicy="no-referrer">';
        if (copies.length) {
          extra += '<div><button type="button" class="wecom-orig" data-wecom-dl="' + escAttr(mid) + '" data-name="' + escAttr(fname) + '" data-copies="' + encodeURIComponent(JSON.stringify(copies)) + '">下载原文件</button></div>';
        }
      }
      if (showCard) {
        extra += '<div class="wecom-file">' +
          '<span class="ext">' + esc(wecomFileExt(fname)) + '</span>' +
          '<span class="nm" title="' + escAttr(fname) + '">' + esc(fname) + '</span>' +
          '<button type="button" class="mini" data-wecom-dl="' + escAttr(mid) + '" data-name="' + escAttr(fname) + '" data-copies="' + encodeURIComponent(JSON.stringify(copies)) + '">下载</button>' +
          '<button type="button" class="mini" data-wecom-locate="' + encodeURIComponent(JSON.stringify({
            time_text: when, sender: who, text: String(m.text || m.snippet || ''), filename: fname,
            session_name: wecomState.sessionName || '', copies: copies
          })) + '">定位申报书</button>' +
          wecomPcsHtml(copies) +
          '<span class="st">' + esc(wecomIdleCacheHint(copies)) + '</span></div>';
      }
      var picks = [];
      if (showCard && wecomCanAppFile(fname) && copies.length) {
        picks.push('<button type="button" class="wecom-pick' + (wecomPickIsApp(m) ? ' on' : '') + '" data-pick="app">申报书</button>');
      }
      if ((showCard || hasThumb) && wecomCanOpinionFile(fname) && copies.length) {
        picks.push('<button type="button" class="wecom-pick' + (wecomPickIsOpFile(m) ? ' on' : '') + '" data-pick="op-file">意见文件</button>');
      }
      if (text) {
        picks.push('<button type="button" class="wecom-pick' + (wecomPickIsOpText(m) ? ' on' : '') + '" data-pick="op-text">意见文本</button>');
      }
      var pickHtml = picks.length ? '<div class="wecom-pickrow">' + picks.join('') + '</div>' : '';
      var cls = 'wecom-msg';
      if (wecomPickIsApp(m)) cls += ' is-app';
      if (wecomPickIsOpFile(m) || wecomPickIsOpText(m)) cls += ' is-op';
      return '<div class="' + cls + '" data-i="' + i + '"><div class="meta"><span class="who">' + esc(who) + '</span><span>' + esc(when) + (typ ? ' · ' + esc(typ) : '') + '</span></div>' +
        (text ? '<div class="body">' + esc(text) + '</div>' : '') + extra + pickHtml +
        '<div class="wecom-intent-after" hidden></div></div>';
    }).join('');
    wecomState.pageItems = items;
    stream.querySelectorAll('[data-wecom-dl]').forEach(function (btn) {
      btn.addEventListener('click', function () { wecomDownload(btn); });
    });
    stream.querySelectorAll('[data-wecom-locate]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        wecomLocate(btn);
      });
    });
    stream.querySelectorAll('img.wecom-thumb').forEach(function (img) {
      img.addEventListener('click', function () { wecomPreview(img.getAttribute('data-preview') || img.src); });
    });
    wecomIntentHydrate(stream, items);
    stream.querySelectorAll('[data-pick]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        wecomPickToggle(btn);
      });
    });
    wecomPickEnsureBar();
    wecomPickRefreshBar();
    if (quiet && !nearBottom) stream.scrollTop = keepScroll;
    else stream.scrollTop = stream.scrollHeight;
  }
  function wecomIntentKey(m) {
    var copies = wecomCopiesOf(m);
    var mid = m.message_id || (copies[0] && copies[0].message_id) || '';
    return String(wecomState.sessionId || '') + '|' + String(mid) + '|' + String(m.time_text || '') + '|' + String(m.sender || '');
  }
  function wecomIntentPayload(m) {
    var fname = wecomFileName(m);
    var hasThumb = !!(wecomIsImage(m) && m.media_url);
    var text = wecomCaption(m, fname, hasThumb || wecomHasFile(m)) || String(m.text || m.snippet || '').trim();
    return {
      sender: m.sender || '',
      time: m.time_text || '',
      filename: fname || '',
      text: text,
      attachment_name: fname || ''
    };
  }
  function wecomIntentBubbleHtml(d) {
    var lines = ['<b class="read">' + esc((d && d.readLabel) || '已读') + '</b>'];
    if (d && d.intentLabel) lines.push('意图：' + esc(d.intentLabel));
    if (d && d.need) lines.push('要什么：' + esc(d.need));
    if (d && d.action) lines.push('建议：' + esc(d.action));
    if (d && d.error) lines.push(esc(d.error));
    return lines.join('<br>');
  }
  function wecomIntentShow(wrap, d) {
    var bubble = wrap.querySelector('.wecom-intent-after');
    if (!bubble) return;
    bubble.innerHTML = wecomIntentBubbleHtml(d);
    bubble.hidden = false;
    wrap.classList.add('has-intent');
  }
  function wecomIntentRestore(stream, items) {
    wecomState.intentCache = wecomState.intentCache || {};
    (items || []).forEach(function (m, i) {
      var hit = wecomState.intentCache[wecomIntentKey(m)];
      if (!hit || !stream) return;
      var wrap = stream.querySelector('.wecom-msg[data-i="' + i + '"]');
      if (wrap) wecomIntentShow(wrap, hit);
    });
  }
  var wecomIntentHydrateGen = 0;
  function wecomIntentHydrate(stream, items) {
    wecomIntentRestore(stream, items);
    var sid = wecomState.sessionId || '';
    if (!sid || !items || !items.length) return;
    var gen = ++wecomIntentHydrateGen;
    var keys = items.map(wecomIntentKey);
    fetch('/api/wecom/intents/lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ session_id: sid, keys: keys })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok || gen !== wecomIntentHydrateGen || sid !== wecomState.sessionId) return;
        var saved = (x.j && x.j.items) || {};
        wecomState.intentCache = wecomState.intentCache || {};
        Object.keys(saved).forEach(function (k) { wecomState.intentCache[k] = saved[k]; });
        var box = document.getElementById('wecomStream');
        if (box) wecomIntentRestore(box, wecomState.pageItems || []);
      }).catch(function () {});
  }
  function wecomIntentPage() {
    if (!wecomState.sessionId) { say(false, '请先打开一个群聊'); return; }
    var items = wecomState.pageItems || [];
    if (!items.length) { say(false, '本页没有聊天记录'); return; }
    var btn = document.getElementById('wecomIntentPageBtn');
    var stream = document.getElementById('wecomStream');
    var sid = wecomState.sessionId;
    var keys = items.map(wecomIntentKey);
    wecomIntentHydrateGen++;
    if (btn) { btn.disabled = true; btn.textContent = '分析中…'; }
    if (stream) {
      stream.querySelectorAll('.wecom-intent-after').forEach(function (el) {
        el.hidden = false;
        el.textContent = '正在分析…';
      });
    }
    fetch('/api/wecom/intent-page', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, keys: keys, messages: items.map(wecomIntentPayload) })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var d = x.j || {};
        if (!x.ok) { say(false, d.detail || '意图分析失败'); return; }
        var rows = d.items || [];
        wecomState.intentCache = wecomState.intentCache || {};
        keys.forEach(function (key, i) {
          var row = rows[i] || { readLabel: '已读', error: '这条没有返回意图' };
          row.readLabel = row.readLabel || '已读';
          wecomState.intentCache[key] = row;
        });
        var same = sid === wecomState.sessionId && keys.join('\n') === (wecomState.pageItems || []).map(wecomIntentKey).join('\n');
        if (same && stream) wecomIntentRestore(stream, wecomState.pageItems || []);
        say(true, '已分析本页 ' + keys.length + ' 条');
      }).catch(function (e) {
        say(false, e.message || '意图分析失败');
      }).finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = '分析意图'; }
      });
  }
  function wecomFileBox() {
    return document.getElementById('wecomFileBox');
  }
  function wecomFileGo() {
    var sec = document.getElementById('sec-files');
    if (sec && sec.scrollIntoView) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  var WECOM_FILE_PAGE = 15;
  var WECOM_FILE_CATS = ['申报书', '修改意见', '护照', '身份证明', '学历证明', '工作证明', '意向协议', '股权证明', '论文全文', '证件照', '电子签', '项目证明', '成果转化证明', '其他'];
  function wecomFileCatRank(label) {
    var i = WECOM_FILE_CATS.indexOf(label || '');
    return i < 0 ? WECOM_FILE_CATS.length : i;
  }
  var wecomFilePage = 1;
  var wecomFileData = null;
  var wecomFileChecked = {};
  var wecomFileQuery = '';
  var wecomFileCat = '';
  function wecomFileKey(f) {
    return String(f.messageId || '') + '|' + String(f.filename || '');
  }
  function wecomFileRows(d) {
    var rows = [];
    ((d && d.groups) || []).forEach(function (g) {
      (g.files || []).forEach(function (f) {
        rows.push({
          label: g.label || g.id || '',
          filename: f.filename || '',
          sender: f.sender || '',
          time: f.time || '',
          messageId: f.messageId || '',
          copies: f.copies || []
        });
      });
    });
    return rows;
  }
  function wecomFileSyncChecks() {
    document.querySelectorAll('#wecomFileBox [data-file]').forEach(function (el) {
      var key = el.getAttribute('data-key') || '';
      if (!key) return;
      if (el.checked) wecomFileChecked[key] = {
        filename: el.getAttribute('data-name') || '',
        messageId: el.getAttribute('data-mid') || ''
      };
      else delete wecomFileChecked[key];
    });
  }
  function wecomFileAllRows() {
    return wecomFileRows(wecomFileData).slice().sort(function (a, b) {
      return wecomFileCatRank(a.label) - wecomFileCatRank(b.label);
    });
  }
  function wecomFileFiltered() {
    var q = String(wecomFileQuery || '').trim().toLowerCase();
    var cat = String(wecomFileCat || '');
    return wecomFileAllRows().filter(function (f) {
      if (cat && f.label !== cat) return false;
      if (!q) return true;
      var blob = (f.label + ' ' + f.filename + ' ' + f.sender + ' ' + f.time).toLowerCase();
      return blob.indexOf(q) >= 0;
    });
  }
  function wecomFileRender(d) {
    wecomFileData = d || {};
    wecomFilePage = 1;
    wecomFileChecked = {};
    wecomFileQuery = '';
    wecomFileCat = '';
    wecomFilePaint(true);
  }
  function wecomFilePaint(skipSync) {
    var box = wecomFileBox();
    if (!box) return;
    if (!skipSync) wecomFileSyncChecks();
    var d = wecomFileData || {};
    var upload = d.upload || {};
    var aidEl = document.getElementById('wecomFileAid');
    var urlEl = document.getElementById('wecomFileUrl');
    var keyEl = document.getElementById('wecomFileKey');
    var qEl = document.getElementById('wecomFileQ');
    var catEl = document.getElementById('wecomFileCat');
    var aidKeep = aidEl ? aidEl.value : '';
    var urlKeep = urlEl ? urlEl.value : (upload.baseUrl || '');
    var keyKeep = keyEl ? keyEl.value : '';
    if (qEl) wecomFileQuery = qEl.value || '';
    if (catEl) wecomFileCat = catEl.value || '';
    var all = wecomFileAllRows();
    var cats = [];
    all.forEach(function (f) {
      if (f.label && cats.indexOf(f.label) < 0) cats.push(f.label);
    });
    var rows = wecomFileFiltered();
    var n = rows.length;
    var pages = Math.max(1, Math.ceil(n / WECOM_FILE_PAGE) || 1);
    if (wecomFilePage > pages) wecomFilePage = pages;
    if (wecomFilePage < 1) wecomFilePage = 1;
    var start = (wecomFilePage - 1) * WECOM_FILE_PAGE;
    var slice = rows.slice(start, start + WECOM_FILE_PAGE);
    var catOpts = '<option value="">全部类别</option>' + cats.map(function (c) {
      return '<option value="' + escAttr(c) + '"' + (c === wecomFileCat ? ' selected' : '') + '>' + esc(c) + '</option>';
    }).join('');
    var picked = Object.keys(wecomFileChecked).length;
    var session = wecomState.sessionName || '';
    var countText = n === all.length ? ('共 ' + all.length + ' 个文件') : ('筛选出 ' + n + ' · 共 ' + all.length);
    var html = '';
    if (!all.length) {
      html += '<div class="wecom-empty">当前日期范围内没有文件</div>';
    } else {
      html += '<div class="wecom-files-bar">' +
        '<div class="wecom-files-find">' +
          '<label class="wecom-files-search"><span>搜索</span><input id="wecomFileQ" value="' + escAttr(wecomFileQuery) + '" placeholder="文件名、发送人" autocomplete="off"></label>' +
          '<label class="wecom-files-cat"><span>类别</span><select id="wecomFileCat">' + catOpts + '</select></label>' +
        '</div>' +
        '<div class="wecom-files-meta">' +
          (session ? '<span class="wecom-files-sess" title="' + escAttr(session) + '">' + esc(session) + '</span>' : '') +
          '<span class="count-pill">' + esc(countText) + '</span>' +
          (picked ? '<span class="wecom-files-picked">已选 ' + picked + '</span>' : '') +
        '</div></div>';
      if (!n) {
        html += '<div class="wecom-empty">没有符合搜索或类别的文件</div>';
      } else {
        var catCount = {};
        rows.forEach(function (f) {
          var lab = f.label || '其他';
          catCount[lab] = (catCount[lab] || 0) + 1;
        });
        html += '<div class="wecom-files-list">';
        var prevLab = '';
        slice.forEach(function (f) {
          var lab = f.label || '其他';
          if (lab !== prevLab) {
            if (prevLab) html += '</div>';
            prevLab = lab;
            html += '<div class="wecom-files-sec"><div class="wecom-files-sec-hd"><b>' + esc(lab) + '</b><em>' + catCount[lab] + '</em></div>';
          }
          var key = wecomFileKey(f);
          var on = wecomFileChecked[key] ? ' checked' : '';
          html += '<label class="wecom-files-row"><input type="checkbox" data-file="1" data-key="' + escAttr(key) + '" data-name="' + escAttr(f.filename) + '" data-mid="' + escAttr(f.messageId) + '"' + on + '>' +
            '<span class="tag">' + esc(lab) + '</span>' +
            '<span class="nm" title="' + escAttr(f.filename) + '">' + esc(f.filename || '未命名') + '</span>' +
            '<span class="meta">' + esc(f.sender) + (f.time ? ' · ' + esc(f.time) : '') + '</span>' +
            '<button type="button" class="mini wecom-files-dl" data-name="' + escAttr(f.filename || 'file') + '" data-copies="' + encodeURIComponent(JSON.stringify(f.copies || [])) + '" data-wecom-dl="' + escAttr(f.messageId || '') + '">下载</button></label>';
        });
        if (prevLab) html += '</div>';
        html += '</div>';
        html += '<div id="wecomFilePager" class="book-pager wecom-files-pager' + (n <= WECOM_FILE_PAGE ? ' hidden' : '') + '">' +
          '<button type="button" class="ghost mini" id="wecomFilePrev"' + (wecomFilePage <= 1 ? ' disabled' : '') + '>上一页</button>' +
          '<div class="page-mid"><div class="page-sub">第 ' + wecomFilePage + ' / ' + pages + ' 页</div></div>' +
          '<button type="button" class="ghost mini" id="wecomFileNext"' + (wecomFilePage >= pages ? ' disabled' : '') + '>下一页</button></div>';
      }
    }
    var keepFocus = document.activeElement && document.activeElement.id === 'wecomFileQ';
    if (all.length) {
      html += '<div class="wecom-files-foot">' +
        '<div class="wecom-files-cfg">' +
          '<label>上传接口<input id="wecomFileUrl" value="' + escAttr(urlKeep) + '" placeholder="暂不填写" autocomplete="off"></label>' +
          '<label>Key<input id="wecomFileKey" type="password" value="' + escAttr(keyKeep) + '" placeholder="' + (upload.hasKey ? '已配置，留空不修改' : '暂不填写') + '" autocomplete="off"></label>' +
          '<label>人才编号<input id="wecomFileAid" value="' + escAttr(aidKeep) + '" placeholder="attach_id" autocomplete="off"></label>' +
        '</div>' +
        '<div class="wecom-files-act"><span id="wecomFileMsg"></span><button type="button" class="primary" id="wecomFileUp">上传选中到人才附件</button></div></div>';
    }
    box.innerHTML = html;
    box.hidden = false;
    var prev = document.getElementById('wecomFilePrev');
    var next = document.getElementById('wecomFileNext');
    if (prev) prev.addEventListener('click', function () {
      if (wecomFilePage > 1) { wecomFilePage -= 1; wecomFilePaint(); }
    });
    if (next) next.addEventListener('click', function () {
      if (wecomFilePage < pages) { wecomFilePage += 1; wecomFilePaint(); }
    });
    var qIn = document.getElementById('wecomFileQ');
    if (qIn) qIn.addEventListener('input', function () {
      wecomFileQuery = qIn.value || '';
      wecomFilePage = 1;
      wecomFilePaint();
    });
    var catIn = document.getElementById('wecomFileCat');
    if (catIn) catIn.addEventListener('change', function () {
      wecomFileCat = catIn.value || '';
      wecomFilePage = 1;
      wecomFilePaint();
    });
    box.querySelectorAll('[data-file]').forEach(function (el) {
      el.addEventListener('change', wecomFileSyncChecks);
    });
    box.querySelectorAll('.wecom-files-dl').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        wecomDownload(btn);
      });
    });
    var up = document.getElementById('wecomFileUp');
    if (up) up.addEventListener('click', wecomFileUpload);
    if (qIn && keepFocus) {
      qIn.focus();
      var len = qIn.value.length;
      try { qIn.setSelectionRange(len, len); } catch (e) {}
    }
  }
  function wecomFileToggle() {
    if (!wecomState.sessionId) {
      say(false, '请先选择会话');
      return;
    }
    var box = wecomFileBox();
    wecomFileGo();
    if (!box) {
      say(false, '文件汇总窗口不在本页');
      return;
    }
    box.hidden = false;
    box.innerHTML = '<div class="wecom-empty">正在汇总本会话文件…</div>';
    var dates = wecomSplitDates();
    fetch('/api/wecom/file-summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: wecomState.sessionId,
        source_id: wecomSourceParam() || '',
        start_date: dates.start_date,
        end_date: dates.end_date
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) {
          if (box) box.innerHTML = '<div class="wecom-empty">' + esc((x.j && x.j.detail) || '汇总失败') + '</div>';
          return;
        }
        wecomFileRender(x.j || {});
      }).catch(function (e) {
        if (box) box.innerHTML = '<div class="wecom-empty">' + esc(e.message || '汇总失败') + '</div>';
      });
  }
  function wecomFileUpload() {
    var msg = document.getElementById('wecomFileMsg');
    var aid = ((document.getElementById('wecomFileAid') || {}).value || '').trim();
    wecomFileSyncChecks();
    var files = [];
    Object.keys(wecomFileChecked).forEach(function (k) {
      files.push(wecomFileChecked[k]);
    });
    if (!aid) { if (msg) msg.textContent = '请填写人才编号'; return; }
    if (!files.length) { if (msg) msg.textContent = '请勾选文件'; return; }
    if (msg) msg.textContent = '提交中…';
    fetch('/api/wecom/talent-files', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attachId: aid, files: files })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (msg) msg.textContent = (x.j && (x.j.detail || x.j.message)) || (x.ok ? '已提交' : '上传失败');
      }).catch(function (e) {
        if (msg) msg.textContent = e.message || '上传失败';
      });
  }
  function wecomPickReset(quiet) {
    wecomState.pick = { app: null, opinions: [] };
    wecomState.pageItems = wecomState.pageItems || [];
    if (!quiet) {
      var stream = document.getElementById('wecomStream');
      if (stream) {
        stream.querySelectorAll('.wecom-pick.on').forEach(function (b) { b.classList.remove('on'); });
        stream.querySelectorAll('.wecom-msg').forEach(function (el) { el.classList.remove('is-app', 'is-op'); });
      }
    }
    wecomPickRefreshBar();
  }
  function wecomPickFileRef(m) {
    var copies = wecomCopiesOf(m);
    var fname = wecomFileName(m);
    return {
      kind: 'file',
      message_id: m.message_id || (copies[0] && copies[0].message_id) || 0,
      filename: fname,
      copies: copies,
      sender: m.sender || '',
      time: m.time_text || '',
      session_id: wecomState.sessionId || ''
    };
  }
  function wecomPickTextRef(m) {
    var copies = wecomCopiesOf(m);
    var fname = wecomFileName(m);
    var hasThumb = !!(wecomIsImage(m) && m.media_url);
    var text = wecomCaption(m, fname, hasThumb || wecomHasFile(m)) || String(m.text || m.snippet || '').trim();
    return {
      kind: 'text',
      message_id: m.message_id || (copies[0] && copies[0].message_id) || 0,
      filename: '群聊修改意见.txt',
      text: text,
      sender: m.sender || '',
      time: m.time_text || ''
    };
  }
  function wecomPickDropOp(mid, kind) {
    wecomState.pick.opinions = (wecomState.pick.opinions || []).filter(function (o) {
      if (String(o.message_id) !== String(mid)) return true;
      if (kind === 'text') return o.kind !== 'text';
      return o.kind === 'text';
    });
  }
  function wecomPickToggle(btn) {
    var wrap = btn.closest('.wecom-msg');
    var i = wrap ? parseInt(wrap.getAttribute('data-i'), 10) : -1;
    var m = (wecomState.pageItems || [])[i];
    if (!m) return;
    var action = btn.getAttribute('data-pick');
    var mid = wecomPickMid(m);
    wecomState.pick = wecomState.pick || { app: null, opinions: [] };
    if (action === 'app') {
      if (wecomPickIsApp(m)) wecomState.pick.app = null;
      else {
        wecomState.pick.app = wecomPickFileRef(m);
        wecomPickDropOp(mid, 'file');
      }
    } else if (action === 'op-file') {
      if (wecomPickIsOpFile(m)) wecomPickDropOp(mid, 'file');
      else {
        if (wecomPickIsApp(m)) wecomState.pick.app = null;
        wecomState.pick.opinions.push(wecomPickFileRef(m));
      }
    } else if (action === 'op-text') {
      if (wecomPickIsOpText(m)) wecomPickDropOp(mid, 'text');
      else wecomState.pick.opinions.push(wecomPickTextRef(m));
    }
    var stream = document.getElementById('wecomStream');
    if (stream && wecomState.pageItems) renderWecomMsgs(stream, wecomState.pageItems, wecomState.searchMode, true);
    else wecomPickRefreshBar();
  }
  function wecomPickEnsureBar() {
    var chat = document.querySelector('.wecom-chat');
    if (!chat) return null;
    var bar = document.getElementById('wecomPickBar');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.id = 'wecomPickBar';
    bar.className = 'wecom-pickbar';
    bar.hidden = true;
    bar.innerHTML = '<div class="wecom-pickbar-sum" id="wecomPickSum"></div>' +
      '<button type="button" class="mini" id="wecomPickClear">清空</button>' +
      '<button type="button" class="mini primary" id="wecomPickUpload">上传任务</button>';
    var pager = document.getElementById('wecomPager');
    if (pager && pager.parentNode === chat) chat.insertBefore(bar, pager);
    else chat.appendChild(bar);
    document.getElementById('wecomPickClear').addEventListener('click', function () { wecomPickReset(); });
    document.getElementById('wecomPickUpload').addEventListener('click', wecomPickUpload);
    return bar;
  }
  function wecomPickRefreshBar() {
    var bar = wecomPickEnsureBar();
    if (!bar) return;
    var pick = wecomState.pick || { app: null, opinions: [] };
    var ops = pick.opinions || [];
    var nFile = ops.filter(function (o) { return o.kind !== 'text'; }).length;
    var nText = ops.filter(function (o) { return o.kind === 'text'; }).length;
    var has = !!(pick.app || ops.length);
    bar.hidden = !has;
    var sum = document.getElementById('wecomPickSum');
    if (!sum) return;
    if (!has) { sum.textContent = ''; return; }
    var parts = [];
    parts.push(pick.app ? ('申报书：' + (pick.app.filename || '已选')) : '未选申报书');
    if (ops.length) parts.push('修改意见 ' + ops.length + ' 条' + (nFile || nText ? '（' + [nFile ? nFile + ' 个文件' : '', nText ? nText + ' 段文本' : ''].filter(Boolean).join(' · ') + '）' : ''));
    else parts.push('未选修改意见（将读申报书标注栏）');
    sum.textContent = parts.join('　·　');
  }
  function wecomPickUpload() {
    var pick = wecomState.pick || {};
    if (!pick.app) {
      say(false, '请先点一条文件消息上的「申报书」');
      return;
    }
    if (!wecomState.sessionId) {
      say(false, '请先选择一个会话');
      return;
    }
    var ops = pick.opinions || [];
    var hint = '上传申报书「' + (pick.app.filename || '') + '」';
    if (ops.length) hint += '，以及 ' + ops.length + ' 条修改意见';
    else hint += '（未选修改意见，将尝试申报书标注栏）';
    hint += '。生成计划后需人工确认才会写入。确定？';
    if (!window.confirm(hint)) return;
    wecomPickDoUpload(false);
  }
  function wecomPickDoUpload(force) {
    var pick = wecomState.pick || {};
    var btn = document.getElementById('wecomPickUpload');
    if (btn) { btn.disabled = true; btn.textContent = '正在取文件…'; }
    fetch('/api/wecom/manual-create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        session_id: wecomState.sessionId,
        session_name: wecomState.sessionName || '',
        source_id: wecomSourceParam() || '',
        app: pick.app,
        opinions: pick.opinions || [],
        force: !!force
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); })
      .then(function (x) {
        var d = x.j || {};
        if (d.needForce && d.errors && d.errors[0] && d.errors[0].existing) {
          var ex = d.errors[0].existing;
          var again = window.confirm('该会话的文件已经上传过（任务 ' + (ex.id || '') + '）。仍要再传一份？');
          if (again) {
            wecomPickDoUpload(true);
            return { skipEnable: true };
          }
          say(false, '已取消。可打开已有任务查看。');
          return;
        }
        var created = (d.created || [])[0];
        if (!x.ok || d.ok === false || !created) {
          say(false, (d.errors && d.errors[0] && d.errors[0].detail) || d.detail || '上传失败');
          return;
        }
        var extra = (d.skipped || []).length
          ? '；部分意见未缓存：' + d.skipped.map(function (s) { return s.filename; }).join('、')
          : '';
        say(true, '已上传到任务列表' + extra + '（生成计划后需确认）');
        wecomPickReset();
        if (typeof loadOverview === 'function') loadOverview();
        if (created.url) {
          var msg = document.getElementById('wecomMsg');
          if (msg) {
            msg.innerHTML = '已上传 <a href="' + escAttr(created.url) + '">' + esc(created.filename || created.id) + '</a>，请到任务列表查看。';
            msg.className = 'msg ok';
          }
        }
      }).catch(function (e) {
        say(false, e.message || '上传失败');
      }).then(function (flag) {
        if (flag && flag.skipEnable) return;
        if (btn) { btn.disabled = false; btn.textContent = '上传任务'; }
      });
  }
  function wecomLocateBox() {
    var box = document.getElementById('wecomLocate');
    if (box) return box;
    box = document.createElement('div');
    box.id = 'wecomLocate';
    box.className = 'wecom-locate';
    box.hidden = true;
    box.innerHTML = '<div class="wecom-locate-card" role="dialog">' +
      '<div class="wecom-locate-hd"><h3>定位申报书修改版本</h3><div class="grow"></div>' +
      '<div class="wecom-locate-win">时间窗 ' +
      '<button type="button" class="mini" data-win="1">当天</button>' +
      '<button type="button" class="mini active" data-win="7">7 天</button>' +
      '<button type="button" class="mini" data-win="30">30 天</button></div>' +
      '<button type="button" class="ghost mini" data-close="1">关闭</button></div>' +
      '<div class="wecom-locate-keys" id="wecomLocateKeys"></div>' +
      '<div id="wecomLocateBody"><div class="wecom-empty">正在对照任务…</div></div></div>';
    box.addEventListener('click', function (e) {
      if (e.target === box || (e.target && e.target.getAttribute && e.target.getAttribute('data-close'))) box.hidden = true;
    });
    box.querySelectorAll('[data-win]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var days = Number(btn.getAttribute('data-win') || 7) || 7;
        box.querySelectorAll('[data-win]').forEach(function (b) { b.classList.toggle('active', b === btn); });
        wecomLocateRun(box._payload || {}, days);
      });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !box.hidden) box.hidden = true;
    });
    document.body.appendChild(box);
    return box;
  }
  function wecomFileHrefTask(tid, name, stamp) {
    var url = '/api/tasks/' + encodeURIComponent(tid) + '/files?dir=' + (stamp ? 'versions' : 'output') +
      '&name=' + encodeURIComponent(name);
    if (stamp) url += '&stamp=' + encodeURIComponent(stamp);
    return url;
  }
  function wecomLocate(btn) {
    var payload = wecomParseJsonAttr(btn, 'data-wecom-locate');
    if (!payload || Array.isArray(payload)) payload = {};
    var box = wecomLocateBox();
    box._payload = payload;
    box.querySelectorAll('[data-win]').forEach(function (b) {
      b.classList.toggle('active', String(b.getAttribute('data-win')) === '7');
    });
    wecomLocateRun(payload, 7);
  }
  function wecomLocateRun(payload, windowDays) {
    var box = wecomLocateBox();
    var keysEl = document.getElementById('wecomLocateKeys');
    var bodyEl = document.getElementById('wecomLocateBody');
    box.hidden = false;
    keysEl.textContent = '正在抽取姓名 / 文件名 / 时间…';
    bodyEl.innerHTML = '<div class="wecom-empty">正在对照任务…</div>';
    fetch('/api/wecom/locate-task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        time_text: payload.time_text || '',
        sender: payload.sender || '',
        text: payload.text || '',
        filename: payload.filename || '',
        session_name: payload.session_name || '',
        windowDays: windowDays || 7
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var d = x.j || {};
        var k = d.keys || {};
        keysEl.innerHTML = '文件：<b>' + esc(k.filename || '（无）') + '</b>　·　姓名：' + esc((k.names || []).join(' / ') || '（未识别）') +
          '　·　编号：' + esc((k.nums || []).join('、') || '（无）') +
          '<br>消息时间：' + esc(k.time || '（无）') + (k.sender ? '　·　发送人：' + esc(k.sender) : '') +
          (k.group ? '　·　会话：' + esc(k.group) : '') +
          '　·　时间窗 ±' + esc(d.windowDays || 7) + ' 天';
        var copies = payload.copies || [];
        var chatBtns = copies.length
          ? '<div class="wecom-locate-item"><div class="ttl">会话里当时发的文件</div>' +
            '<div class="meta">' + esc(k.filename || payload.filename || '聊天文件') + ' · 走企业微信缓存，未打开过则下不了</div>' +
            '<div class="acts"><button type="button" class="mini" data-wecom-dl="' + escAttr((copies[0] && copies[0].message_id) || '') +
            '" data-name="' + escAttr(k.filename || payload.filename || 'chat-file') +
            '" data-copies="' + encodeURIComponent(JSON.stringify(copies)) + '">下载会话文件</button></div></div>'
          : '';
        if (!x.ok) {
          bodyEl.innerHTML = chatBtns + '<div class="wecom-empty">' + esc(d.detail || '定位失败') + '</div>';
        } else if (!(d.items || []).length) {
          bodyEl.innerHTML = chatBtns + '<div class="wecom-empty">' + esc(d.hint || '没有匹配的修改任务') + '</div>';
        } else {
          var html = chatBtns + (d.items || []).map(function (it) {
            var conf = it.confidence || 'low';
            var confLab = { high: '较有把握', medium: '需核对', low: '弱匹配' }[conf] || conf;
            var dls = (it.deliverables || []).map(function (f) {
              return '<a class="mini" href="' + wecomFileHrefTask(it.id, f.name) + '" download>' + esc(f.kind === 'edited' ? '修改后' : (f.kind === 'backup' ? '备份' : f.name)) + '</a>';
            }).join('');
            var vers = (it.versions || []).slice().reverse().map(function (v) {
              var links = (v.files || []).filter(function (n) { return /修改后|备份/.test(n); }).map(function (n) {
                return '<a class="mini" href="' + wecomFileHrefTask(it.id, n, v.stamp) + '" download>' + esc(n) + '</a>';
              }).join('');
              return '<div class="meta">快照 ' + esc(v.at || v.stamp) + ' ' + links + '</div>';
            }).join('');
            return '<div class="wecom-locate-item"><div class="ttl">' + esc(it.appName || it.id) +
              '<span class="wecom-conf ' + escAttr(conf) + '">' + esc(confLab) + ' · ' + esc(it.score) + '</span></div>' +
              '<div class="meta">' + badge(it.status) + '　' + esc(it.personName || '') +
              (it.attachId ? ' · ' + esc(it.attachId) : '') +
              '<br>创建 ' + esc(it.createdAt || '—') + (it.appliedAt ? '　写入 ' + esc(it.appliedAt) : '') +
              (it.owner ? '　提交人 ' + esc(it.owner) : '') + '</div>' +
              '<div class="why">' + esc((it.reasons || []).join('；')) + '</div>' +
              '<div class="acts"><a class="mini primary" href="' + escAttr(it.url || ('/t/' + it.id)) + '" target="_blank" rel="noopener">打开任务</a>' + dls + '</div>' +
              vers + '</div>';
          }).join('');
          bodyEl.innerHTML = html;
        }
        bodyEl.querySelectorAll('[data-wecom-dl]').forEach(function (b) {
          b.addEventListener('click', function () { wecomDownload(b); });
        });
      }).catch(function (e) {
        bodyEl.innerHTML = '<div class="wecom-empty">' + esc(e.message || '定位失败') + '</div>';
      });
  }
  function wecomSplitBox() {
    var box = document.getElementById('wecomSplit');
    if (box) return box;
    box = document.createElement('div');
    box.id = 'wecomSplit';
    box.className = 'wecom-locate';
    box.hidden = true;
    box.innerHTML = '<div class="wecom-locate-card" role="dialog">' +
      '<div class="wecom-locate-hd"><h3>拆解会话为申报任务</h3><div class="grow"></div>' +
      '<button type="button" class="ghost mini" data-close="1">关闭</button></div>' +
      '<div class="wecom-locate-keys" id="wecomSplitKeys"></div>' +
      '<div id="wecomSplitBody"><div class="wecom-empty">正在拆解…</div></div></div>';
    box.addEventListener('click', function (e) {
      var t = e.target;
      if (t && t.getAttribute && t.getAttribute('data-close')) box.hidden = true;
    });
    document.body.appendChild(box);
    return box;
  }
  function wecomSplitDates() {
    return {
      start_date: (document.getElementById('wecomStart') || {}).value || '',
      end_date: (document.getElementById('wecomEnd') || {}).value || ''
    };
  }
  function wecomSplitOpen() {
    if (!wecomState.sessionId) {
      say(false, '请先选择一个会话');
      return;
    }
    var box = wecomSplitBox();
    var keysEl = document.getElementById('wecomSplitKeys');
    var bodyEl = document.getElementById('wecomSplitBody');
    box.hidden = false;
    keysEl.textContent = '正在按申报书文件拆解，并用 Gemini 协助定位修改意见…';
    bodyEl.innerHTML = '<div class="wecom-empty">规则拆解后将调用 Gemini，请稍候…</div>';
    var dates = wecomSplitDates();
    fetch('/api/wecom/split-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: wecomState.sessionId,
        session_name: wecomState.sessionName || '',
        source_id: wecomSourceParam() || '',
        start_date: dates.start_date,
        end_date: dates.end_date,
        windowHours: 48
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var d = x.j || {};
        box._preview = d;
        keysEl.innerHTML = '会话：<b>' + esc(d.session_name || wecomState.sessionName || '') + '</b>　·　日期 ' +
          esc(d.start_date || '—') + ' ~ ' + esc(d.end_date || '—') +
          '　·　消息 ' + esc(d.messageCount || 0) +
          (d.fileCount != null ? '　·　文件 ' + esc(d.fileCount) : '') +
          '　·　申报书 ' + esc(d.count || 0) +
          (d.opinionFileCount ? '　·　意见文档 ' + esc(d.opinionFileCount) : '') +
          (d.ignoredFileCount ? '　·　未当作申报书 ' + esc(d.ignoredFileCount) : '') +
          '（可上传 ' + esc(d.ready || 0) + '）' +
          (d.gemini ? '　·　' + esc(d.gemini) : '') +
          (d.hint ? '<div class="why" style="margin-top:8px">' + esc(d.hint) +
            (d.ignoredSample && d.ignoredSample.length
              ? '　例如：' + esc(d.ignoredSample.join('、'))
              : '') + '</div>' : '');
        if (!x.ok) {
          bodyEl.innerHTML = '<div class="wecom-empty">' + esc(d.detail || '拆解失败') + '</div>';
          return;
        }
        var cases = d.cases || [];
        if (!cases.length) {
          bodyEl.innerHTML = '<div class="wecom-empty">' + esc(d.hint || '没有识别到申报书') + '</div>';
          return;
        }
        bodyEl.innerHTML = cases.map(function (c) {
          var app = c.app || {};
          var ops = (c.opinions || []).map(function (o) {
            if (o.kind === 'text') return '会话文本「' + (o.filename || '群聊修改意见.txt') + '」';
            return '文档 ' + (o.filename || '');
          }).join('；');
          var exist = c.existing
            ? ('<a class="mini" href="' + escAttr(c.existing.url || ('/t/' + c.existing.id)) + '" target="_blank" rel="noopener">已有任务</a> ' + badge(c.existing.status))
            : (c.similar ? ('<a class="mini" href="' + escAttr(c.similar.url || ('/t/' + c.similar.id)) + '" target="_blank" rel="noopener">相近任务</a>') : '');
          var cached = !!(c.ready || app.cached || (app.copies && app.copies.length));
          var upLabel = c.existing ? '重新上传' : '上传';
          var upBtn = cached
            ? '<button type="button" class="mini primary" data-split-up="' + escAttr(c.id) + '" data-up-label="' + escAttr(upLabel) + '">' + esc(upLabel) + '</button>'
            : '';
          return '<div class="wecom-locate-item" data-case-row="' + escAttr(c.id) + '"><div class="ttl">' +
            esc(app.filename || c.id) + '</div>' +
            '<div class="meta">' + esc(app.time || '') + (app.sender ? '　' + esc(app.sender) : '') +
            (c.names && c.names.length ? '　姓名 ' + esc(c.names.join(' / ')) : '') +
            '<br>修改意见：' + esc(ops || '（无，将尝试申报书标注栏）') +
            (app.cached ? '' : '　未缓存') + '</div>' +
            (c.warnings && c.warnings.length ? '<div class="why">' + esc(c.warnings.join('；')) + '</div>' : '') +
            '<div class="acts">' + exist + upBtn + '</div></div>';
        }).join('');
        bodyEl.querySelectorAll('[data-split-up]').forEach(function (btn) {
          btn.addEventListener('click', function () { wecomSplitCreate(btn); });
        });
      }).catch(function (e) {
        bodyEl.innerHTML = '<div class="wecom-empty">' + esc(e.message || '拆解失败') + '</div>';
      });
  }
  function wecomSplitCreate(btn) {
    var cid = (btn && btn.getAttribute && btn.getAttribute('data-split-up')) || '';
    if (!cid) {
      say(false, '没有可上传的申报书');
      return;
    }
    var row = btn.closest('[data-case-row]') || btn.parentElement;
    var acts = row && row.querySelector('.acts');
    function showFail(text) {
      btn.disabled = false;
      btn.textContent = btn.getAttribute('data-up-label') || '上传';
      if (row) {
        var why = row.querySelector('[data-up-err]');
        if (!why) {
          why = document.createElement('div');
          why.className = 'why';
          why.setAttribute('data-up-err', '1');
          row.appendChild(why);
        }
        why.textContent = text;
      }
      say(false, text);
    }
    btn.disabled = true;
    btn.textContent = '正在取文件…';
    var previewCases = ((wecomSplitBox()._preview || {}).cases) || [];
    var one = null;
    for (var i = 0; i < previewCases.length; i++) {
      if (String(previewCases[i].id) === String(cid)) { one = previewCases[i]; break; }
    }
    var dates = wecomSplitDates();
    fetch('/api/wecom/split-create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        session_id: wecomState.sessionId,
        session_name: wecomState.sessionName || '',
        source_id: wecomSourceParam() || '',
        start_date: dates.start_date,
        end_date: dates.end_date,
        windowHours: 48,
        caseIds: [cid],
        case: one,
        force: true
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var d = x.j || {};
        var created = (d.created || [])[0];
        var err = (d.errors || [])[0];
        var skipped = d.skipped || [];
        if (!x.ok || d.ok === false || !created) {
          showFail((err && err.detail) || d.detail || '没有成功创建任务');
          return;
        }
        var extra = skipped.length ? '<div class="meta">部分意见未缓存：' + esc(skipped.map(function (s) { return s.filename; }).join('、')) + '</div>' : '';
        if (acts) {
          acts.innerHTML = '<a class="mini primary" href="' + escAttr(created.url) + '" target="_blank" rel="noopener">打开任务</a>' +
            '<span class="wecom-conf high">已上传</span>';
        }
        if (extra && row) row.insertAdjacentHTML('beforeend', extra);
        say(true, '已上传到任务列表，请到首页查看（生成计划后需确认）');
        if (typeof loadOverview === 'function') loadOverview();
      }).catch(function (e) {
        showFail(e.message || '上传失败');
      });
  }
  function wecomDownload(btn) {
    var name = btn.getAttribute('data-name') || 'chat-file';
    var copies = wecomSortCopies(wecomParseJsonAttr(btn, 'data-copies'));
    var mid = Number(btn.getAttribute('data-wecom-dl') || 0);
    if (!copies.length) copies = wecomCopiesOf({ message_id: mid });
    var wrap = btn.closest('.wecom-file') || btn.parentElement;
    if (!copies.length) {
      wecomSetFileStatus(btn, '没有可查询的电脑副本', 'bad');
      return;
    }
    btn.disabled = true;
    var idx = 0;
    var lastDetail = '';
    function failAll() {
      btn.textContent = btn.classList.contains('wecom-orig') ? '原文件未缓存' : '未缓存';
      btn.disabled = false;
      wecomSetFileStatus(btn, lastDetail || (wecomReplicaNames(copies) + ' 均未缓存。需在对应电脑企业微信里打开过。'), 'bad');
    }
    function saveBlob(blob, label, copyIdx) {
      var href = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
      btn.textContent = '已下载';
      btn.disabled = false;
      wecomSetPcState(wrap, copyIdx, 'hit');
      wecomSetFileStatus(btn, '已从「' + label + '」下载。', 'ok');
    }
    function tryNext() {
      if (idx >= copies.length) {
        failAll();
        return;
      }
      var copyIdx = idx;
      var c = copies[idx++];
      var label = c.source_label || c.source_id || '';
      var url = wecomFileHref(c);
      if (!url) {
        wecomSetPcState(wrap, copyIdx, 'miss');
        tryNext();
        return;
      }
      wecomSetPcState(wrap, copyIdx, 'wait');
      btn.textContent = (copyIdx + 1) + '/' + copies.length;
      wecomSetFileStatus(btn, '正在查「' + label + '」缓存（' + (copyIdx + 1) + '/' + copies.length + '）…', 'wait');
      var deadline = Date.now() + 90000;
      var hung = setTimeout(function () {
        wecomSetFileStatus(btn, '「' + label + '」还在等助手回传，未卡住…', 'wait');
      }, 8000);
      function tick() {
        var ac = window.AbortController ? new AbortController() : null;
        var kill = setTimeout(function () { if (ac) ac.abort(); }, 25000);
        fetch(url, ac ? { signal: ac.signal } : {}).then(function (r) {
          clearTimeout(kill);
          var ct = (r.headers.get('content-type') || '').toLowerCase();
          if (r.status === 202) {
            clearTimeout(hung);
            wecomSetFileStatus(btn, '已通知「' + label + '」助手回传，正在等文件…', 'wait');
            if (Date.now() > deadline) {
              lastDetail = '「' + label + '」等待超时。请确认该电脑企业微信助手在线，并打开过此文件。';
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
              return;
            }
            setTimeout(tick, 2000);
            return;
          }
          clearTimeout(hung);
          if (r.status === 404 || (r.ok && ct.indexOf('json') >= 0)) {
            return r.json().then(function (j) {
              lastDetail = (j && j.detail) || ('「' + label + '」未缓存');
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            }).catch(function () {
              lastDetail = '「' + label + '」未缓存';
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            });
          }
          if (!r.ok) {
            return r.json().then(function (j) {
              lastDetail = (j && j.detail) || ('「' + label + '」HTTP ' + r.status);
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            }).catch(function () {
              lastDetail = '「' + label + '」失败';
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            });
          }
          return r.blob().then(function (blob) {
            if (!blob || blob.size < 4) {
              lastDetail = '「' + label + '」空文件';
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
              return;
            }
            saveBlob(blob, label, copyIdx);
          });
        }).catch(function (e) {
          clearTimeout(kill);
          clearTimeout(hung);
          var aborted = e && (e.name === 'AbortError' || /abort/i.test(String(e.message || '')));
          lastDetail = aborted
            ? ('「' + label + '」查询超时')
            : (e.message || ('「' + label + '」请求失败'));
          wecomSetPcState(wrap, copyIdx, 'miss');
          tryNext();
        });
      }
      tick();
    }
    tryNext();
  }
  function wecomBoardEl() {
    return document.querySelector('.wecom-board');
  }
  function wecomAiReset() {
    wecomState.aiKey = '';
    var panel = document.getElementById('wecomAiPanel');
    var body = document.getElementById('wecomAiBody');
    var sub = document.getElementById('wecomAiSub');
    if (panel) panel.hidden = true;
    if (wecomBoardEl()) wecomBoardEl().classList.remove('has-ai');
    wecomAiTab = 'read';
    wecomAiSyncTabs();
    if (body) body.innerHTML = '<div class="wecom-empty">选择会话后点「AI 解读」，将扫描申报书、修改意见并生成摘要。</div>';
    if (sub) sub.textContent = 'Gemini 读取当前日期窗内的会话';
  }
  function wecomAiOpen() {
    var panel = document.getElementById('wecomAiPanel');
    if (panel) panel.hidden = false;
    if (wecomBoardEl()) wecomBoardEl().classList.add('has-ai');
  }
  function wecomAiSyncTabs() {
    document.querySelectorAll('.wecom-ai-tab').forEach(function (btn) {
      btn.classList.toggle('on', btn.getAttribute('data-tab') === wecomAiTab);
    });
  }
  function wecomAiSetTab(tab) {
    wecomAiTab = tab === 'log' ? 'log' : 'read';
    wecomAiSyncTabs();
    if (wecomAiTab === 'log') wecomAiLoadHistory();
    else if (wecomAiLastRead) wecomAiRender(wecomAiLastRead);
    else {
      var body = document.getElementById('wecomAiBody');
      if (body) body.innerHTML = '<div class="wecom-empty">点「AI 解读」将解读当前会话，配对申报书与修改意见并生成摘要。</div>';
    }
  }
  function wecomAiLoadHistory() {
    var body = document.getElementById('wecomAiBody');
    if (!body) return;
    body.innerHTML = '<div class="wecom-empty">加载解读记录…</div>';
    var url = '/api/wecom/ai-reads?limit=50';
    if (wecomState.sessionId) url += '&session_id=' + encodeURIComponent(wecomState.sessionId);
    wecomQuery(url).then(function (res) {
      if (!res.ok) {
        body.innerHTML = '<div class="wecom-empty">' + esc((res.data && res.data.detail) || '加载失败') + '</div>';
        return;
      }
      var items = (res.data && res.data.items) || [];
      if (!items.length) {
        body.innerHTML = '<div class="wecom-empty">' + (wecomState.sessionId ? '当前会话还没有解读记录。' : '还没有解读记录。') + '</div>';
        return;
      }
      body.innerHTML = items.map(function (it) {
        var tag = it.hasOpinion ? '<span class="wecom-ai-tag ok">有意见</span>' : '<span class="wecom-ai-tag">无意见</span>';
        if (it.readyCount) tag += '<span class="wecom-ai-tag ok">可上传 ' + esc(it.readyCount) + '</span>';
        return '<button type="button" class="wecom-ai-log-item" data-id="' + escAttr(it.id || '') + '">' +
          '<div class="ttl">' + esc(it.session_name || it.session_id || '未命名会话') + '</div>' +
          '<div class="meta">' + esc(it.at || '') + (it.user ? ' · ' + esc(it.user) : '') + '<br>' +
          esc(it.start_date || '—') + ' ~ ' + esc(it.end_date || '—') +
          ' · 消息 ' + esc(it.messageCount || 0) + ' · ' + tag + '</div>' +
          (it.summaryText ? '<div class="snip">' + esc(it.summaryText) + '</div>' : '') +
          '</button>';
      }).join('');
      body.querySelectorAll('.wecom-ai-log-item').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var id = btn.getAttribute('data-id') || '';
          if (!id) return;
          body.innerHTML = '<div class="wecom-empty">加载记录…</div>';
          wecomQuery('/api/wecom/ai-reads/' + encodeURIComponent(id)).then(function (r) {
            if (!r.ok) {
              body.innerHTML = '<div class="wecom-empty">' + esc((r.data && r.data.detail) || '记录不存在') + '</div>';
              return;
            }
            wecomAiTab = 'read';
            wecomAiSyncTabs();
            wecomAiRender(r.data || {});
          });
        });
      });
    }).catch(function (e) {
      body.innerHTML = '<div class="wecom-empty">' + esc(e.message || '加载失败') + '</div>';
    });
  }
  function wecomAiOpenLog() {
    wecomAiOpen();
    wecomAiSetTab('log');
  }
  function wecomAiConfTag(conf) {
    var c = String(conf || '').toLowerCase();
    if (c === 'high') return '<span class="wecom-ai-tag ok">高置信</span>';
    if (c === 'medium') return '<span class="wecom-ai-tag warn">中置信</span>';
    if (c === 'low') return '<span class="wecom-ai-tag">低置信</span>';
    return '';
  }
  function wecomAiRender(d) {
    var body = document.getElementById('wecomAiBody');
    var sub = document.getElementById('wecomAiSub');
    if (!body) return;
    var isWatch = d.scope === 'watch';
    var scan = d.scan || {};
    var sum = d.summary || {};
    var groups = d.groups || [];
    if (isWatch && groups.length) {
      scan = {
        fileCount: 0,
        appFileCount: 0,
        opinionFileCount: 0
      };
      groups.forEach(function (g) {
        var s = g.scan || {};
        scan.fileCount += s.fileCount || 0;
        scan.appFileCount += s.appFileCount || 0;
        scan.opinionFileCount += s.opinionFileCount || 0;
      });
    }
    if (sub) {
      var title = isWatch
        ? ('关注会话 ' + (d.groupCount || groups.length || 0) + ' 个')
        : (d.session_name || wecomState.sessionName || '');
      var bits = [title, (d.start_date || '—') + ' ~ ' + (d.end_date || '—')];
      if (d.recordAt) bits.push('已保存 ' + d.recordAt);
      if (d.engine) bits.push(d.engine);
      if (d.model) bits.push(d.model);
      sub.textContent = bits.filter(Boolean).join(' · ');
    }
    var stats = '<div class="wecom-ai-stat">' +
      (isWatch ? '<span class="wecom-ai-tag">会话 ' + esc(d.groupCount || groups.length || 0) + '</span>' : '') +
      '<span class="wecom-ai-tag">消息 ' + esc(d.messageCount || 0) + '</span>' +
      '<span class="wecom-ai-tag">文件 ' + esc(scan.fileCount || 0) + '</span>' +
      '<span class="wecom-ai-tag">申报书 ' + esc(scan.appFileCount || 0) + '</span>' +
      '<span class="wecom-ai-tag">意见文档 ' + esc(scan.opinionFileCount || 0) + '</span>' +
      (d.readyCount ? '<span class="wecom-ai-tag ok">可上传 ' + esc(d.readyCount) + '</span>' : '') +
      '</div>';
    var hasOp = d.hasOpinion || (d.opinion && d.opinion.hasOpinion);
    var opBlock = '<div class="wecom-ai-card"><h5>配对扫描 ' +
      (hasOp ? '<span class="wecom-ai-tag ok">有修改意见</span>' : '<span class="wecom-ai-tag">暂无配对</span>') +
      '</h5>';
    if (d.geminiNote) opBlock += '<p>' + esc(d.geminiNote) + '</p>';
    if (isWatch && groups.length) {
      opBlock += '<ul class="wecom-ai-list">' + groups.map(function (g) {
        var s = g.scan || {};
        return '<li><b>' + esc(g.session_name || '') + '</b> · 消息 ' + esc(g.messageCount || 0) +
          ' · 申报书 ' + esc(s.appFileCount || 0) +
          (g.readyCount ? ' · 可上传 ' + esc(g.readyCount) : '') +
          (g.geminiNote ? '<br><span style="color:#8a93a6">' + esc(g.geminiNote) + '</span>' : '') +
          '</li>';
      }).join('') + '</ul>';
    }
    if (d.errors && d.errors.length) {
      opBlock += '<p style="color:var(--bad);margin-top:6px">' + esc(d.errors.join('；')) + '</p>';
    }
    opBlock += '</div>';
    var sumBlock = '<div class="wecom-ai-card"><h5>摘要</h5><p>' + esc(sum.summary || '—') + '</p>';
    if (sum.highlights && sum.highlights.length) {
      sumBlock += '<ul class="wecom-ai-list">' + sum.highlights.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>';
    }
    sumBlock += '</div>';
    var riskBlock = '';
    if ((sum.risks && sum.risks.length) || sum.suggestedAction) {
      riskBlock = '<div class="wecom-ai-card"><h5>待办与建议</h5>';
      if (sum.risks && sum.risks.length) {
        riskBlock += '<ul class="wecom-ai-list">' + sum.risks.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>';
      }
      if (sum.suggestedAction) riskBlock += '<p style="margin-top:6px">' + esc(sum.suggestedAction) + '</p>';
      riskBlock += '</div>';
    }
    var cases = d.cases || [];
    var caseBlock = '';
    if (cases.length) {
      caseBlock = '<div class="wecom-ai-card"><h5>申报书配对（' + cases.length + '）</h5>' +
        cases.map(function (c) {
          var st = c.existing ? '<span class="wecom-ai-tag">已有任务</span>'
            : (c.similar ? '<span class="wecom-ai-tag warn">相近任务</span>'
              : (c.ready ? '<span class="wecom-ai-tag ok">可上传</span>' : '<span class="wecom-ai-tag bad">未缓存</span>'));
          var ops = (c.opinions || []).map(function (o) {
            return o.kind === 'text' ? '会话文本' : (o.filename || '意见文档');
          }).join('、');
          var grp = c.session_name ? '<span style="color:#8a93a6">' + esc(c.session_name) + ' · </span>' : '';
          return '<div class="wecom-ai-case">' + grp + '<b>' + esc(c.filename || c.id) + '</b> ' + st +
            '<div style="color:#8a93a6;margin-top:4px">' + esc(c.time || '') +
            (ops ? ' · 意见：' + esc(ops) : ' · 无配对意见') + '</div></div>';
        }).join('') + '</div>';
    }
    var acts = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">' +
      '<button type="button" class="mini primary" id="wecomAiSplitGo">拆解任务</button>' +
      '<button type="button" class="mini" id="wecomAiRefresh">重新解读</button>' +
      '<button type="button" class="mini" id="wecomAiGoLog">解读记录</button></div>';
    body.innerHTML = stats + sumBlock + opBlock + riskBlock + caseBlock + acts;
    var splitGo = document.getElementById('wecomAiSplitGo');
    if (splitGo) splitGo.addEventListener('click', wecomSplitOpen);
    var refresh = document.getElementById('wecomAiRefresh');
    if (refresh) refresh.addEventListener('click', wecomAiRead);
    var goLog = document.getElementById('wecomAiGoLog');
    if (goLog) goLog.addEventListener('click', function () { wecomAiSetTab('log'); });
    wecomAiLastRead = d;
    wecomAiTab = 'read';
    wecomAiSyncTabs();
    wecomState.aiKey = [d.scope || 'session', d.session_id, d.start_date, d.end_date, d.messageCount, hasOp].join('\t');
  }
  function wecomAiRead() {
    if (!wecomState.sessionId) {
      say(false, '请先选择一个会话，再解读当前对话');
      return;
    }
    wecomAiOpen();
    wecomAiTab = 'read';
    wecomAiSyncTabs();
    var body = document.getElementById('wecomAiBody');
    var sub = document.getElementById('wecomAiSub');
    var btn = document.getElementById('wecomAiBtn');
    var name = wecomState.sessionName || wecomState.sessionId;
    if (body) body.innerHTML = '<div class="wecom-empty">正在解读当前会话…<br><span style="font-size:12px;color:#8a93a6">' + esc(name) + ' · 拉取本会话记录 → 规则拆解 → Gemini 配对申报书与修改意见</span></div>';
    if (sub) sub.textContent = '分析中 · ' + name;
    wecomAiLoading = true;
    if (btn) btn.disabled = true;
    var dates = wecomSplitDates();
    fetch('/api/wecom/ai-read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: 'session',
        session_id: wecomState.sessionId,
        session_name: wecomState.sessionName || '',
        source_id: wecomSourceParam() || '',
        start_date: dates.start_date,
        end_date: dates.end_date,
        windowHours: 48
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var d = x.j || {};
        if (!x.ok) {
          if (body) body.innerHTML = '<div class="wecom-empty">' + esc(d.detail || 'AI 解读失败') + '</div>';
          say(false, d.detail || 'AI 解读失败');
          return;
        }
        wecomAiRender(d);
        say(true, 'AI 解读完成');
      }).catch(function (e) {
        if (body) body.innerHTML = '<div class="wecom-empty">' + esc(e.message || '请求失败') + '</div>';
        say(false, e.message || 'AI 解读失败');
      }).finally(function () {
        wecomAiLoading = false;
        if (btn) btn.disabled = false;
      });
  }
  (function bindWecomUi() {
    var gq = document.getElementById('wecomGroupQ');
    if (gq) gq.addEventListener('input', function () {
      clearTimeout(wecomGroupTimer);
      wecomGroupTimer = setTimeout(loadWecomSessions, 280);
    });
    var only = document.getElementById('wecomOnlyGroup');
    if (only) only.addEventListener('change', loadWecomSessions);
    var onlyWatch = document.getElementById('wecomOnlyWatch');
    if (onlyWatch) {
      onlyWatch.checked = true;
      wecomState.watchOnly = true;
      onlyWatch.addEventListener('change', function () {
        wecomState.watchOnly = !!onlyWatch.checked;
        loadWecomSessions();
      });
    }
    var mq = document.getElementById('wecomMsgQ');
    if (mq) mq.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { wecomState.offset = 0; wecomState.fromEnd = 0; wecomState.tail = true; loadWecomMessages(); }
    });
    ['wecomStart', 'wecomEnd'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', function () { wecomState.offset = 0; wecomState.fromEnd = 0; wecomState.tail = true; loadWecomMessages(); });
    });
    var reload = document.getElementById('wecomReload');
    if (reload) reload.addEventListener('click', function () { wecomState.offset = 0; wecomState.fromEnd = 0; wecomState.tail = true; loadWecomBoard(); });
    if (reload && document.querySelector('.admin-shell') && !document.getElementById('wecomFileBtn')) {
      var fileBtn = document.createElement('button');
      fileBtn.type = 'button';
      fileBtn.className = 'mini';
      fileBtn.id = 'wecomFileBtn';
      fileBtn.textContent = '文件汇总';
      reload.parentNode.insertBefore(fileBtn, reload);
      fileBtn.addEventListener('click', wecomFileToggle);
    }
    var aiBtn = document.getElementById('wecomAiBtn');
    if (aiBtn) aiBtn.addEventListener('click', wecomAiRead);
    var aiLogBtn = document.getElementById('wecomAiLogBtn');
    if (aiLogBtn) aiLogBtn.addEventListener('click', wecomAiOpenLog);
    var aiTabRead = document.getElementById('wecomAiTabRead');
    if (aiTabRead) aiTabRead.addEventListener('click', function () { wecomAiSetTab('read'); });
    var aiTabLog = document.getElementById('wecomAiTabLog');
    if (aiTabLog) aiTabLog.addEventListener('click', function () { wecomAiSetTab('log'); });
    var aiClose = document.getElementById('wecomAiClose');
    if (aiClose) aiClose.addEventListener('click', wecomAiReset);
    var splitBtn = document.getElementById('wecomSplitBtn');
    if (splitBtn) splitBtn.addEventListener('click', wecomSplitOpen);
    var intentPageBtn = document.getElementById('wecomIntentPageBtn');
    if (intentPageBtn) intentPageBtn.addEventListener('click', wecomIntentPage);
    wecomPickEnsureBar();
    var prev = document.getElementById('wecomPrev');
    if (prev) prev.addEventListener('click', function () {
      if (!wecomState.hasEarlier) return;
      wecomState.tail = true;
      wecomState.fromEnd = (wecomState.fromEnd || 0) + wecomState.limit;
      loadWecomMessages();
    });
    var next = document.getElementById('wecomNext');
    if (next) next.addEventListener('click', function () {
      wecomState.tail = true;
      wecomState.fromEnd = Math.max(0, (wecomState.fromEnd || 0) - wecomState.limit);
      loadWecomMessages();
    });
  })();

  global.loadWecomBoard = loadWecomBoard;
  global.wecomAiBusy = function () { return wecomAiLoading; };
  global.initWecomBoard = function () {
    if (!document.getElementById('wecomSessions')) return;
    initWecomWatchPanel();
    initWecomUserReview();
    loadWecomBoard(true);
    loadWecomWatchPanel();
  };
  global.loadWecomWatchPanel = loadWecomWatchPanel;
  global.loadWecomWatchLogs = loadWecomWatchLogs;
  if (document.getElementById('wecomWatchPanel')) initWecomWatchPanel();
  if (document.getElementById('wecomUserReview')) initWecomUserReview();
})(window);
