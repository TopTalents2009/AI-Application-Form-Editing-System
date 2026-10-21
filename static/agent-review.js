/* 主界面 AI 审核：Jev 意图 ≥70 → 组长审核 */
(function () {
  var state = {
    page: "queue",
    sessions: [],
    current: null,
    fields: [],
    intents: [],
    hits: [],
    drops: [],
    total: 0,
    dropTotal: 0,
    selected: null,
    stats: null,
    searchTimer: 0,
    dropView: "dropped",
    reviewer: "",
    watchGroups: [],
    runs: {},
    abort: null,
    party: { staff: [], reachingAliases: ["reaching"], titleStaffPattern: "" },
    partyTimer: 0,
    role: "",
    intentFilter: "",
    exPageSize: 10,
    ex: {
      intent: { rows: [], page: 1, q: "" },
      filter: { rows: [], page: 1, q: "" },
      review: { rows: [], page: 1, q: "" }
    }
  };

  function $(id) { return document.getElementById(id); }
  function canReview() {
    return state.role === "admin" || state.role === "leader";
  }
  function applyRoleGate() {
    var ok = canReview();
    [
      "arAnalyzeBtn", "arResumeBtn", "arIncBtn", "arAnalyzeForceBtn",
      "arSavePartyBtn", "arSaveReviewBtn", "arReachAddBtn", "arStaffAddBtn",
      "arApplyPartyJsonBtn"
    ].forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = !ok;
    });
    [
      "arReachIn", "arPartyName", "arPartyAliases", "arTitlePat", "arPartyJson"
    ].forEach(function (id) {
      var el = $(id);
      if (el) el.readOnly = !ok;
    });
    if (!ok && $("arPartySaveNote") && !$("arPartySaveNote").textContent) {
      $("arPartySaveNote").textContent = "仅组长或管理员可改名单、分析与审核。";
    }
    updateAnalyzeBtn();
  }
  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function senderLabel(s) {
    s = String(s || "").trim();
    if (!s || /^(null|none|undefined|nil)$/i.test(s)) return "未知";
    return s;
  }
  function api(path, opts) {
    return fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" }
    }, opts || {})).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || data.ok === false) {
          var d = data.detail || data.message || "请求失败";
          throw new Error(typeof d === "string" ? d : JSON.stringify(d));
        }
        return data;
      });
    });
  }
  function showPage(id) {
    state.page = id;
    document.querySelectorAll("#arTabs .ar-tab").forEach(function (el) {
      el.classList.toggle("on", el.getAttribute("data-ar-page") === id);
    });
    document.querySelectorAll("#arMain .ar-page").forEach(function (el) {
      el.classList.toggle("hidden", el.id !== "arPage-" + id);
    });
    if (id === "dropped") loadDrops();
    if (id === "examples") loadExamples();
    if (id === "party") {
      if (!(state.party.staff || []).length) loadParty();
      else renderParty();
    }
  }
  function renderBar() {
    var el = $("arBar");
    if (!el) return;
    var cur = state.current;
    if (!cur) {
      el.textContent = "请先在左侧选择一个会话。Jev 意图评分只处理这一个会话。";
      return;
    }
    var st = state.stats || {};
    el.innerHTML = "<strong>当前会话</strong> " +
      '<span class="ar-chip">' + esc(cur.kindLabel || "") + "</span> " +
      (cur.is_delivery ? '<span class="ar-chip jia">交付群</span> ' : "") +
      esc(cur.name) +
      ' <span class="ar-note" style="margin:0">过滤掉 ' + (st.dropped || 0) +
      " · 保留 " + (st.kept || 0) +
      " · 待审 ≥70 " + (st.queue || 0) +
      " · 已审核 " + (st.reviewed || 0) + "</span>";
  }
  function renderStats() {
    var st = state.stats || {};
    var box = $("arStats");
    if (!box) return;
    box.innerHTML =
      '<span class="ar-chip jia">待审 ' + (st.queue || 0) + "</span>" +
      '<span class="ar-chip yi">乙方原位 ' + (st.yi || 0) + "</span>" +
      '<span class="ar-chip">闲聊/确认 ' + (st.dropped || 0) + "</span>" +
      '<span class="ar-chip">Jev 已评 ' + (st.kept || 0) + "</span>" +
      '<span class="ar-chip ok">已审核 ' + (st.reviewed || 0) + "</span>";
    renderIntentNav();
  }
  function hitReviewed(hit) {
    if (!hit) return false;
    if (hit.reviewed) return true;
    var review = hit.review;
    return !!(review && (review.id || review.should_filter || review.selected_intent ||
      (review.selected_intents && review.selected_intents.length)));
  }
  function hitIntentCodes(hit) {
    if (!hit) return ["none"];
    if (hit.skip_jev || hit.party === "乙方") return ["yi"];
    if (hit.intent) return [String(hit.intent)];
    return ["none"];
  }
  function intentName(code) {
    if (!code) return "全部";
    if (code === "__queue__") return "待审";
    if (code === "__reviewed__") return "已审核";
    if (code === "yi") return "乙方";
    if (code === "none") return "未判定";
    var it = (state.intents || []).find(function (x) { return x && x.id === code; });
    if (it && it.name) return it.name;
    var pool = (state.hits || []).concat(state.drops || []);
    for (var i = 0; i < pool.length; i++) {
      if (pool[i].intent === code && pool[i].intent_label) return pool[i].intent_label;
    }
    return code;
  }
  function intentCatalog() {
    var rows = [];
    var seen = {};
    (state.intents || []).forEach(function (it) {
      if (!it || !it.id || seen[it.id]) return;
      seen[it.id] = true;
      rows.push({ id: it.id, name: it.name || it.id });
    });
    var pool = (state.hits || []).concat(state.drops || []);
    pool.forEach(function (h) {
      hitIntentCodes(h).forEach(function (c) {
        if (c === "yi" || c === "none" || seen[c]) return;
        seen[c] = true;
        rows.push({ id: c, name: intentName(c) });
      });
    });
    rows.push({ id: "yi", name: "乙方" });
    rows.push({ id: "none", name: "未判定" });
    return rows;
  }
  function hitMatchesFilter(hit, filter) {
    if (!filter) return true;
    if (filter === "__queue__") {
      return !!hit.shown && !hitReviewed(hit) && !(hit.skip_jev || hit.party === "乙方");
    }
    if (filter === "__reviewed__") return hitReviewed(hit);
    return hitIntentCodes(hit).indexOf(filter) >= 0;
  }
  function countIntentBuckets(items) {
    var counts = { "": items.length, __queue__: 0, __reviewed__: 0 };
    items.forEach(function (hit) {
      if (hitMatchesFilter(hit, "__queue__")) counts.__queue__++;
      if (hitReviewed(hit)) counts.__reviewed__++;
      hitIntentCodes(hit).forEach(function (c) {
        counts[c] = (counts[c] || 0) + 1;
      });
    });
    return counts;
  }
  function filteredHits(items) {
    var f = state.intentFilter || "";
    if (!f) return items;
    return items.filter(function (h) { return hitMatchesFilter(h, f); });
  }
  function ensureFilter(items) {
    if (!state.intentFilter) return;
    var counts = countIntentBuckets(items);
    if (!counts[state.intentFilter]) state.intentFilter = "";
  }
  function renderIntentNavBox(id, items) {
    var box = $(id);
    if (!box) return;
    if (!items.length) {
      box.innerHTML = "";
      return;
    }
    var counts = countIntentBuckets(items);
    var chips = [{ id: "", name: "全部" }];
    if (counts.__queue__) chips.push({ id: "__queue__", name: "待审" });
    if (counts.__reviewed__) chips.push({ id: "__reviewed__", name: "已审核" });
    intentCatalog().forEach(function (it) {
      if ((counts[it.id] || 0) > 0) chips.push(it);
    });
    var cur = state.intentFilter || "";
    box.innerHTML = chips.map(function (it) {
      var key = it.id || "";
      var n = key ? (counts[key] || 0) : counts[""];
      var on = cur === key ? " on" : "";
      var cls = "ar-intent-chip" + on;
      if (it.id === "__queue__") cls += " queue";
      if (it.id === "__reviewed__") cls += " reviewed";
      if (it.id === "yi") cls += " yi";
      return '<button type="button" class="' + cls + '" data-ar-intent="' + esc(key) + '">' +
        esc(it.name) + " <b>" + n + "</b></button>";
    }).join("");
  }
  function renderIntentNav() {
    renderIntentNavBox("arIntentNav", state.hits || []);
    renderIntentNavBox("arIntentNavDrop", state.drops || []);
  }
  function emptyHitsHtml(analyzed) {
    if (state.intentFilter) {
      return '<div class="wecom-empty">「' + esc(intentName(state.intentFilter)) +
        "」没有对应消息。点全部或其他分类。</div>";
    }
    return '<div class="wecom-empty">' + (analyzed
      ? "已分析。当前没有可显示的消息。"
      : "尚未分析。点「分析本会话」做第一次全量分析。") + "</div>";
  }
  function paintCurrentHits(keepScroll) {
    var analyzed = !!(state.stats && state.stats.total > 0);
    paintHitList($("arHitList"), filteredHits(state.hits || []), emptyHitsHtml(analyzed), keepScroll);
  }
  function paintCurrentDrops(keepScroll) {
    var empty = state.intentFilter
      ? '<div class="wecom-empty">「' + esc(intentName(state.intentFilter)) + "」没有对应消息。</div>"
      : '<div class="wecom-empty">没有记录。</div>';
    paintHitList($("arDropList"), filteredHits(state.drops || []), empty, keepScroll);
  }
  function applyIntentFilter(id) {
    state.intentFilter = (state.intentFilter === id) ? "" : id;
    renderIntentNav();
    if (state.page === "dropped") paintCurrentDrops(false);
    else paintCurrentHits(false);
  }
  function arWatchOnly() {
    var el = $("arOnlyWatch");
    return !el || !!el.checked;
  }
  function arGroupKind() {
    var el = $("arOnlyGroup");
    return (el && el.checked) ? "group" : "all";
  }
  function arWatched(name, sid) {
    if (typeof window.wecomGroupWatched === "function") {
      return window.wecomGroupWatched(name, sid);
    }
    var groups = state.watchGroups || [];
    if (!groups.length) return false;
    name = String(name || "");
    sid = String(sid || "");
    var blob = (name + " " + sid).toLowerCase();
    for (var i = 0; i < groups.length; i++) {
      var g = String(groups[i] || "");
      if (!g) continue;
      if (g === name || g === sid) return true;
      var gl = g.toLowerCase();
      if (gl && blob.indexOf(gl) >= 0) return true;
    }
    return false;
  }
  function arWatchHint() {
    var el = $("arWatchHint");
    if (!el) return;
    var n = (state.watchGroups || []).length;
    el.textContent = n
      ? ("已关注 " + n + " 条会话 · 与上方会话记录共用 ★")
      : "点会话左侧 ★ 关注（与上方会话记录相同）";
  }
  function renderSessions() {
    var list = $("arSessions");
    if (!list) return;
    var curId = state.current && state.current.session_id;
    var rows = state.sessions || [];
    arWatchHint();
    if (!rows.length) {
      list.innerHTML = '<div class="wecom-empty">' + (arWatchOnly()
        ? "没有已关注的会话。点会话左侧 ★ 关注，或取消「仅关注」。"
        : "没有匹配的会话。可取消「仅群聊」，或先在上方会话记录里同步。") + "</div>";
      return;
    }
    list.innerHTML = rows.map(function (s) {
      var sid = s.session_id || "";
      var name = s.name || sid;
      var watched = s.watched || arWatched(name, sid);
      var on = sid === curId ? " on" : "";
      var run = state.runs[sid] || {};
      var analyzed = !!(s.analyzed || run.analyzed || run.hits);
      var incomplete = !!(run.incomplete || (analyzed && run.full_done === false));
      var prog = (run.progress_done && run.progress_total)
        ? (" " + run.progress_done + "/" + run.progress_total)
        : "";
      var meta = (s.kindLabel || "") + (s.msg_count ? " · " + s.msg_count + " 条" : "") +
        (incomplete ? " · 未完成" + prog + "，可继续" : (analyzed ? " · 已分析" : "")) +
        (analyzed && run.queue ? " · 待审 " + run.queue : "") +
        (analyzed && run.reviewed ? " · 已审核 " + run.reviewed : "") +
        (s.last_time ? " · " + s.last_time : "");
      var starTitle = watched ? "取消关注" : "加入关注会话";
      return '<div class="wecom-item-wrap' + on + '" data-id="' + esc(sid) + '" data-sid="' + esc(sid) + '" data-name="' + esc(name) + '">' +
        '<button type="button" class="wecom-star' + (watched ? " on" : "") + '" title="' + esc(starTitle) + '">★</button>' +
        '<button type="button" class="wecom-item"><b>' + esc(name) + "</b><span>" + esc(meta) + "</span></button></div>";
    }).join("");
  }
  function renderHit(hit) {
    var review = hit.review || {};
    var selected = state.selected && String(state.selected.message_id) === String(hit.message_id);
    var body = (hit.filename ? "【文件】" + hit.filename + "\n" : "") + (hit.text || "");
    var skipJev = !!(hit.skip_jev || hit.party === "乙方");
    var chatter = !skipJev && (hit.intent === "confirm" || hit.intent === "chat" || !hit.gemini_useful);
    var useful = skipJev ? "原位展示" : (chatter ? "闲聊/确认" : "已评分");
    var reviewed = hitReviewed(hit);
    var reviewChip = "";
    if (reviewed) {
      reviewChip = '<span class="ar-chip ok">已审核</span>';
      if (review.should_filter) reviewChip += '<span class="ar-chip drop">应过滤</span>';
    }
    var score = hit.score || 0;
    var scoreCls = score >= 70 ? "ok" : "bad";
    var labs = reviewLabels(review);
    var intentChips = labs.length
      ? labs.map(function (l) { return '<span class="ar-chip jia">' + esc(l) + "</span>"; }).join("")
      : (hit.intent_label ? '<span class="ar-chip jia">' + esc(hit.intent_label) + "</span>" : "");
    var extra = skipJev
      ? '<span class="ar-chip yi">原位展示</span><span class="ar-chip">不进入 Jev</span>'
      : (
        '<span class="ar-chip">' + esc(useful) + "</span>" +
        intentChips +
        '<span class="ar-chip ' + scoreCls + '">评分 ' + esc(score) + "</span>" +
        (hit.need ? '<span class="ar-chip">' + esc(hit.need) + "</span>" : "")
      );
    var reviewBtn = !skipJev && canReview()
      ? '<div style="margin-top:8px"><button type="button" class="mini' + (reviewed ? " ar-btn-reviewed" : "") +
        '" data-ar-open="' + esc(String(hit.message_id || "")) + '">' +
        (reviewed ? "已审核 · 改" : "审核本条") + "</button></div>"
      : "";
    return '<article class="ar-hit' + (selected ? " selected" : "") + (skipJev ? " ar-hit-yi" : "") +
      (reviewed ? " ar-hit-reviewed" : "") + '">' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
      '<span class="ar-chip ' + (hit.party === "甲方" ? "jia" : "yi") + '">' + esc(hit.party || "") + "</span>" +
      "<strong>" + esc(senderLabel(hit.sender)) + "</strong>" +
      '<span class="ar-note" style="margin:0">' + esc(hit.time_text) + "</span>" +
      reviewChip +
      "</div>" +
      '<div class="ar-msg">' + esc(body) + "</div>" +
      "<div>" + extra + "</div>" +
      (!skipJev && hit.gemini_reason ? '<div class="ar-note" style="margin:6px 0 0">' + esc(hit.gemini_reason) + "</div>" : "") +
      (!skipJev && hit.action ? '<div class="ar-note" style="margin:4px 0 0">Jev：' + esc(hit.action) + "</div>" : "") +
      (hit.error ? '<div class="ar-note" style="margin:4px 0 0;color:#b42318">' + esc(hit.error) + "</div>" : "") +
      reviewBtn +
      "</article>";
  }
  function loadSessions() {
    var q = ($("arSessionQ") || {}).value || "";
    var kind = arGroupKind();
    var list = $("arSessions");
    if (list) list.innerHTML = '<div class="wecom-empty">正在加载会话…</div>';
    var url = "/api/wecom/groups?kind=" + encodeURIComponent(kind) + "&q=" + encodeURIComponent(q);
    if (arWatchOnly()) url += "&watch_only=1&limit=1000";
    return fetch(url).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || "加载会话失败");
        return j;
      });
    }).then(function (data) {
      if (Array.isArray(data.watchGroups)) state.watchGroups = data.watchGroups;
      var items = data.items || [];
      if (arWatchOnly() && (state.watchGroups || []).length) {
        items = items.filter(function (it) {
          return arWatched(it.display_name || "", it.username || it.session_id || "");
        });
      }
      state.sessions = items.map(function (it) {
        var sid = it.username || it.session_id || "";
        var name = (typeof wecomSessionTitle === "function") ? wecomSessionTitle(it) : (it.display_name || sid);
        var delivery = String(name).indexOf("交付") >= 0;
        return {
          session_id: sid,
          source_id: it.source_id || "*",
          name: name,
          msg_count: it.msg_count || 0,
          last_time: it.last_time || "",
          synced_at: it.synced_at || "",
          is_group: !!it.is_group,
          is_delivery: delivery,
          watched: arWatched(name, sid),
          kindLabel: delivery ? "交付群" : (it.is_group ? "群聊" : "单聊"),
          replicas: it.replicas || [],
          analyzed: false
        };
      });
      return api("/api/agent-review/runs").then(function (runs) {
        state.runs = runs.data || {};
        (state.sessions || []).forEach(function (s) {
          var r = state.runs[s.session_id];
          if (r) {
            s.analyzed = !!r.analyzed;
            s.queue = r.queue || 0;
            s.hits = r.hits || 0;
            s.full_done = !!r.full_done;
            s.incomplete = !!r.incomplete;
            s.progress_done = r.progress_done || 0;
            s.progress_total = r.progress_total || 0;
          }
        });
        renderSessions();
      });
    }).catch(function (e) {
      if (list) list.innerHTML = '<div class="wecom-empty">' + esc(e.message) + "</div>";
    });
  }
  function toggleWatch(btn) {
    if (typeof window.wecomToggleWatch === "function") {
      window.wecomToggleWatch(btn);
      return;
    }
    var wrap = btn.closest(".wecom-item-wrap");
    if (!wrap) return;
    var name = wrap.getAttribute("data-name") || "";
    var sid = wrap.getAttribute("data-id") || wrap.getAttribute("data-sid") || "";
    var watch = !arWatched(name, sid);
    btn.disabled = true;
    fetch("/api/wecom/watch-groups/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: name, session_id: sid, watch: watch })
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (x) {
      var j = x.j || {};
      if (!x.ok) throw new Error(j.detail || "操作失败");
      state.watchGroups = j.groups || [];
      btn.classList.toggle("on", watch);
      arWatchHint();
      if (typeof window.wecomReloadSessions === "function") window.wecomReloadSessions();
      if (arWatchOnly() && !watch) return loadSessions();
    }).catch(function () {
    }).finally(function () {
      btn.disabled = false;
    });
  }
  function currentSession() {
    return state.current || { session_id: "", source_id: "", name: "" };
  }
  function arScrollToLatest(el) {
    if (!el) return;
    function go() { el.scrollTop = el.scrollHeight; }
    go();
    requestAnimationFrame(function () {
      go();
      requestAnimationFrame(go);
    });
    setTimeout(go, 60);
    setTimeout(go, 220);
  }
  function paintHitList(el, items, emptyHtml, keepScroll) {
    if (!el) return;
    var top = el.scrollTop;
    if (!items.length) {
      el.innerHTML = emptyHtml;
      if (keepScroll) el.scrollTop = top;
      return;
    }
    el.innerHTML = '<div class="wecom-stream-list">' + items.map(renderHit).join("") + "</div>";
    if (keepScroll) {
      el.scrollTop = top;
      requestAnimationFrame(function () { el.scrollTop = top; });
      return;
    }
    arScrollToLatest(el);
  }
  function loadHits(opts) {
    var cur = currentSession();
    if (!cur.session_id) return Promise.resolve();
    var url = "/api/agent-review/hits?session_id=" + encodeURIComponent(cur.session_id) +
      "&view=all&offset=0&limit=0";
    if ($("arOnlyJia") && $("arOnlyJia").checked) url += "&party=" + encodeURIComponent("甲方");
    return api(url).then(function (data) {
      state.hits = data.data || [];
      state.total = data.total || 0;
      state.stats = data.stats || state.stats;
      syncRunStats(cur.session_id);
      ensureFilter(state.hits);
      renderBar();
      renderStats();
      renderSessions();
      paintCurrentHits(!!(opts && opts.keepScroll));
      updateAnalyzeBtn();
    });
  }
  function syncRunStats(sid) {
    if (!sid) return;
    var run = state.runs[sid] || {};
    run.queue = (state.stats && state.stats.queue) || 0;
    run.reviewed = (state.stats && state.stats.reviewed) || 0;
    state.runs[sid] = run;
  }
  function loadDrops(opts) {
    var cur = currentSession();
    if (!cur.session_id) return Promise.resolve();
    var view = ($("arDropView") || {}).value || state.dropView || "dropped";
    state.dropView = view;
    var url = "/api/agent-review/hits?session_id=" + encodeURIComponent(cur.session_id) +
      "&view=" + encodeURIComponent(view) + "&offset=0&limit=0";
    return api(url).then(function (data) {
      state.drops = data.data || [];
      state.dropTotal = data.total || 0;
      state.stats = data.stats || state.stats;
      syncRunStats(cur.session_id);
      ensureFilter(state.drops);
      renderBar();
      renderStats();
      renderSessions();
      paintCurrentDrops(!!(opts && opts.keepScroll));
    });
  }
  function setBusy(on) {
    ["arAnalyzeBtn", "arIncBtn", "arAnalyzeForceBtn", "arResumeBtn"].forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = !!on;
    });
    if ($("arPauseBtn")) $("arPauseBtn").hidden = !on;
  }
  function showProgress(ev) {
    var box = $("arProgress");
    if (!box) return;
    box.hidden = false;
    var phase = ev.phase || "load";
    var pct = Math.max(0, Math.min(100, parseInt(ev.percent, 10) || 0));
    box.classList.toggle("is-indeterminate", phase === "load" || !ev.total);
    $("arProgressLab").textContent = ev.label || "处理中…";
    $("arProgressPct").textContent = pct + "%";
    var bar = $("arProgressBar");
    if (bar && phase !== "load") bar.style.width = pct + "%";
    var bits = [];
    if (ev.total) bits.push("进度 " + (ev.done || 0) + "/" + ev.total);
    if (ev.jevDone != null) bits.push("Jev " + ev.jevDone);
    if (ev.dropped != null) bits.push("已过滤 " + ev.dropped);
    $("arProgressMeta").textContent = bits.join(" · ");
  }
  function hideProgress() {
    var box = $("arProgress");
    if (box) box.hidden = true;
  }
  function readSSE(res, onEvent) {
    var reader = res.body.getReader();
    var dec = new TextDecoder();
    var buf = "";
    function pump() {
      return reader.read().then(function (chunk) {
        buf += dec.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
        var parts = buf.split("\n\n");
        buf = chunk.done ? "" : parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var lines = parts[i].split("\n");
          var data = [];
          for (var j = 0; j < lines.length; j++) {
            if (lines[j].indexOf("data:") === 0) data.push(lines[j].slice(5).trim());
          }
          if (!data.length) continue;
          try { onEvent(JSON.parse(data.join(""))); } catch (e) {}
        }
        if (chunk.done) return;
        return pump();
      });
    }
    return pump();
  }
  function currentRun() {
    var cur = state.current || {};
    return state.runs[cur.session_id] || {};
  }
  function sessionAnalyzed() {
    var cur = state.current || {};
    var run = currentRun();
    return !!(cur.analyzed || cur.hits || (state.stats && state.stats.total > 0) || run.analyzed || run.hits);
  }
  function sessionIncomplete() {
    var run = currentRun();
    var cur = state.current || {};
    var hits = run.hits || cur.hits || (state.stats && state.stats.total) || 0;
    if (run.incomplete) return true;
    if (hits && (run.full_done === false || run.full_done === 0)) return true;
    return false;
  }
  function updateAnalyzeBtn() {
    if (!canReview()) {
      ["arAnalyzeBtn", "arResumeBtn", "arIncBtn", "arAnalyzeForceBtn"].forEach(function (id) {
        if ($(id)) $(id).hidden = true;
      });
      return;
    }
    var analyzed = sessionAnalyzed();
    var incomplete = sessionIncomplete();
    if ($("arAnalyzeBtn")) $("arAnalyzeBtn").hidden = analyzed;
    if ($("arResumeBtn")) $("arResumeBtn").hidden = !incomplete;
    if ($("arIncBtn")) $("arIncBtn").hidden = !analyzed || incomplete;
    if ($("arAnalyzeForceBtn")) $("arAnalyzeForceBtn").hidden = !analyzed;
  }
  function pauseAnalyze() {
    if (state.abort) state.abort.abort();
  }
  function analyze(force) {
    var cur = currentSession();
    var note = $("arQueueNote");
    if (!canReview()) {
      if (note) note.textContent = "仅组长或管理员可以分析会话。";
      return Promise.resolve();
    }
    if (!cur.session_id) {
      if (note) note.textContent = "请先在左侧点一个会话。";
      return Promise.resolve();
    }
    var incomplete = !force && sessionIncomplete();
    var incremental = !force && sessionAnalyzed() && !incomplete;
    if (note) {
      note.textContent = incomplete
        ? ("从断点继续分析「" + cur.name + "」…")
        : (incremental ? ("增量分析「" + cur.name + "」…") : ("全量分析「" + cur.name + "」…"));
    }
    if (state.abort) state.abort.abort();
    state.abort = (typeof AbortController !== "undefined") ? new AbortController() : null;
    setBusy(true);
    showProgress({
      phase: "load",
      percent: 3,
      label: incomplete ? "正在从断点继续…" : (incremental ? "正在读取新消息…" : "正在全量读取会话…")
    });
    if (!incremental && !incomplete) {
      $("arHitList").innerHTML = '<div class="wecom-empty">Jev 意图评分中，请稍候…</div>';
    }
    var finished = null;
    return fetch("/api/agent-review/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      signal: state.abort ? state.abort.signal : undefined,
      body: JSON.stringify({
        session_id: cur.session_id,
        source_id: cur.source_id,
        name: cur.name,
        force: !!force,
        stream: true
      })
    }).then(function (res) {
      var ctype = (res.headers.get("content-type") || "").toLowerCase();
      if (!res.ok) {
        return res.json().then(function (j) {
          throw new Error(j.detail || j.message || ("分析失败 HTTP " + res.status));
        });
      }
      if (ctype.indexOf("text/event-stream") < 0) {
        return res.json().then(function (data) {
          finished = data;
        });
      }
      return readSSE(res, function (ev) {
        if (!ev) return;
        if (ev.phase === "error") throw new Error(ev.message || "分析失败");
        showProgress(ev);
        if (ev.phase === "done") finished = ev.result || ev;
      });
    }).then(function () {
      var data = finished;
      if (!data) throw new Error("分析没有返回结果");
      hideProgress();
      setBusy(false);
      state.stats = data.stats || {};
      if (state.current) state.current.msg_count = data.msg_count;
      renderBar();
      renderStats();
      if (note) {
        var mode = data.resume ? "断点续跑" : (data.incremental ? "增量" : "全量");
        note.textContent = mode + "分析「" + data.name + "」完成。本次新消息 " +
          (data.new_count || 0) + " 条，待处理甲方 " + (data.pending || 0) +
          " 条；乙方原位 " + (data.placedYi || (data.stats && data.stats.yi) || 0) +
          "；Jev " + (data.jevCalls || 0) +
          "；待审 ≥" + (data.scoreMin || 70) + " 共 " + ((data.stats && data.stats.queue) || 0) + " 条。";
      }
      if (state.current) {
        state.current.analyzed = true;
        state.current.hits = (data.stats && data.stats.total) || state.current.hits;
        state.current.queue = (data.stats && data.stats.queue) || 0;
      }
      if (data.session_id) {
        state.runs[data.session_id] = {
          analyzed: true,
          full_done: true,
          incomplete: false,
          hits: (data.stats && data.stats.total) || 0,
          queue: (data.stats && data.stats.queue) || 0
        };
      }
      renderSessions();
      updateAnalyzeBtn();
      return loadHits();
    }).catch(function (e) {
      hideProgress();
      setBusy(false);
      var aborted = e && (e.name === "AbortError" || /abort/i.test(String(e.message || "")));
      if (note) {
        note.textContent = aborted
          ? "已暂停，进度已保存。点「继续分析」从断点接着跑。"
          : (e.message + "。已保存断点，可点「继续分析」。");
      }
      var run = state.runs[cur.session_id] || {};
      run.incomplete = true;
      run.full_done = false;
      run.analyzed = true;
      state.runs[cur.session_id] = run;
      if (state.current) {
        state.current.analyzed = true;
        state.current.incomplete = true;
      }
      updateAnalyzeBtn();
      renderSessions();
      return loadHits();
    });
  }
  function selectSession(sessionId) {
    var row = (state.sessions || []).find(function (s) { return s.session_id === sessionId; });
    if (!row) return Promise.resolve();
    state.current = row;
    state.selected = null;
    state.hits = [];
    state.drops = [];
    state.stats = null;
    state.intentFilter = "";
    renderSessions();
    $("arChatTitle").textContent = row.name || sessionId;
    var run = state.runs[sessionId] || {};
    var analyzed = !!(row.analyzed || run.analyzed || run.hits);
    var incomplete = !!(run.incomplete || row.incomplete);
    $("arChatSub").textContent = (row.kindLabel || "") +
      (incomplete ? " · 上次未完成，可继续" : (analyzed ? " · 已分析，直接显示上次结果" : " · 尚未分析")) +
      (row.session_id ? " · " + row.session_id : "");
    var note = $("arQueueNote");
    if (note) {
      note.textContent = incomplete
        ? "上次分析中断。点「继续分析」从断点接着跑，已完成的结果会保留。"
        : (analyzed
          ? "已分析过，正在打开上次结果。新消息请点「增量分析」。"
          : "尚未分析。点「分析本会话」做第一次全量分析。");
    }
    showPage("queue");
    updateAnalyzeBtn();
    $("arHitList").innerHTML = analyzed
      ? '<div class="wecom-empty">正在载入上次分析结果…</div>'
      : '<div class="wecom-empty">尚未分析。点「分析本会话」做第一次全量分析。</div>';
    return loadHits();
  }
  function findHit(mid) {
    function same(h) { return String(h.message_id) === String(mid); }
    return (state.hits || []).find(same) || (state.drops || []).find(same) || state.selected;
  }
  function closeReview() {
    var mask = $("arReviewMask");
    if (mask) mask.hidden = true;
  }
  function placePop() {
    var pop = $("arReviewPop");
    if (!pop) return;
    pop.style.left = "";
    pop.style.top = "";
    var body = pop.querySelector(".ar-pop-body");
    if (body) body.scrollTop = 0;
  }
  function reviewLabels(review) {
    review = review || {};
    if (review.should_filter) return ["应过滤"];
    var items = review.selected_intents;
    if (Array.isArray(items) && items.length) {
      return items.map(function (x) {
        if (typeof x === "string") return x;
        var lab = x.label || x.intentLabel || x.intent || "";
        if (x.note && (x.intent === "other" || lab === "其他") && lab.indexOf(x.note) < 0) {
          return "其他：" + x.note;
        }
        return lab;
      }).filter(Boolean);
    }
    if (review.selected_label) {
      return String(review.selected_label).split(/[、,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
    }
    if (review.selected_intent) {
      return String(review.selected_intent).split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
    }
    return [];
  }
  function pickedCodes(review) {
    review = review || {};
    var items = review.selected_intents;
    if (Array.isArray(items) && items.length) {
      return items.map(function (x) {
        return String((typeof x === "string") ? x : (x.intent || x.id || "")).trim();
      }).filter(Boolean);
    }
    var s = review.selected_intent || review.correct_intent || "";
    return String(s).split(/[,，]/).map(function (x) { return x.trim(); }).filter(Boolean);
  }
  function otherNoteOf(review) {
    review = review || {};
    var items = review.selected_intents;
    if (Array.isArray(items)) {
      for (var i = 0; i < items.length; i++) {
        var x = items[i];
        if (!x || typeof x === "string") continue;
        if (String(x.intent || "") !== "other") continue;
        if (x.note) return String(x.note);
        var lab = String(x.label || "");
        var m = lab.match(/^其他[:：]\s*(.+)$/);
        if (m) return m[1].trim();
      }
    }
    var n = String(review.intent_note || "").trim();
    if (n) return n;
    var lab2 = String(review.selected_label || "");
    var m2 = lab2.match(/其他[:：]\s*([^、]+)/);
    return m2 ? m2[1].trim() : "";
  }
  function syncOtherNote(on) {
    var inp = $("arOtherNote");
    if (!inp) return;
    inp.hidden = !on;
    inp.disabled = !on;
    if (on) {
      try { inp.focus(); } catch (e) {}
    }
    requestAnimationFrame(function () { placePop(); });
  }
  function syncCustomIntent(on) {
    var inp = $("arCustomIntent");
    var box = $("arCustomIntentOn");
    if (box) box.checked = !!on;
    if (!inp) return;
    inp.hidden = !on;
    inp.disabled = !on;
    if (on) {
      try { inp.focus(); } catch (e) {}
    }
    requestAnimationFrame(function () { placePop(); });
  }
  function fillReview(hit, anchor) {
    state.selected = hit;
    $("arReviewTitle").textContent = senderLabel(hit.sender) + " · " + (hit.time_text || "");
    $("arReviewerLine").textContent = "审核人：" + (state.reviewer || "当前登录用户");
    var body = (hit.filename ? "【文件】" + hit.filename + "\n" : "") + (hit.text || "");
    $("arReviewBody").innerHTML = '<article class="ar-hit ar-pop-quote">' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
      '<span class="ar-chip ' + (hit.party === "甲方" ? "jia" : "yi") + '">' + esc(hit.party || "") + "</span>" +
      "<strong>" + esc(senderLabel(hit.sender)) + "</strong>" +
      '<span class="ar-note" style="margin:0">' + esc(hit.time_text) + "</span></div>" +
      '<div class="ar-msg">' + esc(body) + "</div></article>";
    var review = hit.review || {};
    var picked = pickedCodes(review);
    if (!picked.length && hit.intent) picked = [String(hit.intent)];
    var cands = (hit.candidates || []).filter(function (c) { return c && !c.fallback; });
    if (!cands.length && hit.intent) {
      cands = [{ intent: hit.intent, intentLabel: hit.intent_label, score: hit.score, need: hit.need, action: hit.action }];
    }
    var shown = {};
    cands.forEach(function (c) { shown[String(c.intent || "")] = true; });
    picked.forEach(function (code) {
      if (!code || shown[code]) return;
      cands.push({ intent: code, intentLabel: intentName(code), score: "", extra: true });
      shown[code] = true;
    });
    var customPrefill = otherNoteOf(review);
    {
      var hint = '<div class="ar-note">下面是本次 Jev 给出的意图，可多选、标「应过滤」，或自行填写更准确的意图。手填的名字会进入样本，下次分析会出现在题目里。</div>';
      function candRow(c, i, allowAuto, mini) {
        var id = String(c.intent || i);
        var on = picked.indexOf(id) >= 0 || (allowAuto && !picked.length && i === 0);
        var hasScore = c.score !== "" && c.score != null && !isNaN(Number(c.score));
        var row = '<label class="ar-cand' + (mini ? " ar-cand-mini" : "") + '">' +
          '<input type="checkbox" name="ar-cand" value="' + esc(id) + '"' +
          ' data-score="' + esc(c.score) + '" data-label="' + esc(c.intentLabel || "") + '"' +
          (on ? " checked" : "") + ">" +
          '<div class="ar-cand-main"><strong>' + esc(c.intentLabel || c.intent) + "</strong>" +
          (!mini && c.action ? '<div class="ar-note" style="margin:4px 0 0">' + esc(c.action) + "</div>" : "") +
          (!mini && c.need ? '<div class="ar-note" style="margin:2px 0 0">需求：' + esc(c.need) + "</div>" : "") +
          "</div>" +
          (!mini && hasScore ? '<div class="ar-cand-score">' + esc(String(c.score)) + "</div>" : "") +
          "</label>";
        if (id === "other") {
          row += '<input type="text" class="ar-other-note" id="arOtherNote" maxlength="80" autocomplete="off"' +
            ' placeholder="请填写具体意图，例如：询问进度"' +
            (on ? "" : " hidden disabled") +
            ' value="' + esc(otherNoteOf(review)) + '">';
          row = '<div class="ar-cand-block">' + row + "</div>";
        }
        return row;
      }
      var customOn = !review.should_filter && !!customPrefill && picked.some(function (c) {
        return c === "other" || c === customPrefill;
      }) && !cands.some(function (c) { return String(c.intent) === customPrefill; });
      $("arCandidateBox").innerHTML = hint +
        '<label class="ar-cand ar-cand-drop"><input type="checkbox" id="arShouldFilter"' +
        (review.should_filter ? " checked" : "") + ">" +
        '<div class="ar-cand-main"><strong>应过滤</strong>' +
        '<div class="ar-note" style="margin:4px 0 0">本条离开待审；相同文本下次分析也会过滤。</div></div></label>' +
        '<div class="ar-cand-acts"><button type="button" class="mini" data-ar-cand-act="all">全选</button> ' +
        '<button type="button" class="mini" data-ar-cand-act="none">清空</button></div>' +
        '<div class="ar-cands">' +
          (cands.length ? cands.map(function (c, i) { return candRow(c, i, !review.should_filter && !customPrefill); }).join("") : "") +
        "</div>" +
        '<div class="ar-cand-block ar-cand-custom">' +
          '<label class="ar-cand"><input type="checkbox" id="arCustomIntentOn"' + (customOn ? " checked" : "") + ">" +
          '<div class="ar-cand-main"><strong>补充意图</strong>' +
          '<div class="ar-note" style="margin:4px 0 0">Jev 没给出合适项时手填，例如「形式审核缺项」。</div></div></label>' +
          '<input type="text" class="ar-other-note" id="arCustomIntent" maxlength="40" autocomplete="off"' +
            ' placeholder="填写更准确的意图"' +
            (customOn ? "" : " hidden disabled") +
            ' value="' + esc(customPrefill || "") + '">' +
        "</div>";
    }
    var labs = reviewLabels(review);
    $("arSaveNote").textContent = labs.length
      ? ("已选：" + labs.join("、") + (review.selected_score == null ? "" : " · " + review.selected_score))
      : "";
    var mask = $("arReviewMask");
    if (mask) mask.hidden = false;
    requestAnimationFrame(function () { placePop(); });
  }
  function saveReview() {
    if (!canReview()) {
      $("arSaveNote").textContent = "仅组长或管理员可以审核。";
      return Promise.resolve();
    }
    if (!state.selected || !state.current) return Promise.resolve();
    var hit = state.selected;
    var shouldFilter = !!( $("arShouldFilter") && $("arShouldFilter").checked );
    var els = document.querySelectorAll('input[name="ar-cand"]:checked');
    var customOn = !!( $("arCustomIntentOn") && $("arCustomIntentOn").checked );
    var customText = (($("arCustomIntent") && $("arCustomIntent").value) || "").trim();
    if (!shouldFilter && !els.length && !customText) {
      $("arSaveNote").textContent = "请选择意图、填写补充意图，或标记应过滤";
      return Promise.resolve();
    }
    if (!shouldFilter && customOn && !customText) {
      $("arSaveNote").textContent = "请填写补充意图";
      syncCustomIntent(true);
      return Promise.resolve();
    }
    if (shouldFilter) {
      var payloadDrop = {
        source_id: hit.source_id || state.current.source_id || "*",
        session_id: state.current.session_id,
        message_id: String(hit.message_id),
        sender: hit.sender,
        time_text: hit.time_text,
        text: hit.text,
        filename: hit.filename,
        intent: hit.intent,
        score: hit.score,
        gemini_useful: hit.gemini_useful,
        should_filter: true,
        correct_useful: false,
        selected_intents: [],
        selected_intent: "",
        selected_label: "应过滤",
        selected_score: null,
        intent_note: ""
      };
      return api("/api/agent-review/reviews", { method: "POST", body: JSON.stringify(payloadDrop) }).then(function (saved) {
        hit.review = saved.data;
        $("arSaveNote").textContent = "已标记应过滤 · " + (saved.data.updated_at || "");
        closeReview();
        var stay = { keepScroll: true };
        if (state.page === "dropped") return loadDrops(stay);
        if (state.page === "examples") return loadExamples();
        return loadHits(stay);
      }).catch(function (e) {
        $("arSaveNote").textContent = e.message;
      });
    }
    var otherNote = "";
    var selected_intents = [].map.call(els, function (el) {
      var score = el.getAttribute("data-score");
      var item = {
        intent: el.value,
        label: el.getAttribute("data-label") || "",
        score: (score === "" || score == null) ? null : Number(score)
      };
      if (el.value === "other") {
        otherNote = (($("arOtherNote") && $("arOtherNote").value) || "").trim();
        if (!otherNote) {
          item.missingNote = true;
          return item;
        }
        item.note = otherNote;
        if (!item.label || item.label === "其他") item.label = "其他：" + otherNote;
      }
      return item;
    });
    if (selected_intents.some(function (x) { return x.missingNote; })) {
      $("arSaveNote").textContent = "选「其他」时请填写具体意图";
      syncOtherNote(true);
      return Promise.resolve();
    }
    if (customText) {
      var exists = selected_intents.some(function (x) { return x.intent === customText || x.label === customText; });
      if (!exists) {
        selected_intents.push({ intent: customText, label: customText, note: customText, score: null });
      }
    }
    var payload = {
      source_id: hit.source_id || state.current.source_id || "*",
      session_id: state.current.session_id,
      message_id: String(hit.message_id),
      sender: hit.sender,
      time_text: hit.time_text,
      text: hit.text,
      filename: hit.filename,
      intent: hit.intent,
      score: hit.score,
      gemini_useful: hit.gemini_useful,
      selected_intents: selected_intents,
      selected_intent: selected_intents.map(function (x) { return x.intent; }).join(","),
      selected_label: selected_intents.map(function (x) { return x.label; }).filter(Boolean).join("、"),
      selected_score: (function () {
        var nums = selected_intents.map(function (x) { return x.score; }).filter(function (n) {
          return n != null && !isNaN(n);
        });
        return nums.length ? Math.max.apply(null, nums) : null;
      })(),
      intent_note: otherNote,
      should_filter: false
    };
    return api("/api/agent-review/reviews", { method: "POST", body: JSON.stringify(payload) }).then(function (saved) {
      hit.review = saved.data;
      $("arSaveNote").textContent = "已保存 " + (saved.data.selected_label || saved.data.selected_intent || "") +
        " · " + (saved.data.updated_at || "");
      closeReview();
      var stay = { keepScroll: true };
      if (state.page === "dropped") return loadDrops(stay);
      if (state.page === "examples") return loadExamples();
      return loadHits(stay);
    }).catch(function (e) {
      $("arSaveNote").textContent = e.message;
    });
  }
  function reviewLabs(r) {
    if (!r) return [];
    if (r.should_filter) return ["应过滤"];
    if (Array.isArray(r.selected_intents) && r.selected_intents.length) {
      return r.selected_intents.map(function (x) {
        return (typeof x === "string") ? x : (x.label || x.intent || "");
      }).filter(Boolean);
    }
    if (r.selected_label) {
      return String(r.selected_label).split(/[、,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
    }
    if (r.selected_intent || r.correct_intent) {
      return [r.selected_label || r.selected_intent || r.correct_intent];
    }
    return [];
  }
  function exampleBlob(key, r) {
    if (key === "review") {
      return [
        r.session_id, r.sender, r.reviewer, r.updated_at, r.text, r.filename,
        reviewLabs(r).join(" "),
        r.should_filter ? "应过滤" : "已采信"
      ].join(" ").toLowerCase();
    }
    var lab = r.label === "drop" ? "应过滤" : (r.label === "keep" ? "应保留" : (r.intentLabel || r.label || ""));
    return [lab, r.filename, r.text, r.reviewer, r.session_id, r.score, r.enabled === false ? "停用" : "启用"].join(" ").toLowerCase();
  }
  function exampleCard(key, r) {
    if (key === "review") {
      var labs = reviewLabs(r);
      var labHtml = labs.map(function (lab) {
        return '<span class="ar-chip ' + (r.should_filter ? "drop" : "jia") + '">' + esc(lab) + "</span>";
      }).join(" ") || '<span class="ar-chip">—</span>';
      var blob = ((r.filename ? "【" + r.filename + "】 " : "") + (r.text || "")).slice(0, 160);
      return '<div class="ar-hit ar-hit-reviewed"><span class="ar-chip ok">已审核</span> ' + labHtml +
        (r.sender ? ' <span class="ar-chip">' + esc(r.sender) + "</span>" : "") +
        (r.reviewer ? ' <span class="ar-chip">' + esc(r.reviewer) + "</span>" : "") +
        '<div class="ar-msg">' + esc(blob || ((r.session_id || "") + " · " + (r.updated_at || "").slice(0, 16))) + "</div></div>";
    }
    var blob = ((r.filename ? "【" + r.filename + "】 " : "") + (r.text || "")).slice(0, 160);
    var lab = r.label === "drop" ? "应过滤" : (r.label === "keep" ? "应保留" : (r.intentLabel || r.label || ""));
    return '<div class="ar-hit"><span class="ar-chip ' + (r.label === "drop" ? "drop" : "jia") + '">' + esc(lab) + "</span> " +
      (r.score != null ? '<span class="ar-chip">评分 ' + esc(r.score) + "</span> " : "") +
      (r.enabled === false ? '<span class="ar-chip">停用</span> ' : "") +
      '<div class="ar-msg">' + esc(blob || "（无正文）") + "</div></div>";
  }
  function paintExamplePanel(key) {
    var ids = {
      intent: { list: "arExIntentList", pager: "arExIntentPager", info: "arExIntentPageInfo", count: "arExIntentCount" },
      filter: { list: "arExFilterList", pager: "arExFilterPager", info: "arExFilterPageInfo", count: "arExFilterCount" },
      review: { list: "arExReviewList", pager: "arExReviewPager", info: "arExReviewPageInfo", count: "arExReviewCount" }
    };
    var p = state.ex[key];
    var el = ids[key];
    var box = $(el.list);
    if (!box || !p) return;
    var q = (p.q || "").trim().toLowerCase();
    var rows = p.rows.filter(function (r) { return !q || exampleBlob(key, r).indexOf(q) >= 0; });
    var size = state.exPageSize;
    var pages = Math.max(1, Math.ceil(rows.length / size) || 1);
    if (p.page > pages) p.page = pages;
    if (p.page < 1) p.page = 1;
    var count = $(el.count);
    if (count) count.textContent = q ? ("筛选 " + rows.length + " / " + p.rows.length) : String(p.rows.length);
    var info = $(el.info);
    if (info) info.textContent = "第 " + p.page + " / " + pages + " 页 · 共 " + rows.length + " 条";
    var pager = $(el.pager);
    if (pager) {
      pager.hidden = rows.length <= size;
      var prev = pager.querySelector('[data-ar-pg="-1"]');
      var next = pager.querySelector('[data-ar-pg="1"]');
      if (prev) prev.disabled = p.page <= 1;
      if (next) next.disabled = p.page >= pages;
    }
    var empty = key === "review" ? "还没有审核记录" : "暂无";
    if (!rows.length) {
      box.innerHTML = '<div class="ar-note">' + (q ? "没有匹配的记录" : empty) + "</div>";
      return;
    }
    box.innerHTML = rows.slice((p.page - 1) * size, p.page * size).map(function (r) {
      return exampleCard(key, r);
    }).join("");
  }
  function loadExamples() {
    return Promise.all([
      api("/api/agent-review/examples?browse=1&limit=400"),
      api("/api/agent-review/reviews")
    ]).then(function (got) {
      var data = got[0] || {};
      state.ex.intent.rows = data.intent || [];
      state.ex.filter.rows = data.filter || [];
      state.ex.review.rows = (got[1] && got[1].data) || [];
      paintExamplePanel("intent");
      paintExamplePanel("filter");
      paintExamplePanel("review");
    }).catch(function (e) {
      ["arExIntentList", "arExFilterList", "arExReviewList"].forEach(function (id) {
        if ($(id)) $(id).innerHTML = '<div class="wecom-empty">' + esc(e.message) + "</div>";
      });
    });
  }
  function splitAliases(text) {
    return String(text || "").split(/[,，、;；]+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }
  function staffBucket(name) {
    if (/申报/.test(name || "")) return "申报组";
    if (/匹配/.test(name || "")) return "匹配组";
    return "其他同事";
  }
  function currentParty() {
    var pat = $("arTitlePat");
    return {
      staff: (state.party.staff || []).map(function (row) {
        return {
          name: String(row.name || "").trim(),
          aliases: (row.aliases || []).map(function (a) { return String(a || "").trim(); }).filter(Boolean)
        };
      }).filter(function (row) { return row.name; }),
      reachingAliases: (state.party.reachingAliases || []).map(function (a) { return String(a || "").trim(); }).filter(Boolean),
      titleStaffPattern: pat ? String(pat.value || "").trim() : (state.party.titleStaffPattern || "")
    };
  }
  function renderReach() {
    var box = $("arReachBox");
    if (!box) return;
    var rows = state.party.reachingAliases || [];
    if (!rows.length) {
      box.innerHTML = '<span class="ar-staff-empty">还没有甲方别名</span>';
      return;
    }
    box.innerHTML = rows.map(function (name, i) {
      return '<span class="ar-party-chip">' + esc(name) +
        '<button type="button" data-ar-del-reach="' + i + '" aria-label="删除">×</button></span>';
    }).join("");
  }
  function renderStaff() {
    var box = $("arStaffBox");
    if (!box) return;
    var q = ($("arPartyQ") && $("arPartyQ").value || "").trim().toLowerCase();
    var groups = { "申报组": [], "匹配组": [], "其他同事": [] };
    (state.party.staff || []).forEach(function (row, i) {
      var blob = ((row.name || "") + " " + (row.aliases || []).join(" ")).toLowerCase();
      if (q && blob.indexOf(q) < 0) return;
      groups[staffBucket(row.name)].push({ row: row, i: i });
    });
    var html = "";
    Object.keys(groups).forEach(function (title) {
      var items = groups[title];
      if (!items.length) return;
      html += '<div class="ar-staff-group"><h4>' + esc(title) + " · " + items.length + "</h4>";
      html += '<div class="ar-staff-hd"><span>姓名</span><span>别名</span><span></span></div>';
      html += items.map(function (it) {
        var aliases = (it.row.aliases || []).join("，");
        return '<div class="ar-staff-row" data-staff-i="' + it.i + '">' +
          '<input data-k="name" value="' + esc(it.row.name || "") + '" spellcheck="false">' +
          '<input data-k="aliases" value="' + esc(aliases) + '" placeholder="别名，逗号分隔" spellcheck="false">' +
          '<button type="button" class="mini" data-ar-del-staff="' + it.i + '">删除</button></div>';
      }).join("");
      html += "</div>";
    });
    box.innerHTML = html || '<div class="ar-staff-empty">' + (q ? "没有匹配的人员" : "还没有乙方名单，请在上方添加。") + "</div>";
  }
  function renderParty() {
    var party = state.party || {};
    var staffN = (party.staff || []).length;
    var reachN = (party.reachingAliases || []).length;
    if ($("arPartyStats")) {
      $("arPartyStats").innerHTML =
        '<span class="ar-chip yi">乙方 ' + staffN + " 人</span>" +
        '<span class="ar-chip jia">甲方别名 ' + reachN + "</span>" +
        '<span class="ar-chip">企业微信未命中 → 甲方</span>';
    }
    if ($("arTitlePat") && document.activeElement !== $("arTitlePat")) {
      $("arTitlePat").value = party.titleStaffPattern || "";
    }
    if ($("arPartyJson") && document.activeElement !== $("arPartyJson")) {
      $("arPartyJson").value = JSON.stringify(currentParty(), null, 2);
    }
    renderReach();
    renderStaff();
    if ($("arPartyProbe") && $("arPartyProbe").value) probeParty();
  }
  function loadParty() {
    return api("/api/agent-review/party").then(function (data) {
      state.party = data.data || { staff: [], reachingAliases: ["reaching"], titleStaffPattern: "" };
      if ($("arPartySaveNote")) {
        $("arPartySaveNote").textContent = "本公司人员 " + (data.staffCount || 0) + " 人";
      }
      renderParty();
    }).catch(function (e) {
      if ($("arPartySaveNote")) $("arPartySaveNote").textContent = e.message;
    });
  }
  function saveParty() {
    if (!canReview()) {
      if ($("arPartySaveNote")) $("arPartySaveNote").textContent = "仅组长或管理员可改名单。";
      return Promise.resolve();
    }
    var data = currentParty();
    state.party = data;
    return api("/api/agent-review/party", { method: "PUT", body: JSON.stringify(data) }).then(function (res) {
      state.party = res.data || data;
      if ($("arPartySaveNote")) {
        $("arPartySaveNote").textContent = "名单已保存，乙方 " + (res.staffCount || 0) + " 人。";
      }
      renderParty();
    }).catch(function (e) {
      if ($("arPartySaveNote")) $("arPartySaveNote").textContent = e.message;
    });
  }
  function applyPartyJson() {
    if (!canReview()) {
      if ($("arPartySaveNote")) $("arPartySaveNote").textContent = "仅组长或管理员可改名单。";
      return;
    }
    try {
      var data = JSON.parse($("arPartyJson").value);
      if (!data || typeof data !== "object") throw new Error("不是对象");
      state.party = {
        staff: data.staff || [],
        reachingAliases: data.reachingAliases || ["reaching"],
        titleStaffPattern: data.titleStaffPattern || ""
      };
      if ($("arTitlePat")) $("arTitlePat").value = state.party.titleStaffPattern || "";
      renderParty();
      if ($("arPartySaveNote")) $("arPartySaveNote").textContent = "已用 JSON 覆盖，点保存后生效。";
    } catch (e) {
      if ($("arPartySaveNote")) $("arPartySaveNote").textContent = "JSON 格式不对：" + e.message;
    }
  }
  function addReach() {
    if (!canReview()) return;
    var name = ($("arReachIn").value || "").trim();
    if (!name) return;
    state.party.reachingAliases = state.party.reachingAliases || [];
    if (state.party.reachingAliases.some(function (x) { return String(x).toLowerCase() === name.toLowerCase(); })) {
      $("arPartySaveNote").textContent = "这个甲方别名已在名单里。";
      return;
    }
    state.party.reachingAliases.push(name);
    $("arReachIn").value = "";
    renderParty();
  }
  function addStaff() {
    if (!canReview()) return;
    var name = ($("arPartyName").value || "").trim();
    var aliases = splitAliases($("arPartyAliases").value);
    if (!name) {
      $("arPartySaveNote").textContent = "请填写姓名。";
      return;
    }
    state.party.staff = state.party.staff || [];
    var hit = state.party.staff.filter(function (row) { return String(row.name || "").toLowerCase() === name.toLowerCase(); })[0];
    if (hit) {
      var seen = {};
      (hit.aliases || []).concat(aliases).forEach(function (a) {
        if (a && a !== hit.name) seen[a] = true;
      });
      hit.aliases = Object.keys(seen);
      $("arPartySaveNote").textContent = "已合并到现有人员。";
    } else {
      state.party.staff.push({ name: name, aliases: aliases.filter(function (a) { return a !== name; }) });
      $("arPartySaveNote").textContent = "已加入名单，点保存后生效。";
    }
    $("arPartyName").value = "";
    $("arPartyAliases").value = "";
    renderParty();
  }
  function probeParty() {
    var sender = ($("arPartyProbe") && $("arPartyProbe").value || "").trim();
    var out = $("arPartyProbeOut");
    if (!out) return;
    if (!sender) {
      out.textContent = "输入名字后即时显示甲 / 乙方";
      out.className = "ar-party-probe-out";
      return;
    }
    api("/api/agent-review/party/preview", {
      method: "POST",
      body: JSON.stringify({
        sender: sender,
        platform: ($("arPartyPlat") && $("arPartyPlat").value) || "wecom",
        party: currentParty()
      })
    }).then(function (res) {
      var row = res.data || {};
      var cls = row.party === "甲方" ? "jia" : "yi";
      out.className = "ar-party-probe-out";
      out.innerHTML = '<span class="ar-chip ' + cls + '">' + esc(row.party || "") + "</span> " +
        esc(row.reason || "") +
        (row.staffName ? " · " + esc(row.staffName) : "");
    }).catch(function (e) {
      out.textContent = e.message;
    });
  }
  function loadBase() {
    if (!$("agentReviewHome")) return;
    applyRoleGate();
    api("/api/auth/me").then(function (j) {
      var u = j && j.user;
      state.reviewer = (u && (u.realName || u.username)) || "";
      state.role = (u && u.role) || "";
      applyRoleGate();
    }).catch(function () {});
    api("/api/agent-review/intents").then(function (data) {
      state.intents = data.data || [];
      state.fields = data.fields || [];
      return loadSessions();
    }).then(function () {
      renderBar();
      return loadParty();
    }).catch(function (e) {
      if ($("arSessions")) $("arSessions").innerHTML = '<div class="wecom-empty">' + esc(e.message) + "</div>";
    });
  }
  function bind() {
    if (!$("agentReviewHome")) return;
    document.querySelectorAll("#arTabs .ar-tab").forEach(function (btn) {
      btn.addEventListener("click", function () { showPage(btn.getAttribute("data-ar-page")); });
    });
    $("arAnalyzeBtn").addEventListener("click", function () { analyze(false); });
    if ($("arResumeBtn")) $("arResumeBtn").addEventListener("click", function () { analyze(false); });
    if ($("arPauseBtn")) $("arPauseBtn").addEventListener("click", pauseAnalyze);
    $("arIncBtn").addEventListener("click", function () { analyze(false); });
    $("arAnalyzeForceBtn").addEventListener("click", function () { analyze(true); });
    if ($("arOnlyJia")) $("arOnlyJia").addEventListener("change", function () {
      loadHits();
    });
    $("arOnlyGroup").addEventListener("change", loadSessions);
    $("arOnlyWatch").addEventListener("change", loadSessions);
    $("arSessionQ").addEventListener("input", function () {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(loadSessions, 250);
    });
    $("arSessions").addEventListener("click", function (ev) {
      var star = ev.target.closest(".wecom-star");
      if (star) {
        ev.preventDefault();
        ev.stopPropagation();
        toggleWatch(star);
        return;
      }
      var wrap = ev.target.closest(".wecom-item-wrap");
      if (wrap) selectSession(wrap.getAttribute("data-id") || wrap.getAttribute("data-sid"));
    });
    $("arDropView").addEventListener("change", function () {
      loadDrops();
    });
    function onOpen(ev) {
      var btn = ev.target.closest("[data-ar-open]");
      if (!btn) return;
      var hit = findHit(btn.getAttribute("data-ar-open"));
      if (hit) fillReview(hit, btn);
    }
    $("arHitList").addEventListener("click", onOpen);
    $("arDropList").addEventListener("click", onOpen);
    function onIntentNav(ev) {
      var btn = ev.target.closest("[data-ar-intent]");
      if (!btn) return;
      applyIntentFilter(btn.getAttribute("data-ar-intent") || "");
    }
    if ($("arIntentNav")) $("arIntentNav").addEventListener("click", onIntentNav);
    if ($("arIntentNavDrop")) $("arIntentNavDrop").addEventListener("click", onIntentNav);
    if ($("arCandidateBox")) {
      $("arCandidateBox").addEventListener("click", function (ev) {
        var act = ev.target.getAttribute("data-ar-cand-act");
        if (!act) return;
        ev.preventDefault();
        document.querySelectorAll('input[name="ar-cand"]').forEach(function (el) {
          el.checked = act === "all";
        });
        var otherEl = document.querySelector('#arCandidateBox input[name="ar-cand"][value="other"]');
        syncOtherNote(!!(otherEl && otherEl.checked));
      });
      $("arCandidateBox").addEventListener("change", function (ev) {
        var el = ev.target;
        if (!el) return;
        if (el.id === "arShouldFilter") {
          if (el.checked) {
            document.querySelectorAll('input[name="ar-cand"]').forEach(function (x) { x.checked = false; });
            syncOtherNote(false);
            syncCustomIntent(false);
          }
          return;
        }
        if (el.id === "arCustomIntentOn") {
          syncCustomIntent(!!el.checked);
          if (el.checked && $("arShouldFilter")) $("arShouldFilter").checked = false;
          return;
        }
        if (el.name === "ar-cand") {
          if (el.checked && $("arShouldFilter")) $("arShouldFilter").checked = false;
          if (el.value === "other") syncOtherNote(!!el.checked);
        }
      });
    }
    $("arSaveReviewBtn").addEventListener("click", saveReview);
    $("arReviewClose").addEventListener("click", closeReview);
    $("arReviewMask").addEventListener("click", function (ev) {
      if (ev.target === $("arReviewMask")) closeReview();
    });
    $("arReviewPop").addEventListener("click", function (ev) { ev.stopPropagation(); });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && $("arReviewMask") && !$("arReviewMask").hidden) closeReview();
    });
    $("arLoadExamplesBtn").addEventListener("click", loadExamples);
    var exBox = $("arExampleBox");
    if (exBox) {
      exBox.addEventListener("click", function (ev) {
        var hd = ev.target.closest(".ar-fold-hd");
        if (hd) {
          var fold = hd.closest(".ar-fold");
          if (!fold) return;
          var open = fold.classList.contains("bd-collapsed");
          fold.classList.toggle("bd-collapsed", !open);
          hd.setAttribute("aria-expanded", open ? "true" : "false");
          return;
        }
        var pg = ev.target.closest("[data-ar-pg]");
        if (!pg || pg.disabled) return;
        var fold = pg.closest(".ar-fold");
        var key = fold && fold.getAttribute("data-ar-ex");
        if (!key || !state.ex[key]) return;
        state.ex[key].page += Number(pg.getAttribute("data-ar-pg") || 0);
        paintExamplePanel(key);
      });
      [
        ["arExIntentQ", "intent"],
        ["arExFilterQ", "filter"],
        ["arExReviewQ", "review"]
      ].forEach(function (pair) {
        var inp = $(pair[0]);
        if (!inp) return;
        inp.addEventListener("input", function () {
          state.ex[pair[1]].q = inp.value || "";
          state.ex[pair[1]].page = 1;
          paintExamplePanel(pair[1]);
        });
      });
    }
    $("arLoadPartyBtn").addEventListener("click", loadParty);
    $("arSavePartyBtn").addEventListener("click", saveParty);
    if ($("arApplyPartyJsonBtn")) $("arApplyPartyJsonBtn").addEventListener("click", applyPartyJson);
    if ($("arReachAddBtn")) $("arReachAddBtn").addEventListener("click", addReach);
    if ($("arStaffAddBtn")) $("arStaffAddBtn").addEventListener("click", addStaff);
    if ($("arReachIn")) $("arReachIn").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); addReach(); }
    });
    if ($("arPartyName")) $("arPartyName").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); addStaff(); }
    });
    if ($("arPartyAliases")) $("arPartyAliases").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); addStaff(); }
    });
    if ($("arPartyQ")) $("arPartyQ").addEventListener("input", function () {
      renderStaff();
    });
    if ($("arTitlePat")) $("arTitlePat").addEventListener("input", function () {
      state.party.titleStaffPattern = $("arTitlePat").value;
    });
    if ($("arPartyProbe")) $("arPartyProbe").addEventListener("input", function () {
      clearTimeout(state.partyTimer);
      state.partyTimer = setTimeout(probeParty, 250);
    });
    if ($("arPartyPlat")) $("arPartyPlat").addEventListener("change", probeParty);
    if ($("arReachBox")) $("arReachBox").addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-ar-del-reach]");
      if (!btn) return;
      var i = Number(btn.getAttribute("data-ar-del-reach"));
      if (!isFinite(i)) return;
      state.party.reachingAliases.splice(i, 1);
      renderParty();
    });
    if ($("arStaffBox")) {
      $("arStaffBox").addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-ar-del-staff]");
        if (!btn) return;
        var i = Number(btn.getAttribute("data-ar-del-staff"));
        if (!isFinite(i)) return;
        state.party.staff.splice(i, 1);
        renderParty();
      });
      $("arStaffBox").addEventListener("input", function (ev) {
        var row = ev.target.closest("[data-staff-i]");
        if (!row) return;
        var i = Number(row.getAttribute("data-staff-i"));
        var item = state.party.staff[i];
        if (!item) return;
        if (ev.target.getAttribute("data-k") === "name") item.name = ev.target.value;
        if (ev.target.getAttribute("data-k") === "aliases") item.aliases = splitAliases(ev.target.value);
      });
    }
  }
  bind();
  loadBase();
  window.arReloadSessions = loadSessions;
})();
