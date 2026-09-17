/** 会话令牌：iframe 内第三方 Cookie 会被浏览器丢掉，用 sessionStorage + Bearer 兜底。 */
(function (w) {
  var KEY = "sb_session";
  function get() {
    try { return sessionStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  }
  function save(t) {
    if (!t) return;
    try { sessionStorage.setItem(KEY, t); } catch (e) {}
  }
  function clear() {
    try { sessionStorage.removeItem(KEY); } catch (e) {}
  }
  var _fetch = w.fetch;
  w.fetch = function (input, init) {
    init = init ? Object.assign({}, init) : {};
    var headers = new Headers(init.headers || {});
    var tok = get();
    if (tok && !headers.has("Authorization")) {
      headers.set("Authorization", "Bearer " + tok);
    }
    init.headers = headers;
    if (init.credentials == null) init.credentials = "same-origin";
    return _fetch.call(this, input, init);
  };
  w.sbSaveSession = save;
  w.sbClearSession = clear;
  w.sbSessionToken = get;
  /** <img> 不会带 Authorization；iframe 里 Cookie 又被拦。用带鉴权的 fetch 换成 blob URL。 */
  w.sbAuthBlob = function (url) {
    return w.fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    }).then(function (blob) {
      return URL.createObjectURL(blob);
    });
  };
  w.sbBindAuthImages = function (root, bag) {
    bag = bag || [];
    if (!root || !root.querySelectorAll) return bag;
    Array.prototype.forEach.call(root.querySelectorAll("img[data-auth-src]"), function (img) {
      var src = img.getAttribute("data-auth-src");
      if (!src || img.getAttribute("data-auth-bound") === src) return;
      img.setAttribute("data-auth-bound", src);
      w.sbAuthBlob(src).then(function (u) {
        bag.push(u);
        img.src = u;
        var a = img.closest ? img.closest("a[href]") : null;
        if (a) {
          var href = a.getAttribute("href") || "";
          var asrc = a.getAttribute("data-auth-src") || "";
          if (href === src || asrc === src || href.indexOf("/api/feedback/") === 0) a.href = u;
        }
      }).catch(function () {
        img.alt = "图片无法加载";
        img.removeAttribute("src");
        img.classList.add("auth-img-fail");
      });
    });
    return bag;
  };
})(window);
