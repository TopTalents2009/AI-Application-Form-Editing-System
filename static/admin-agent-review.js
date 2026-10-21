/* 管理后台：AI 审核样本，用于优化下一次 Jev 分析 */
(function () {
  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function sayAr(ok, t) {
    var el = document.getElementById("arAdminMsg");
    if (!el) return;
    el.className = "msg " + (ok ? "ok" : "bad");
    el.textContent = t;
    setTimeout(function () { if (el.textContent === t) el.textContent = ""; }, 4000);
  }
  function api(path, opts) {
    return fetch(path, Object.assign({ cache: "no-store" }, opts || {})).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || "请求失败");
        return j;
      });
    });
  }
  var exById = {};
  var intentOpts = [];
  var PAGE = 10;
  var panels = {
    intent: { rows: [], page: 1, q: "", cat: "", empty: "暂无意图样本", body: "arAdminIntentBody", pager: "arIntentPager", info: "arIntentPageInfo", count: "arIntentCount", search: "arIntentQ", cats: "arIntentCats" },
    filter: { rows: [], page: 1, q: "", cat: "", empty: "暂无过滤样本", body: "arAdminFilterBody", pager: "arFilterPager", info: "arFilterPageInfo", count: "arFilterCount", search: "arFilterQ", cats: "arFilterCats" },
    review: { rows: [], page: 1, q: "", cat: "", empty: "还没有审核记录。请在主界面 AI 审核里点「审核本条」。", body: "arAdminReviewBody", pager: "arReviewPager", info: "arReviewPageInfo", count: "arReviewCount", search: "arReviewQ", cats: "arReviewCats" }
  };
  function blobOf(r) {
    return ((r.filename ? "【" + r.filename + "】 " : "") + (r.text || "")).trim();
  }
  function kindTitle(r) {
    return (r && r.kind === "filter") ? "过滤样本" : "意图样本";
  }
  function labelText(r) {
    if (!r) return "";
    if (r.kind === "filter") return r.label === "drop" ? "应过滤" : (r.label === "keep" ? "应保留" : (r.label || ""));
    return r.intentLabel || r.label || "";
  }
  function fillStats(st) {
    st = st || {};
    var map = {
      arStReviews: st.reviews,
      arStIntent: st.intentOn,
      arStFilter: st.filterOn,
      arStPinned: st.pinned
    };
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = map[id] == null ? "—" : String(map[id]);
    });
    var hint = document.getElementById("arAdminHint");
    if (hint) {
      hint.textContent = "下次分析会把启用中的意图样本（最多 " + (st.jevLimit || 16) +
        " 条，置顶优先并按意图交叉取样）写入 Jev 题目。";
    }
  }
  function rowHtml(r) {
    var on = r.enabled !== false;
    var pin = !!r.pinned;
    var lab = labelText(r);
    return "<tr data-id=\"" + esc(r.id) + "\">" +
      "<td>" + (pin ? "<span class=\"ar-admin-pin\">置顶</span> " : "") +
        "<span class=\"ar-chip " + (r.kind === "intent" ? "jia" : (r.label === "drop" ? "drop" : "")) + "\">" + esc(lab) + "</span>" +
        (r.score != null && r.score !== "" ? " <span class=\"ar-chip\">" + esc(r.score) + "</span>" : "") +
      "</td>" +
      "<td class=\"ar-admin-txt\" title=\"" + esc(blobOf(r)) + "\">" + esc(blobOf(r).slice(0, 160) || "（无正文）") + "</td>" +
      "<td>" + esc(r.reviewer || "") + "</td>" +
      "<td>" + esc((r.created_at || "").slice(0, 16)) + "</td>" +
      "<td>" + (on ? "<span class=\"badge st-done\">启用</span>" : "<span class=\"badge st-pending\">停用</span>") + "</td>" +
      "<td>" +
        "<button type=\"button\" class=\"mini\" data-ar-view=\"1\">预览</button> " +
        "<button type=\"button\" class=\"mini\" data-ar-edit=\"1\">编辑</button> " +
        "<button type=\"button\" class=\"mini\" data-ar-pin=\"" + (pin ? "0" : "1") + "\">" + (pin ? "取消置顶" : "置顶") + "</button> " +
        "<button type=\"button\" class=\"mini\" data-ar-on=\"" + (on ? "0" : "1") + "\">" + (on ? "停用" : "启用") + "</button> " +
        "<button type=\"button\" class=\"mini" + (pin ? "" : " bad") + "\" data-ar-del=\"1\">删除</button>" +
      "</td></tr>";
  }
  function reviewLabs(r) {
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
  function reviewRowHtml(r) {
    var labs = reviewLabs(r);
    var labHtml = labs.map(function (lab) {
      return "<span class=\"ar-chip jia\">" + esc(lab) + "</span>";
    }).join(" ") || "—";
    return "<tr>" +
      "<td>" + esc((r.updated_at || "").slice(0, 16)) + "</td>" +
      "<td title=\"" + esc(r.session_id || "") + "\">" + esc((r.session_id || "").slice(0, 22)) + "</td>" +
      "<td>" + esc(r.sender || "") + "</td>" +
      "<td>" + labHtml +
        (r.selected_score != null ? " <span class=\"ar-chip\">" + esc(r.selected_score) + "</span>" : "") + "</td>" +
      "<td>" + esc(r.reviewer || "") + "</td>" +
      "<td><span class=\"ar-chip ok\">已审核</span> " +
        (r.should_filter ? "应过滤" : (r.filter_ok === false ? "过滤已纠正" : "已采信")) + "</td></tr>";
  }
  function searchBlob(key, r) {
    if (key === "review") {
      return [
        r.session_id, r.sender, r.reviewer, r.updated_at, r.text, r.filename,
        reviewLabs(r).join(" "),
        r.should_filter ? "应过滤" : "",
        r.filter_ok === false ? "过滤已纠正" : "已采信"
      ].join(" ").toLowerCase();
    }
    return [
      labelText(r), blobOf(r), r.reviewer, r.session_id, r.message_id, r.filename, r.score,
      r.enabled === false ? "停用" : "启用",
      r.pinned ? "置顶" : ""
    ].join(" ").toLowerCase();
  }
  function rowCats(key, r) {
    if (key === "filter") return [r.label === "keep" ? "keep" : "drop"];
    if (key === "review") {
      if (r.should_filter) return ["__filter__"];
      var codes = [];
      if (Array.isArray(r.selected_intents)) {
        r.selected_intents.forEach(function (x) {
          var c = (typeof x === "string") ? x : (x && x.intent) || "";
          if (c && codes.indexOf(c) < 0) codes.push(c);
        });
      }
      if (!codes.length && r.selected_intent) {
        String(r.selected_intent).split(/[，,]/).forEach(function (s) {
          var c = s.trim();
          if (c && codes.indexOf(c) < 0) codes.push(c);
        });
      }
      return codes.length ? codes : ["other"];
    }
    return [r.label || "other"];
  }
  function catName(key, id) {
    if (!id) return "全部";
    if (id === "__filter__" || (key === "filter" && id === "drop")) return "应过滤";
    if (key === "filter" && id === "keep") return "应保留";
    var found = "";
    (intentOpts || []).forEach(function (it) {
      if (it && it.id === id) found = it.name || id;
    });
    if (found) return found;
    if (id === "other") return "其他";
    return id;
  }
  function catOrder(key) {
    if (key === "filter") return ["drop", "keep"];
    var ids = (intentOpts || []).map(function (it) { return it && it.id; }).filter(Boolean);
    if (key === "review") ids = ["__filter__"].concat(ids);
    return ids;
  }
  function filteredRows(key) {
    var p = panels[key];
    var q = (p.q || "").trim().toLowerCase();
    var cat = p.cat || "";
    return p.rows.filter(function (r) {
      if (cat && rowCats(key, r).indexOf(cat) < 0) return false;
      if (q && searchBlob(key, r).indexOf(q) < 0) return false;
      return true;
    });
  }
  function paintCats(key) {
    var p = panels[key];
    var box = document.getElementById(p.cats);
    if (!box) return;
    var counts = { "": p.rows.length };
    p.rows.forEach(function (r) {
      rowCats(key, r).forEach(function (c) {
        counts[c] = (counts[c] || 0) + 1;
      });
    });
    if (p.cat && !counts[p.cat]) p.cat = "";
    var chips = [{ id: "", name: "全部" }];
    var seen = { "": true };
    catOrder(key).forEach(function (id) {
      if (!id || seen[id] || !counts[id]) return;
      seen[id] = true;
      chips.push({ id: id, name: catName(key, id) });
    });
    Object.keys(counts).forEach(function (id) {
      if (!id || seen[id] || !counts[id]) return;
      seen[id] = true;
      chips.push({ id: id, name: catName(key, id) });
    });
    var cur = p.cat || "";
    box.innerHTML = chips.map(function (it) {
      var on = cur === it.id ? " on" : "";
      var n = it.id ? (counts[it.id] || 0) : counts[""];
      var cls = "ar-intent-chip" + on;
      if (it.id === "__filter__" || it.id === "drop") cls += " queue";
      return '<button type="button" class="' + cls + '" data-ar-cat="' + esc(it.id) + '">' +
        esc(it.name) + " <b>" + n + "</b></button>";
    }).join("");
  }
  function paintPager(p, shown, pages) {
    var pager = document.getElementById(p.pager);
    var info = document.getElementById(p.info);
    var count = document.getElementById(p.count);
    if (count) {
      count.textContent = (p.q || p.cat)
        ? ("筛选 " + shown + " / " + p.rows.length)
        : String(p.rows.length);
    }
    if (info) info.textContent = "第 " + p.page + " / " + pages + " 页 · 共 " + shown + " 条";
    if (!pager) return;
    pager.hidden = shown <= PAGE;
    var prev = pager.querySelector('[data-ar-pg="-1"]');
    var next = pager.querySelector('[data-ar-pg="1"]');
    if (prev) prev.disabled = p.page <= 1;
    if (next) next.disabled = p.page >= pages;
  }
  function paintPanel(key) {
    var p = panels[key];
    var tb = document.getElementById(p.body);
    if (!tb) return;
    paintCats(key);
    var rows = filteredRows(key);
    var pages = Math.max(1, Math.ceil(rows.length / PAGE) || 1);
    if (p.page > pages) p.page = pages;
    if (p.page < 1) p.page = 1;
    paintPager(p, rows.length, pages);
    if (!rows.length) {
      tb.innerHTML = "<tr><td colspan=\"6\">" + esc((p.q || p.cat) ? "没有匹配的记录" : p.empty) + "</td></tr>";
      return;
    }
    var slice = rows.slice((p.page - 1) * PAGE, p.page * PAGE);
    tb.innerHTML = slice.map(key === "review" ? reviewRowHtml : rowHtml).join("");
  }
  function loadAgentReviewAdmin() {
    if (!document.getElementById("sec-aireview")) return Promise.resolve();
    return Promise.all([
      api("/api/admin/agent-review/examples"),
      api("/api/admin/agent-review/reviews"),
      intentOpts.length ? Promise.resolve({ data: intentOpts }) : api("/api/agent-review/intents")
    ]).then(function (got) {
      var ex = got[0] || {};
      var rv = got[1] || {};
      if (got[2] && got[2].data && got[2].data.length && !intentOpts.length) intentOpts = got[2].data;
      fillStats(ex);
      var rows = ex.data || [];
      exById = {};
      rows.forEach(function (r) { if (r && r.id != null) exById[String(r.id)] = r; });
      panels.intent.rows = rows.filter(function (r) { return r.kind === "intent"; });
      panels.filter.rows = rows.filter(function (r) { return r.kind === "filter"; });
      panels.review.rows = rv.data || [];
      paintPanel("intent");
      paintPanel("filter");
      paintPanel("review");
    }).catch(function (e) {
      sayAr(false, e.message || "加载失败");
    });
  }
  function closeExPop() {
    var mask = document.getElementById("arExMask");
    if (mask) mask.hidden = true;
  }
  function openExPop(row, edit) {
    var mask = document.getElementById("arExMask");
    var body = document.getElementById("arExBody");
    var title = document.getElementById("arExTitle");
    var sub = document.getElementById("arExSub");
    var editBtn = document.getElementById("arExEditBtn");
    var saveBtn = document.getElementById("arExSaveBtn");
    var note = document.getElementById("arExNote");
    if (!mask || !body || !row) return;
    title.textContent = kindTitle(row);
    sub.textContent = (row.reviewer ? ("审核人：" + row.reviewer) : "") +
      (row.created_at ? ((row.reviewer ? " · " : "") + String(row.created_at).slice(0, 16)) : "");
    note.textContent = "";
    mask.dataset.id = String(row.id);
    mask.dataset.mode = edit ? "edit" : "view";
    if (editBtn) editBtn.hidden = !!edit;
    if (saveBtn) saveBtn.hidden = !edit;
    if (edit) {
      var labelField = "";
      if (row.kind === "filter") {
        labelField = "<select id=\"arExLabel\">" +
          '<option value="keep"' + (row.label === "keep" ? " selected" : "") + ">应保留</option>" +
          '<option value="drop"' + (row.label === "drop" ? " selected" : "") + ">应过滤</option></select>";
      } else {
        var opts = "";
        var seen = {};
        (intentOpts || []).forEach(function (it) {
          if (!it || !it.id) return;
          seen[it.id] = true;
          opts += '<option value="' + esc(it.id) + '">' + esc(it.name || it.id) + "</option>";
        });
        if (row.label && !seen[row.label]) {
          opts = '<option value="' + esc(row.label) + '">' + esc(labelText(row)) + "</option>" + opts;
        }
        labelField = '<input id="arExLabel" list="arExIntentList" maxlength="40" autocomplete="off" value="' +
          esc(row.label || "") + '" placeholder="可填写新意图">' +
          '<datalist id="arExIntentList">' + opts + "</datalist>";
      }
      body.innerHTML = '<form class="ar-ex-form" id="arExForm">' +
        '<div class="ar-ex-row2">' +
          '<div class="f-row"><label>' + (row.kind === "filter" ? "标签" : "意图") + "</label>" +
            labelField + "</div>" +
          '<div class="f-row"><label>评分</label>' +
            '<input id="arExScore" type="number" min="0" max="100" placeholder="可空" value="' +
            esc(row.score != null && row.score !== "" ? String(row.score) : "") + '"></div>' +
        "</div>" +
        '<div class="f-row"><label>文件名</label>' +
          '<input id="arExFile" maxlength="240" value="' + esc(row.filename || "") + '" placeholder="可空"></div>' +
        '<div class="f-row"><label>原文</label>' +
          '<textarea id="arExText" maxlength="500">' + esc(row.text || "") + "</textarea></div>" +
        '<p class="sec-desc" style="margin:0">保存后会置顶，避免按审核记录重建时被覆盖。最多 500 字。</p>' +
        "</form>";
    } else {
      body.innerHTML = '<div class="ar-ex-meta">' +
        '<span class="ar-chip ' + (row.kind === "intent" ? "jia" : (row.label === "drop" ? "drop" : "")) + '">' + esc(labelText(row)) + "</span>" +
        (row.score != null && row.score !== "" ? '<span class="ar-chip">评分 ' + esc(row.score) + "</span>" : "") +
        (row.pinned ? '<span class="ar-admin-pin">置顶</span>' : "") +
        (row.enabled === false ? '<span class="badge st-pending">停用</span>' : '<span class="badge st-done">启用</span>') +
        "</div>" +
        (row.filename ? "<p class=\"sec-desc\" style=\"margin:0 0 8px\">文件：" + esc(row.filename) + "</p>" : "") +
        '<pre class="ar-ex-pre">' + esc(row.text || "（无正文）") + "</pre>" +
        (row.session_id ? "<p class=\"sec-desc\" style=\"margin:10px 0 0\">会话 " + esc(row.session_id) +
          (row.message_id ? " · 消息 " + esc(row.message_id) : "") + "</p>" : "");
    }
    mask.hidden = false;
  }
  function saveExPop() {
    var mask = document.getElementById("arExMask");
    var id = mask && mask.dataset.id;
    var note = document.getElementById("arExNote");
    if (!id) return;
    var labelEl = document.getElementById("arExLabel");
    var scoreEl = document.getElementById("arExScore");
    var fileEl = document.getElementById("arExFile");
    var textEl = document.getElementById("arExText");
    var scoreRaw = scoreEl ? String(scoreEl.value || "").trim() : "";
    var body = {
      label: labelEl ? labelEl.value : "",
      filename: fileEl ? fileEl.value : "",
      text: textEl ? textEl.value : "",
      score: scoreRaw === "" ? null : Number(scoreRaw)
    };
    if (note) note.textContent = "保存中…";
    patchExample(id, body).then(function () {
      sayAr(true, "已保存并置顶");
      var row = exById[String(id)];
      if (row) openExPop(row, false);
      else closeExPop();
    }).catch(function (e) {
      if (note) note.textContent = e.message || "保存失败";
    });
  }
  function patchExample(id, body) {
    return api("/api/admin/agent-review/examples/" + encodeURIComponent(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () { return loadAgentReviewAdmin(); });
  }
  function bindTable(id) {
    var tb = document.getElementById(id);
    if (!tb) return;
    tb.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button");
      if (!btn) return;
      var tr = btn.closest("tr");
      var eid = tr && tr.getAttribute("data-id");
      if (!eid) return;
      if (btn.getAttribute("data-ar-view") || btn.getAttribute("data-ar-edit")) {
        var row = exById[String(eid)];
        if (!row) { sayAr(false, "样本已刷新，请重试"); return; }
        openExPop(row, !!btn.getAttribute("data-ar-edit"));
        return;
      }
      if (btn.getAttribute("data-ar-del")) {
        if (!confirm("删除后，下次分析不再使用这条样本。确定删除？")) return;
        api("/api/admin/agent-review/examples/" + encodeURIComponent(eid), { method: "DELETE" })
          .then(function () { sayAr(true, "已删除"); return loadAgentReviewAdmin(); })
          .catch(function (e) { sayAr(false, e.message); });
        return;
      }
      if (btn.hasAttribute("data-ar-pin")) {
        patchExample(eid, { pinned: btn.getAttribute("data-ar-pin") === "1" })
          .then(function () { sayAr(true, "已更新置顶"); })
          .catch(function (e) { sayAr(false, e.message); });
        return;
      }
      if (btn.hasAttribute("data-ar-on")) {
        patchExample(eid, { enabled: btn.getAttribute("data-ar-on") === "1" })
          .then(function () { sayAr(true, "已更新启用状态"); })
          .catch(function (e) { sayAr(false, e.message); });
      }
    });
  }
  function bind() {
    if (!document.getElementById("sec-aireview")) return;
    bindTable("arAdminIntentBody");
    bindTable("arAdminFilterBody");
    var sec = document.getElementById("sec-aireview");
    sec.addEventListener("click", function (ev) {
      var hd = ev.target.closest(".ar-fold-hd");
      if (hd) {
        var fold = hd.closest(".ar-fold");
        if (!fold) return;
        var open = fold.classList.contains("bd-collapsed");
        fold.classList.toggle("bd-collapsed", !open);
        hd.setAttribute("aria-expanded", open ? "true" : "false");
        return;
      }
      var catBtn = ev.target.closest("[data-ar-cat]");
      if (catBtn) {
        var fold = catBtn.closest(".ar-fold");
        var ckey = fold && fold.getAttribute("data-ar-fold");
        if (!ckey || !panels[ckey]) return;
        var cid = catBtn.getAttribute("data-ar-cat") || "";
        panels[ckey].cat = (panels[ckey].cat === cid) ? "" : cid;
        panels[ckey].page = 1;
        paintPanel(ckey);
        return;
      }
      var pg = ev.target.closest("[data-ar-pg]");
      if (!pg || pg.disabled) return;
      var box = pg.closest(".ar-fold");
      var key = box && box.getAttribute("data-ar-fold");
      if (!key || !panels[key]) return;
      panels[key].page += Number(pg.getAttribute("data-ar-pg") || 0);
      paintPanel(key);
    });
    Object.keys(panels).forEach(function (key) {
      var inp = document.getElementById(panels[key].search);
      if (!inp) return;
      inp.addEventListener("input", function () {
        panels[key].q = inp.value || "";
        panels[key].page = 1;
        paintPanel(key);
      });
    });
    var mask = document.getElementById("arExMask");
    var pop = document.getElementById("arExPop");
    var closeBtn = document.getElementById("arExClose");
    var editBtn = document.getElementById("arExEditBtn");
    var saveBtn = document.getElementById("arExSaveBtn");
    if (closeBtn) closeBtn.addEventListener("click", closeExPop);
    if (mask) mask.addEventListener("click", function (ev) {
      if (ev.target === mask) closeExPop();
    });
    if (pop) pop.addEventListener("click", function (ev) { ev.stopPropagation(); });
    if (editBtn) editBtn.addEventListener("click", function () {
      var id = mask && mask.dataset.id;
      var row = id && exById[String(id)];
      if (row) openExPop(row, true);
    });
    if (saveBtn) saveBtn.addEventListener("click", saveExPop);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && mask && !mask.hidden) closeExPop();
    });
    var reload = document.getElementById("arAdminReload");
    if (reload) reload.addEventListener("click", function () {
      loadAgentReviewAdmin().then(function () { sayAr(true, "已刷新"); });
    });
    var rebuild = document.getElementById("arAdminRebuild");
    if (rebuild) rebuild.addEventListener("click", function () {
      if (!confirm("用全部审核记录重建样本？已置顶/停用的状态会保留。")) return;
      rebuild.disabled = true;
      api("/api/admin/agent-review/examples/rebuild", { method: "POST" })
        .then(function (j) {
          sayAr(true, "已重建：意图 " + (j.intentOn || 0) + " · 过滤 " + (j.filterOn || 0) + " · 审核 " + (j.reviewsUsed || 0));
          return loadAgentReviewAdmin();
        })
        .catch(function (e) { sayAr(false, e.message); })
        .finally(function () { rebuild.disabled = false; });
    });
  }
  bind();
  window.loadAgentReviewAdmin = loadAgentReviewAdmin;
})();
