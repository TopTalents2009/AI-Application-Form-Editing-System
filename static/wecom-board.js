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
  var wecomState = { sources: [], sourceId: '*', sourceKind: '', sessionId: '', sessionName: '', offset: 0, limit: 80, total: 0, searchMode: false, replicas: [], tail: true, sessionKey: '', msgKey: '', sourceKey: '', pageItems: [], pick: { app: null, opinions: [] } };
  var wecomGroupTimer = null;
  var wecomMsgTimer = null;

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
        st.innerHTML = '<span class="' + (ok ? 'dot-ok' : (svc ? '' : 'dot-bad')) + '">●</span> ' +
          (ok ? '解析服务已连通 · ' + esc(h.baseUrl || '') + ' · ' + (h.source_count || 0) + ' 台电脑' :
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
        wecomState.tail = true;
        wecomState.sessionKey = '';
        wecomState.msgKey = '';
        wecomPickReset(true);
        renderWecomSources();
        loadWecomSessions();
        document.getElementById('wecomChatTitle').textContent = '未选择会话';
        document.getElementById('wecomChatSub').textContent = '从左侧点开群；重复群已合并';
        document.getElementById('wecomStream').innerHTML = '<div class="wecom-empty">选择群后显示消息。</div>';
        document.getElementById('wecomPager').hidden = true;
      });
    });
  }
  function wecomSessionKey(items) {
    return (items || []).map(function (it) {
      return [it.username || '', it.display_name || '', it.msg_count || '', it.last_time || '', (it.replicas || []).length].join('\t');
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
    var scroll = list ? list.scrollTop : 0;
    wecomQuery(url).then(function (res) {
      if (!res.ok) {
        if (!quiet) wecomFail(list, (res.data && res.data.detail) || '加载会话失败');
        return;
      }
      var items = res.data.items || [];
      if (!items.length) {
        if (!quiet) wecomFail(list, q ? '没有匹配的会话' : '没有群会话。可取消「仅群聊」，或先在解析助手里勾选并同步。');
        return;
      }
      var key = wecomSessionKey(items);
      if (quiet && wecomState.sessionKey === key) return;
      wecomState.sessionKey = key;
      list.innerHTML = items.map(function (it) {
        var sid = it.username || '';
        var name = it.display_name || sid || '未命名会话';
        var reps = it.replicas || [];
        var nrep = reps.length;
        var labels = reps.map(function (r) { return r.label || r.source_id; }).filter(Boolean).join('、');
        var meta = (nrep > 1 ? nrep + ' 台电脑' : (labels || '1 台')) +
          (it.msg_count ? ' · ' + it.msg_count + ' 条' : '') +
          (it.last_time ? ' · ' + it.last_time : '');
        if (nrep > 1 && labels) meta += ' · ' + labels;
        return '<button type="button" class="wecom-item' + (sid === wecomState.sessionId ? ' on' : '') + '" data-id="' + escAttr(sid) + '" data-name="' + escAttr(name) + '" data-replicas="' + encodeURIComponent(JSON.stringify(reps)) + '">' +
          '<b>' + esc(name) + '</b><span>' + esc(meta || sid) + '</span></button>';
      }).join('');
      list.querySelectorAll('.wecom-item').forEach(function (btn) {
        btn.addEventListener('click', function () {
          wecomState.sessionId = btn.getAttribute('data-id') || '';
          wecomState.sessionName = btn.getAttribute('data-name') || '';
          wecomState.replicas = wecomParseJsonAttr(btn, 'data-replicas');
          wecomState.offset = 0;
          wecomState.tail = true;
          wecomState.searchMode = false;
          wecomState.msgKey = '';
          wecomPickReset(true);
          list.querySelectorAll('.wecom-item').forEach(function (x) { x.classList.toggle('on', x === btn); });
          loadWecomMessages();
        });
      });
      if (quiet) list.scrollTop = scroll;
    }).catch(function (e) { if (!quiet) wecomFail(list, e.message || '加载失败'); });
  }
  function loadWecomMessages(quiet) {
    var stream = document.getElementById('wecomStream');
    var title = document.getElementById('wecomChatTitle');
    var sub = document.getElementById('wecomChatSub');
    var pager = document.getElementById('wecomPager');
    if (!wecomState.sessionId) {
      if (!quiet) {
        wecomFail(stream, '选择群后显示消息。');
        if (pager) pager.hidden = true;
      }
      return;
    }
    if (title) title.textContent = wecomState.sessionName || wecomState.sessionId;
    var q = ((document.getElementById('wecomMsgQ') || {}).value || '').trim();
    var hasMsgs = !!(stream && stream.querySelector('.wecom-msg'));
    if (!quiet || !hasMsgs) stream.innerHTML = '<div class="wecom-empty">加载中…</div>';
    if (q) {
      wecomState.searchMode = true;
      var surl = '/api/wecom/search?q=' + encodeURIComponent(q) + '&session_id=' + encodeURIComponent(wecomState.sessionId) + '&limit=80';
      if (wecomSourceParam()) surl += '&source_id=' + encodeURIComponent(wecomSourceParam());
      wecomQuery(surl).then(function (res) {
        if (pager) pager.hidden = true;
        if (!res.ok) { if (!quiet) wecomFail(stream, (res.data && res.data.detail) || '搜索失败'); return; }
        var items = res.data.items || [];
        if (sub) sub.textContent = '关键词「' + q + '」命中 ' + items.length + ' 条 · 来自 ' + wecomReplicaNames();
        var key = wecomMsgKey(items);
        if (!(quiet && wecomState.msgKey === key)) {
          wecomState.msgKey = key;
          renderWecomMsgs(stream, items, true, quiet);
        }
      }).catch(function (e) { if (!quiet) wecomFail(stream, e.message || '搜索失败'); });
      return;
    }
    wecomState.searchMode = false;
    var start = (document.getElementById('wecomStart') || {}).value || '';
    var end = (document.getElementById('wecomEnd') || {}).value || '';
    var url = '/api/wecom/groups/' + encodeURIComponent(wecomState.sessionId) + '/messages?offset=' + wecomState.offset + '&limit=' + wecomState.limit;
    if (wecomState.tail) url += '&tail=1';
    if (wecomSourceParam()) url += '&source_id=' + encodeURIComponent(wecomSourceParam());
    if (start) url += '&start_date=' + encodeURIComponent(start);
    if (end) url += '&end_date=' + encodeURIComponent(end);
    wecomQuery(url).then(function (res) {
      if (!res.ok) {
        if (!quiet) { wecomFail(stream, (res.data && res.data.detail) || '加载消息失败'); if (pager) pager.hidden = true; }
        return;
      }
      var d = res.data || {};
      wecomState.total = d.total || 0;
      wecomState.offset = d.offset || 0;
      wecomState.tail = false;
      if (d.replicas && d.replicas.length) wecomState.replicas = d.replicas;
      if (d.display_name && !wecomState.sessionName) wecomState.sessionName = d.display_name;
      if (title && wecomState.sessionName) title.textContent = wecomState.sessionName;
      var names = wecomReplicaNames();
      if (sub) sub.textContent = (d.total || 0) + ' 条（重复已合并）' + (start || end ? ' · 已按日期筛选' : '') +
        ' · ' + names + ' · 默认看最新';
      var items = d.items || [];
      var key = wecomMsgKey(items);
      if (!(quiet && wecomState.msgKey === key)) {
        wecomState.msgKey = key;
        renderWecomMsgs(stream, items, false, quiet);
      }
      if (pager) {
        pager.hidden = wecomState.total <= wecomState.limit;
        var from = wecomState.total ? (wecomState.offset + 1) : 0;
        var to = Math.min(wecomState.offset + wecomState.limit, wecomState.total);
        document.getElementById('wecomPageInfo').textContent = from + '–' + to + ' / ' + wecomState.total;
        document.getElementById('wecomPrev').disabled = wecomState.offset <= 0;
        document.getElementById('wecomNext').disabled = wecomState.offset + wecomState.limit >= wecomState.total;
      }
    }).catch(function (e) { if (!quiet) wecomFail(stream, e.message || '加载失败'); });
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
        (text ? '<div class="body">' + esc(text) + '</div>' : '') + extra + pickHtml + '</div>';
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
      say(false, '请先选择一个群');
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
          var again = window.confirm('该群文件已经上传过（任务 ' + (ex.id || '') + '）。仍要再传一份？');
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
          (k.group ? '　·　群：' + esc(k.group) : '') +
          '　·　时间窗 ±' + esc(d.windowDays || 7) + ' 天';
        var copies = payload.copies || [];
        var chatBtns = copies.length
          ? '<div class="wecom-locate-item"><div class="ttl">群里当时发的文件</div>' +
            '<div class="meta">' + esc(k.filename || payload.filename || '聊天文件') + ' · 走企业微信缓存，未打开过则下不了</div>' +
            '<div class="acts"><button type="button" class="mini" data-wecom-dl="' + escAttr((copies[0] && copies[0].message_id) || '') +
            '" data-name="' + escAttr(k.filename || payload.filename || 'chat-file') +
            '" data-copies="' + encodeURIComponent(JSON.stringify(copies)) + '">下载群文件</button></div></div>'
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
      '<div class="wecom-locate-hd"><h3>拆解群聊为申报任务</h3><div class="grow"></div>' +
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
      say(false, '请先选择一个群');
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
        keysEl.innerHTML = '群：<b>' + esc(d.session_name || wecomState.sessionName || '') + '</b>　·　日期 ' +
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
            if (o.kind === 'text') return '群文本「' + (o.filename || '群聊修改意见.txt') + '」';
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
  (function bindWecomUi() {
    var gq = document.getElementById('wecomGroupQ');
    if (gq) gq.addEventListener('input', function () {
      clearTimeout(wecomGroupTimer);
      wecomGroupTimer = setTimeout(loadWecomSessions, 280);
    });
    var only = document.getElementById('wecomOnlyGroup');
    if (only) only.addEventListener('change', loadWecomSessions);
    var mq = document.getElementById('wecomMsgQ');
    if (mq) mq.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { wecomState.offset = 0; wecomState.tail = true; loadWecomMessages(); }
    });
    ['wecomStart', 'wecomEnd'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', function () { wecomState.offset = 0; wecomState.tail = true; loadWecomMessages(); });
    });
    var reload = document.getElementById('wecomReload');
    if (reload) reload.addEventListener('click', function () { wecomState.offset = 0; wecomState.tail = true; loadWecomBoard(); });
    var splitBtn = document.getElementById('wecomSplitBtn');
    if (splitBtn) splitBtn.addEventListener('click', wecomSplitOpen);
    wecomPickEnsureBar();
    var prev = document.getElementById('wecomPrev');
    if (prev) prev.addEventListener('click', function () {
      wecomState.tail = false;
      wecomState.offset = Math.max(0, wecomState.offset - wecomState.limit);
      loadWecomMessages();
    });
    var next = document.getElementById('wecomNext');
    if (next) next.addEventListener('click', function () {
      wecomState.tail = false;
      wecomState.offset += wecomState.limit;
      loadWecomMessages();
    });
  })();

  global.loadWecomBoard = loadWecomBoard;
  global.initWecomBoard = function () {
    if (!document.getElementById('wecomSessions')) return;
    loadWecomBoard(true);
  };
})(window);
