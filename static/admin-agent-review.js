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
  function blobOf(r) {
    return ((r.filename ? "【" + r.filename + "】 " : "") + (r.text || "")).trim();
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
    var lab = r.intentLabel || r.label || "";
    return "<tr data-id=\"" + esc(r.id) + "\">" +
      "<td>" + (pin ? "<span class=\"ar-admin-pin\">置顶</span> " : "") +
        "<span class=\"ar-chip " + (r.kind === "intent" ? "jia" : "") + "\">" + esc(lab) + "</span>" +
        (r.score != null && r.score !== "" ? " <span class=\"ar-chip\">" + esc(r.score) + "</span>" : "") +
      "</td>" +
      "<td class=\"ar-admin-txt\" title=\"" + esc(blobOf(r)) + "\">" + esc(blobOf(r).slice(0, 160) || "（无正文）") + "</td>" +
      "<td>" + esc(r.reviewer || "") + "</td>" +
      "<td>" + esc((r.created_at || "").slice(0, 16)) + "</td>" +
      "<td>" + (on ? "<span class=\"badge st-done\">启用</span>" : "<span class=\"badge st-pending\">停用</span>") + "</td>" +
      "<td>" +
        "<button type=\"button\" class=\"mini\" data-ar-pin=\"" + (pin ? "0" : "1") + "\">" + (pin ? "取消置顶" : "置顶") + "</button> " +
        "<button type=\"button\" class=\"mini\" data-ar-on=\"" + (on ? "0" : "1") + "\">" + (on ? "停用" : "启用") + "</button> " +
        "<button type=\"button\" class=\"mini" + (pin ? "" : " bad") + "\" data-ar-del=\"1\">删除</button>" +
      "</td></tr>";
  }
  function paintTable(id, rows, empty) {
    var tb = document.getElementById(id);
    if (!tb) return;
    if (!(rows || []).length) {
      tb.innerHTML = "<tr><td colspan=\"6\">" + esc(empty) + "</td></tr>";
      return;
    }
    tb.innerHTML = rows.map(rowHtml).join("");
  }
  function paintReviews(rows) {
    var tb = document.getElementById("arAdminReviewBody");
    if (!tb) return;
    if (!(rows || []).length) {
      tb.innerHTML = "<tr><td colspan=\"6\">还没有审核记录。请在主界面 AI 审核里点「审核本条」。</td></tr>";
      return;
    }
    tb.innerHTML = rows.slice(0, 200).map(function (r) {
      var labs = [];
      if (Array.isArray(r.selected_intents) && r.selected_intents.length) {
        labs = r.selected_intents.map(function (x) {
          return (typeof x === "string") ? x : (x.label || x.intent || "");
        }).filter(Boolean);
      } else if (r.selected_label) {
        labs = String(r.selected_label).split(/[、,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
      } else if (r.selected_intent || r.correct_intent) {
        labs = [r.selected_label || r.selected_intent || r.correct_intent];
      }
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
        "<td>" + (r.filter_ok === false ? "过滤已纠正" : "已采信") + "</td></tr>";
    }).join("");
  }
  function loadAgentReviewAdmin() {
    if (!document.getElementById("sec-aireview")) return Promise.resolve();
    return Promise.all([
      api("/api/admin/agent-review/examples"),
      api("/api/admin/agent-review/reviews")
    ]).then(function (got) {
      var ex = got[0] || {};
      var rv = got[1] || {};
      fillStats(ex);
      var rows = ex.data || [];
      paintTable("arAdminIntentBody", rows.filter(function (r) { return r.kind === "intent"; }), "暂无意图样本");
      paintTable("arAdminFilterBody", rows.filter(function (r) { return r.kind === "filter"; }), "暂无过滤样本");
      paintReviews(rv.data || []);
    }).catch(function (e) {
      sayAr(false, e.message || "加载失败");
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
