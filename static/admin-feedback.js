/* 管理后台：用户反馈 */
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
  var fbFilter = '';
  var fbExpanded = {};
  var fbAllItems = [];
  var fbPage = 1;
  var FB_PAGE_SIZE = 12;
  var fbMsgEl = document.getElementById('fbAdminMsg');
  function fbPreviewText(it) {
    var t = String(it.content || '').replace(/\s+/g, ' ').trim();
    if (!t && it.files && it.files.length) return '（' + it.files.length + ' 张截图）';
    if (!t) return '（无文字）';
    return t.length > 72 ? t.slice(0, 72) + '…' : t;
  }
  function fbIsOpen(it) {
    if (fbExpanded[it.id] !== undefined) return !!fbExpanded[it.id];
    return it.status === 'new';
  }
  function fbSetAll(open) {
    var box = document.getElementById('fbAdminList');
    if (!box) return;
    box.querySelectorAll('.fb-item[data-id]').forEach(function (el) {
      var id = el.getAttribute('data-id');
      fbExpanded[id] = open;
      el.classList.toggle('bd-collapsed', !open);
    });
  }
  function sayFb(ok, t) {
    if (!fbMsgEl) return;
    fbMsgEl.className = 'msg ' + (ok ? 'ok' : 'bad');
    fbMsgEl.textContent = t;
    setTimeout(function () { if (fbMsgEl.textContent === t) fbMsgEl.textContent = ''; }, 4000);
  }
  function fbLabel(st) {
    if (st === 'done') return '已处理';
    if (st === 'read') return '已读';
    return '待处理';
  }
  function setFbBadge(n) {
    var b = document.getElementById('fbBadge');
    if (!b) return;
    if (n > 0) { b.hidden = false; b.textContent = n > 99 ? '99+' : String(n); }
    else { b.hidden = true; b.textContent = ''; }
  }
  function fbFilteredItems() {
    return fbFilter ? fbAllItems.filter(function (it) { return it.status === fbFilter; }) : fbAllItems;
  }
  function renderFbPager(total, page, pages) {
    var pager = document.getElementById('fbAdminPager');
    var info = document.getElementById('fbPageInfo');
    var prev = document.getElementById('fbPagePrev');
    var next = document.getElementById('fbPageNext');
    if (!pager) return;
    if (pages <= 1) {
      pager.hidden = true;
      if (info) info.textContent = total ? ('共 ' + total + ' 条') : '';
      return;
    }
    pager.hidden = false;
    if (info) info.textContent = '第 ' + page + ' / ' + pages + ' 页 · 共 ' + total + ' 条';
    if (prev) prev.disabled = page <= 1;
    if (next) next.disabled = page >= pages;
  }
  function renderFeedbackList() {
    var box = document.getElementById('fbAdminList');
    var shown = fbFilteredItems();
    var total = shown.length;
    if (!total) {
      revokeFbAuthBlobs();
      box.innerHTML = '<div class="files-muted">暂无反馈</div>';
      renderFbPager(0, 1, 1);
      return;
    }
    var pages = Math.max(1, Math.ceil(total / FB_PAGE_SIZE));
    if (fbPage > pages) fbPage = pages;
    if (fbPage < 1) fbPage = 1;
    var start = (fbPage - 1) * FB_PAGE_SIZE;
    var pageItems = shown.slice(start, start + FB_PAGE_SIZE);
    box.innerHTML = pageItems.map(function (it) {
        var who = esc(it.realName || it.username || '') + (it.department ? ' · ' + esc(it.department) : '') + (it.username ? '（' + esc(it.username) + '）' : '');
        var sum = fbPreviewText(it);
        var open = fbIsOpen(it);
        var imgs = (it.files || []).map(function (f) {
          return '<a class="fb-img-link" href="' + esc(f.url) + '" data-auth-src="' + esc(f.url) + '"><img data-auth-src="' + esc(f.url) + '" alt="' + esc(f.name) + '"></a>';
        }).join('');
        var ops = '';
        if (it.status === 'new') ops += '<button type="button" class="mini" data-act="read" data-id="' + it.id + '">标为已读</button>';
        if (it.status !== 'done') ops += '<button type="button" class="mini" data-act="done" data-id="' + it.id + '">已处理</button>';
        ops += '<button type="button" class="mini" data-act="del" data-id="' + it.id + '">删除</button>';
        var replyHtml = '';
        if (it.reply) {
          replyHtml += '<div class="fb-reply"><span class="fb-rp-meta">管理员回复' + (it.replyAt ? ' · ' + esc(it.replyAt) : '') + '</span><span class="fb-rp-text">' + esc(it.reply) + '</span></div>';
        }
        replyHtml += '<div class="fb-reply-box">' +
          '<input type="text" class="fb-reply-input" maxlength="4000" data-saved="' + escAttr(it.reply || '') + '" value="' + escAttr(it.reply || '') + '" placeholder="填写处理情况，回复给提交者…">' +
          '<button type="button" class="mini" data-act="reply-send" data-id="' + it.id + '">' + (it.reply ? '更新回复' : '发送回复') + '</button>' +
          '</div>';
        return '<div class="fb-item' + (open ? '' : ' bd-collapsed') + '" data-id="' + it.id + '">' +
          '<div class="fb-item-hd" role="button" tabindex="0" aria-expanded="' + (open ? 'true' : 'false') + '">' +
          '<svg class="fb-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
          '<span class="fb-st ' + esc(it.status || 'new') + '">' + esc(fbLabel(it.status)) + '</span>' +
          '<span class="fb-meta"><span>' + who + '</span><span>' + esc(it.createdAt) + '</span></span>' +
          '<span class="fb-sum' + (sum.indexOf('（') === 0 ? ' muted' : '') + '">' + esc(sum) + '</span>' +
          '</div>' +
          '<div class="fb-item-bd">' +
          (it.content ? '<div class="fb-body">' + esc(it.content) + '</div>' : '<div class="fb-body" style="color:#98a2b6">（无文字，仅截图）</div>') +
          (imgs ? '<div class="fb-imgs">' + imgs + '</div>' : '') +
          replyHtml +
          '<div class="fb-ops">' + ops + '</div>' +
          '</div></div>';
    }).join('');
    revokeFbAuthBlobs();
    if (window.sbBindAuthImages) fbAuthBlobs = window.sbBindAuthImages(box, []);
    renderFbPager(total, fbPage, pages);
  }
  function loadFeedback() {
    fetch('/api/feedback', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (res) {
      fbAllItems = (res && res.items) || [];
      setFbBadge(res && res.unread ? res.unread : 0);
      renderFeedbackList();
    }).catch(function (e) {
      document.getElementById('fbAdminList').textContent = '加载失败：' + (e.message || '');
      var pager = document.getElementById('fbAdminPager');
      if (pager) pager.hidden = true;
    });
  }
  document.querySelectorAll('.fb-filter').forEach(function (btn) {
    btn.addEventListener('click', function () {
      fbFilter = btn.getAttribute('data-st') || '';
      fbPage = 1;
      document.querySelectorAll('.fb-filter').forEach(function (b) { b.classList.toggle('active', b === btn); });
      if (fbAllItems.length) renderFeedbackList();
      else loadFeedback();
    });
  });
  document.getElementById('fbPagePrev').addEventListener('click', function () {
    if (fbPage <= 1) return;
    fbPage--;
    renderFeedbackList();
    var sec = document.getElementById('sec-feedback');
    if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  document.getElementById('fbPageNext').addEventListener('click', function () {
    var pages = Math.max(1, Math.ceil(fbFilteredItems().length / FB_PAGE_SIZE));
    if (fbPage >= pages) return;
    fbPage++;
    renderFeedbackList();
    var sec = document.getElementById('sec-feedback');
    if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  document.getElementById('fbExpandAll').addEventListener('click', function () { fbSetAll(true); });
  document.getElementById('fbCollapseAll').addEventListener('click', function () { fbSetAll(false); });
  document.getElementById('fbAdminList').addEventListener('keydown', function (ev) {
    var hd = ev.target.closest ? ev.target.closest('.fb-item-hd') : null;
    if (!hd || (ev.key !== 'Enter' && ev.key !== ' ')) return;
    ev.preventDefault();
    fbToggleItem(hd.closest ? hd.closest('.fb-item') : null);
  });
  var fbAuthBlobs = [];
  function revokeFbAuthBlobs() {
    fbAuthBlobs.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) {} });
    fbAuthBlobs = [];
  }
  var fbPrev = { urls: [], names: [], i: 0, scale: 1, x: 0, y: 0, drag: null };
  var FB_ZOOM_MIN = 0.25, FB_ZOOM_MAX = 8, FB_ZOOM_STEP = 1.25;
  function fbPreviewClose() {
    var mask = document.getElementById('fbPreview');
    if (mask) mask.hidden = true;
    fbPrev.drag = null;
    var stage = document.getElementById('fbPreviewStage');
    if (stage) stage.classList.remove('dragging');
  }
  function fbApplyZoom() {
    var img = document.getElementById('fbPreviewImg');
    var pct = document.getElementById('fbPreviewZoomPct');
    var stage = document.getElementById('fbPreviewStage');
    if (img) img.style.transform = 'translate(' + fbPrev.x + 'px,' + fbPrev.y + 'px) scale(' + fbPrev.scale + ')';
    if (pct) pct.textContent = Math.round(fbPrev.scale * 100) + '%';
    if (stage) stage.classList.toggle('zoomed', fbPrev.scale > 1.02);
  }
  function fbResetZoom() {
    fbPrev.scale = 1;
    fbPrev.x = 0;
    fbPrev.y = 0;
    fbApplyZoom();
  }
  function fbZoomTo(next, clientX, clientY) {
    next = Math.max(FB_ZOOM_MIN, Math.min(FB_ZOOM_MAX, next));
    var stage = document.getElementById('fbPreviewStage');
    var s0 = fbPrev.scale || 1;
    if (s0 <= 0) s0 = 1;
    if (stage && clientX != null && clientY != null) {
      var rect = stage.getBoundingClientRect();
      var cx = clientX - (rect.left + rect.width / 2);
      var cy = clientY - (rect.top + rect.height / 2);
      var k = next / s0;
      fbPrev.x = cx - (cx - fbPrev.x) * k;
      fbPrev.y = cy - (cy - fbPrev.y) * k;
    }
    fbPrev.scale = next;
    if (Math.abs(next - 1) < 0.02) {
      fbPrev.scale = 1;
      fbPrev.x = 0;
      fbPrev.y = 0;
    }
    fbApplyZoom();
  }
  function fbPreviewShow(i) {
    if (!fbPrev.urls.length) return;
    fbPrev.i = (i + fbPrev.urls.length) % fbPrev.urls.length;
    var img = document.getElementById('fbPreviewImg');
    var cap = document.getElementById('fbPreviewCap');
    if (img) {
      img.alt = fbPrev.names[fbPrev.i] || '';
      var url = fbPrev.urls[fbPrev.i] || '';
      if (url.indexOf('blob:') === 0 || url.indexOf('data:') === 0) {
        img.src = url;
      } else if (window.sbAuthBlob && url) {
        var idx = fbPrev.i;
        img.removeAttribute('src');
        window.sbAuthBlob(url).then(function (u) {
          fbAuthBlobs.push(u);
          if (fbPrev.i !== idx) return;
          fbPrev.urls[idx] = u;
          img.src = u;
        }).catch(function () {
          if (fbPrev.i === idx) img.alt = '图片无法加载';
        });
      } else {
        img.src = url;
      }
    }
    if (cap) cap.textContent = fbPrev.names[fbPrev.i] || '';
    var many = fbPrev.urls.length > 1;
    var prev = document.getElementById('fbPreviewPrev');
    var next = document.getElementById('fbPreviewNext');
    if (prev) prev.hidden = !many;
    if (next) next.hidden = !many;
    var mask = document.getElementById('fbPreview');
    if (mask) mask.hidden = false;
    fbResetZoom();
  }
  document.getElementById('fbPreviewClose').addEventListener('click', fbPreviewClose);
  document.getElementById('fbPreviewPrev').addEventListener('click', function (ev) { ev.stopPropagation(); fbPreviewShow(fbPrev.i - 1); });
  document.getElementById('fbPreviewNext').addEventListener('click', function (ev) { ev.stopPropagation(); fbPreviewShow(fbPrev.i + 1); });
  document.getElementById('fbPreviewZoomIn').addEventListener('click', function (ev) {
    ev.stopPropagation();
    fbZoomTo(fbPrev.scale * FB_ZOOM_STEP);
  });
  document.getElementById('fbPreviewZoomOut').addEventListener('click', function (ev) {
    ev.stopPropagation();
    fbZoomTo(fbPrev.scale / FB_ZOOM_STEP);
  });
  document.getElementById('fbPreviewZoomReset').addEventListener('click', function (ev) {
    ev.stopPropagation();
    fbResetZoom();
  });
  document.getElementById('fbPreview').addEventListener('click', function (ev) {
    if (ev.target === this || ev.target.id === 'fbPreviewStage') fbPreviewClose();
  });
  var fbStage = document.getElementById('fbPreviewStage');
  fbStage.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    fbZoomTo(fbPrev.scale * (ev.deltaY < 0 ? FB_ZOOM_STEP : 1 / FB_ZOOM_STEP), ev.clientX, ev.clientY);
  }, { passive: false });
  fbStage.addEventListener('dblclick', function (ev) {
    ev.preventDefault();
    if (fbPrev.scale > 1.05) fbResetZoom();
    else fbZoomTo(2, ev.clientX, ev.clientY);
  });
  fbStage.addEventListener('pointerdown', function (ev) {
    if (ev.target.id !== 'fbPreviewImg') return;
    if (fbPrev.scale <= 1.02) return;
    fbPrev.drag = { x: ev.clientX, y: ev.clientY, ox: fbPrev.x, oy: fbPrev.y };
    fbStage.classList.add('dragging');
    try { fbStage.setPointerCapture(ev.pointerId); } catch (err) {}
    ev.preventDefault();
  });
  fbStage.addEventListener('pointermove', function (ev) {
    if (!fbPrev.drag) return;
    fbPrev.x = fbPrev.drag.ox + (ev.clientX - fbPrev.drag.x);
    fbPrev.y = fbPrev.drag.oy + (ev.clientY - fbPrev.drag.y);
    fbApplyZoom();
  });
  function fbEndDrag() {
    fbPrev.drag = null;
    fbStage.classList.remove('dragging');
  }
  fbStage.addEventListener('pointerup', fbEndDrag);
  fbStage.addEventListener('pointercancel', fbEndDrag);
  document.getElementById('fbPreviewImg').addEventListener('dragstart', function (ev) { ev.preventDefault(); });
  document.addEventListener('keydown', function (ev) {
    var mask = document.getElementById('fbPreview');
    if (!mask || mask.hidden) return;
    if (ev.key === 'Escape') fbPreviewClose();
    else if (ev.key === 'ArrowLeft') fbPreviewShow(fbPrev.i - 1);
    else if (ev.key === 'ArrowRight') fbPreviewShow(fbPrev.i + 1);
    else if (ev.key === '+' || ev.key === '=' || ev.key === 'Add') { ev.preventDefault(); fbZoomTo(fbPrev.scale * FB_ZOOM_STEP); }
    else if (ev.key === '-' || ev.key === '_' || ev.key === 'Subtract') { ev.preventDefault(); fbZoomTo(fbPrev.scale / FB_ZOOM_STEP); }
    else if (ev.key === '0') { ev.preventDefault(); fbResetZoom(); }
  });
  function fbToggleItem(item) {
    if (!item) return;
    var id = item.getAttribute('data-id');
    var hd = item.querySelector('.fb-item-hd');
    var next = item.classList.contains('bd-collapsed');
    fbExpanded[id] = next;
    item.classList.toggle('bd-collapsed', !next);
    if (hd) hd.setAttribute('aria-expanded', next ? 'true' : 'false');
  }
  document.getElementById('fbAdminList').addEventListener('click', function (ev) {
    var hd = ev.target.closest ? ev.target.closest('.fb-item-hd') : null;
    if (hd && !(ev.target.closest && ev.target.closest('button'))) {
      fbToggleItem(hd.closest ? hd.closest('.fb-item') : null);
      return;
    }
    var link = ev.target.closest ? ev.target.closest('a.fb-img-link') : null;
    if (link) {
      ev.preventDefault();
      var wrap = link.closest ? link.closest('.fb-imgs') : null;
      var links = wrap ? Array.prototype.slice.call(wrap.querySelectorAll('a.fb-img-link')) : [link];
      fbPrev.urls = links.map(function (a) {
        var im = a.querySelector('img');
        if (im && im.src && im.src.indexOf('blob:') === 0) return im.src;
        return a.getAttribute('data-auth-src') || a.getAttribute('href') || '';
      }).filter(Boolean);
      fbPrev.names = links.map(function (a) {
        var im = a.querySelector('img');
        return (im && im.getAttribute('alt')) || '';
      });
      var idx = links.indexOf(link);
      fbPreviewShow(idx < 0 ? 0 : idx);
      return;
    }
    var btn = ev.target.closest ? ev.target.closest('button[data-act]') : null;
    if (!btn) return;
    var id = btn.getAttribute('data-id');
    var act = btn.getAttribute('data-act');
    if (act === 'del') {
      if (!confirm('删除这条反馈？图片会一并删除。')) return;
      fetch('/api/feedback/' + id, { method: 'DELETE' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          if (!res.ok) { sayFb(false, (res.data && res.data.detail) || '删除失败'); return; }
          sayFb(true, '已删除');
          loadFeedback();
        });
      return;
    }
    if (act === 'reply-send') {
      var box = btn.closest ? btn.closest('.fb-item') : null;
      var input = box ? box.querySelector('.fb-reply-input') : null;
      var text = input ? input.value.trim() : '';
      if (!text) { sayFb(false, '请先填写回复内容'); return; }
      btn.disabled = true;
      fetch('/api/feedback/' + id + '/reply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reply: text })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          if (!res.ok) { sayFb(false, (res.data && res.data.detail) || '回复失败'); }
          else { sayFb(true, '✓ 已回复并标为已处理，提交者可查看'); }
          loadFeedback();
        })
        .finally(function () { if (btn) btn.disabled = false; });
      return;
    }
    var st = act === 'done' ? 'done' : 'read';
    fetch('/api/feedback/' + id, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: st })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        if (!res.ok) { sayFb(false, (res.data && res.data.detail) || '更新失败'); return; }
        sayFb(true, st === 'done' ? '已标记处理' : '已标为已读');
        loadFeedback();
      });
  });
  global.loadFeedback = loadFeedback;
  global.setFbBadge = setFbBadge;
})(window);
