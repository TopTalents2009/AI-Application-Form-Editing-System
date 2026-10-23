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
  function senderLabel(s) {
    var v = String(s || '').trim();
    if (!v || /^(null|none|undefined|nil|unknown|未知)$/i.test(v)) return '未知';
    return v;
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
    offset: 0, limit: 80, total: 0, fromEnd: 0, hasEarlier: false, hasNewer: false, searchMode: false, replicas: [], tail: true,
    sessionKey: '', msgKey: '', sourceKey: '', pageItems: [], pick: { app: null, opinions: [] },
    watchGroups: [], watchOnly: true, focusMid: '', sessionReplicas: [], replicaMap: {},
    railTab: 'ai', pendingLocate: null, locateHoldFiles: false, aiStale: false, bindPromptView: 'time_window',
    bindOpenKeys: null
  };
  var wecomGroupTimer = null;
  var wecomMsgTimer = null;
  var wecomMsgReq = 0;
  var wecomAiLoading = false;
  var wecomAiTab = 'read';
  var wecomAiLastRead = null;
  var wecomAiFocusFile = null;
  var wecomAiHitTimer = 0;
  var wecomAiJumping = false;

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
  function wecomLooksLikeId(s) {
    s = String(s || '').trim();
    if (!s) return true;
    return /^[MRS]:/i.test(s) || /^\d{8,}$/.test(s);
  }
  function wecomSessionTitle(it) {
    var sid = String((it && (it.username || it.session_id)) || '');
    var name = String((it && it.display_name) || '').trim();
    if (name && !wecomLooksLikeId(name) && name !== sid) return name;
    if (sid.charAt(0) === 'R' || sid.charAt(0) === 'S') return '未命名群聊';
    return '未备注联系人';
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
    return '可查 ' + names.join(' · ') + ' 等拥有该会话的电脑 · 点击下载';
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
        if (typeof window.arReloadSessions === 'function') window.arReloadSessions();
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

  function wecomOpenSession(sid, name, opts) {
    opts = opts || {};
    sid = String(sid || '');
    name = String(name || '');
    if (!sid && !name) return;
    if (opts.aroundId || opts.aroundTime) {
      wecomState.pendingLocate = {
        sessionId: sid,
        sessionName: name,
        messageId: String(opts.aroundId || ''),
        time: String(opts.aroundTime || ''),
        skipDates: !!opts.skipDates
      };
    }
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
      wecomResetMsgWindow();
      wecomState.searchMode = false;
      wecomMsgReq += 1;
      var title = document.getElementById('wecomChatTitle');
      if (title) title.textContent = name || sid || '未选择会话';
      var stream = document.getElementById('wecomStream');
      if (stream) stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
      loadWecomMessages(false, {
        aroundId: opts.aroundId || '',
        aroundTime: opts.aroundTime || '',
        skipDates: !!opts.skipDates
      });
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
    el = document.getElementById('wwTalentUpload') || document.getElementById('igTalentUpload');
    if (el) el.checked = cfg.talentUploadEnabled !== false;
    el = document.getElementById('wwTalentUploadMax') || document.getElementById('igTalentUploadMax');
    if (el) el.value = cfg.talentUploadMax != null ? cfg.talentUploadMax : 10;
  }

  function wwCollectPanelForm() {
    var out = {};
    var el;
    el = document.getElementById('wwEnabled');
    if (el) out.enabled = !!el.checked;
    el = document.getElementById('wwMode');
    if (el) out.mode = el.value || 'auto';
    el = document.getElementById('wwWindow');
    if (el) out.windowHours = parseInt(el.value, 10) || 48;
    el = document.getElementById('wwTimeout');
    if (el) out.timeoutSec = parseInt(el.value, 10) || 60;
    el = document.getElementById('wwOpinionWait');
    if (el) out.opinionWaitMin = parseInt(el.value, 10) || 0;
    el = document.getElementById('wwOwner');
    if (el) out.owner = String(el.value || '').trim();
    el = document.getElementById('wwAllowNoOpinion');
    if (el) out.allowNoOpinion = !!el.checked;
    el = document.getElementById('wwSkipDismissed');
    if (el) out.skipDismissed = !!el.checked;
    el = document.getElementById('wwTalentUpload');
    if (el) out.talentUploadEnabled = !!el.checked;
    el = document.getElementById('wwTalentUploadMax');
    if (el) out.talentUploadMax = parseInt(el.value, 10) || 10;
    return out;
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
        if (g.issueHits) bits.push('重要问题 ' + g.issueHits);
        if (g.createdCount) bits.push('提交 ' + g.createdCount);
        if (g.pendingCount) bits.push('待审 ' + g.pendingCount);
        if (g.skippedCount) bits.push('跳过 ' + g.skippedCount);
        if (g.talentUploaded) bits.push('入库 ' + g.talentUploaded);
        return '<div class="grp">' +
          '<div class="grp-hd"><span class="grp-name">' + esc(g.sessionName || g.sessionId || '—') + '</span>' +
          wwGroupStatusTag(g.status) + '</div>' +
          (bits.length ? '<div class="grp-bits">' + esc(bits.join(' · ')) + '</div>' : '') +
          (g.note ? '<div class="grp-note">' + esc(g.note) + '</div>' : '') +
          (g.fileClassNote ? '<div class="grp-note">' + esc(g.fileClassNote) + '</div>' : '') +
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
            if (typeof global.loadIngestBoard === 'function') global.loadIngestBoard(true);
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
    var key = wecomSourceListKey();
    if (wecomState.sourceKey === key && document.getElementById('wecomSources') && document.getElementById('wecomSources').children.length) return;
    wecomState.sourceKey = key;
    renderWecomSources();
  }
  function wecomSourceListKey() {
    return (wecomState.sources || []).map(function (s) {
      return [s.id || '', s.operator_name || '', s.computer_name || '', s.kind || ''].join('\t');
    }).join('\n') + '\n' + (wecomState.sourceId || '*');
  }
  function wecomApplySource(id, kind) {
    wecomState.sourceId = id || '*';
    wecomState.sourceKind = kind || '';
    wecomState.sessionId = '';
    wecomState.replicas = [];
    wecomResetMsgWindow();
    wecomState.sessionKey = '';
    wecomPickReset(true);
    wecomState.sourceKey = wecomSourceListKey();
    loadWecomSessions();
    var title = document.getElementById('wecomChatTitle');
    var sub = document.getElementById('wecomChatSub');
    var stream = document.getElementById('wecomStream');
    if (title) title.textContent = '未选择会话';
    if (sub) sub.textContent = '从左侧点开会话；同一会话在多台电脑上已合并';
    if (stream) stream.innerHTML = '<div class="wecom-empty">选择会话后显示消息。</div>';
  }
  function renderWecomSources() {
    var box = document.getElementById('wecomSources');
    if (!box) return;
    var cur = wecomState.sourceId || '*';
    var html = '<label class="wecom-src-label"><span>电脑</span><select class="wecom-src-sel" id="wecomSourceSel">';
    html += '<option value="*" data-kind=""' + (!cur || cur === '*' ? ' selected' : '') + '>全部电脑（合并）</option>';
    html += wecomState.sources.map(function (s) {
      var id = s.id || '';
      var label = s.operator_name || s.computer_name || id;
      var tag = (s.kind === 'local' || id === 'local') ? '本机' : '远端';
      return '<option value="' + escAttr(id) + '" data-kind="' + escAttr(s.kind || '') + '"' + (id === cur ? ' selected' : '') + '>' + esc(label) + ' · ' + tag + '</option>';
    }).join('');
    html += '</select></label>';
    box.innerHTML = html;
    wecomState.sourceKey = wecomSourceListKey();
    var sel = document.getElementById('wecomSourceSel');
    if (sel) sel.addEventListener('change', function () {
      var opt = sel.options[sel.selectedIndex];
      wecomApplySource(sel.value || '*', opt ? (opt.getAttribute('data-kind') || '') : '');
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
        var name = wecomSessionTitle(it);
        var watchName = (sid.indexOf('M:') === 0 || wecomLooksLikeId(it.display_name) || !it.display_name)
          ? sid
          : it.display_name;
        var reps = it.replicas || [];
        wecomRememberReplicas(sid, reps);
        var nrep = reps.length;
        var labels = reps.map(function (r) { return r.label || r.source_id; }).filter(Boolean).join('、');
        var meta = (it.synced_at ? '同步 ' + it.synced_at : '未同步') +
          (it.msg_count ? ' · ' + it.msg_count + ' 条' : '') +
          (it.last_time ? ' · 消息 ' + it.last_time : '');
        if (nrep > 1 && labels) meta += ' · ' + labels;
        var watched = wecomGroupWatched(watchName, sid);
        var starTitle = watched ? '取消关注（值班不再扫描）' : '加入关注会话（群聊和单聊都会扫描）';
        return '<div class="wecom-item-wrap' + (sid === wecomState.sessionId ? ' on' : '') + '" data-id="' + escAttr(sid) + '" data-name="' + escAttr(watchName) + '" title="' + escAttr(sid) + '">' +
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
          wecomState.sessionReplicas = wecomState.replicas || [];
          wecomRememberReplicas(wecomState.sessionId, wecomState.sessionReplicas);
          wecomResetMsgWindow();
          wecomState.searchMode = false;
          wecomMsgReq += 1;
          wecomPickReset(true);
          if (wecomRailIsOpen()) {
            if (wecomState.railTab === 'files' && !wecomState.locateHoldFiles) wecomFileLoad();
            if (wecomState.railTab === 'ai' && wecomAiLastRead) {
              wecomState.aiStale = true;
              var staleEl = document.getElementById('wecomAiStale');
              if (staleEl) staleEl.hidden = false;
            }
          }
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
  var wecomLoadingMore = false;
  function wecomResetMsgWindow() {
    wecomState.offset = 0;
    wecomState.fromEnd = 0;
    wecomState.tail = true;
    wecomState.hasEarlier = false;
    wecomState.hasNewer = false;
    wecomState.pageItems = [];
    wecomState.msgKey = '';
    wecomState.focusMid = '';
    wecomLoadingMore = false;
  }
  var wecomScrollToken = 0;
  function wecomScrollToLatest(stream) {
    if (!stream) stream = document.getElementById('wecomStream');
    if (!stream) return;
    var token = ++wecomScrollToken;
    function go() {
      if (token !== wecomScrollToken) return;
      stream.scrollTop = stream.scrollHeight;
    }
    go();
    requestAnimationFrame(function () {
      go();
      requestAnimationFrame(go);
    });
    [40, 160, 360, 800].forEach(function (ms) { setTimeout(go, ms); });
    stream.querySelectorAll('img').forEach(function (img) {
      if (!img.complete) img.addEventListener('load', go, { once: true });
    });
  }
  function wecomItemKey(m) {
    var copies = (m && m.copies) || [];
    var mid = (m && (m.message_id || (copies[0] && copies[0].message_id))) || '';
    return String(mid) + '\t' + String((m && m.time_text) || '') + '\t' + String((m && m.sender) || '');
  }
  function wecomMergeMsgs(a, b) {
    var seen = {};
    var out = [];
    (a || []).concat(b || []).forEach(function (m) {
      var k = wecomItemKey(m);
      if (seen[k]) return;
      seen[k] = 1;
      out.push(m);
    });
    return out;
  }
  function wecomFillChatSub(d, items, start, end) {
    var sub = document.getElementById('wecomChatSub');
    if (!sub) return;
    var n = (items || []).length;
    var names = wecomReplicaNames();
    var bits = ['已显示 ' + n + ' 条（重复已合并）'];
    if (wecomState.hasEarlier) bits.push('上滑加载更早');
    else bits.push('已到最早');
    if (wecomState.hasNewer) bits.push('下滑加载更新');
    if (d && d.aroundHit) bits.push('已定位原文');
    if (d && d.readMode === 'local') bits.push('本地直读');
    if (start || end) bits.push('已按日期筛选');
    sub.textContent = bits.join(' · ') + ' · ' + names;
  }
  function wecomOnStreamScroll() {
    var stream = document.getElementById('wecomStream');
    if (!stream || wecomState.searchMode || wecomLoadingMore) return;
    if (!wecomState.sessionId || !(wecomState.pageItems || []).length) return;
    if (wecomState.hasEarlier && stream.scrollTop < 72) {
      wecomLoadingMore = true;
      loadWecomMessages(true, { earlier: true });
      return;
    }
    if (wecomState.hasNewer) {
      var gap = stream.scrollHeight - stream.scrollTop - stream.clientHeight;
      if (gap < 72) {
        wecomLoadingMore = true;
        loadWecomMessages(true, { newer: true });
      }
    }
  }
  function loadWecomMessages(quiet, opts) {
    opts = opts || {};
    var stream = document.getElementById('wecomStream');
    var title = document.getElementById('wecomChatTitle');
    var sub = document.getElementById('wecomChatSub');
    var sid = wecomState.sessionId;
    var pend = wecomState.pendingLocate;
    if (pend && String(pend.sessionId || '') === String(sid || '')) {
      if (!opts.aroundId && pend.messageId) opts.aroundId = pend.messageId;
      if (!opts.aroundTime && pend.time) opts.aroundTime = pend.time;
      if (pend.skipDates) opts.skipDates = true;
    }
    var aroundId = String(opts.aroundId || '').trim();
    var aroundTime = String(opts.aroundTime || '').trim();
    if (!wecomState.sessionId) {
      if (!quiet) {
        wecomFail(stream, '选择会话后显示消息。');
      }
      return;
    }
    var keepList = !!(opts.earlier || opts.newer);
    if (quiet && !keepList && wecomLoadingMore) return;
    var reqId = ++wecomMsgReq;
    if (title) title.textContent = wecomSessionTitle({ display_name: wecomState.sessionName, username: sid });
    var q = ((document.getElementById('wecomMsgQ') || {}).value || '').trim();
    if (aroundId || aroundTime || keepList) q = '';
    var hasMsgs = !!(stream && stream.querySelector('.wecom-msg'));
    if (!quiet && !keepList) {
      if (stream) stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
    } else if (!hasMsgs && stream && !keepList) {
      stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
    }
    if (opts.earlier && stream && !document.getElementById('wecomEarlierHint')) {
      var hint = document.createElement('div');
      hint.id = 'wecomEarlierHint';
      hint.className = 'wecom-earlier-hint';
      hint.textContent = '加载更早消息…';
      stream.insertBefore(hint, stream.firstChild);
    }
    if (q) {
      wecomState.searchMode = true;
      wecomState.hasEarlier = false;
      wecomState.hasNewer = false;
      wecomLoadingMore = false;
      var surl = '/api/wecom/search?q=' + encodeURIComponent(q) + '&session_id=' + encodeURIComponent(sid) + '&limit=0';
      if (wecomSourceParam()) surl += '&source_id=' + encodeURIComponent(wecomSourceParam());
      wecomQuery(surl).then(function (res) {
        if (!wecomMsgAlive(reqId, sid)) return;
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
    var start = opts.skipDates ? '' : ((document.getElementById('wecomStart') || {}).value || '');
    var end = opts.skipDates ? '' : ((document.getElementById('wecomEnd') || {}).value || '');
    var page = wecomState.limit || 80;
    var fe0 = wecomState.fromEnd || 0;
    var have = (wecomState.pageItems || []).length;
    var params = [];
    if (aroundId) params.push('around_id=' + encodeURIComponent(aroundId));
    if (aroundTime) params.push('around_time=' + encodeURIComponent(aroundTime));
    if (wecomSourceParam()) params.push('source_id=' + encodeURIComponent(wecomSourceParam()));
    if (start) params.push('start_date=' + encodeURIComponent(start));
    if (end) params.push('end_date=' + encodeURIComponent(end));
    if (!(aroundId || aroundTime)) {
      params.push('tail=1');
      if (opts.earlier) {
        params.push('from_end=' + (fe0 + have));
        params.push('limit=' + page);
      } else if (opts.newer) {
        var step = Math.min(page, Math.max(1, fe0));
        params.push('from_end=' + Math.max(0, fe0 - step));
        params.push('limit=' + step);
      } else {
        params.push('from_end=' + fe0);
        params.push('limit=' + ((quiet && have) ? Math.max(page, have) : page));
      }
    } else {
      params.push('limit=' + page);
    }
    var url = '/api/wecom/groups/' + encodeURIComponent(sid) + '/messages?' + params.join('&');
    var moreOk = false;
    wecomQuery(url).then(function (res) {
      if (!wecomMsgAlive(reqId, sid)) return;
      if (!res.ok) {
        if (!quiet && !keepList) { wecomFail(stream, (res.data && res.data.detail) || '加载消息失败'); }
        return;
      }
      var d = res.data || {};
      wecomState.total = d.total || 0;
      wecomState.offset = d.offset || 0;
      if (d.replicas && d.replicas.length) {
        wecomState.replicas = d.replicas;
        wecomRememberReplicas(wecomState.sessionId, d.replicas);
      }
      if (d.display_name && !wecomLooksLikeId(d.display_name)) wecomState.sessionName = d.display_name;
      if (title) title.textContent = wecomSessionTitle({ display_name: wecomState.sessionName || d.display_name, username: sid });
      var items = d.items || [];
      if (opts.earlier) {
        wecomState.hasEarlier = !!d.hasEarlier;
        wecomState.fromEnd = fe0;
        wecomState.tail = !wecomState.hasNewer;
        var mergedUp = wecomMergeMsgs(items, wecomState.pageItems);
        if (mergedUp.length <= have) {
          wecomState.hasEarlier = false;
          var hintEl = document.getElementById('wecomEarlierHint');
          if (hintEl) hintEl.remove();
          wecomFillChatSub(d, wecomState.pageItems, start, end);
          return;
        }
        wecomState.pageItems = mergedUp;
        wecomState.msgKey = wecomMsgKey(mergedUp);
        moreOk = true;
        renderWecomMsgs(stream, mergedUp, false, true, '', 'prepend');
        wecomFillChatSub(d, mergedUp, start, end);
        return;
      }
      if (opts.newer) {
        var mergedDown = wecomMergeMsgs(wecomState.pageItems, items);
        moreOk = mergedDown.length > have;
        wecomState.pageItems = mergedDown;
        wecomState.fromEnd = d.fromEnd || 0;
        wecomState.hasNewer = !!d.hasNewer;
        wecomState.hasEarlier = wecomState.hasEarlier || !!d.hasEarlier;
        wecomState.tail = !wecomState.hasNewer;
        wecomState.msgKey = wecomMsgKey(mergedDown);
        renderWecomMsgs(stream, mergedDown, false, true, '', 'keep');
        wecomFillChatSub(d, mergedDown, start, end);
        return;
      }
      wecomState.fromEnd = d.fromEnd || 0;
      wecomState.hasEarlier = !!d.hasEarlier;
      wecomState.hasNewer = !!d.hasNewer;
      wecomState.tail = !wecomState.hasNewer;
      wecomFillChatSub(d, items, start, end);
      var key = wecomMsgKey(items);
      var focusMid = aroundId || wecomState.focusMid || '';
      if (!(quiet && wecomState.msgKey === key)) {
        wecomState.msgKey = key;
        renderWecomMsgs(stream, items, false, quiet, focusMid, focusMid ? '' : 'latest');
      } else if (focusMid) {
        wecomHighlightMid(focusMid);
      } else if (!quiet) {
        wecomScrollToLatest(stream);
      }
      if (aroundId || (pend && pend.messageId)) {
        var hitMid = aroundId || (pend && pend.messageId) || '';
        if (d.aroundHit || (hitMid && wecomHighlightMid(hitMid))) {
          say(true, '已定位到聊天记录');
        } else {
          say(false, '各群记录中未找到对应原文');
        }
      }
      var holdFiles = wecomState.locateHoldFiles;
      var locateMid = aroundId || (pend && pend.messageId) || '';
      wecomState.focusMid = '';
      wecomState.pendingLocate = null;
      wecomState.locateHoldFiles = false;
      if (holdFiles) {
        wecomRailOpen('files');
        if (locateMid) wecomFileHighlightRow(locateMid);
      }
    }).catch(function (e) {
      if (!wecomMsgAlive(reqId, sid)) return;
      if (!quiet && !keepList) wecomFail(stream, e.message || '加载失败');
    }).then(function () {
      if (keepList) {
        wecomLoadingMore = false;
        var hintEl = document.getElementById('wecomEarlierHint');
        if (hintEl) hintEl.remove();
        if (moreOk) wecomOnStreamScroll();
      }
    });
  }
  function wecomFileHref(copy, name) {
    if (!copy || !copy.source_id || !copy.message_id) return '';
    var url = '/api/wecom/sources/' + encodeURIComponent(copy.source_id) + '/files/' + encodeURIComponent(copy.message_id);
    var cid = copy.session_id || wecomState.sessionId || '';
    if (cid) url += '?session_id=' + encodeURIComponent(cid);
    var base = wecomFileBaseName(name || '') || '';
    if (base) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'name=' + encodeURIComponent(base);
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
  function wecomRememberReplicas(sid, reps) {
    wecomState.replicaMap = wecomState.replicaMap || {};
    sid = String(sid || '').trim();
    if (!sid || !reps || !reps.length) return;
    var by = {};
    ((wecomState.replicaMap[sid] || []).concat(reps)).forEach(function (r) {
      var id = r && (r.source_id || r.id);
      if (id) by[id] = r;
    });
    wecomState.replicaMap[sid] = Object.keys(by).map(function (k) { return by[k]; });
  }
  function wecomPeerReplicas() {
    var sid = wecomState.sessionId || '';
    var mapped = (wecomState.replicaMap && wecomState.replicaMap[sid]) || [];
    if (mapped.length) return mapped;
    if (wecomState.sessionReplicas && wecomState.sessionReplicas.length) return wecomState.sessionReplicas;
    return wecomState.replicas || [];
  }
  function wecomExpandCopies(copies, mid, sessionId) {
    copies = wecomSortCopies(copies || []);
    var cid = String(sessionId || wecomState.sessionId || '').trim();
    var bySrc = {};
    var mids = [];
    copies.forEach(function (c) {
      if (!c || !c.source_id) return;
      var row = {
        source_id: c.source_id,
        source_label: c.source_label || c.label || '',
        kind: c.kind || '',
        message_id: Number(c.message_id || 0),
        session_id: c.session_id || cid
      };
      bySrc[row.source_id] = row;
      if (row.message_id && mids.indexOf(row.message_id) < 0) mids.push(row.message_id);
    });
    var nmid = Number(mid || 0);
    if (nmid && mids.indexOf(nmid) < 0) mids.push(nmid);
    var fallback = mids[0] || nmid;
    wecomPeerReplicas().forEach(function (r) {
      var sid = r && (r.source_id || r.id);
      if (!sid || bySrc[sid] || !fallback) return;
      bySrc[sid] = {
        source_id: sid,
        source_label: r.label || r.source_label || '',
        kind: r.kind || '',
        message_id: fallback,
        session_id: cid
      };
    });
    return wecomSortCopies(Object.keys(bySrc).map(function (k) { return bySrc[k]; }).filter(function (c) {
      return c.source_id && c.message_id;
    }));
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
      var oneMid = Number((m && m.message_id) || 0);
      if (oneMid) {
        var sid = (m && m.source_id) || wecomSourceParam();
        if (sid) {
          copies = [{ source_id: sid, message_id: oneMid, session_id: wecomState.sessionId, source_label: wecomRemoteName(), kind: wecomState.sourceKind || '' }];
        }
      }
    }
    var mid = Number((m && m.message_id) || (copies[0] && copies[0].message_id) || 0);
    return wecomExpandCopies(copies, mid, wecomState.sessionId);
  }
  var WECOM_MEDIA_URL = /https?:\/\/\S*(?:wework\.qpic\.cn|wx\.qlogo\.cn|mmbiz\.qpic\.cn|pic\.weixin\.qq\.com|imunion\.weixin\.qq\.com|weixin\.qq\.com\/cgi-bin\/mmae-bin)\S*/gi;
  function wecomStripMediaUrls(s) {
    return String(s || '').replace(WECOM_MEDIA_URL, ' ').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  }
  function wecomExtractFileName(text) {
    var s = wecomStripMediaUrls(text);
    var hit = s.match(/\[?(?:文件|file)?\]?\s*([^\n[\]/\\]+?\.(?:zip|rar|7z|pdf|xlsx?|xlsm|docx?|docm|wps|wpt|et|dps|pptx?|png|jpe?g|gif|webp|bmp|mp4|txt|md|ofd))/i);
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
  function wecomFileBaseName(name) {
    var s = String(name || '').replace(/\\/g, '/').trim();
    var i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
    return i >= 0 ? s.slice(i + 1) : s;
  }
  function wecomIsImageFileName(name) {
    return /\.(png|jpe?g|gif|webp|bmp|tif|tiff)$/i.test(wecomFileBaseName(name) || String(name || ''));
  }
  function wecomIsNonImageFileName(name) {
    return /\.(pdf|docx?|docm|dotx?|wps|wpt|xlsx?|xlsm|xlsb|et|ett|pptx?|dps|dpt|zip|rar|7z|txt|md|csv|rtf|ofd|mp[34]|m4a|wav|aac|ogg|flac|amr|wma|webm)$/i.test(wecomFileBaseName(name) || String(name || ''));
  }
  function wecomLooksLikeImageStem(name) {
    var base = wecomFileBaseName(name) || String(name || '');
    var stem = base.replace(/\.[^.]+$/, '');
    return stem === '图片' || /企业微信截图|微信截图|屏幕截图|screenshot/i.test(base);
  }
  function wecomIsImage(m) {
    var name = String((m && m.attachment_name) || '');
    var fromText = wecomExtractFileName((m && (m.text || m.snippet)) || '');
    if (wecomIsImageFileName(name) || wecomIsImageFileName(fromText)) return true;
    if (wecomLooksLikeImageStem(name) || wecomLooksLikeImageStem(fromText)) return true;
    if (wecomIsNonImageFileName(name) || wecomIsNonImageFileName(fromText)) return false;
    var t = Number((m && m.msg_type) || 0);
    if (t === 4 || t === 15) return true;
    var label = String((m && m.msg_type_label) || '').trim();
    if (label === '图片' || /^(image|photo)$/i.test(label)) return true;
    return !!wecomThumbSrcFromMedia(m && m.media_url);
  }
  function wecomThumbIsAvatarUrl(url) {
    return /qlogo\.cn|\/mmhead\//i.test(String(url || ''));
  }
  function wecomThumbSrcFromMedia(url) {
    url = String(url || '').trim();
    if (!url || wecomThumbIsAvatarUrl(url)) return '';
    if (!/qpic\.cn|pic\.weixin/i.test(url)) return '';
    return '/api/wecom/media?url=' + encodeURIComponent(url);
  }
  function wecomThumbSrcFromCopy(copy, name) {
    var url = wecomFileHref(copy, name);
    if (!url) return '';
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'inline=1';
  }
  function wecomThumbSrcFromLocal(f) {
    if (!f) return '';
    var name = f.filename || f.attachment_name || '';
    if (!wecomFileWantThumb(f) && !wecomLooksLikeImageStem(name) && !wecomIsImageFileName(name)) return '';
    var q = [];
    if (name) q.push('name=' + encodeURIComponent(name));
    var mid = f.messageId || f.message_id || '';
    if (mid) q.push('message_id=' + encodeURIComponent(String(mid)));
    var sid = f.sessionId || wecomState.sessionId || '';
    if (sid) q.push('session_id=' + encodeURIComponent(sid));
    var copies = wecomExpandCopies(f.copies || [], mid, sid);
    var src = f.cacheSourceId || (copies[0] && copies[0].source_id) || '';
    if (src) q.push('source_id=' + encodeURIComponent(src));
    var mids = [];
    copies.forEach(function (c) {
      var n = Number((c && c.message_id) || 0);
      if (n && mids.indexOf(n) < 0) mids.push(n);
    });
    if (mid && mids.indexOf(Number(mid)) < 0) mids.unshift(Number(mid));
    if (mids.length) q.push('message_ids=' + mids.join(','));
    if (!name && !mids.length) return '';
    return '/api/wecom/local-image?' + q.join('&');
  }
  function wecomThumbSrc(m) {
    if (!wecomIsImage(m)) return '';
    var local = wecomThumbSrcFromLocal({
      filename: wecomFileName(m),
      messageId: m.message_id,
      copies: wecomCopiesOf(m),
      sessionId: wecomState.sessionId,
      isImage: true
    });
    return local || wecomThumbSrcFromMedia(m && m.media_url);
  }
  function wecomFileWantThumb(f) {
    if (!f) return false;
    var base = wecomFileBaseName(f.filename);
    if (wecomIsNonImageFileName(base || f.filename)) return false;
    return !!(wecomIsImageFileName(f.filename) || wecomIsImageFileName(base) || wecomLooksLikeImageStem(f.filename) || f.isImage);
  }
  function wecomFileThumbFallbacks(f, primary) {
    var out = [];
    var seen = {};
    function add(u) {
      u = String(u || '').trim();
      if (!u || seen[u] || u === primary) return;
      seen[u] = true;
      out.push(u);
    }
    add(wecomThumbSrcFromLocal(f));
    add(wecomThumbSrcFromMedia(f && (f.mediaUrl || f.media_url) || ''));
    wecomExpandCopies((f && f.copies) || [], (f && f.messageId) || '', (f && f.sessionId) || wecomState.sessionId).forEach(function (c) {
      add(wecomThumbSrcFromCopy(c, f && f.filename));
    });
    return out;
  }
  function wecomFileThumbSrc(f) {
    if (!wecomFileWantThumb(f)) return '';
    return wecomThumbSrcFromLocal(f) || wecomThumbSrcFromMedia(f.mediaUrl || f.media_url || '');
  }
  function wecomThumbHtml(src, alt, cls, fallback) {
    var media = [];
    var files = [];
    var seen = {};
    function add(u) {
      u = String(u || '').trim();
      if (!u || seen[u]) return;
      seen[u] = true;
      if (/\/api\/wecom\/media\?/.test(u)) media.push(u);
      else files.push(u);
    }
    add(src);
    if (Array.isArray(fallback)) fallback.forEach(add);
    else add(fallback);
    var isFiles = (cls || '').indexOf('wecom-files-thumb') >= 0;
    if (!media.length && !files.length && !isFiles) return '';
    var extra = '';
    if (media.length) extra += ' data-media="' + escAttr(media.join('\n')) + '"';
    if (files.length) extra += ' data-file-srcs="' + escAttr(files.join('\n')) + '"';
    var boxCls = 'wecom-thumb-box' + (isFiles ? '' : ' wecom-thumb-box-chat');
    return '<span class="' + boxCls + '"' + extra + '><img class="' + (cls || 'wecom-thumb') + '" alt="预览" hidden referrerpolicy="no-referrer"></span>';
  }
  var wecomThumbFileQ = [];
  var wecomThumbFileN = 0;
  function wecomThumbBlobLooksImage(buf) {
    if (!buf || buf.byteLength < 12) return false;
    var u8 = new Uint8Array(buf);
    if (u8[0] === 0xFF && u8[1] === 0xD8 && u8[2] === 0xFF) return true;
    if (u8[0] === 0x89 && u8[1] === 0x50 && u8[2] === 0x4E && u8[3] === 0x47) return true;
    if (u8[0] === 0x47 && u8[1] === 0x49 && u8[2] === 0x46 && u8[3] === 0x38) return true;
    if (u8[0] === 0x42 && u8[1] === 0x4D) return true;
    if (u8[0] === 0x52 && u8[1] === 0x49 && u8[2] === 0x46 && u8[3] === 0x46 && u8[8] === 0x57 && u8[9] === 0x45 && u8[10] === 0x42 && u8[11] === 0x50) return true;
    if (u8[0] === 0x49 && u8[1] === 0x49 && u8[2] === 0x2A && u8[3] === 0x00) return true;
    if (u8[0] === 0x4D && u8[1] === 0x4D && u8[2] === 0x00 && u8[3] === 0x2A) return true;
    return false;
  }
  function wecomThumbFetchFile(url, done) {
    wecomThumbFileQ.push({ url: url, done: done });
    wecomThumbFilePump();
  }
  function wecomThumbFilePump() {
    while (wecomThumbFileN < 3 && wecomThumbFileQ.length) {
      (function (job) {
        wecomThumbFileN += 1;
        fetch(job.url, { credentials: 'same-origin', headers: { Accept: 'image/*,*/*;q=0.8' } }).then(function (r) {
          var ct = (r.headers.get('content-type') || '').toLowerCase();
          if (r.status === 202 || r.status === 204) {
            job.tries = (job.tries || 0) + 1;
            if (job.tries < 4) {
              setTimeout(function () {
                wecomThumbFileQ.push(job);
                wecomThumbFilePump();
              }, 700 * job.tries);
              return;
            }
            job.done('');
            return;
          }
          if (!r.ok || ct.indexOf('json') >= 0) {
            job.done('');
            return;
          }
          return r.blob().then(function (blob) {
            if (!blob || blob.size < 24 || (blob.type && blob.type.indexOf('json') >= 0)) {
              job.done('');
              return;
            }
            return blob.slice(0, 16).arrayBuffer().then(function (buf) {
              if (!wecomThumbBlobLooksImage(buf)) {
                job.done('');
                return;
              }
              job.done(URL.createObjectURL(blob));
            });
          });
        }).catch(function () {
          job.done('');
        }).then(function () {
          wecomThumbFileN -= 1;
          wecomThumbFilePump();
        });
      })(wecomThumbFileQ.shift());
    }
  }
  function wecomBindThumbs(root) {
    if (!root) return;
    function loadBox(box) {
      var st = box.getAttribute('data-thumb-state') || '';
      if (st === 'loading' || st === 'ok') return;
      box.setAttribute('data-thumb-state', 'loading');
      var img = box.querySelector('img');
      if (!img) return;
      var media = String(box.getAttribute('data-media') || '').split('\n').filter(Boolean);
      var files = String(box.getAttribute('data-file-srcs') || '').split('\n').filter(Boolean);
      function show(url) {
        if (!url) return;
        img.hidden = false;
        img.src = url;
        img.setAttribute('data-preview', url);
        box.classList.add('has-img');
        box.setAttribute('data-thumb-state', 'ok');
      }
      function miss() {
        img.hidden = true;
        img.removeAttribute('src');
        box.classList.remove('has-img');
        box.setAttribute('data-thumb-state', 'miss');
      }
      function tryFiles(i, after) {
        if (i >= files.length) {
          after();
          return;
        }
        wecomThumbFetchFile(files[i], function (url) {
          if (url) show(url);
          else tryFiles(i + 1, after);
        });
      }
      function tryMedia(i, after) {
        if (i >= media.length) {
          after();
          return;
        }
        var url = media[i];
        var probe = new Image();
        var settled = false;
        function finish(ok) {
          if (settled) return;
          settled = true;
          probe.onload = probe.onerror = null;
          if (ok && probe.naturalWidth >= 8) show(url);
          else tryMedia(i + 1, after);
        }
        probe.onload = function () { finish(true); };
        probe.onerror = function () { finish(false); };
        probe.referrerPolicy = 'no-referrer';
        probe.src = url;
      }
      var preferFiles = files.length > 0;
      if (preferFiles) tryFiles(0, function () { tryMedia(0, miss); });
      else tryMedia(0, function () { tryFiles(0, miss); });
    }
    var boxes = root.querySelectorAll('.wecom-thumb-box');
    function boxInView(b) {
      var r = b.getBoundingClientRect();
      var h = window.innerHeight || 0;
      var w = window.innerWidth || 0;
      var host = root.getBoundingClientRect ? root.getBoundingClientRect() : null;
      if (host && host.height > 0) {
        return r.width > 0 && r.height > 0 && r.bottom > host.top - 160 && r.top < host.bottom + 160;
      }
      return r.width > 0 && r.height > 0 && r.bottom > -40 && r.top < h + 160 && r.right > 0 && r.left < w;
    }
    boxes.forEach(function (b) {
      if (boxInView(b)) loadBox(b);
    });
    if (typeof IntersectionObserver === 'function') {
      try {
        var io = new IntersectionObserver(function (ents) {
          ents.forEach(function (en) {
            if (!en.isIntersecting) return;
            io.unobserve(en.target);
            loadBox(en.target);
          });
        }, { root: null, rootMargin: '200px', threshold: 0.01 });
        boxes.forEach(function (b) {
          if (!b.getAttribute('data-thumb-state')) io.observe(b);
        });
      } catch (err) {
        boxes.forEach(loadBox);
      }
    } else {
      boxes.forEach(loadBox);
    }
    boxes.forEach(function (box) {
      box.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var img = box.querySelector('img');
        var src = (img && !img.hidden && (img.getAttribute('data-preview') || img.src)) || '';
        if (src) wecomPreview(src);
      });
    });
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
  function renderWecomMsgs(stream, items, fromSearch, quiet, focusMid, anchor) {
    if (!items.length) {
      if (!quiet || !stream.querySelector('.wecom-msg')) {
        wecomFail(stream, fromSearch ? '没有命中的消息' : '该时间范围内没有消息');
      }
      return;
    }
    var keepScroll = stream.scrollTop;
    var prevH = stream.scrollHeight;
    var nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80;
    var hint = (!fromSearch && wecomState.hasEarlier)
      ? '<div class="wecom-earlier-hint">上滑加载更早消息</div>' : '';
    stream.innerHTML = hint + '<div class="wecom-stream-list">' + items.map(function (m, i) {
      var fname = wecomFileName(m);
      var copies = wecomCopiesOf(m);
      var mid = m.message_id || (copies[0] && copies[0].message_id) || '';
      var thumbSrc = wecomThumbSrc(m);
      var thumbFb = copies.map(function (c) { return wecomThumbSrcFromCopy(c, fname); }).filter(Boolean);
      if (m.media_url) thumbFb.push(wecomThumbSrcFromMedia(m.media_url));
      var hasThumb = wecomIsImage(m);
      var showCard = wecomHasFile(m);
      var text = wecomCaption(m, fname, hasThumb || showCard);
      var who = senderLabel(m.sender);
      var when = m.time_text || '';
      var typ = m.msg_type_label || '';
      if (showCard && /系统消息/.test(typ)) typ = '文件';
      var extra = '';
      if (hasThumb) {
        extra += wecomThumbHtml(thumbSrc, '预览', '', thumbFb);
        if (copies.length && !showCard) {
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
      var mids = [String(mid || '')];
      (copies || []).forEach(function (c) {
        var cm = String((c && c.message_id) || '');
        if (cm && mids.indexOf(cm) < 0) mids.push(cm);
      });
      return '<div class="' + cls + '" data-i="' + i + '" data-mid="' + escAttr(mid) + '" data-mids="' + escAttr(mids.join(',')) + '"><div class="meta"><span class="who">' + esc(who) + '</span><span>' + esc(when) + (typ ? ' · ' + esc(typ) : '') + '</span></div>' +
        (text ? '<div class="body">' + esc(text) + '</div>' : '') + extra + pickHtml +
        '<div class="wecom-intent-after" hidden></div></div>';
    }).join('') + '</div>';
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
    wecomBindThumbs(stream);
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
    if (focusMid && wecomHighlightMid(focusMid)) {
      return;
    }
    if (anchor === 'prepend') {
      stream.scrollTop = stream.scrollHeight - prevH + keepScroll;
      return;
    }
    if (anchor === 'keep' || (quiet && !nearBottom && anchor !== 'latest')) {
      stream.scrollTop = keepScroll;
      return;
    }
    if (fromSearch) {
      stream.scrollTop = 0;
      return;
    }
    wecomScrollToLatest(stream);
  }
  function wecomFindMsgEl(stream, mid) {
    var want = String(mid || '').trim();
    if (!stream || !want) return null;
    var nodes = stream.querySelectorAll('.wecom-msg');
    for (var i = 0; i < nodes.length; i++) {
      if (String(nodes[i].getAttribute('data-mid') || '') === want) return nodes[i];
      var extras = String(nodes[i].getAttribute('data-mids') || '').split(',');
      if (extras.indexOf(want) >= 0) return nodes[i];
    }
    return null;
  }
  function wecomHighlightMid(mid) {
    var stream = document.getElementById('wecomStream');
    var hit = wecomFindMsgEl(stream, mid);
    if (!hit) return false;
    if (stream) stream.querySelectorAll('.wecom-msg.hit').forEach(function (el) { el.classList.remove('hit'); });
    hit.classList.add('hit');
    if (hit.scrollIntoView) hit.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return true;
  }
  function wecomGoChatSec() {
    var sec = document.getElementById('sec-wecom') || document.getElementById('wecomHome');
    if (sec && sec.scrollIntoView) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function wecomLocateOrigin(btn) {
    wecomLocateCopy({
      sessionId: wecomState.sessionId,
      sessionName: wecomState.sessionName,
      messageId: String((btn && btn.getAttribute('data-mid')) || '').trim(),
      time: String((btn && btn.getAttribute('data-time')) || '').trim()
    });
  }
  function wecomFileHighlightRow(mid) {
    var box = wecomFileBox();
    if (!box) return;
    var want = String(mid || '').trim();
    box.querySelectorAll('.wecom-files-row.hit').forEach(function (el) { el.classList.remove('hit'); });
    if (!want) return;
    box.querySelectorAll('.wecom-files-row').forEach(function (row) {
      var inp = row.querySelector('[data-mid]');
      if (!inp || String(inp.getAttribute('data-mid') || '') !== want) return;
      row.classList.add('hit');
      if (row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
      setTimeout(function () { row.classList.remove('hit'); }, 2200);
    });
  }
  function wecomLocateCopy(loc) {
    loc = loc || {};
    var sid = String(loc.sessionId || '').trim();
    var sname = String(loc.sessionName || '').trim();
    var mid = String(loc.messageId || '').trim();
    var time = String(loc.time || '').trim();
    if (!sid && !wecomState.sessionId) {
      say(false, '请先选择会话');
      return;
    }
    if (!mid && !time) {
      say(false, '这条没有对应的聊天记录位置');
      return;
    }
    var holdFiles = wecomRailIsOpen() && wecomState.railTab === 'files';
    if (holdFiles) wecomState.locateHoldFiles = true;
    wecomGoChatSec();
    var qEl = document.getElementById('wecomMsgQ');
    if (qEl) qEl.value = '';
    sid = sid || wecomState.sessionId;
    sname = sname || wecomState.sessionName;
    if (sid && sid !== wecomState.sessionId) {
      wecomState.locateHoldFiles = true;
      wecomOpenSession(sid, sname, { aroundId: mid, aroundTime: time, skipDates: true });
      return;
    }
    var stream = document.getElementById('wecomStream');
    if (mid && stream && wecomFindMsgEl(stream, mid)) {
      wecomHighlightMid(mid);
      if (holdFiles) {
        wecomRailOpen('files');
        wecomFileHighlightRow(mid);
        wecomState.locateHoldFiles = false;
      }
      say(true, '已定位到聊天记录');
      return;
    }
    wecomState.locateHoldFiles = holdFiles || wecomState.locateHoldFiles;
    wecomState.focusMid = mid;
    wecomState.offset = 0;
    loadWecomMessages(false, { aroundId: mid, aroundTime: time, skipDates: true });
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
        if (box) {
          var stayLatest = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
          wecomIntentRestore(box, wecomState.pageItems || []);
          if (stayLatest && !wecomState.hasNewer) wecomScrollToLatest(box);
        }
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
    var gen = ++wecomIntentHydrateGen;
    var CHUNK = 40;
    var off = 0;
    var okN = 0;
    if (btn) { btn.disabled = true; btn.textContent = '分析中…'; }
    if (stream) {
      stream.querySelectorAll('.wecom-intent-after').forEach(function (el) {
        el.hidden = false;
        el.textContent = '正在分析…';
      });
    }
    function done(ok, msg) {
      if (btn) { btn.disabled = false; btn.textContent = '分析意图'; }
      say(ok, msg);
    }
    function nextChunk() {
      if (gen !== wecomIntentHydrateGen || sid !== wecomState.sessionId) {
        done(false, '已切换会话，分析中止');
        return;
      }
      if (off >= items.length) {
        var box = document.getElementById('wecomStream');
        if (box) wecomIntentRestore(box, wecomState.pageItems || []);
        done(true, '已分析 ' + okN + ' / ' + keys.length + ' 条');
        return;
      }
      var end = Math.min(off + CHUNK, items.length);
      if (btn) btn.textContent = '分析中 ' + end + '/' + items.length + '…';
      var slice = items.slice(off, end);
      var sliceKeys = keys.slice(off, end);
      fetch('/api/wecom/intent-page', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sid,
          keys: sliceKeys,
          messages: slice.map(wecomIntentPayload)
        })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (x) {
          var d = x.j || {};
          if (!x.ok) { done(false, d.detail || '意图分析失败'); return; }
          var rows = d.items || [];
          wecomState.intentCache = wecomState.intentCache || {};
          sliceKeys.forEach(function (key, i) {
            var row = rows[i] || { readLabel: '已读', intentLabel: '其他', error: '这条没有返回意图' };
            row.readLabel = row.readLabel || '已读';
            wecomState.intentCache[key] = row;
          });
          okN += sliceKeys.length;
          var box = document.getElementById('wecomStream');
          if (box) wecomIntentRestore(box, wecomState.pageItems || []);
          off = end;
          nextChunk();
        }).catch(function (e) {
          done(false, e.message || '意图分析失败');
        });
    }
    nextChunk();
  }
  function wecomProgressHtml() {
    return '<div class="ar-progress wecom-progress">'
      + '<div class="ar-progress-row">'
      + '<span class="ar-progress-spin"></span>'
      + '<span class="ar-progress-lab">准备中…</span>'
      + '<span class="ar-progress-pct">0%</span></div>'
      + '<div class="ar-progress-track"><div class="ar-progress-bar"></div></div>'
      + '<div class="ar-progress-meta"></div></div>';
  }
  function wecomProgressPaint(root, ev) {
    if (!root || !ev) return;
    var pct = Math.max(0, Math.min(100, Number(ev.percent) || 0));
    var lab = root.querySelector('.ar-progress-lab');
    var pEl = root.querySelector('.ar-progress-pct');
    var bar = root.querySelector('.ar-progress-bar');
    var meta = root.querySelector('.ar-progress-meta');
    if (lab) lab.textContent = ev.label || '';
    if (pEl) pEl.textContent = pct + '%';
    if (bar) bar.style.width = pct + '%';
    if (meta) {
      var bits = [];
      if (ev.detail) bits.push(ev.detail);
      if (ev.files != null) bits.push('文件 ' + ev.files);
      if (ev.messages != null) bits.push('消息 ' + ev.messages);
      meta.textContent = bits.join(' · ');
    }
  }
  function wecomReadSSE(res, onEvent) {
    if (!res.body || !res.body.getReader) {
      return Promise.reject(new Error('浏览器不支持进度推送'));
    }
    var reader = res.body.getReader();
    var dec = new TextDecoder();
    var buf = '';
    function pump() {
      return reader.read().then(function (x) {
        if (x.done) throw new Error('解析中断');
        buf += dec.decode(x.value, { stream: true });
        var parts = buf.split('\n\n');
        buf = parts.pop() || '';
        for (var i = 0; i < parts.length; i++) {
          var line = parts[i].replace(/^data:\s*/, '').trim();
          if (!line) continue;
          try {
            var ev = JSON.parse(line);
            onEvent(ev);
            if (ev.phase === 'done' || ev.phase === 'error') return ev;
          } catch (err) {}
        }
        return pump();
      });
    }
    return pump();
  }
  function wecomPostProgress(url, payload, box, thenOk, thenErr) {
    var root = null;
    if (box) {
      box.innerHTML = wecomProgressHtml();
      root = box.querySelector('.ar-progress');
      wecomProgressPaint(root, { percent: 2, label: '开始解析…' });
    }
    var body = {};
    Object.keys(payload || {}).forEach(function (k) { body[k] = payload[k]; });
    body.stream = true;
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (res) {
      var ct = (res.headers.get('content-type') || '');
      if (!res.ok && ct.indexOf('text/event-stream') < 0) {
        return res.json().then(function (j) {
          var d = j && (typeof j.detail === 'string' ? j.detail : (j.message || ''));
          throw new Error(d || '请求失败');
        }, function () { throw new Error('请求失败'); });
      }
      if (ct.indexOf('text/event-stream') >= 0) {
        return wecomReadSSE(res, function (ev) {
          if (ev.phase === 'error') throw new Error(ev.message || '解析失败');
          wecomProgressPaint(root, ev);
        }).then(function (last) {
          if (last && last.phase === 'done') return last.result || last;
          throw new Error((last && last.message) || '解析中断');
        });
      }
      return res.json().then(function (j) {
        if (!res.ok) throw new Error((j && (j.detail || j.message)) || '请求失败');
        return j;
      });
    }).then(thenOk).catch(thenErr);
  }
  function wecomFileBox() {
    return document.getElementById('wecomFileBox');
  }
  function wecomFileGo() {
    wecomRailOpen('files');
  }
  var wecomFileCacheKey = '';
  var wecomFileLoading = false;
  var wecomFileReq = 0;
  function wecomFileRequestKey() {
    var dates = wecomSplitDates();
    return [wecomState.sessionId || '', dates.start_date || '', dates.end_date || '', wecomSourceParam() || ''].join('\t');
  }
  function wecomFileLoad(force) {
    var box = wecomFileBox();
    if (!wecomState.sessionId) {
      say(false, '请先选择会话');
      return;
    }
    if (!box) return;
    var key = wecomFileRequestKey();
    var had = !force && wecomFileData && wecomFileCacheKey === key && wecomFileData.session_id === wecomState.sessionId;
    if (had) wecomFilePaint(true);
    if (wecomFileLoading && !force && had) return;
    wecomFileLoading = true;
    var req = ++wecomFileReq;
    var dates = wecomSplitDates();
    var payload = {
      session_id: wecomState.sessionId,
      session_name: wecomState.sessionName || '',
      source_id: wecomSourceParam() || '',
      start_date: dates.start_date,
      end_date: dates.end_date,
      force: !!force
    };
    var target = box;
    if (had) {
      var wrap = box.querySelector('.wecom-file-progress-host');
      if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'wecom-file-progress-host';
        box.insertBefore(wrap, box.firstChild);
      }
      target = wrap;
    }
    wecomPostProgress('/api/wecom/file-summary', payload, target, function (d) {
      if (req !== wecomFileReq) return;
      wecomFileLoading = false;
      wecomFileCacheKey = key;
      wecomFileRender(d || {}, had);
    }, function (e) {
      if (req !== wecomFileReq) return;
      wecomFileLoading = false;
      if (had) {
        var host = box.querySelector('.wecom-file-progress-host');
        if (host) host.parentNode.removeChild(host);
        say(false, e.message || '汇总失败');
        return;
      }
      if (box) box.innerHTML = '<div class="wecom-empty">' + esc(e.message || '汇总失败') + '</div>';
    });
  }
  var WECOM_FILE_CATS = ['申报书', '修改意见', '护照', '身份证明', '学历证明', '工作证明', '意向协议', '股权证明', '论文全文', '证件照', '电子签', '项目证明', '成果转化证明', '其他'];
  function wecomFileCatRank(label) {
    var i = WECOM_FILE_CATS.indexOf(label || '');
    return i < 0 ? WECOM_FILE_CATS.length : i;
  }
  var wecomFileData = null;
  var wecomFileChecked = {};
  var wecomFileQuery = '';
  var wecomFileCat = '';
  function wecomFileKey(f) {
    return String(f.messageId || '') + '|' + String(f.filename || '');
  }
  function wecomFileUploadTag(f) {
    var st = String((f && f.uploadStatus) || '');
    var aid = String((f && f.attachId) || '');
    if (st === 'uploaded') return '<span class="wecom-files-up ok">已入库' + (aid ? ' ' + esc(aid) : '') + '</span>';
    if (st === 'error' || st === 'missing') return '<span class="wecom-files-up bad" title="' + escAttr((f && f.uploadNote) || '') + '">入库失败</span>';
    if (st === 'no_id') return '<span class="wecom-files-up skip">无编号</span>';
    if (st === 'pending') return '<span class="wecom-files-up skip">待入库</span>';
    return '';
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
          reason: f.reason || '',
          source: f.source || '',
          messageId: f.messageId || '',
          copies: f.copies || [],
          sessionId: f.sessionId || d.session_id || '',
          sessionName: f.sessionName || d.session_name || '',
          mediaUrl: f.mediaUrl || f.media_url || '',
          isImage: !!f.isImage,
          attachId: f.attachId || '',
          uploadStatus: f.uploadStatus || '',
          uploadNote: f.uploadNote || '',
          type: g.id || f.type || '',
          kind: g.id || f.kind || '',
          label: g.label || g.id || ''
        });
      });
    });
    return rows;
  }
  function wecomFileLocatePayload(f) {
    var copies = f.copies || [];
    var c = copies[0] || {};
    return {
      sessionId: c.session_id || f.sessionId || wecomState.sessionId || '',
      sessionName: c.session_name || c.sessionName || f.sessionName || wecomState.sessionName || '',
      messageId: String(c.message_id || f.messageId || ''),
      time: f.time || ''
    };
  }
  function wecomFileSyncChecks() {
    document.querySelectorAll('#wecomFileBox [data-file]').forEach(function (el) {
      var key = el.getAttribute('data-key') || '';
      if (!key) return;
      if (el.checked) {
        var row = wecomFileAllRows().filter(function (f) { return wecomFileKey(f) === key; })[0] || {};
        wecomFileChecked[key] = {
          filename: el.getAttribute('data-name') || row.filename || '',
          messageId: el.getAttribute('data-mid') || row.messageId || '',
          copies: row.copies || [],
          sessionId: row.sessionId || wecomState.sessionId || '',
          type: row.type || row.kind || '',
          kind: row.kind || row.type || '',
          label: row.label || '',
          attachId: row.attachId || ''
        };
      }
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
      var blob = (f.label + ' ' + f.filename + ' ' + (f.sender || '') + ' ' + (f.time || '') + ' ' + (f.reason || '')).toLowerCase();
      return blob.indexOf(q) >= 0;
    });
  }
  var wecomFilePendingCat = '';
  var wecomFilePendingNote = '';
  function wecomFileOpenCat(cat) {
    wecomFilePendingCat = String(cat || '');
    wecomFilePendingNote = wecomFilePendingCat ? ('已打开文件汇总 · ' + wecomFilePendingCat) : '已打开文件汇总';
    wecomRailOpen('files');
    if (!wecomState.sessionId) {
      say(false, '请先选择会话');
      wecomFilePendingCat = '';
      wecomFilePendingNote = '';
      return;
    }
    var key = wecomFileRequestKey();
    if (wecomFileData && wecomFileCacheKey === key && wecomFileData.session_id === wecomState.sessionId) {
      wecomFileCat = wecomFilePendingCat;
      wecomFilePendingCat = '';
      wecomFileQuery = '';
      wecomFilePaint(true);
      if (wecomFilePendingNote) say(true, wecomFilePendingNote);
      wecomFilePendingNote = '';
      return;
    }
    wecomFileLoad(false);
  }
  function wecomFileRender(d, keepView) {
    wecomFileData = d || {};
    if (!keepView) {
      wecomFileChecked = {};
      wecomFileQuery = '';
      wecomFileCat = wecomFilePendingCat || '';
      wecomFilePendingCat = '';
    } else if (wecomFilePendingCat) {
      wecomFileCat = wecomFilePendingCat;
      wecomFilePendingCat = '';
    }
    wecomFilePaint(true);
    if (wecomFilePendingNote) {
      say(true, wecomFilePendingNote);
      wecomFilePendingNote = '';
    }
  }
  function wecomFilePaint(skipSync) {
    var box = wecomFileBox();
    if (!box) return;
    if (!skipSync) wecomFileSyncChecks();
    var d = wecomFileData || {};
    var aidEl = document.getElementById('wecomFileAid');
    var qEl = document.getElementById('wecomFileQ');
    var catEl = document.getElementById('wecomFileCat');
    var aidKeep = aidEl ? aidEl.value : '';
    if (qEl) wecomFileQuery = qEl.value || '';
    if (catEl) wecomFileCat = catEl.value || '';
    var all = wecomFileAllRows();
    var cats = [];
    all.forEach(function (f) {
      if (f.label && cats.indexOf(f.label) < 0) cats.push(f.label);
    });
    var rows = wecomFileFiltered();
    var n = rows.length;
    var catOpts = '<option value="">全部类别</option>' + cats.map(function (c) {
      return '<option value="' + escAttr(c) + '"' + (c === wecomFileCat ? ' selected' : '') + '>' + esc(c) + '</option>';
    }).join('');
    var picked = Object.keys(wecomFileChecked).length;
    var session = wecomState.sessionName || '';
    var countText = n === all.length ? ('共 ' + all.length + ' 个文件') : ('筛选出 ' + n + ' · 共 ' + all.length);
    var modeText = d.mode === 'incremental'
      ? ('增量' + (d.newFileCount ? (' · 新增 ' + d.newFileCount) : ''))
      : (d.mode === 'cache' ? '已缓存' : (d.mode === 'full' ? '全量' : ''));
    var html = '';
    if (!all.length) {
      html += '<div class="wecom-empty">当前日期范围内没有文件</div>';
      html += '<div class="wecom-files-act" style="padding:8px 0"><button type="button" class="mini" id="wecomFileRescan">重新全量扫描</button></div>';
    } else {
      html += '<div class="wecom-files-bar">' +
        '<div class="wecom-files-find">' +
          '<label class="wecom-files-search"><span>搜索</span><input id="wecomFileQ" value="' + escAttr(wecomFileQuery) + '" placeholder="文件名 / 发出人" autocomplete="off"></label>' +
          '<label class="wecom-files-cat"><span>类别</span><select id="wecomFileCat">' + catOpts + '</select></label>' +
        '</div>' +
        '<div class="wecom-files-meta">' +
          (session ? '<span class="wecom-files-sess" title="' + escAttr(session) + '">' + esc(session) + '</span>' : '') +
          '<span class="count-pill">' + esc(countText) + '</span>' +
          (modeText ? '<span class="count-pill">' + esc(modeText) + '</span>' : '') +
          (d.classifyNote ? '<span class="wecom-files-sess" title="' + escAttr(d.classifyNote) + '">' + esc(d.classifyNote) + '</span>' : '') +
          '<button type="button" class="mini" id="wecomFileRescan" title="忽略缓存，重新通读本会话全部文件">重新全量扫描</button>' +
          ((d.autoUpload && d.autoUpload.note) ? '<span class="wecom-files-sess" title="' + escAttr(d.autoUpload.note) + '">' + esc(d.autoUpload.note) + '</span>' : '') +
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
        rows.forEach(function (f) {
          var lab = f.label || '其他';
          if (lab !== prevLab) {
            if (prevLab) html += '</div>';
            prevLab = lab;
            html += '<div class="wecom-files-sec"><div class="wecom-files-sec-hd"><b>' + esc(lab) + '</b><em>' + catCount[lab] + '</em></div>';
          }
          var key = wecomFileKey(f);
          var on = wecomFileChecked[key] ? ' checked' : '';
          var who = senderLabel(f.sender);
          var when = String(f.time || '').trim() || '未知';
          var why = String(f.reason || '').trim();
          var srcMap = {context: '聊天', gemini: '模型', filename: '文件名'};
          var srcLab = srcMap[f.source] || '';
          var wantThumb = wecomFileWantThumb(f);
          var thumbSrc = wecomFileThumbSrc(f);
          var thumbFb = wecomFileThumbFallbacks(f, thumbSrc);
          var shownName = wecomFileBaseName(f.filename) || f.filename || '未命名';
          var aiMids = wecomExpandCopies(f.copies || [], f.messageId, f.sessionId || wecomState.sessionId).map(function (c) {
            return String((c && c.message_id) || '');
          }).filter(Boolean);
          if (f.messageId && aiMids.indexOf(String(f.messageId)) < 0) aiMids.unshift(String(f.messageId));
          var aiJump = escAttr(JSON.stringify({
            messageId: String(f.messageId || ''),
            filename: f.filename || '',
            label: lab,
            attachId: String(f.attachId || ''),
            sender: f.sender || '',
            time: f.time || '',
            sessionId: f.sessionId || wecomState.sessionId || '',
            mids: aiMids
          }));
          html += '<div class="wecom-files-row' + (wantThumb ? ' is-img' : '') + '"><input type="checkbox" data-file="1" data-key="' + escAttr(key) + '" data-name="' + escAttr(f.filename) + '" data-mid="' + escAttr(f.messageId) + '"' + on + '>' +
            (wantThumb ? wecomThumbHtml(thumbSrc, '预览', 'wecom-files-thumb', thumbFb) : '') +
            '<button type="button" class="nm wecom-files-ai" title="' + escAttr((f.filename || shownName) + '　在最新解读记录中查看此文件') + '" data-ai-file="' + aiJump + '">' + esc(shownName) + wecomFileUploadTag(f) + '</button>' +
            '<span class="wecom-files-who" title="' + escAttr('发出人 ' + who + ' 发出时间 ' + when + (why ? '　' + why : '') + (f.attachId ? '　编号 ' + f.attachId : '') + (f.uploadNote ? '　' + f.uploadNote : '')) + '">' +
            '<em>发出人</em>' + esc(who) + '<em>发出时间</em>' + esc(when) +
            (f.attachId ? '<em>编号</em>' + esc(f.attachId) : '') +
            (srcLab ? '<em>依据</em>' + esc(srcLab) : '') +
            (why ? '<em>分类</em>' + esc(why) : '') + '</span>' +
            '<span class="wecom-files-acts">' +
            '<button type="button" class="mini wecom-files-ai" data-ai-file="' + aiJump + '" title="打开最新解读记录中的对应文件">解读</button>' +
            '<button type="button" class="mini wecom-files-loc" data-locate="' + escAttr(JSON.stringify(wecomFileLocatePayload(f))) + '">定位</button>' +
            '<button type="button" class="mini wecom-files-dl" data-name="' + escAttr(f.filename || 'file') + '" data-copies="' + encodeURIComponent(JSON.stringify(wecomExpandCopies(f.copies || [], f.messageId, f.sessionId || wecomState.sessionId))) + '" data-wecom-dl="' + escAttr(f.messageId || '') + '">下载</button></span></div>';
        });
        if (prevLab) html += '</div>';
        html += '</div>';
      }
    }
    var keepFocus = document.activeElement && document.activeElement.id === 'wecomFileQ';
    if (all.length) {
      html += '<div class="wecom-files-foot">' +
        '<div class="wecom-files-cfg">' +
          '<label>人才编号<input id="wecomFileAid" value="' + escAttr(aidKeep) + '" placeholder="attach_id" autocomplete="off"></label>' +
        '</div>' +
        '<div class="wecom-files-act"><span id="wecomFileMsg"></span><button type="button" class="primary" id="wecomFileUp">上传选中到人才附件</button></div></div>';
    }
    box.innerHTML = html;
    box.hidden = false;
    var qIn = document.getElementById('wecomFileQ');
    if (qIn) qIn.addEventListener('input', function () {
      wecomFileQuery = qIn.value || '';
      wecomFilePaint();
    });
    var catIn = document.getElementById('wecomFileCat');
    if (catIn) catIn.addEventListener('change', function () {
      wecomFileCat = catIn.value || '';
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
    box.querySelectorAll('.wecom-files-loc').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          wecomLocateCopy(JSON.parse(btn.getAttribute('data-locate') || '{}'));
        } catch (e) {
          say(false, '定位参数无效');
        }
      });
    });
    box.querySelectorAll('.wecom-files-ai').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          wecomAiOpenLatestForFile(JSON.parse(btn.getAttribute('data-ai-file') || '{}'));
        } catch (e) {
          say(false, '无法打开解读记录');
        }
      });
    });
    wecomBindThumbs(box);
    var up = document.getElementById('wecomFileUp');
    if (up) up.addEventListener('click', wecomFileUpload);
    var rescan = document.getElementById('wecomFileRescan');
    if (rescan) rescan.addEventListener('click', function () { wecomFileLoad(true); });
    if (qIn && keepFocus) {
      qIn.focus();
      var len = qIn.value.length;
      try { qIn.setSelectionRange(len, len); } catch (e) {}
    }
  }
  function wecomFileToggle() {
    wecomRailOpen('files');
    wecomFileLoad(false);
  }
  function wecomFileUpload() {
    var msg = document.getElementById('wecomFileMsg');
    var aid = ((document.getElementById('wecomFileAid') || {}).value || '').trim();
    wecomFileSyncChecks();
    var files = [];
    Object.keys(wecomFileChecked).forEach(function (k) {
      files.push(wecomFileChecked[k]);
    });
    if (!files.length) { if (msg) msg.textContent = '请勾选文件'; return; }
    var missing = files.filter(function (f) { return !(f.attachId || aid); });
    if (!aid && missing.length) { if (msg) msg.textContent = '有文件未识别编号，请填写人才编号'; return; }
    if (msg) msg.textContent = '提交中…';
    fetch('/api/wecom/talent-files', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attachId: aid, files: files })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (msg) msg.textContent = (x.j && (x.j.detail || x.j.message)) || (x.ok ? '已提交' : '上传失败');
        if (x.ok) wecomFileLoad(false);
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
    chat.appendChild(bar);
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
            if (o.kind === 'text') {
              var tx = String(o.text || '').trim();
              return tx ? ('文本意见：' + tx) : ('文本意见「' + (o.filename || '群聊修改意见.txt') + '」');
            }
            return '意见文件：' + (o.filename || '修改意见');
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
    var copies = wecomParseJsonAttr(btn, 'data-copies');
    var mid = Number(btn.getAttribute('data-wecom-dl') || 0);
    copies = wecomExpandCopies(copies, mid, wecomState.sessionId);
    if (!copies.length) copies = wecomCopiesOf({ message_id: mid });
    var wrap = btn.closest('.wecom-file') || btn.parentElement;
    if (!copies.length) {
      wecomSetFileStatus(btn, '没有可查询的电脑副本', 'bad');
      return;
    }
    btn.disabled = true;
    var idx = 0;
    var lastDetail = '';
    var tried = [];
    function wecomCopyFailHint(detail, label) {
      var d = String(detail || '').trim();
      var who = '「' + (label || '该电脑') + '」';
      if (/message not found/i.test(d)) {
        return who + '会话记录里没有这条消息（不是整批都没查到）';
      }
      if (!d) return who + '未缓存';
      if (d.indexOf(who) === 0 || d.indexOf('「') === 0) return d;
      return who + d;
    }
    function failAll() {
      btn.textContent = btn.classList.contains('wecom-orig') ? '原文件未缓存' : '未缓存';
      btn.disabled = false;
      var names = wecomReplicaNames(copies);
      var sum = '已查 ' + copies.length + ' 台电脑（' + names + '），均未取到文件。';
      if (tried.length) sum += ' ' + tried.join('；');
      else if (lastDetail) sum += ' ' + lastDetail;
      wecomSetFileStatus(btn, sum, 'bad');
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
      var url = wecomFileHref(c, name);
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
              tried.push(lastDetail);
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
              lastDetail = wecomCopyFailHint((j && j.detail) || '', label);
              tried.push(lastDetail);
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            }).catch(function () {
              lastDetail = wecomCopyFailHint('', label);
              tried.push(lastDetail);
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            });
          }
          if (!r.ok) {
            return r.json().then(function (j) {
              lastDetail = wecomCopyFailHint((j && j.detail) || ('HTTP ' + r.status), label);
              tried.push(lastDetail);
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            }).catch(function () {
              lastDetail = '「' + label + '」失败';
              tried.push(lastDetail);
              wecomSetPcState(wrap, copyIdx, 'miss');
              tryNext();
            });
          }
          return r.blob().then(function (blob) {
            if (!blob || blob.size < 4) {
              lastDetail = '「' + label + '」空文件';
              tried.push(lastDetail);
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
          tried.push(lastDetail);
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
  function wecomRailEl() {
    return document.getElementById('wecomRail');
  }
  function wecomRailIsOpen() {
    var rail = wecomRailEl();
    return !!(rail && !rail.hidden);
  }
  function wecomRailSyncTabs() {
    document.querySelectorAll('.wecom-rail-tab').forEach(function (btn) {
      btn.classList.toggle('on', btn.getAttribute('data-rail') === wecomState.railTab);
    });
    var aiPane = document.getElementById('wecomRailAi');
    var filesPane = document.getElementById('wecomRailFiles');
    if (aiPane) aiPane.hidden = wecomState.railTab !== 'ai';
    if (filesPane) filesPane.hidden = wecomState.railTab !== 'files';
  }
  var WECOM_RAIL_W_KEY = 'wecomRailWidthPx';
  var WECOM_RAIL_W_MIN = 280;
  var WECOM_RAIL_W_MAX = 900;
  var WECOM_RAIL_W_DEFAULT = 420;
  function wecomRailWidthClamp(px) {
    var n = Math.round(Number(px) || WECOM_RAIL_W_DEFAULT);
    if (n < WECOM_RAIL_W_MIN) n = WECOM_RAIL_W_MIN;
    if (n > WECOM_RAIL_W_MAX) n = WECOM_RAIL_W_MAX;
    var board = wecomBoardEl();
    if (board) {
      var maxByBoard = Math.floor((board.getBoundingClientRect().width || 0) * 0.72);
      if (maxByBoard >= WECOM_RAIL_W_MIN) n = Math.min(n, maxByBoard);
    }
    return n;
  }
  function wecomRailApplyWidth(px, persist) {
    var board = wecomBoardEl();
    if (!board) return;
    var w = wecomRailWidthClamp(px);
    board.style.setProperty('--wecom-rail-w', w + 'px');
    if (persist) {
      try { localStorage.setItem(WECOM_RAIL_W_KEY, String(w)); } catch (e) {}
    }
    return w;
  }
  function wecomRailRestoreWidth() {
    var saved = 0;
    try { saved = parseInt(localStorage.getItem(WECOM_RAIL_W_KEY) || '', 10) || 0; } catch (e) { saved = 0; }
    wecomRailApplyWidth(saved || WECOM_RAIL_W_DEFAULT, false);
  }
  function wecomRailBindResizer() {
    var handle = document.getElementById('wecomRailResizer');
    var board = wecomBoardEl();
    if (!handle || !board || handle.getAttribute('data-bound') === '1') return;
    handle.setAttribute('data-bound', '1');
    var dragging = false;
    var startX = 0;
    var startW = WECOM_RAIL_W_DEFAULT;
    function onMove(ev) {
      if (!dragging) return;
      var dx = startX - (ev.clientX || 0);
      wecomRailApplyWidth(startW + dx, false);
    }
    function onUp() {
      if (!dragging) return;
      dragging = false;
      board.classList.remove('is-resizing-rail');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      var cur = getComputedStyle(board).getPropertyValue('--wecom-rail-w').replace('px', '').trim();
      wecomRailApplyWidth(cur || startW, true);
    }
    handle.addEventListener('mousedown', function (ev) {
      if (ev.button !== 0) return;
      ev.preventDefault();
      dragging = true;
      startX = ev.clientX || 0;
      var cur = parseFloat(getComputedStyle(board).getPropertyValue('--wecom-rail-w')) || WECOM_RAIL_W_DEFAULT;
      startW = wecomRailWidthClamp(cur);
      board.classList.add('is-resizing-rail');
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
  function wecomRailOpen(tab) {
    wecomState.railTab = tab === 'files' ? 'files' : 'ai';
    var rail = wecomRailEl();
    if (rail) rail.hidden = false;
    if (wecomBoardEl()) wecomBoardEl().classList.add('has-rail');
    wecomRailRestoreWidth();
    wecomRailBindResizer();
    wecomRailSyncTabs();
    if (wecomState.railTab === 'files') {
      var filesBox = wecomFileBox && wecomFileBox();
      if (filesBox) wecomBindThumbs(filesBox);
    }
  }
  function wecomRailClose() {
    wecomState.aiKey = '';
    wecomState.aiStale = false;
    var rail = wecomRailEl();
    if (rail) rail.hidden = true;
    if (wecomBoardEl()) wecomBoardEl().classList.remove('has-rail');
    wecomAiTab = 'read';
    wecomAiSyncTabs();
    var body = document.getElementById('wecomAiBody');
    var sub = document.getElementById('wecomAiSub');
    if (body) body.innerHTML = '<div class="wecom-empty">选择会话后点「AI 解读」，将扫描全部文件、按规则分类，再按时间确认版本与修改意见。</div>' + wecomAiBindSkeleton();
    if (sub) sub.textContent = '扫描全部文件 → 规则分类 → 按时间绑定版本';
  }
  function wecomAiReset() {
    wecomRailClose();
  }
  function wecomAiOpen() {
    wecomRailOpen('ai');
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
      if (body) body.innerHTML = '<div class="wecom-empty">点「AI 解读」将扫描当前会话全部文件，按规则分类后再按时间确认申报书版本与修改意见。</div>' + wecomAiBindSkeleton();
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
  function wecomAiBindLabels() {
    return {
      time_window: '时间窗',
      name_id: '编号匹配',
      semantic: '语义上下文',
      semantic_strict: '语义严格'
    };
  }
  function wecomAiBindLocate(payload) {
    wecomLocateCopy(payload || {});
  }
  function wecomAiBindTalentKey(t, idx) {
    return String((t && (t.attachId || t.label)) || '') + '#' + String(idx || 0);
  }
  function wecomAiBindDisplayLabel(t) {
    var aid = String((t && t.attachId) || '').trim();
    var generic = /^(内容|号文|图片|正文|附件|扫描件|文件|材料|申报书|简历|护照|证件|证明|合同|协议|清单|其他|未识别编号)$/;
    var vers = (t && t.versions) || [];
    var fname = String((vers[0] && vers[0].filename) || '').trim();
    var lab = String((t && t.label) || '').trim();
    var company = String((t && t.company) || '').trim();
    if (/^\d{4,6}$/.test(aid)) {
      if (lab && lab.indexOf(aid) >= 0 && lab.length > aid.length) return lab;
      return company ? (aid + ' ' + company) : aid;
    }
    if (fname) return fname;
    if (lab && !generic.test(lab)) return lab;
    return fname || lab || '未识别编号';
  }
  function wecomAiBindEnsureOpen(talents) {
    var list = talents || [];
    if (!wecomState.bindOpenKeys || typeof wecomState.bindOpenKeys !== 'object') {
      wecomState.bindOpenKeys = {};
      if (list.length === 1) wecomState.bindOpenKeys[wecomAiBindTalentKey(list[0], 0)] = true;
    }
  }
  function wecomAiBindVersionLabel(v, idx) {
    var n = 0;
    var cv = String((v && v.chatVersion) || '');
    var m = cv.match(/(\d+)\s*$/);
    if (m) n = parseInt(m[1], 10) || 0;
    if (!n) n = (idx || 0) + 1;
    return '第' + n + '版';
  }
  function wecomAiBindLocatePayload(d, row) {
    return {
      sessionId: (row && (row.sessionId || row.session_id)) || d.session_id || wecomState.sessionId || '',
      sessionName: (row && (row.sessionName || row.session_name)) || d.session_name || wecomState.sessionName || '',
      messageId: String((row && (row.messageId || row.message_id)) || ''),
      time: (row && row.time) || ''
    };
  }
  function wecomAiCollapseRepeatedText(text) {
    var s = String(text || '').trim();
    if (s.length < 12) return s;
    var parts = s.split(/\n\s*\n+/).map(function (p) { return p.trim(); }).filter(Boolean);
    if (parts.length >= 2) {
      var norms = parts.map(function (p) { return p.replace(/\s+/g, ''); });
      var allSame = norms.every(function (n) { return n === norms[0]; });
      if (allSame) return parts[0];
    }
    var compact = s.replace(/\s+/g, '');
    if (compact.length >= 12) {
      var maxN = Math.min(24, Math.floor(compact.length / 6));
      for (var n = maxN; n >= 2; n--) {
        if (compact.length % n) continue;
        var unit = compact.slice(0, compact.length / n);
        if (unit && unit.repeat(n) === compact) {
          return s.slice(0, Math.max(1, Math.floor(s.length / n))).trim() || unit;
        }
      }
    }
    return s;
  }
  function wecomAiBindOpinionBody(op) {
    var kind = String((op && op.kind) || 'file');
    if (kind === 'text') {
      var body = wecomAiCollapseRepeatedText(String((op && op.text) || ''));
      if (body) return { tag: '文本意见', main: body, isText: true };
      return { tag: '文本意见', main: '（无正文，请重新解读以加载）', isText: true };
    }
    return { tag: '意见文件', main: String((op && op.filename) || '修改意见'), isText: false };
  }
  function wecomAiBindOpinionKey(op) {
    var kind = String((op && op.kind) || 'file');
    var body = kind === 'text'
      ? wecomAiCollapseRepeatedText(String((op && op.text) || '')).replace(/\s+/g, '').slice(0, 240)
      : String((op && op.filename) || '').trim().toLowerCase();
    var sender = String((op && op.sender) || '').trim();
    var day = String((op && op.time) || '').slice(0, 10);
    return [kind, body, sender, day].join('|');
  }
  function wecomAiBindDedupeOpinions(ops) {
    var list = ops || [];
    var out = [];
    var seenMid = {};
    var seenKey = {};
    list.forEach(function (op) {
      if (!op) return;
      var mid = String(op.messageId || op.message_id || '').trim();
      if (mid && seenMid[mid]) return;
      var key = wecomAiBindOpinionKey(op);
      if (key && seenKey[key]) return;
      if (mid) seenMid[mid] = true;
      if (key) seenKey[key] = true;
      out.push(op);
    });
    return out;
  }
  function wecomAiBindOpinionHtml(d, op) {
    var meta = wecomAiBindOpinionBody(op);
    var who = String((op && op.sender) || '').trim();
    var when = String((op && op.time) || '').trim();
    var loc = wecomAiBindLocatePayload(d, op);
    var html = '<div class="wecom-ai-bind-op-row">' +
      '<button type="button" class="wecom-ai-bind-op' + (meta.isText ? ' text' : '') + (wecomAiFileIsFocus(op) ? ' hit' : '') + '" data-ai-file-mid="' + escAttr(loc.messageId) + '" data-ai-file-name="' + escAttr((op && op.filename) || '') + '" data-locate="' + escAttr(JSON.stringify(loc)) + '" title="定位到聊天记录">' +
      '<span class="kind">' + esc(meta.tag) + '</span>' +
      '<span class="body">' + esc(meta.main) + '</span></button>';
    if (who || when) {
      html += '<div class="wecom-ai-bind-op-meta">' + esc([who, when].filter(Boolean).join(' · ')) + '</div>';
    }
    html += '</div>';
    return html;
  }
  function wecomAiIsScreenshotName(name) {
    var s = String(name || '');
    var base = s.replace(/\\/g, '/').split('/').pop() || s;
    if (/企业微信截图|微信截图|屏幕截图|screenshot|截图_\d{6,}/i.test(s) || /企业微信截图|微信截图|屏幕截图|screenshot|截图_\d{6,}/i.test(base)) return true;
    return /[\\/]temp[\\/]/i.test(s) && /\.(png|jpe?g|gif|webp|bmp|tif|tiff)$/i.test(base);
  }
  function wecomAiIsAppFileName(name) {
    if (wecomAiIsScreenshotName(name)) return false;
    var base = String(name || '').replace(/\\/g, '/').split('/').pop() || '';
    return /\.(docx?|docm|wps|xlsx?|xlsm|xls|pdf)$/i.test(base);
  }
  function wecomAiBindAppVersions(t) {
    return ((t && t.versions) || []).filter(function (v) {
      return wecomAiIsAppFileName((v && v.filename) || '');
    });
  }
  function wecomAiBindOne(d, block) {
    var html = '';
    if (block.error) html += '<p style="color:var(--bad)">' + esc(block.error) + '</p>';
    var talents = (block.talents || []).map(function (t) {
      return Object.assign({}, t, { versions: wecomAiBindAppVersions(t) });
    }).filter(function (t) { return (t.versions || []).length; });
    wecomAiBindEnsureOpen(talents);
    if (!talents.length) html += '<p>当前会话未识别到申报书版本</p>';
    talents.forEach(function (t, ti) {
      var key = wecomAiBindTalentKey(t, ti);
      var open = !!(wecomState.bindOpenKeys && wecomState.bindOpenKeys[key]);
      var vers = t.versions || [];
      var opN = 0;
      vers.forEach(function (v) { opN += wecomAiBindDedupeOpinions(v.opinions || []).length; });
      var title = wecomAiBindDisplayLabel(t);
      var aid = String(t.attachId || '').trim();
      var subBits = [];
      if (aid && /^\d{4,6}$/.test(aid) && title.indexOf(aid) < 0) subBits.push('编号 ' + aid);
      if (t.company && title.indexOf(t.company) < 0) subBits.push(t.company);
      subBits.push(vers.length + ' 个版本');
      if (opN) subBits.push(opN + ' 条意见');
      html += '<div class="wecom-ai-bind-talent' + (open ? ' open' : '') + (wecomAiTalentHasFocus(t) ? ' focus' : '') + '" data-bind-key="' + escAttr(key) + '">' +
        '<button type="button" class="wecom-ai-bind-talent-hd" data-bind-toggle="' + escAttr(key) + '" aria-expanded="' + (open ? 'true' : 'false') + '">' +
        '<span class="chev" aria-hidden="true"></span>' +
        '<span class="ttl"><b>' + esc(title) + '</b>' +
        (subBits.length ? '<em>' + esc(subBits.join(' · ')) + '</em>' : '') + '</span></button>';
      if (open) {
        html += '<div class="wecom-ai-bind-talent-bd">';
        if (!vers.length) {
          html += '<div class="wecom-ai-bind-miss">暂无申报书版本</div>';
        }
        vers.forEach(function (v, vi) {
          var ops = wecomAiBindDedupeOpinions(v.opinions || []);
          var isJump = !!v.jumpFromFiles;
          var vlab = (isJump && v.focusKind && v.focusKind !== '申报书') ? v.focusKind : wecomAiBindVersionLabel(v, vi);
          var vcls = (isJump && v.focusKind !== '申报书') ? ' jump' : ' app';
          html += '<div class="wecom-ai-bind-ver">' +
            '<div class="wecom-ai-bind-ver-hd">' +
            '<button type="button" class="wecom-ai-bind-op' + vcls + (wecomAiFileIsFocus(v) ? ' hit' : '') + '" data-ai-file-mid="' + escAttr(String(v.messageId || v.message_id || '')) + '" data-ai-file-name="' + escAttr(v.filename || '') + '" data-locate="' + escAttr(JSON.stringify(wecomAiBindLocatePayload(d, v))) + '" title="定位申报书">' +
            '<span class="kind">' + esc(vlab) + '</span>' +
            '<span class="body">' + esc(v.filename || '申报书') + '</span></button>' +
            '<span class="wecom-ai-bind-op-meta">' + esc([(v.sender || ''), (v.time || '')].filter(Boolean).join(' · ')) + '</span></div>';
          if (!ops.length) {
            html += '<div class="wecom-ai-bind-miss">本版无对应修改意见</div>';
          } else {
            html += '<div class="wecom-ai-bind-ops">';
            ops.forEach(function (op) { html += wecomAiBindOpinionHtml(d, op); });
            html += '</div>';
          }
          html += '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    var unbound = wecomAiBindDedupeOpinions(block.unboundOpinions || []);
    if (unbound.length) {
      var unboundOpen = unbound.some(wecomAiFileIsFocus);
      html += '<details class="wecom-ai-bind-unbound"' + (unboundOpen ? ' open' : '') + '><summary>未绑定意见 ' + unbound.length + ' 条</summary>';
      unbound.forEach(function (op) { html += wecomAiBindOpinionHtml(d, op); });
      html += '</details>';
    }
    return html;
  }
  function wecomAiBindSkeleton() {
    return '<div class="wecom-ai-card" id="wecomAiBindCard"><h5>申报书版本与修改意见</h5>' +
      wecomAiBindPinHtml() +
      '<div class="wecom-ai-bind-tabs">' +
      '<button type="button" class="wecom-ai-bind-tab on">时间窗</button></div>' +
      '<p>先扫描全部文件并按规则分类，再按时间确认各版申报书对应的修改意见</p></div>';
  }
  function wecomAiBindBlock(d) {
    var binds = d.bindPrompts;
    if (!binds || !Object.keys(binds).length) {
      return wecomAiBindSkeleton();
    }
    var labels = wecomAiBindLabels();
    var keys = Object.keys(binds);
    var view = wecomState.bindPromptView;
    var side = view === 'side';
    if (!side && keys.indexOf(view) < 0) view = keys[0];
    wecomState.bindPromptView = side ? 'side' : view;
    var tabs = '<div class="wecom-ai-bind-tabs">' + keys.map(function (k) {
      return '<button type="button" class="wecom-ai-bind-tab' + (!side && k === view ? ' on' : '') + '" data-bind="' + escAttr(k) + '">' + esc(labels[k] || k) + '</button>';
    }).join('') + '<button type="button" class="wecom-ai-bind-tab' + (side ? ' on' : '') + '" data-bind="side">并排</button></div>';
    var html = '<div class="wecom-ai-card" id="wecomAiBindCard"><h5>申报书版本与修改意见</h5>' + wecomAiBindPinHtml() + tabs;
    if (side) {
      html += '<div class="wecom-ai-bind-side">' + keys.map(function (k) {
        var block = binds[k] || {};
        return '<div class="wecom-ai-bind-col"><div class="wecom-ai-bind-col-hd">' + esc(labels[k] || k) + '</div>' + wecomAiBindOne(d, block) + '</div>';
      }).join('') + '</div>';
    } else {
      html += wecomAiBindOne(d, binds[view] || {});
    }
    html += '</div>';
    return html;
  }
  function wecomAiFileTypesBlock(d) {
    var ft = d.fileTypes || {};
    var items = ft.items || [];
    if (!items.length && !(ft.fileCount)) {
      var groups = d.groups || [];
      groups.forEach(function (g) {
        var one = (g.fileTypes || {}).items || [];
        one.forEach(function (it) {
          var row = Object.assign({}, it);
          row.session_name = row.session_name || g.session_name || '';
          items.push(row);
        });
      });
    }
    if (!items.length) return '';
    items = items.map(function (it) {
      var row = Object.assign({}, it);
      var lab = row.label || '其他';
      if ((lab === '申报书' || row.type === 'app') && (wecomAiIsScreenshotName(row.filename) || row.isImage || !wecomAiIsAppFileName(row.filename))) {
        row.label = '其他';
        row.type = 'other';
      }
      return row;
    });
    var counts = {};
    items.forEach(function (it) {
      var lab = it.label || '其他';
      counts[lab] = (counts[lab] || 0) + 1;
    });
    var order = ['申报书', '修改意见', '护照', '身份证明', '学历证明', '工作证明', '意向协议', '股权证明', '论文全文', '证件照', '电子签', '项目证明', '成果转化证明', '其他'];
    var pills = order.filter(function (k) { return counts[k]; }).map(function (k) {
      return '<button type="button" class="wecom-ai-tag jump" data-ai-jump="files" data-ai-cat="' + escAttr(k) + '" title="打开文件汇总 · ' + escAttr(k) + '">' + esc(k) + ' ' + esc(counts[k]) + '</button>';
    }).join('');
    var byLab = {};
    items.forEach(function (it) {
      var lab = it.label || '其他';
      (byLab[lab] = byLab[lab] || []).push(it);
    });
    Object.keys(byLab).forEach(function (lab) {
      if (order.indexOf(lab) < 0) order.push(lab);
    });
    var html = '<div class="wecom-ai-card"><h5>文件分类（规则：文件名与前后聊天）</h5>';
    if (ft.geminiNote) html += '<p class="wecom-ai-sub">' + esc(ft.geminiNote) + '</p>';
    html += '<div class="wecom-ai-stat" style="margin:8px 0">' + pills + '</div>';
    order.forEach(function (lab) {
      var rows = byLab[lab] || [];
      if (!rows.length) return;
      html += '<div class="wecom-ai-files-sec"><button type="button" class="wecom-ai-files-sec-hd jump" data-ai-jump="files" data-ai-cat="' + escAttr(lab) + '" title="打开文件汇总 · ' + escAttr(lab) + '"><b>' + esc(lab) + '</b><em>' + rows.length + '</em></button>';
      var hasFocus = rows.some(function (it) { return wecomAiFileIsFocus(it); });
      var shown = rows;
      if (hasFocus) {
        var focused = rows.filter(wecomAiFileIsFocus);
        var rest = rows.filter(function (it) { return !wecomAiFileIsFocus(it); });
        shown = focused.concat(rest);
      } else if (rows.length > 12) {
        shown = rows.slice(0, 12);
      }
      shown.forEach(function (it) {
        var mid = it.messageId || it.message_id || '';
        var loc = {
          sessionId: it.sessionId || it.session_id || d.session_id || wecomState.sessionId || '',
          sessionName: it.session_name || d.session_name || wecomState.sessionName || '',
          messageId: String(mid || ''),
          time: it.time || ''
        };
        var thumbFile = {
          filename: it.filename,
          mediaUrl: it.mediaUrl || it.media_url || '',
          copies: it.copies || [],
          messageId: mid,
          sessionId: loc.sessionId,
          isImage: it.isImage
        };
        var wantThumb = wecomFileWantThumb(thumbFile);
        var thumbSrc = wecomFileThumbSrc(thumbFile);
        var on = wecomAiFileIsFocus(it) ? ' hit' : '';
        html += '<button type="button" class="wecom-ai-file jump' + (wantThumb ? ' is-img' : '') + on + '" data-ai-file-mid="' + escAttr(String(mid || '')) + '" data-ai-file-name="' + escAttr(it.filename || '') + '" data-ai-locate="' + escAttr(JSON.stringify(loc)) + '">' +
          (wantThumb ? wecomThumbHtml(thumbSrc, '预览', 'wecom-files-thumb', wecomFileThumbFallbacks(thumbFile, thumbSrc)) : '') +
          '<span class="nm">' + esc(wecomFileBaseName(it.filename) || it.filename || '未命名') + '</span>' +
          '<div class="why">' + esc((it.sender ? it.sender + ' · ' : '') + (it.time || '') + (it.reason ? ' · ' + it.reason : '')) + '</div></button>';
      });
      if (rows.length > shown.length) html += '<div class="why">另有 ' + (rows.length - shown.length) + ' 个 · <button type="button" class="linkish" data-ai-jump="files" data-ai-cat="' + escAttr(lab) + '">在文件汇总查看</button></div>';
      html += '</div>';
    });
    html += '</div>';
    return html;
  }
  function wecomAiFileBase(name) {
    var s = String(name || '').trim().toLowerCase();
    s = s.replace(/^\[.*?\]\s*/, '').replace(/^【.*?】\s*/, '');
    var i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
    if (i >= 0) s = s.slice(i + 1);
    return s;
  }
  function wecomAiFocusMidSet() {
    var f = wecomAiFocusFile || {};
    var out = {};
    [f.messageId].concat(f.mids || []).forEach(function (id) {
      var s = String(id || '').trim();
      if (s && s !== '0') out[s] = true;
    });
    return out;
  }
  function wecomAiCollectMids(it) {
    var ids = [];
    if (!it) return ids;
    ids.push(it.messageId || it.message_id);
    (it.copies || []).forEach(function (c) {
      ids.push((c && (c.message_id || c.messageId)) || '');
    });
    return ids.map(function (x) { return String(x || '').trim(); }).filter(function (s) { return s && s !== '0'; });
  }
  function wecomAiNameClose(a, b) {
    var x = wecomAiFileBase(a).replace(/\.[^.]+$/, '');
    var y = wecomAiFileBase(b).replace(/\.[^.]+$/, '');
    if (!x || !y) return false;
    if (x === y) return true;
    if (x.length >= 8 && y.indexOf(x) >= 0) return true;
    if (y.length >= 8 && x.indexOf(y) >= 0) return true;
    return false;
  }
  function wecomAiFileIsFocus(it) {
    var focus = wecomAiFocusFile;
    if (!focus || !it) return false;
    var mids = wecomAiFocusMidSet();
    var its = wecomAiCollectMids(it);
    for (var i = 0; i < its.length; i++) {
      if (mids[its[i]]) return true;
    }
    return wecomAiNameClose(focus.filename, it.filename);
  }
  function wecomAiTalentHasFocus(t) {
    var vers = wecomAiBindAppVersions(t);
    for (var i = 0; i < vers.length; i++) {
      if (wecomAiFileIsFocus(vers[i])) return true;
      var ops = vers[i].opinions || [];
      for (var j = 0; j < ops.length; j++) {
        if (wecomAiFileIsFocus(ops[j])) return true;
      }
    }
    return false;
  }
  function wecomAiBindHasFocus(block) {
    var talents = (block && block.talents) || [];
    for (var i = 0; i < talents.length; i++) {
      if (wecomAiTalentHasFocus(talents[i])) return true;
    }
    return ((block && block.unboundOpinions) || []).some(wecomAiFileIsFocus);
  }
  function wecomAiFocusRow() {
    var focus = wecomAiFocusFile;
    if (!focus) return null;
    var row = null;
    wecomFileAllRows().forEach(function (f) {
      if (!row && wecomAiFileIsFocus(f)) row = f;
    });
    return row || {
      messageId: focus.messageId,
      filename: focus.filename,
      label: focus.label || '',
      attachId: focus.attachId || '',
      sender: focus.sender || '',
      time: focus.time || '',
      copies: (focus.mids || []).map(function (id) { return { message_id: id }; })
    };
  }
  function wecomAiAidFromName(name) {
    if (wecomAiIsScreenshotName(name)) return '';
    var base = String(name || '').replace(/\\/g, '/').split('/').pop() || '';
    base = base.replace(/\.[^.]+$/, '');
    var m = base.match(/(?:^|[^\d])(\d{4,6})(?=[^\d]|$)/);
    return m ? m[1] : '';
  }
  function wecomAiFocusAid() {
    var focus = wecomAiFocusFile || {};
    var row = wecomAiFocusRow() || {};
    return String(focus.attachId || row.attachId || '').trim() || wecomAiAidFromName(focus.filename || row.filename || '');
  }
  function wecomAiFocusKind() {
    var row = wecomAiFocusRow() || {};
    var name = (wecomAiFocusFile && wecomAiFocusFile.filename) || row.filename || '';
    var lab = String((wecomAiFocusFile && wecomAiFocusFile.label) || row.label || '').trim();
    if (wecomAiIsScreenshotName(name) || (lab === '申报书' && !wecomAiIsAppFileName(name))) {
      lab = lab === '申报书' ? '其他' : lab;
    }
    if (lab === '申报书' || lab === '修改意见') return lab;
    var base = wecomAiFileBase(name);
    if (wecomAiIsAppFileName(name) && /申报书|申请表|application/i.test(base)) return '申报书';
    if (/修改意见|意见稿|修订意见/i.test(base)) return '修改意见';
    return lab || '其他';
  }
  function wecomAiBindPinHtml() {
    var f = wecomAiFocusFile;
    if (!f || !(f.filename || f.messageId)) return '';
    var kind = wecomAiFocusKind();
    return '<div class="wecom-ai-bind-pin hit" data-ai-file-mid="' + escAttr(f.messageId || '') + '" data-ai-file-name="' + escAttr(f.filename || '') + '">' +
      '<span class="kind">' + esc(kind === '其他' ? '跳转文件' : kind) + '</span>' +
      '<span class="nm">' + esc(f.filename || f.messageId) + '</span></div>';
  }
  function wecomAiPrepareFocusOpen(d) {
    if (!wecomAiFocusFile || !d) return;
    var binds = d.bindPrompts || {};
    var foundView = '';
    Object.keys(binds).forEach(function (k) {
      var block = binds[k] || {};
      if (wecomAiBindHasFocus(block) && !foundView) foundView = k;
      var talents = block.talents || [];
      talents.forEach(function (t, ti) {
        if (!wecomAiTalentHasFocus(t)) return;
        wecomState.bindOpenKeys = wecomState.bindOpenKeys || {};
        wecomState.bindOpenKeys[wecomAiBindTalentKey(t, ti)] = true;
      });
    });
    if (foundView && wecomState.bindPromptView !== 'side') wecomState.bindPromptView = foundView;
  }
  function wecomAiCloneBinds(d) {
    var out = Object.assign({}, d);
    var src = d.bindPrompts;
    out.bindPrompts = {};
    if (!src || typeof src !== 'object' || Array.isArray(src)) return out;
    Object.keys(src).forEach(function (k) {
      var block = src[k] || {};
      out.bindPrompts[k] = {
        error: block.error,
        talents: (block.talents || []).map(function (t) {
          return Object.assign({}, t, {
            versions: (t.versions || []).map(function (v) {
              return Object.assign({}, v, { opinions: (v.opinions || []).slice() });
            })
          });
        }),
        unboundOpinions: (block.unboundOpinions || []).slice()
      };
    });
    return out;
  }
  function wecomAiMakeFocusBindRow(row, kind) {
    row = row || {};
    var mid = String(row.messageId || row.message_id || '');
    var copies = row.copies || ((wecomAiFocusFile && wecomAiFocusFile.mids) || []).map(function (id) {
      return { message_id: id };
    });
    if (kind === '修改意见') {
      var name = String(row.filename || '');
      var isText = !name || /\.(txt|md)$/i.test(name);
      return {
        messageId: mid,
        filename: name,
        time: row.time || '',
        sender: row.sender || '',
        kind: isText ? 'text' : 'file',
        text: isText ? ('从文件汇总跳转：' + (name || '修改意见')) : '',
        copies: copies,
        jumpFromFiles: true,
        focusKind: kind
      };
    }
    return {
      messageId: mid,
      filename: row.filename || '',
      time: row.time || '',
      sender: row.sender || '',
      opinions: [],
      copies: copies,
      jumpFromFiles: true,
      focusKind: kind || '申报书',
      company: row.company || ''
    };
  }
  function wecomAiFindBindTalentIdx(talents, aid) {
    var i;
    for (i = 0; i < talents.length; i++) {
      if (wecomAiTalentHasFocus(talents[i])) return i;
    }
    if (aid) {
      for (i = 0; i < talents.length; i++) {
        if (String(talents[i].attachId || '') === aid) return i;
      }
    }
    return -1;
  }
  function wecomAiInjectFocusBind(d) {
    var focus = wecomAiFocusFile;
    if (!focus || !d) return d;
    if (!(focus.messageId || focus.filename)) return d;
    var row = wecomAiFocusRow() || {};
    var kind = wecomAiFocusKind();
    var aid = wecomAiFocusAid();
    var out = wecomAiCloneBinds(d);
    if (!Object.keys(out.bindPrompts).length) {
      out.bindPrompts.time_window = { talents: [], unboundOpinions: [] };
    }
    Object.keys(out.bindPrompts).forEach(function (k) {
      var block = out.bindPrompts[k];
      if (wecomAiBindHasFocus(block)) return;
      if (kind !== '申报书' && kind !== '修改意见') return;
      var talents = block.talents || [];
      var ti = wecomAiFindBindTalentIdx(talents, aid);
      if (kind === '修改意见') {
        var op = wecomAiMakeFocusBindRow(row, kind);
        if (ti >= 0) {
          var t = talents[ti];
          var vers = (t.versions || []).slice();
          if (vers.length) {
            var last = Object.assign({}, vers[vers.length - 1], {
              opinions: (vers[vers.length - 1].opinions || []).concat([op])
            });
            vers[vers.length - 1] = last;
          } else {
            vers = [{
              filename: t.label || '申报书',
              messageId: '',
              chatVersion: 'chat-1',
              opinions: [op],
              jumpFromFiles: true,
              focusKind: '申报书'
            }];
          }
          talents[ti] = Object.assign({}, t, { versions: vers });
        } else {
          block.unboundOpinions = (block.unboundOpinions || []).concat([op]);
        }
        return;
      }
      if (!wecomAiIsAppFileName(row.filename || '')) return;
      var app = wecomAiMakeFocusBindRow(row, '申报书');
      app.chatVersion = 'chat-' + ((ti >= 0 ? ((talents[ti].versions || []).length) : 0) + 1);
      if (ti >= 0) {
        var host = talents[ti];
        talents[ti] = Object.assign({}, host, { versions: (host.versions || []).concat([app]) });
      } else {
        talents.push({
          attachId: aid,
          label: (aid ? aid + ' ' : '') + (row.filename || kind || '跳转文件'),
          company: '',
          person: '',
          versions: [app],
          jumpFromFiles: true
        });
      }
    });
    return out;
  }
  function wecomAiInjectFocusItem(d) {
    return wecomAiInjectFocusBind(d);
  }
  function wecomAiElIsFocus(el) {
    if (!el || !wecomAiFocusFile) return false;
    return wecomAiFileIsFocus({
      messageId: el.getAttribute('data-ai-file-mid') || '',
      filename: el.getAttribute('data-ai-file-name') || ''
    });
  }
  function wecomAiFindFocusEl(body) {
    if (!body) return null;
    var ops = body.querySelectorAll('.wecom-ai-bind-op');
    for (var i = 0; i < ops.length; i++) {
      if (wecomAiElIsFocus(ops[i])) return ops[i];
    }
    return body.querySelector('.wecom-ai-bind-talent.focus') ||
      body.querySelector('.wecom-ai-bind-pin') ||
      document.getElementById('wecomAiBindCard');
  }
  function wecomAiScrollToEl(el) {
    var body = document.getElementById('wecomAiBody');
    if (!el || !body) return;
    var br = body.getBoundingClientRect();
    var er = el.getBoundingClientRect();
    var extra = Math.max(16, Math.floor(br.height / 4) - Math.floor(er.height / 2));
    body.scrollTop = Math.max(0, body.scrollTop + (er.top - br.top) - extra);
  }
  function wecomAiApplyFocusFile(keep) {
    var focus = wecomAiFocusFile;
    if (!focus) return false;
    var body = document.getElementById('wecomAiBody');
    var hit = wecomAiFindFocusEl(body);
    if (!hit) {
      if (!keep) {
        wecomAiFocusFile = null;
        say(false, '最新解读记录里没有这个文件，可点「重新解读」');
      }
      return false;
    }
    if (body) {
      body.querySelectorAll('.wecom-ai-file.hit, .wecom-ai-bind-op.hit, .wecom-ai-case.hit, .wecom-ai-bind-pin.hit, #wecomAiBindCard.hit').forEach(function (el) {
        if (el !== hit) el.classList.remove('hit');
      });
    }
    hit.classList.add('hit');
    wecomAiScrollToEl(hit);
    if (!keep) {
      wecomAiFocusFile = null;
      say(true, '已定位到申报书版本与修改意见');
      if (wecomAiHitTimer) clearTimeout(wecomAiHitTimer);
      wecomAiHitTimer = setTimeout(function () { hit.classList.remove('hit'); }, 6000);
    }
    return true;
  }
  function wecomAiOpenLatestForFile(f) {
    f = f || {};
    if (!wecomState.sessionId) {
      say(false, '请先选择会话');
      return;
    }
    wecomAiOpen();
    wecomAiTab = 'read';
    wecomAiSyncTabs();
    wecomAiFocusFile = {
      messageId: String(f.messageId || '').trim(),
      filename: String(f.filename || '').trim(),
      label: String(f.label || '').trim(),
      attachId: String(f.attachId || '').trim(),
      sender: String(f.sender || '').trim(),
      time: String(f.time || '').trim(),
      mids: Array.isArray(f.mids) ? f.mids.map(function (id) { return String(id || '').trim(); }).filter(Boolean) : []
    };
    if (wecomAiJumping) return;
    wecomAiJumping = true;
    var body = document.getElementById('wecomAiBody');
    if (body) body.innerHTML = '<div class="wecom-empty">正在打开最新解读记录…</div>';
    var last = wecomAiLastRead;
    var same = last && String(last.session_id || '') === String(wecomState.sessionId || '');
    function done(d) {
      wecomAiJumping = false;
      if (!d) {
        wecomAiFocusFile = null;
        if (body) body.innerHTML = '<div class="wecom-empty">当前会话还没有解读记录。请先点「AI 解读」。</div>' + wecomAiBindSkeleton();
        say(false, '当前会话还没有解读记录，请先点「AI 解读」');
        return;
      }
      wecomAiRender(d);
    }
    wecomQuery('/api/wecom/ai-read-latest?session_id=' + encodeURIComponent(wecomState.sessionId)).then(function (res) {
      if (res.ok) {
        done(res.data || {});
        return;
      }
      done(same ? last : null);
    }).catch(function () {
      done(same ? last : null);
    });
  }
  function wecomAiJump(kind, cat) {
    kind = String(kind || '');
    cat = String(cat || '');
    if (kind === 'messages') {
      wecomGoChatSec();
      var stream = document.getElementById('wecomStream');
      if (stream) {
        stream.classList.add('wecom-stream-flash');
        if (stream.scrollIntoView) stream.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        setTimeout(function () { stream.classList.remove('wecom-stream-flash'); }, 1200);
      }
      say(true, '已切换到聊天消息');
      return;
    }
    if (kind === 'files') {
      wecomFileOpenCat(cat || '');
      return;
    }
    if (kind === 'app') {
      wecomFileOpenCat('申报书');
      return;
    }
    if (kind === 'opinion') {
      wecomFileOpenCat('修改意见');
      return;
    }
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
      var bits = [title];
      if (d.start_date || d.end_date) bits.push((d.start_date || '—') + ' ~ ' + (d.end_date || '—'));
      else if (!isWatch) bits.push('全部文件');
      if (d.recordAt) bits.push('已保存 ' + d.recordAt);
      if (d.engine) bits.push(d.engine);
      if (d.model) bits.push(d.model);
      sub.textContent = bits.filter(Boolean).join(' · ');
    }
    var stats = '<div class="wecom-ai-stat">' +
      (isWatch ? '<span class="wecom-ai-tag">会话 ' + esc(d.groupCount || groups.length || 0) + '</span>' : '') +
      '<button type="button" class="wecom-ai-tag jump" data-ai-jump="messages" title="查看聊天消息">消息 ' + esc(d.messageCount || 0) + '</button>' +
      '<button type="button" class="wecom-ai-tag jump" data-ai-jump="files" title="打开文件汇总">文件 ' + esc(scan.fileCount || 0) + '</button>' +
      '<button type="button" class="wecom-ai-tag jump" data-ai-jump="app" title="打开文件汇总 · 申报书">申报书 ' + esc(scan.appFileCount || 0) + '</button>' +
      '<button type="button" class="wecom-ai-tag jump" data-ai-jump="opinion" title="打开文件汇总 · 修改意见">意见文档 ' + esc(scan.opinionFileCount || 0) + '</button>' +
      (d.readyCount ? '<button type="button" class="wecom-ai-tag ok jump" data-ai-jump="cases" title="查看可上传申报书">可上传 ' + esc(d.readyCount) + '</button>' : '') +
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
          var opsHtml = wecomAiBindDedupeOpinions(c.opinions || []).map(function (o) {
            if (o.kind === 'text') {
              var tx = wecomAiCollapseRepeatedText(String(o.text || ''));
              return '<div class="wecom-ai-case-op text">' + esc(tx || ('文本意见 · ' + (o.filename || '群聊修改意见'))) + '</div>';
            }
            return '<div class="wecom-ai-case-op file">' + esc(o.filename || '意见文档') + '</div>';
          }).join('');
          var grp = c.session_name ? '<span style="color:#8a93a6">' + esc(c.session_name) + ' · </span>' : '';
          var canUp = c.ready && !c.existing;
          var upBtn = canUp
            ? ' <button type="button" class="mini primary" data-ai-up="' + escAttr(c.id || '') + '" data-sid="' + escAttr(c.session_id || d.session_id || '') + '">上传</button>'
            : '';
          var caseMid = String((c.messageId || c.message_id || (c.app && (c.app.messageId || c.app.message_id)) || '') || '');
          return '<div class="wecom-ai-case' + (wecomAiFileIsFocus(c.app || c) ? ' hit' : '') + '" data-ai-case="' + escAttr(c.id || '') + '" data-ai-file-mid="' + escAttr(caseMid) + '" data-ai-file-name="' + escAttr(c.filename || (c.app && c.app.filename) || '') + '">' + grp + '<b>' + esc(c.filename || c.id) + '</b> ' + st + upBtn +
            '<div style="color:#8a93a6;margin-top:4px">' + esc(c.time || '') + '</div>' +
            (opsHtml ? ('<div class="wecom-ai-case-ops">' + opsHtml + '</div>') : '<div class="wecom-ai-bind-miss">无配对意见</div>') +
            '</div>';
        }).join('') + '</div>';
    }
    var stale = wecomState.aiStale ? '<div class="wecom-ai-stale" id="wecomAiStale">已切换会话，请重新解读</div>' : '<div class="wecom-ai-stale" id="wecomAiStale" hidden>已切换会话，请重新解读</div>';
    var hasOpEarly = d.hasOpinion || (d.opinion && d.opinion.hasOpinion);
    var nextAiKey = [d.scope || 'session', d.session_id, d.start_date, d.end_date, d.messageCount, hasOpEarly, d.recordId || d.id || d.recordAt || ''].join('\t');
    if (wecomState.aiKey && wecomState.aiKey !== nextAiKey) wecomState.bindOpenKeys = null;
    var renderD = d;
    if (wecomAiFocusFile) {
      renderD = wecomAiInjectFocusItem(d);
      wecomAiPrepareFocusOpen(renderD);
    }
    var bindBlock = wecomAiBindBlock(renderD);
    var fileBlock = wecomAiFileTypesBlock(renderD);
    var acts = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">' +
      '<button type="button" class="mini primary" id="wecomAiSplitGo">拆解任务</button>' +
      '<button type="button" class="mini" id="wecomAiRefresh">重新解读</button>' +
      '<button type="button" class="mini" id="wecomAiGoLog">解读记录</button></div>';
    body.innerHTML = stats + stale + bindBlock + fileBlock + sumBlock + opBlock + riskBlock + caseBlock + acts;
    body.querySelectorAll('.wecom-ai-bind-tab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        wecomState.bindPromptView = btn.getAttribute('data-bind') || 'time_window';
        wecomAiRender(d);
      });
    });
    body.querySelectorAll('[data-bind-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var key = btn.getAttribute('data-bind-toggle') || '';
        if (!key) return;
        wecomState.bindOpenKeys = wecomState.bindOpenKeys || {};
        wecomState.bindOpenKeys[key] = !wecomState.bindOpenKeys[key];
        wecomAiRender(d);
      });
    });
    body.querySelectorAll('.wecom-ai-bind-op').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          wecomAiBindLocate(JSON.parse(btn.getAttribute('data-locate') || '{}'));
        } catch (e) {
          say(false, '定位参数无效');
        }
      });
    });
    body.querySelectorAll('[data-ai-jump]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var kind = btn.getAttribute('data-ai-jump') || '';
        if (kind === 'cases') {
          var box = body.querySelector('.wecom-ai-case');
          if (box && box.scrollIntoView) box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          say(true, '已定位到申报书配对');
          return;
        }
        wecomAiJump(kind, btn.getAttribute('data-ai-cat') || '');
      });
    });
    body.querySelectorAll('[data-ai-locate]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          wecomLocateCopy(JSON.parse(btn.getAttribute('data-ai-locate') || '{}'));
        } catch (e) {
          say(false, '定位参数无效');
        }
      });
    });
    var splitGo = document.getElementById('wecomAiSplitGo');
    if (splitGo) splitGo.addEventListener('click', wecomSplitOpen);
    body.querySelectorAll('[data-ai-up]').forEach(function (btn) {
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        wecomAiCaseUpload(btn, d);
      });
    });
    var refresh = document.getElementById('wecomAiRefresh');
    if (refresh) refresh.addEventListener('click', wecomAiRead);
    var goLog = document.getElementById('wecomAiGoLog');
    if (goLog) goLog.addEventListener('click', function () { wecomAiSetTab('log'); });
    wecomBindThumbs(body);
    wecomAiLastRead = d;
    wecomAiTab = 'read';
    wecomAiSyncTabs();
    wecomState.aiStale = false;
    wecomState.aiKey = nextAiKey;
    if (wecomAiFocusFile) {
      requestAnimationFrame(function () {
        wecomAiApplyFocusFile(true);
        setTimeout(function () { wecomAiApplyFocusFile(true); }, 80);
        setTimeout(function () { wecomAiApplyFocusFile(false); }, 280);
      });
    }
  }
  function wecomAiCaseUpload(btn, d, force) {
    var cid = (btn && btn.getAttribute && btn.getAttribute('data-ai-up')) || '';
    if (!cid) {
      say(false, '没有可上传的申报书');
      return;
    }
    var sid = (btn.getAttribute('data-sid') || (d && d.session_id) || wecomState.sessionId || '').trim();
    var sname = (d && d.session_name) || wecomState.sessionName || '';
    var cases = (d && d.cases) || [];
    var hit = null;
    for (var i = 0; i < cases.length; i++) {
      if (String(cases[i].id || '') === String(cid)) { hit = cases[i]; break; }
    }
    if (!sid && hit) sid = String(hit.session_id || '').trim();
    if (!sname && hit) sname = hit.session_name || '';
    if (!sid) {
      say(false, '缺少会话，无法上传');
      return;
    }
    var row = btn.closest('.wecom-ai-case') || btn.parentElement;
    btn.disabled = true;
    btn.textContent = '正在取文件…';
    fetch('/api/wecom/split-create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        session_id: sid,
        session_name: sname,
        source_id: wecomSourceParam() || '',
        start_date: (d && d.start_date) || '',
        end_date: (d && d.end_date) || '',
        windowHours: 48,
        caseIds: [cid],
        case: hit || undefined,
        force: !!force
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        var got = x.j || {};
        var created = (got.created || [])[0];
        var err = (got.errors || [])[0];
        if (!x.ok || got.ok === false || !created) {
          btn.disabled = false;
          btn.textContent = '上传';
          say(false, (err && err.detail) || got.detail || '上传失败');
          return;
        }
        var href = created.url || ('/t/' + created.id);
        btn.outerHTML = '<a class="mini primary" href="' + escAttr(href) + '" target="_blank" rel="noopener">打开任务</a>' +
          ' <span class="wecom-ai-tag ok">已上传</span>';
        if (row) {
          var tag = row.querySelector('.wecom-ai-tag.ok');
          if (tag && tag.textContent.indexOf('可上传') >= 0) tag.textContent = '已上传';
        }
        say(true, '已上传到任务列表（生成计划后需确认）');
        if (typeof loadOverview === 'function') loadOverview();
      }).catch(function (e) {
        btn.disabled = false;
        btn.textContent = '上传';
        say(false, e.message || '上传失败');
      });
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
    if (sub) sub.textContent = '分析中 · ' + name;
    wecomAiLoading = true;
    if (btn) btn.disabled = true;
    wecomPostProgress('/api/wecom/ai-read', {
      scope: 'session',
      session_id: wecomState.sessionId,
      session_name: wecomState.sessionName || '',
      source_id: wecomSourceParam() || '',
      start_date: '',
      end_date: '',
      windowHours: 0,
      bind: true,
      bindPrompts: ['time_window']
    }, body, function (d) {
      wecomAiLoading = false;
      if (btn) btn.disabled = false;
      wecomAiRender(d || {});
      say(true, 'AI 解读完成');
    }, function (e) {
      wecomAiLoading = false;
      if (btn) btn.disabled = false;
      if (body) body.innerHTML = '<div class="wecom-empty">' + esc(e.message || '请求失败') + '</div>';
      say(false, e.message || 'AI 解读失败');
    });
  }
  function idTraceOpText(op) {
    if (!op) return '修改意见';
    if (typeof op === 'string') return op;
    if (op.kind === 'text' || (op.text && !op.name && !op.snippet)) {
      return wecomAiCollapseRepeatedText(String(op.text || '')) || op.filename || '文本意见';
    }
    return op.name || op.filename || op.snippet || op.text || '修改意见';
  }
  function idTraceOpMeta(op) {
    if (!op || typeof op !== 'object') return '';
    var bits = [op.sender, op.time, op.snippet && op.name ? op.snippet : ''].filter(Boolean);
    return bits.join(' · ');
  }
  function idTraceCell(s) { return '<td>' + (s || '—') + '</td>'; }
  function idTraceActs(items) {
    var bits = (items || []).filter(Boolean);
    return bits.length ? bits.join(' ') : '—';
  }
  function idTraceSessAttrs(sid, name) {
    if (!sid && !name) return '';
    return ' data-id-trace-sid="' + escAttr(sid || '') + '" data-id-trace-sname="' + escAttr(name || '') + '"';
  }
  function idTraceTable(cols, rows, emptyText) {
    if (!(rows || []).length) return '<div class="id-trace-miss">' + esc(emptyText || '暂无') + '</div>';
    return '<div class="id-trace-table-wrap"><table class="id-trace-list"><thead><tr>' +
      cols.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
  }
  function idTraceRow(cells, sid, name, aid) {
    var extra = idTraceSessAttrs(sid, name);
    if (aid) extra += ' data-id-trace-aid="' + escAttr(aid) + '"';
    return '<tr' + extra + '>' + cells.map(idTraceCell).join('') + '</tr>';
  }
  function idTraceOpenSess(sid, name) {
    if (!sid && !name) return '';
    return '<button type="button" class="mini"' + idTraceSessAttrs(sid, name) + '>打开会话</button>';
  }
  function idTraceOpinionList(ops, clauses, dedupe) {
    var rows = [];
    var list = ops || [];
    if (dedupe) list = wecomAiBindDedupeOpinions(list);
    list.forEach(function (op) {
      rows.push({
        kind: (op && op.kind === 'text') ? '文本意见' : '修改意见',
        text: idTraceOpText(op),
        meta: idTraceOpMeta(op)
      });
    });
    (clauses || []).forEach(function (c) {
      var line = (c && (c.clause || c.opinion)) || '';
      if (line) rows.push({ kind: '条款', text: line, meta: (c && c.opinion && c.clause) ? c.opinion : '' });
    });
    return rows;
  }
  function idTraceIsOpinion(f) {
    var kind = String((f && (f.kind || f.type)) || '');
    var label = String((f && f.label) || '');
    return kind === 'opinion' || label === '修改意见';
  }
  function idTraceByGroupHtml(d) {
    d = d || {};
    var html = '';
    if (d.geminiNote) html += '<p class="id-trace-sub">' + esc(d.geminiNote) + '</p>';
    html += '<div class="id-trace-sec"><h4>对应群聊</h4>';
    var groups = d.sessions || [];
    if (!groups.length) {
      var seenG = {};
      (d.chatVersions || []).forEach(function (v) {
        var sid0 = v.sessionId || '';
        var name0 = v.sessionName || '';
        var gk = sid0 + '|' + name0;
        if (!sid0 && !name0 || seenG[gk]) return;
        seenG[gk] = true;
        groups.push({ sessionId: sid0, sessionName: name0, isDelivery: v.isDelivery, lastTime: v.time || '' });
      });
    }
    if (!groups.length) {
      if (!(d.versions || []).length && !(d.chatVersions || []).length) {
        html += '<div class="id-trace-miss">未在群聊里找到该编号</div>';
      }
      html += '</div>';
    } else {
      groups.forEach(function (s) {
        var sid = s.sessionId || '';
        var name = s.sessionName || '';
        var gfiles = [];
        var gvers = [];
        var boundOpFn = {};
        function markOpFn(v) {
          (v.opinions || []).forEach(function (op) {
            var fn = String((op && (op.filename || op.name)) || '').toLowerCase();
            if (fn) boundOpFn[fn] = true;
          });
        }
        (d.chatVersions || []).forEach(function (v) {
          if ((sid && v.sessionId === sid) || (name && v.sessionName === name)) {
            gvers.push(v);
            markOpFn(v);
          }
        });
        (d.versions || []).forEach(function (v) {
          if (name && v.sessionName === name) {
            gvers.push(v);
            markOpFn(v);
          }
        });
        (d.files || []).forEach(function (f) {
          (f.copies || []).forEach(function (c) {
            if ((sid && c.sessionId === sid) || (name && c.sessionName === name)) {
              var row = {
                filename: f.filename || '',
                kind: c.kind || f.kind || '',
                label: c.label || f.label || f.kind || '',
                sender: c.sender || '',
                time: c.time || '',
                sessionId: c.sessionId,
                sessionName: c.sessionName,
                classReason: c.classReason || f.classReason || ''
              };
              if (idTraceIsOpinion(row)) {
                var fnk = String(row.filename || '').toLowerCase();
                if (fnk && boundOpFn[fnk]) return;
                if (fnk) boundOpFn[fnk] = true;
                gvers.push({
                  version: '修改意见',
                  filename: row.filename,
                  time: row.time,
                  sender: row.sender,
                  sessionId: row.sessionId,
                  sessionName: row.sessionName,
                  opinions: [{ kind: 'file', filename: row.filename, sender: row.sender, time: row.time, text: row.classReason }]
                });
                return;
              }
              gfiles.push(row);
            }
          });
        });
        (d.fileOpinions || []).forEach(function (op) {
          var fnk = String(op.filename || '').toLowerCase();
          if (fnk && boundOpFn[fnk]) return;
          if ((sid && op.sessionId === sid) || (name && op.sessionName === name)) {
            if (fnk) boundOpFn[fnk] = true;
            gvers.push({
              version: '修改意见',
              filename: op.filename || '',
              time: op.time || '',
              sender: op.sender || '',
              sessionId: op.sessionId,
              sessionName: op.sessionName,
              opinions: [op]
            });
          }
        });
        var opN = 0;
        gvers.forEach(function (v) { opN += (v.opinions || []).length + (v.clauses || []).length; });
        html += '<details class="id-trace-g" open>' +
          '<summary>' + esc(name || sid || '未命名') +
          (s.isDelivery ? ' <span class="tag deliv">交付</span>' : '') +
          '<em>' + gfiles.length + ' 个文件 · ' + (gvers.length || opN ? (gvers.length + ' 个版本 / ' + opN + ' 条意见') : '暂无意见') + '</em>' +
          (s.lastTime ? '<em>' + esc(s.lastTime) + '</em>' : '') +
          '</summary>';
        html += idTraceTable(
          ['类型', '文件', '发送人', '时间'],
          gfiles.map(function (f) {
            return idTraceRow([
              esc(f.label || '文件'), esc(f.filename || ''), esc(f.sender || ''),
              esc(f.time || '') + (f.classReason ? '<div class="id-trace-sub">' + esc(f.classReason) + '</div>' : '')
            ], f.sessionId, f.sessionName);
          }),
          '该群暂无对应文件'
        );
        var verRows = [];
        gvers.forEach(function (v) {
          var ops = idTraceOpinionList(v.opinions, v.clauses, true);
          var ver = esc(v.chatVersion || v.version || '版本');
          var fn = esc(v.filename || v.appName || '');
          if (!ops.length) {
            verRows.push(idTraceRow([ver, fn, '—', '本版无对应修改意见', esc(v.time || v.at || '')]));
            return;
          }
          ops.forEach(function (op) {
            verRows.push(idTraceRow([ver, fn, esc(op.kind), esc(op.text), esc(op.meta || v.time || v.at || '')]));
          });
        });
        html += idTraceTable(
          ['版本', '申报书', '类别', '修改意见', '时间'],
          verRows,
          '该群暂无对应修改意见版本'
        );
        html += '</details>';
      });
      html += '</div>';
    }
    var unbound = (d.versions || []).filter(function (v) { return !v.sessionName; });
    if (unbound.length) {
      var verRows = [];
      unbound.forEach(function (v) {
        var ops = idTraceOpinionList(v.opinions, v.clauses, false);
        var ver = esc(v.version || ('v' + (v.versionN || '')));
        var fn = esc(v.filename || v.appName || '申报书');
        var acts = idTraceActs([
          v.url ? '<a class="mini" href="' + escAttr(v.url) + '" target="_blank" rel="noopener">打开文件</a>' : '',
          v.taskId ? '<a class="mini" href="/t/' + encodeURIComponent(v.taskId) + '" target="_blank" rel="noopener">打开任务</a>' : ''
        ]);
        if (!ops.length) {
          verRows.push(idTraceRow([ver, fn, '—', '本版无对应修改意见', esc(v.at || ''), acts]));
          return;
        }
        ops.forEach(function (op) {
          verRows.push(idTraceRow([ver, fn, esc(op.kind), esc(op.text), esc(op.meta || v.at || ''), acts]));
        });
      });
      html += '<div class="id-trace-sec"><h4>各版本修改意见</h4>' + idTraceTable(
        ['版本', '申报书', '类别', '意见', '时间', '操作'],
        verRows
      ) + '</div>';
    }
    return html;
  }
  function idTraceDetailHtml(d, opts) {
    opts = opts || {};
    d = d || {};
    var html = '';
    if (opts.embedded) return idTraceByGroupHtml(d);
    if (!opts.embedded) {
      var person = d.personName ? (' · ' + d.personName) : '';
      html += '<div class="id-trace-head"><button type="button" class="mini" data-id-trace-back="1">返回总览</button><b>' + esc(d.attachId || '') + '</b>' + esc(person) +
        '<span class="id-trace-stats">' +
        (d.sessionCount || 0) + ' 个群 · ' +
        (d.fileCount || 0) + ' 个同一文件 · ' +
        (d.versionCount || 0) + ' 个版本 · ' +
        ((d.deliveries || []).length) + ' 条交付</span></div>';
    }

    html += '<div class="id-trace-sec"><h4>出现过的群</h4>' + idTraceTable(
      ['群', '类型', '痕迹', '最近时间'],
      (d.sessions || []).map(function (s) {
        return idTraceRow([
          esc(s.sessionName || s.sessionId || '未命名'),
          s.isDelivery ? '<span class="tag deliv">交付</span>' : '群聊',
          String(s.hitCount || 0),
          esc(s.lastTime || '')
        ], s.sessionId, s.sessionName);
      }),
      '未在群聊里找到该编号'
    ) + '</div>';

    html += '<div class="id-trace-sec"><h4>交付记录</h4>' + idTraceTable(
      ['群 / 任务', '文件', '时间', '状态', '操作'],
      (d.deliveries || []).map(function (s) {
        return idTraceRow([
          esc(s.sessionName || '交付'),
          esc(s.filename || ''),
          esc(s.lastTime || ''),
          STATUS[s.status] ? badge(s.status) : esc(s.status || '交付'),
          idTraceActs([
            idTraceOpenSess(s.sessionId, s.sessionName),
            s.taskId ? '<a class="mini" href="/t/' + encodeURIComponent(s.taskId) + '" target="_blank" rel="noopener">打开任务</a>' : ''
          ])
        ], s.sessionId, s.sessionName);
      }),
      '没有带「交付」的会话或任务'
    ) + '</div>';

    var fileRows = [];
    (d.files || []).forEach(function (f) {
      var copies = f.copies || [];
      if (!copies.length) {
        fileRows.push(idTraceRow([
          esc(f.label || f.kind || '文件'),
          esc(f.filename || ''),
          '—', '', '', '1 处'
        ]));
        return;
      }
      copies.forEach(function (c) {
        fileRows.push(idTraceRow([
          esc(f.label || f.kind || '文件'),
          esc(f.filename || ''),
          esc(c.sessionName || c.sessionId || '未命名群') + (c.isDelivery ? ' <span class="tag deliv">交付</span>' : ''),
          esc(c.sender || ''),
          esc(c.time || ''),
          esc(c.source || '') + (copies.length > 1 ? ' · ' + copies.length + ' 处' : '')
        ], c.sessionId, c.sessionName));
      });
    });
    html += '<div class="id-trace-sec"><h4>同一文件（跨群）</h4>' + idTraceTable(
      ['类型', '文件', '所在群', '发送人', '时间', '来源'],
      fileRows,
      '没有跨群出现的同一文件'
    ) + '</div>';

    var verRows = [];
    (d.versions || []).forEach(function (v) {
      var ops = idTraceOpinionList(v.opinions, v.clauses, false);
      var acts = idTraceActs([
        v.url ? '<a class="mini" href="' + escAttr(v.url) + '" target="_blank" rel="noopener">打开文件</a>' : '',
        v.taskId ? '<a class="mini" href="/t/' + encodeURIComponent(v.taskId) + '" target="_blank" rel="noopener">打开任务</a>' : ''
      ]);
      var ver = esc(v.version || ('v' + (v.versionN || '')));
      var fn = esc(v.filename || v.appName || '申报书');
      if (!ops.length) {
        verRows.push(idTraceRow([ver, fn, '—', '本版无对应修改意见', esc(v.at || ''), acts]));
        return;
      }
      ops.forEach(function (op) {
        verRows.push(idTraceRow([
          ver, fn, esc(op.kind), esc(op.text), esc(op.meta || v.at || ''), acts
        ]));
      });
    });
    html += '<div class="id-trace-sec"><h4>各版本修改意见</h4>' + idTraceTable(
      ['版本', '申报书', '类别', '意见', '时间', '操作'],
      verRows,
      '人才库还没有该编号的申报书版本'
    ) + '</div>';

    if ((d.chatVersions || []).length) {
      var chatRows = [];
      (d.chatVersions || []).forEach(function (v) {
        var ops = idTraceOpinionList(v.opinions, [], true);
        var sess = esc(v.sessionName || v.sessionId || '');
        var fn = esc(v.filename || '');
        var ver = esc(v.chatVersion || '群聊版本');
        var acts = idTraceActs([idTraceOpenSess(v.sessionId, v.sessionName)]);
        if (!ops.length) {
          chatRows.push(idTraceRow([ver, fn, sess, '—', '本版无对应修改意见', acts], v.sessionId, v.sessionName));
          return;
        }
        ops.forEach(function (op) {
          chatRows.push(idTraceRow([
            ver, fn, sess, esc(op.kind), esc(op.text) + (op.meta ? '<div class="id-trace-sub">' + esc(op.meta) + '</div>' : ''), acts
          ], v.sessionId, v.sessionName));
        });
      });
      html += '<div class="id-trace-sec"><h4>群聊里的版本与意见</h4>' + idTraceTable(
        ['版本', '文件', '所在群', '类别', '意见', '操作'],
        chatRows
      ) + '</div>';
    }

    if ((d.tasks || []).length) {
      html += '<div class="id-trace-sec"><h4>相关任务</h4>' + idTraceTable(
        ['文件', '状态', '群', '时间', '版本', '操作'],
        (d.tasks || []).map(function (t) {
          return idTraceRow([
            esc(t.filename || t.sessionName || t.id || '任务'),
            badge(t.status),
            esc(t.sessionName || ''),
            esc(t.createdAt || ''),
            esc(t.version || ''),
            t.id ? '<a class="mini" href="/t/' + encodeURIComponent(t.id) + '" target="_blank" rel="noopener">打开任务</a>' : '—'
          ], '', t.sessionName);
        })
      ) + '</div>';
    }
    return html;
  }
  function idTraceRender(d) {
    var box = document.getElementById('idTraceBox');
    if (!box) return;
    if (!d || d.ok === false) {
      box.innerHTML = '<div class="wecom-empty">' + esc((d && (d.detail || d.message)) || '未找到') + '</div>';
      return;
    }
    box.innerHTML = idTraceDetailHtml(d, {});
  }
  var ID_TRACE_PAGE_SIZE = 10;
  var ID_TRACE_CHEV = '<svg class="id-trace-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
  var idTraceOverviewCache = [];
  var idTraceOverviewInfo = {};
  var idTraceFilterTimer = 0;
  var idTracePage = 1;
  var idTraceOpen = {};
  var idTraceDetail = {};
  function idTraceFilteredItems() {
    var qel = document.getElementById('idTraceQ');
    var q = ((qel && qel.value) || '').trim().toLowerCase();
    var items = idTraceOverviewCache || [];
    if (!q) return items;
    return items.filter(function (it) {
      return String(it.attachId || '').toLowerCase().indexOf(q) >= 0 ||
        String(it.personName || '').toLowerCase().indexOf(q) >= 0;
    });
  }
  function idTracePagerHtml(page, pages, total) {
    return '<div class="id-trace-pager book-pager">' +
      '<button type="button" class="mini" data-id-trace-page="prev"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<span class="id-trace-page-info">第 ' + page + ' / ' + pages + ' 页 · 每页 ' + ID_TRACE_PAGE_SIZE + ' 条 · 共 ' + total + ' 个编号</span>' +
      '<button type="button" class="mini" data-id-trace-page="next"' + (page >= pages ? ' disabled' : '') + '>下一页</button>' +
      '</div>';
  }
  function idTraceIdHtml(it) {
    var aid = String((it && it.attachId) || '');
    var open = !!idTraceOpen[aid];
    var d = idTraceDetail[aid];
    var names = (it && it.sessions) || [];
    var groups = names.slice(0, 3).join('、');
    var stats = [
      (it.sessionCount || 0) + ' 个群' + (groups ? '（' + groups + ((it.sessionCount || 0) > 3 ? '…' : '') + '）' : ''),
      (it.fileCount || 0) + ' 个文件',
      (it.versionCount || 0) + ' 个版本',
      (it.opinionCount || 0) + ' 条意见',
      (it.deliveryCount || 0) + ' 条交付'
    ].join(' · ');
    var body = d
      ? idTraceDetailHtml(d, { embedded: true })
      : '<div class="wecom-empty">展开后用 Gemini 判断文件类型和修改意见…</div>';
    return '<details class="id-trace-id"' + (open ? ' open' : '') + ' data-id-trace-aid="' + escAttr(aid) + '">' +
      '<summary class="id-trace-id-hd">' + ID_TRACE_CHEV + '<b>' + esc(aid) + '</b>' +
      (it.personName ? '<em>' + esc(it.personName) + '</em>' : '') +
      '<em>' + esc(stats) + '</em>' +
      (it.lastTime ? '<em>' + esc(it.lastTime) + '</em>' : '') +
      '</summary><div class="id-trace-id-bd">' + body + '</div></details>';
  }
  function idTraceRenderOverview() {
    var box = document.getElementById('idTraceBox');
    if (!box) return;
    var items = idTraceFilteredItems();
    var total = items.length;
    var pages = Math.max(1, Math.ceil(total / ID_TRACE_PAGE_SIZE) || 1);
    if (idTracePage > pages) idTracePage = pages;
    if (idTracePage < 1) idTracePage = 1;
    if (total === 1 && items[0] && items[0].attachId) idTraceOpen[items[0].attachId] = true;
    var slice = items.slice((idTracePage - 1) * ID_TRACE_PAGE_SIZE, idTracePage * ID_TRACE_PAGE_SIZE);
    var watchN = idTraceOverviewInfo.watchCount;
    var html = '<div class="id-trace-head"><b>关注会话中的编号</b><span class="id-trace-stats">' +
      (watchN != null && watchN !== '' ? '关注会话 ' + watchN + ' 个 · ' : '') +
      '共 ' + total + ' 个编号 · 每页 ' + ID_TRACE_PAGE_SIZE + ' 条</span></div>';
    html += idTracePagerHtml(idTracePage, pages, total);
    if (!slice.length) html += '<div class="wecom-empty">' + esc(idTraceOverviewInfo.note || '关注会话里尚未归纳到人才编号') + '</div>';
    else html += slice.map(idTraceIdHtml).join('');
    if (total > ID_TRACE_PAGE_SIZE) html += idTracePagerHtml(idTracePage, pages, total);
    box.innerHTML = html;
    slice.forEach(function (it) {
      if (it && idTraceOpen[it.attachId] && !idTraceDetail[it.attachId]) {
        var el = box.querySelector('details.id-trace-id[data-id-trace-aid="' + String(it.attachId).replace(/[^\d]/g, '') + '"]');
        if (el) idTraceEnsureDetail(String(it.attachId), el);
      }
    });
  }
  function idTraceFilterOverview() {
    idTracePage = 1;
    idTraceRenderOverview();
  }
  function idTraceEnsureDetail(aid, el) {
    aid = String(aid || '');
    if (!aid || idTraceDetail[aid]) {
      if (idTraceDetail[aid] && el) {
        var bd = el.querySelector('.id-trace-id-bd');
        if (bd && !bd.querySelector('.id-trace-sec')) bd.innerHTML = idTraceDetailHtml(idTraceDetail[aid], { embedded: true });
      }
      return;
    }
    var bd = el && el.querySelector('.id-trace-id-bd');
    if (bd) bd.innerHTML = '<div class="wecom-empty">正在用 Gemini 判断文件类型和修改意见…</div>';
    fetch('/api/wecom/id-trace?q=' + encodeURIComponent(aid) + '&lite=1', { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error((x.j && (x.j.detail || x.j.message)) || '加载失败');
        idTraceDetail[aid] = x.j;
        var host = el && el.querySelector('.id-trace-id-bd');
        if (host) host.innerHTML = idTraceDetailHtml(x.j, { embedded: true });
      })
      .catch(function (e) {
        var host = el && el.querySelector('.id-trace-id-bd');
        if (host) host.innerHTML = '<div class="wecom-empty">' + esc(e.message || '加载失败') + '</div>';
      });
  }
  function idTraceJumpAid(aid) {
    aid = String(aid || '').trim();
    if (!aid) { idTraceFilterOverview(); return; }
    var items = idTraceOverviewCache || [];
    var idx = -1;
    for (var i = 0; i < items.length; i++) {
      if (String(items[i].attachId) === aid) { idx = i; break; }
    }
    var qel = document.getElementById('idTraceQ');
    if (qel) qel.value = aid;
    if (idx < 0) {
      loadIdTrace(aid);
      return;
    }
    idTracePage = Math.floor(idx / ID_TRACE_PAGE_SIZE) + 1;
    idTraceOpen[aid] = true;
    idTraceRenderOverview();
  }
  function loadIdTraceOverview() {
    var box = document.getElementById('idTraceBox');
    var go = document.getElementById('idTraceGo');
    if (box) box.innerHTML = '<div class="wecom-empty">正在查阅全部关注会话，归纳编号…</div>';
    if (go) go.disabled = true;
    fetch('/api/wecom/id-trace', { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error((x.j && (x.j.detail || x.j.message)) || '加载失败');
        idTraceOverviewCache = (x.j && x.j.items) || [];
        idTraceOverviewInfo = { watchCount: x.j && x.j.watchCount, note: x.j && x.j.note };
        var qel = document.getElementById('idTraceQ');
        var v = ((qel && qel.value) || '').trim();
        if (/^\d{4,6}$/.test(v)) idTraceJumpAid(v);
        else if (v) idTraceFilterOverview();
        else idTraceRenderOverview();
      })
      .catch(function (e) {
        if (box) box.innerHTML = '<div class="wecom-empty">' + esc(e.message || '加载失败') + '</div>';
      })
      .then(function () { if (go) go.disabled = false; });
  }
  function loadIdTrace(q) {
    var box = document.getElementById('idTraceBox');
    var go = document.getElementById('idTraceGo');
    q = String(q || '').trim();
    if (!q) {
      loadIdTraceOverview();
      return;
    }
    if (box) box.innerHTML = '<div class="wecom-empty">正在按编号汇总各群痕迹，可能需要十几秒…</div>';
    if (go) go.disabled = true;
    fetch('/api/wecom/id-trace?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error((x.j && (x.j.detail || x.j.message)) || '查询失败');
        if (x.j && x.j.overview) {
          idTraceOverviewCache = x.j.items || [];
          idTraceOverviewInfo = { watchCount: x.j.watchCount, note: x.j.note };
          idTraceRenderOverview(x.j);
          return;
        }
        idTraceRender(x.j);
      })
      .catch(function (e) {
        if (box) box.innerHTML = '<div class="wecom-empty">' + esc(e.message || '查询失败') + '</div>';
      })
      .then(function () { if (go) go.disabled = false; });
  }
  function initIdTracePanel() {
    var panel = document.getElementById('idTracePanel');
    if (!panel || panel.getAttribute('data-bound') === '1') return;
    panel.setAttribute('data-bound', '1');
    var q = document.getElementById('idTraceQ');
    var go = document.getElementById('idTraceGo');
    var box = document.getElementById('idTraceBox');
    function run() {
      var v = ((q && q.value) || '').trim();
      if (!v) { idTracePage = 1; if (idTraceOverviewCache.length) idTraceRenderOverview(); else loadIdTraceOverview(); return; }
      if (/^\d{4,6}$/.test(v) && idTraceOverviewCache.length) { idTraceJumpAid(v); return; }
      if (idTraceOverviewCache.length) idTraceFilterOverview();
      else loadIdTraceOverview();
    }
    if (go) go.addEventListener('click', run);
    if (q) {
      q.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); run(); }
      });
      q.addEventListener('input', function () {
        clearTimeout(idTraceFilterTimer);
        idTraceFilterTimer = setTimeout(function () {
          if (idTraceOverviewCache.length) idTraceFilterOverview();
        }, 200);
      });
    }
    if (box) {
      box.addEventListener('toggle', function (e) {
        var t = e.target;
        if (!t || !t.classList || !t.classList.contains('id-trace-id')) return;
        var aid = t.getAttribute('data-id-trace-aid') || '';
        if (!aid) return;
        if (t.open) {
          idTraceOpen[aid] = true;
          idTraceEnsureDetail(aid, t);
        } else {
          delete idTraceOpen[aid];
        }
      }, true);
      box.addEventListener('click', function (e) {
        if (e.target.closest('a')) return;
        var pageBtn = e.target.closest('[data-id-trace-page]');
        if (pageBtn) {
          if (pageBtn.disabled) return;
          var act = pageBtn.getAttribute('data-id-trace-page');
          if (act === 'prev' && idTracePage > 1) idTracePage -= 1;
          if (act === 'next') idTracePage += 1;
          idTraceRenderOverview();
          return;
        }
        if (e.target.closest('[data-id-trace-back]')) {
          if (q) q.value = '';
          idTracePage = 1;
          idTraceRenderOverview();
          return;
        }
        if (e.target.closest('summary')) return;
        var btn = e.target.closest('[data-id-trace-sid], [data-id-trace-sname]');
        if (!btn) return;
        wecomOpenSession(btn.getAttribute('data-id-trace-sid') || '', btn.getAttribute('data-id-trace-sname') || '');
      });
    }
    loadIdTraceOverview();
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
    var streamEl = document.getElementById('wecomStream');
    if (streamEl) streamEl.addEventListener('scroll', wecomOnStreamScroll);
    var mq = document.getElementById('wecomMsgQ');
    if (mq) mq.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { wecomResetMsgWindow(); loadWecomMessages(); }
    });
    ['wecomStart', 'wecomEnd'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', function () { wecomResetMsgWindow(); loadWecomMessages(); });
    });
    var reload = document.getElementById('wecomReload');
    if (reload) reload.addEventListener('click', function () { wecomResetMsgWindow(); loadWecomBoard(); });
    var fileBtn = document.getElementById('wecomFileBtn');
    if (!fileBtn && reload && wecomFileBox()) {
      fileBtn = document.createElement('button');
      fileBtn.type = 'button';
      fileBtn.className = 'mini';
      fileBtn.id = 'wecomFileBtn';
      fileBtn.textContent = '文件汇总';
      reload.parentNode.insertBefore(fileBtn, reload);
    }
    if (fileBtn) fileBtn.addEventListener('click', wecomFileToggle);
    var aiBtn = document.getElementById('wecomAiBtn');
    if (aiBtn) aiBtn.addEventListener('click', wecomAiRead);
    var aiLogBtn = document.getElementById('wecomAiLogBtn');
    if (aiLogBtn) aiLogBtn.addEventListener('click', wecomAiOpenLog);
    var aiTabRead = document.getElementById('wecomAiTabRead');
    if (aiTabRead) aiTabRead.addEventListener('click', function () { wecomAiSetTab('read'); });
    var aiTabLog = document.getElementById('wecomAiTabLog');
    if (aiTabLog) aiTabLog.addEventListener('click', function () { wecomAiSetTab('log'); });
    wecomRailRestoreWidth();
    wecomRailBindResizer();
    var railClose = document.getElementById('wecomRailClose');
    if (railClose) railClose.addEventListener('click', wecomRailClose);
    var railTabAi = document.getElementById('wecomRailTabAi');
    if (railTabAi) railTabAi.addEventListener('click', function () { wecomRailOpen('ai'); });
    var railTabFiles = document.getElementById('wecomRailTabFiles');
    if (railTabFiles) railTabFiles.addEventListener('click', function () {
      wecomRailOpen('files');
      if (wecomState.sessionId) wecomFileLoad();
    });
    var splitBtn = document.getElementById('wecomSplitBtn');
    if (splitBtn) splitBtn.addEventListener('click', wecomSplitOpen);
    var intentPageBtn = document.getElementById('wecomIntentPageBtn');
    if (intentPageBtn) intentPageBtn.addEventListener('click', wecomIntentPage);
    wecomPickEnsureBar();
    initIdTracePanel();
  })();

  global.wecomSessionTitle = wecomSessionTitle;
  global.loadWecomBoard = loadWecomBoard;
  global.wecomReloadSessions = loadWecomSessions;
  global.wecomToggleWatch = wecomToggleWatch;
  global.wecomGroupWatched = wecomGroupWatched;
  global.wecomIsImage = wecomIsImage;
  global.wecomThumbHtml = wecomThumbHtml;
  global.wecomBindThumbs = wecomBindThumbs;
  global.wecomThumbSrcFromLocal = wecomThumbSrcFromLocal;
  global.wecomThumbSrcFromMedia = wecomThumbSrcFromMedia;
  global.wecomPreview = wecomPreview;
  global.wecomAiBusy = function () { return wecomAiLoading; };
  global.initWecomBoard = function () {
    if (!document.getElementById('wecomSessions')) return;
    initWecomWatchPanel();
    initWecomUserReview();
    loadWecomBoard(true);
    loadWecomWatchPanel();
  };
  global.initWecomWatchPanel = initWecomWatchPanel;
  global.loadWecomWatchPanel = loadWecomWatchPanel;
  global.loadWecomWatchLogs = loadWecomWatchLogs;
  if (document.getElementById('wecomWatchPanel')) initWecomWatchPanel();
  if (document.getElementById('wecomUserReview')) initWecomUserReview();
  if (document.getElementById('idTracePanel')) initIdTracePanel();
  // 尽早拉关注名单，避免主页 AI 审核比登录后的看板初始化更早跑完、把会话滤空。
  if (document.getElementById('wecomSessions') || document.getElementById('arSessions')) {
    loadWecomWatchGroups();
  }
})(window);
