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
})(window);
