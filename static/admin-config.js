/* 管理后台：模型配置 */
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
  var cfgData = null;
  var cfgMsg = document.getElementById('cfgMsg');
  function sayCfg(ok, t) { cfgMsg.className = 'msg ' + (ok ? 'ok' : 'bad'); cfgMsg.textContent = t; setTimeout(function () { if (cfgMsg.textContent === t) cfgMsg.textContent = ''; }, 5000); }

  function loadConfig() {
    fetch('/api/config').then(function (r) { return r.json(); }).then(function (cfg) {
      cfgData = cfg;
      renderConfig(cfg);
    }).catch(function (e) { sayCfg(false, '配置加载失败：' + (e.message || '')); });
  }
  var cfgJump = document.getElementById('cfgJump');
  if (cfgJump) cfgJump.addEventListener('click', function (e) {
    var a = e.target.closest('a');
    if (!a) return;
    var id = (a.getAttribute('href') || '').replace('#', '');
    var el = document.getElementById(id);
    if (!el) return;
    e.preventDefault();
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  function renderConfig(cfg) {
    cfgData = cfg;
    var ed = cfg.edit || {};
    var fams = [
      { key: 'grok', name: 'Grok' },
      { key: 'gemini', name: 'Gemini' },
      { key: 'doubao', name: '火山' }
    ];
    var wrap = document.getElementById('cfgCards');
    wrap.innerHTML = '';
    fams.forEach(function (f) {
      wrap.appendChild(buildModelCard(f, ed));
    });
    bindModelIdChange(fams, ed);
    renderClassifySelect(fams, ed);
    renderPoolCard(ed);
    renderPapersCard(ed);
    renderPsCard(ed);
    renderProjectProofCard(ed);
    renderWecomCard(ed);
    renderAlertCfgCard(ed);
    renderScanPdfCard(ed);
    renderPortalCard(ed);
    renderMysqlCard(ed);
    renderInboxCard(ed);
  }

  function collectFields(key, root) {
    var p = {};
    var ints = {
      timeoutSec: 1, intervalSec: 1, windowHours: 1, opinionWaitMin: 1, talentUploadMax: 1,
      port: 1, pageMinChars: 1, geminiMaxPages: 1, maxTurns: 1, dedupeMinutes: 1,
      slaWorkdays: 1, recentDays: 1, maxLines: 1
    };
    var floats = { temperature: 1, qaMinScore: 1 };
    var secrets = { apiKey: 1, password: 1, webhook: 1, fileUploadKey: 1 };
    (root || document).querySelectorAll('[data-f="' + key + '"]').forEach(function (el) {
      var k = el.getAttribute('data-k');
      if (!k) return;
      if (secrets[k]) { if (el.value.trim()) p[k] = el.value.trim(); return; }
      if (el.type === 'checkbox') { p[k] = !!el.checked; return; }
      if (ints[k]) {
        var n = parseInt(el.value, 10);
        if (!isNaN(n)) p[k] = n;
        return;
      }
      if (floats[k]) {
        var t = parseFloat(el.value);
        if (!isNaN(t)) p[k] = t;
        return;
      }
      if (k === 'id') { if (el.value.trim()) p.id = el.value.trim(); return; }
      p[k] = (el.value == null) ? '' : el.value;
    });
    return p;
  }

  function postConfig(payload) {
    return fetch('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); });
  }

  function cardMsg(el, ok, t) {
    if (!el) return;
    el.className = 'cfg-save-msg ' + (ok ? 'ok' : 'bad');
    el.textContent = t;
    if (ok) setTimeout(function () { if (el.textContent === t) el.textContent = ''; }, 4000);
  }

  function buildModelCard(f, ed) {
    var m = ed[f.key] || {};
    var card = document.createElement('div');
    card.className = 'acard';
    card.innerHTML =
      '<h3>' + esc(m.label || f.name) +
        '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (m.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (m.hasKey ? '密钥已配置' : '未配置密钥') + '</span></h3>' +
      '<div class="cfg-grid">' +
        '<div class="f-row span2"><label>模型 ID</label><div class="inp"><input data-f="' + f.key + '" data-k="id" data-orig="' + esc(m.id || '') + '" value="' + esc(m.id || '') + '" placeholder="模型标识，修改后默认采用下拉自动跟随"></div></div>' +
        '<div class="f-row span2"><label>Base URL</label><div class="inp"><input data-f="' + f.key + '" data-k="baseUrl" value="' + esc(m.baseUrl || '') + '" placeholder="OpenAI 兼容接口地址"></div></div>' +
        '<div class="f-row span2"><label>API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="' + f.key + '" data-k="apiKey" value="" placeholder="' + (m.hasKey ? '••••••••（已配置）' : '填入密钥') + '"></div></div>' +
        '<div class="f-row"><label>超时（秒）</label><div class="inp"><input type="number" min="30" max="900" data-f="' + f.key + '" data-k="timeoutSec" value="' + esc(m.timeoutSec || '') + '"></div></div>' +
        ((f.key === 'grok' || f.key === 'gemini')
          ? '<div class="f-row"><label>' + (f.key === 'gemini' ? '思考强度' : '推理强度') + '</label><div class="inp"><select data-f="' + f.key + '" data-k="reasoningEffort" class="role-sel" style="width:100%;height:46px;padding:0 10px">' +
              [['low','短（low）'],['medium','中（medium）'],['high','长（high）']].map(function (v) { return '<option value="' + v[0] + '"' + ((m.reasoningEffort || 'medium') === v[0] ? ' selected' : '') + '>' + v[1] + '</option>'; }).join('') +
            '</select></div></div>'
          : '') +
        (f.key === 'gemini'
          ? '<div class="f-row"><label>温度</label><div class="inp"><input type="number" step="0.1" min="0" max="2" data-f="gemini" data-k="temperature" value="' + esc(m.temperature == null ? 0.1 : m.temperature) + '"></div></div>' +
            '<div class="f-row"><label>API 请求格式</label><div class="inp"><select data-f="gemini" data-k="apiFormat" class="role-sel" style="width:100%;height:46px;padding:0 10px">' +
              [['openai','OpenAI 兼容（/chat/completions）'],['genai','Google GenAI（generateContent）']].map(function (v) { return '<option value="' + v[0] + '"' + ((m.apiFormat || 'openai') === v[0] ? ' selected' : '') + '>' + v[1] + '</option>'; }).join('') +
            '</select></div></div>' +
            '<div class="f-row"><label>检索</label><div class="chk"><input type="checkbox" data-f="gemini" data-k="enableSearch"' + (m.enableSearch ? ' checked' : '') + '> 打开检索（Google Search）</div></div>'
          : '') +
        (f.key !== 'grok'
          ? '<div class="f-row"><label>流式</label><div class="chk"><input type="checkbox" data-f="' + f.key + '" data-k="stream"' + (m.stream ? ' checked' : '') + '> 使用流式输出</div></div>'
          : '') +
      '</div>' +
      '<div class="cfg-save-row">' +
        '<button type="button" class="mini cfg-save-one">保存本模型</button>' +
        '<span class="cfg-save-msg"></span>' +
      '</div>';
    var btn = card.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () { saveOneModel(f.key, card); });
    return card;
  }

  function saveOneModel(famKey, card) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    var ed = cfgData.edit || {};
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    var payload = {};
    payload[famKey] = collectFields(famKey, card);
    var idInp = card.querySelector('[data-k="id"]');
    var curId = idInp ? idInp.value.trim() : (ed[famKey] || {}).id || '';
    if (curId && ed.classifyModel === curId) payload.classifyModel = curId;
    postConfig(payload).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var got = res.data.edit || {};
        for (var k in got) ed[k] = got[k];
        var hk = !!(ed[famKey] || {}).hasKey;
        var dot = card.querySelector('.keydot');
        if (dot) { dot.style.color = hk ? 'var(--ok)' : 'var(--bad)'; dot.textContent = '● ' + (hk ? '密钥已配置' : '未配置密钥'); }
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function bindModelIdChange(fams, ed) {
    document.querySelectorAll('#cfgCards input[data-k="id"]').forEach(function (inp) {
      inp.addEventListener('change', function () {
        var v = inp.value.trim();
        if (!v) return;
        if (ed.classifyModel === inp.getAttribute('data-orig')) ed.classifyModel = v;
        renderClassifySelect(fams, ed);
      });
    });
  }

  function renderClassifySelect(fams, ed) {
    var sel = document.getElementById('classifyModel');
    var opts = [];
    fams.forEach(function (f) {
      var m = ed[f.key] || {};
      var id = m.id || '';
      var inp = document.querySelector('#cfgCards input[data-f="' + f.key + '"][data-k="id"]');
      if (inp && inp.value.trim()) id = inp.value.trim();
      if (id) opts.push([id, (m.label || f.key) + ' · ' + id]);
    });
    sel.innerHTML = opts.map(function (o) {
      return '<option value="' + esc(o[0]) + '"' + (ed.classifyModel === o[0] ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('');
  }

  function renderPoolCard(ed) {
    var box = document.getElementById('poolCfgCard');
    if (!box) return;
    var p = ed.pool || {};
    var ready = !!p.configured;
    box.innerHTML =
      '<div class="acard">' +
        '<h3>人才库 / 企业库 API ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (p.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (p.hasKey ? '密钥已配置' : '未配置密钥') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">生成计划时按申报书编号 / 姓名 / 企业统一社会信用代码检索外部只读人才库与企业库补齐空缺字段；密钥仅存本机 config.json，界面不回显。Base URL / 检索模式留空 = 不修改。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>Base URL</label><div class="inp"><input data-f="pool" data-k="baseUrl" value="' + esc(p.baseUrl || '') + '" placeholder="http://192.168.2.23:8078"></div></div>' +
          '<div class="f-row span2"><label>API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="pool" data-k="apiKey" value="" placeholder="' + (p.hasKey ? '••••••••（已配置）' : '填入 X-API-KEY') + '"></div></div>' +
          '<div class="f-row"><label>检索模式</label><div class="inp"><input data-f="pool" data-k="mode" value="' + esc(p.mode || 'all') + '" placeholder="all"></div></div>' +
          '<div class="f-row span2"><label>人才附件上传接口</label><div class="inp"><input data-f="pool" data-k="fileUploadUrl" value="' + esc(p.fileUploadUrl || '') + '" placeholder="可空，默认走 Base URL /api/external-read/v1/talent-files"><div style="font-size:12px;color:#8a93a6;margin-top:6px">值班扫描到新材料时按此接口入库。multipart 字段 attach_id、kind、file。</div></div></div>' +
          '<div class="f-row span2"><label>人才附件上传 Key</label><div class="inp"><input type="password" data-f="pool" data-k="fileUploadKey" value="" placeholder="' + (p.fileUploadHasKey ? '••••••••（已配置，留空不修改）' : '可空，默认用人才库 API Key') + '"></div></div>' +
          '<div class="f-row"><label>就绪状态</label><div style="padding-top:9px;font-size:13px;font-weight:700;color:' + (ready ? 'var(--ok)' : 'var(--bad)') + '">' + (ready ? '已就绪 · 检索时参与' : '未配置 · 跳过检索，缺数据写遗留') + '</div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存人才库配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () { savePoolConfig(box); });
  }

  function renderPapersCard(ed) {
    var box = document.getElementById('papersCfgCard');
    if (!box) return;
    var p = ed.papers || {};
    var ready = !!p.configured;
    var authOk = !!(p.hasKey || p.hasPassword);
    box.innerHTML =
      '<div class="acard">' +
        '<h3>论文导出 API ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (authOk ? 'var(--ok)' : 'var(--bad)') + '">● ' + (p.authMode === 'apiKey' ? 'API Key' : (p.authMode === 'session' ? '网页账号' : '未配置鉴权')) + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">意见点名缺论文全文时，人才库未命中后按人才编号拉取科研成果附件。优先使用 <code>API Key</code>（见 <code>论文api文档.md</code>）；未配密钥时可填论文系统管理员账号密码（网页登录）。Base URL / 账号留空 = 不修改。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>Base URL</label><div class="inp"><input data-f="papers" data-k="baseUrl" value="' + esc(p.baseUrl || '') + '" placeholder="http://192.168.2.62:8000"></div></div>' +
          '<div class="f-row span2"><label>API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="papers" data-k="apiKey" value="" placeholder="' + (p.hasKey ? '••••••••（已配置）' : '填入 X-Api-Key') + '"></div></div>' +
          '<div class="f-row"><label>管理员账号</label><div class="inp"><input data-f="papers" data-k="username" value="' + esc(p.username || '') + '" placeholder="admin"></div></div>' +
          '<div class="f-row"><label>管理员密码（留空 = 不修改）</label><div class="inp"><input type="password" data-f="papers" data-k="password" value="" placeholder="' + (p.hasPassword ? '••••••••（已配置）' : '填入密码') + '"></div></div>' +
          '<div class="f-row"><label>就绪状态</label><div style="padding-top:9px;font-size:13px;font-weight:700;color:' + (ready ? 'var(--ok)' : 'var(--bad)') + '">' + (ready ? '已就绪 · 缺论文时可拉取' : '未配置 · 跳过论文 API') + '</div></div>' +
          '<div class="f-row"><label>连通性</label><div style="padding-top:9px"><button type="button" class="mini" id="papersHealthBtn">检测论文 API</button><span id="papersHealthMsg" style="margin-left:8px;font-size:12px;color:#7c88a1"></span></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存论文 API 配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () { savePapersConfig(box); });
    var hb = box.querySelector('#papersHealthBtn');
    if (hb) hb.addEventListener('click', function () {
      var msg = box.querySelector('#papersHealthMsg');
      if (msg) msg.textContent = '检测中…';
      fetch('/api/papers/health').then(function (r) { return r.json(); }).then(function (j) {
        if (!msg) return;
        if (j.ok) msg.textContent = '✓ 服务与鉴权正常';
        else if (j.service_ok) msg.textContent = '服务可达；' + (j.error || '鉴权未通过');
        else msg.textContent = '✗ ' + (j.error || '不可达');
        msg.style.color = j.ok ? 'var(--ok)' : 'var(--bad)';
      }).catch(function (e) { if (msg) msg.textContent = '检测失败：' + (e.message || e); });
    });
  }

  function renderPsCard(ed) {
    var box = document.getElementById('psCfgCard');
    if (!box) return;
    var p = ed.ps || {};
    var ready = !!p.configured;
    box.innerHTML =
      '<div class="acard">' +
        '<h3>PS 修图工单 API ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (p.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (p.hasKey ? '密钥已配置' : '未配置密钥') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">人工确认页点「上传」并填写备注、任务提交人姓名后才会传到 p 图工单；未确认不上传。密钥仅存本机 config.json。Base URL 留空 = 不修改。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>Base URL</label><div class="inp"><input data-f="ps" data-k="baseUrl" value="' + esc(p.baseUrl || '') + '" placeholder="http://192.168.2.50:18427/api/external/v1"></div></div>' +
          '<div class="f-row span2"><label>API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="ps" data-k="apiKey" value="" placeholder="' + (p.hasKey ? '••••••••（已配置）' : '填入 ptk_live_…') + '"></div></div>' +
          '<div class="f-row span2"><label>工单网页地址（可空）</label><div class="inp"><input data-f="ps" data-k="webUrl" value="' + esc(p.webUrl || '') + '" placeholder="可空，默认用 Base URL 主机 /tickets/{id}"></div></div>' +
          '<div class="f-row"><label>就绪状态</label><div style="padding-top:9px;font-size:13px;font-weight:700;color:' + (ready ? 'var(--ok)' : 'var(--bad)') + '">' + (ready ? '已就绪 · 确认后可上传工单' : '未配置 · 跳过工单上传') + '</div></div>' +
          '<div class="f-row"><label>连通性</label><div style="padding-top:9px"><button type="button" class="mini" id="psHealthBtn">检测 PS API</button><span id="psHealthMsg" style="margin-left:8px;font-size:12px;color:#7c88a1"></span></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存 PS 工单配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () { savePsConfig(box); });
    var hb = box.querySelector('#psHealthBtn');
    if (hb) hb.addEventListener('click', function () {
      var msg = box.querySelector('#psHealthMsg');
      if (msg) msg.textContent = '检测中…';
      fetch('/api/ps/health').then(function (r) { return r.json(); }).then(function (j) {
        if (!msg) return;
        if (j.ok) msg.textContent = '✓ ' + (j.detail || '服务与鉴权正常');
        else if (j.service_ok) msg.textContent = '服务可达；' + (j.error || '鉴权未通过');
        else msg.textContent = '✗ ' + (j.error || '不可达');
        msg.style.color = j.ok ? 'var(--ok)' : 'var(--bad)';
      }).catch(function (e) { if (msg) msg.textContent = '检测失败：' + (e.message || e); });
    });
  }

  function savePapersConfig(card) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    var ed = cfgData.edit || {};
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    postConfig({ papers: collectFields('papers', card) }).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var got = res.data.edit || {};
        for (var k in got) ed[k] = got[k];
        var p = ed.papers || {};
        var dot = card.querySelector('.keydot');
        if (dot) { dot.style.color = p.hasKey ? 'var(--ok)' : 'var(--bad)'; dot.textContent = '● ' + (p.hasKey ? '密钥已配置' : '未配置密钥'); }
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function savePsConfig(card) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    var ed = cfgData.edit || {};
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    postConfig({ ps: collectFields('ps', card) }).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var got = res.data.edit || {};
        for (var k in got) ed[k] = got[k];
        var p = ed.ps || {};
        var dot = card.querySelector('.keydot');
        if (dot) { dot.style.color = p.hasKey ? 'var(--ok)' : 'var(--bad)'; dot.textContent = '● ' + (p.hasKey ? '密钥已配置' : '未配置密钥'); }
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function savePoolConfig(card) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    var ed = cfgData.edit || {};
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    postConfig({ pool: collectFields('pool', card) }).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var got = res.data.edit || {};
        for (var k in got) ed[k] = got[k];
        var p = ed.pool || {};
        var dot = card.querySelector('.keydot');
        if (dot) { dot.style.color = p.hasKey ? 'var(--ok)' : 'var(--bad)'; dot.textContent = '● ' + (p.hasKey ? '密钥已配置' : '未配置密钥'); }
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function collectWecom(card) {
    var p = collectFields('wecomChat', card);
    p.watch = collectFields('wecomWatch', card);
    p.intent = collectFields('wecomIntent', card);
    return p;
  }

  function renderProjectProofCard(ed) {
    var box = document.getElementById('projectProofCfgCard');
    if (!box) return;
    var pp = ed.projectProof || {};
    var cb = pp.codebuddy || {};
    var g = pp.generate || {};
    var ready = !!g.configured;
    box.innerHTML =
      '<div class="acard">' +
        '<h3>项目证明 API ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (g.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (g.hasKey ? '密钥已配置' : '未配置密钥') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">调用 <code>/api/external/documents</code>，只传已确定的经历名单（letters / projects），不读简历、不读会话。人才库命中来华前单位及主持/参与项目后才会生成。密钥留空表示不修改。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>生成接口 Base URL</label><div class="inp"><input data-f="ppGen" data-k="baseUrl" value="' + esc(g.baseUrl || '') + '" placeholder="http://192.168.2.62:3602"></div></div>' +
          '<div class="f-row span2"><label>生成接口 API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="ppGen" data-k="apiKey" value="" placeholder="' + (g.hasKey ? '••••••••（已配置）' : '填入密钥') + '"></div></div>' +
          '<div class="f-row"><label>documents 路径</label><div class="inp"><input data-f="ppGen" data-k="path" value="' + esc(g.path || '') + '" placeholder="/api/external/documents"></div></div>' +
          '<div class="f-row"><label>generate 路径</label><div class="inp"><input data-f="ppGen" data-k="generatePath" value="' + esc(g.generatePath || '') + '" placeholder="/api/external/generate"></div></div>' +
          '<div class="f-row"><label>projects 路径</label><div class="inp"><input data-f="ppGen" data-k="projectsPath" value="' + esc(g.projectsPath || '') + '" placeholder="/api/external/projects"></div></div>' +
          '<div class="f-row"><label>生成超时（秒）</label><div class="inp"><input data-f="ppGen" data-k="timeoutSec" value="' + esc(g.timeoutSec || 300) + '" placeholder="300"></div></div>' +
          '<div class="f-row span2"><label>CodeBuddy 命令</label><div class="inp"><input data-f="ppCb" data-k="cmd" value="' + esc(cb.cmd || '') + '" placeholder="codebuddy.cmd"></div></div>' +
          '<div class="f-row"><label>检索模型</label><div class="inp"><input data-f="ppCb" data-k="model" value="' + esc(cb.model || '') + '" placeholder="可空，用 CodeBuddy 默认"></div></div>' +
          '<div class="f-row"><label>权限模式</label><div class="inp"><input data-f="ppCb" data-k="permissionMode" value="' + esc(cb.permissionMode || 'bypassPermissions') + '" placeholder="bypassPermissions"></div></div>' +
          '<div class="f-row"><label>检索超时（秒）</label><div class="inp"><input data-f="ppCb" data-k="timeoutSec" value="' + esc(cb.timeoutSec || 180) + '" placeholder="180"></div></div>' +
          '<div class="f-row"><label>检索轮数</label><div class="inp"><input data-f="ppCb" data-k="maxTurns" value="' + esc(cb.maxTurns || 10) + '" placeholder="10"></div></div>' +
          '<div class="f-row"><label>就绪状态</label><div style="padding-top:9px;font-size:13px;font-weight:700;color:' + (ready ? 'var(--ok)' : 'var(--bad)') + '">' + (ready ? '已配置 · 缺项目证明时可生成' : '未配置 · 跳过生成接口') + '</div></div>' +
          '<div class="f-row"><label>连通性</label><div style="padding-top:9px"><button type="button" class="mini" id="ppProbeBtn">检测生成接口</button><span id="ppProbeMsg" style="margin-left:8px;font-size:12px;color:#7c88a1"></span></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存项目证明配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () { saveProjectProofConfig(box); });
    var hb = box.querySelector('#ppProbeBtn');
    if (hb) hb.addEventListener('click', function () {
      var msg = box.querySelector('#ppProbeMsg');
      if (msg) msg.textContent = '检测中…';
      fetch('/api/project-proof/probe', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (j) {
        if (!msg) return;
        if (j.ok) msg.textContent = '✓ ' + (j.note || '服务可达，密钥已被接受');
        else msg.textContent = '✗ ' + (j.error || '检测失败');
        msg.style.color = j.ok ? 'var(--ok)' : 'var(--bad)';
      }).catch(function (e) { if (msg) msg.textContent = '检测失败：' + (e.message || e); });
    });
  }

  function collectProjectProof(root) {
    var card = root || document.getElementById('projectProofCfgCard');
    if (!card) return null;
    return {
      codebuddy: collectFields('ppCb', card),
      generate: collectFields('ppGen', card)
    };
  }

  function saveProjectProofConfig(card) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    var ed = cfgData.edit || {};
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    postConfig({ projectProof: collectProjectProof(card) }).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var got = res.data.edit || {};
        for (var k in got) ed[k] = got[k];
        renderProjectProofCard(ed);
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function renderWecomCard(ed) {
    var box = document.getElementById('wecomCfgCard');
    if (!box) return;
    var p = ed.wecomChat || {};
    var w = p.watch || {};
    var intent = p.intent || {};
    var ready = !!p.configured;
    var groups = (p.groups || []).join('\n');
    var mode = w.mode || 'auto';
    var readMode = p.readMode || 'auto';
    box.innerHTML =
      '<div class="acard">' +
        '<h3>企业微信聊天记录 ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (p.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (p.hasKey ? '密钥已配置' : '未配置密钥') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">管理后台「会话记录」只读调用本仓库 <code>parser/</code> 解析服务（默认 <code>http://127.0.0.1:8767</code>，由 start.cmd 一并拉起）。关注会话一行一个，群聊和单聊都可以；值班扫描必须填写关注会话，留空只浏览不自动提交。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>Base URL</label><div class="inp"><input data-f="wecomChat" data-k="baseUrl" value="' + esc(p.baseUrl || '') + '" placeholder="http://127.0.0.1:8767"></div></div>' +
          '<div class="f-row span2"><label>API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="wecomChat" data-k="apiKey" value="" placeholder="' + (p.hasKey ? '••••••••（已配置）' : '填入 X-API-Key') + '"></div></div>' +
          '<div class="f-row"><label>读取方式</label><div class="inp"><select data-f="wecomChat" data-k="readMode">' +
            '<option value="auto"' + (readMode === 'auto' ? ' selected' : '') + '>自动（本机优先）</option>' +
            '<option value="local"' + (readMode === 'local' ? ' selected' : '') + '>只读本机目录</option>' +
            '<option value="http"' + (readMode === 'http' ? ' selected' : '') + '>只走 HTTP 解析服务</option>' +
          '</select></div></div>' +
          '<div class="f-row"><label>本机数据目录</label><div class="inp"><input data-f="wecomChat" data-k="dataRoot" value="' + esc(p.dataRoot || '') + '" placeholder="可空，默认解析服务 data 目录"></div></div>' +
          '<div class="f-row span2"><label>关注会话（值班必填）</label><div class="inp"><textarea data-f="wecomChat" data-k="groups" rows="3" placeholder="会话名称或编号">' + esc(groups) + '</textarea><div style="font-size:12px;color:#8a93a6;margin-top:6px">也可在会话记录左侧点 ★ 直接关注；此处与看板同步。群聊、单聊都会扫描。</div></div></div>' +
          '<div class="f-row"><label>就绪状态</label><div style="padding-top:9px;font-size:13px;font-weight:700;color:' + (ready ? 'var(--ok)' : 'var(--bad)') + '">' + (ready ? '已配置密钥 · 可打开会话记录' : '未配置 · 看板无法列出消息') + '</div></div>' +
          '<div class="f-row"><label>连通性</label><div style="padding-top:9px"><button type="button" class="mini" id="wecomHealthBtn">检测解析服务</button><span id="wecomHealthMsg" style="margin-left:8px;font-size:12px;color:#7c88a1"></span></div></div>' +
        '</div>' +
        '<h3 style="margin:22px 0 10px;font-size:14px">聊天值班（Gemini）' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (w.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (w.hasKey ? 'Gemini 已配置' : '未配置 Gemini') + '</span></h3>' +
        '<p style="margin:-4px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">判断使用与出计划相同的 Gemini，按固定题目只认会话里的修改意见，再匹配申报书并提交到「待确认」。不会自动写入正文。模型：' + esc(w.model || '未配置') + '。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row"><label>开启值班</label><div style="padding-top:10px"><label style="display:flex;align-items:center;gap:8px;font-size:13px"><input type="checkbox" data-f="wecomWatch" data-k="enabled"' + (w.enabled ? ' checked' : '') + '> 聊天同步后增量扫描</label></div></div>' +
          '<div class="f-row"><label>识别到意见后</label><div class="inp"><select data-f="wecomWatch" data-k="mode">' +
            '<option value="auto"' + (mode === 'auto' ? ' selected' : '') + '>自动提交待确认任务</option>' +
            '<option value="review"' + (mode === 'review' ? ' selected' : '') + '>只列入待人审</option>' +
            '<option value="off"' + (mode === 'off' ? ' selected' : '') + '>关闭</option>' +
          '</select></div></div>' +
          '<div class="f-row span2"><label>扫描时机</label><div style="padding-top:10px;font-size:13px;color:#5b6577;line-height:1.6">聊天记录同步过来后做一次增量扫描，不再按固定间隔。</div></div>' +
          '<div class="f-row"><label>回看窗口（小时）</label><div class="inp"><input data-f="wecomWatch" data-k="windowHours" value="' + esc(w.windowHours || 48) + '" placeholder="48"></div></div>' +
          '<div class="f-row"><label>值班账号</label><div class="inp"><input data-f="wecomWatch" data-k="owner" value="' + esc(w.owner || '') + '" placeholder="任务归属用户名，可空"></div></div>' +
          '<div class="f-row"><label>判断超时（秒）</label><div class="inp"><input data-f="wecomWatch" data-k="timeoutSec" value="' + esc(w.timeoutSec || 60) + '" placeholder="60"></div></div>' +
          '<div class="f-row"><label>意见等待（分钟）</label><div class="inp"><input data-f="wecomWatch" data-k="opinionWaitMin" value="' + esc(w.opinionWaitMin != null ? w.opinionWaitMin : 60) + '" placeholder="60"></div></div>' +
          '<div class="f-row span2"><label>无独立意见时</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="wecomWatch" data-k="allowNoOpinion"' + (w.allowNoOpinion ? ' checked' : '') + '> 允许仅凭申报书标注栏提交</div></div>' +
          '<div class="f-row span2"><label>已驳回会话</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="wecomWatch" data-k="skipDismissed"' + (w.skipDismissed !== false ? ' checked' : '') + '> 跳过已人工驳回的会话</div></div>' +
          '<div class="f-row"><label>新材料入库</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="wecomWatch" data-k="talentUploadEnabled"' + (w.talentUploadEnabled !== false ? ' checked' : '') + '> 扫描到附件时入库</div></div>' +
          '<div class="f-row"><label>每会话最多入库</label><div class="inp"><input data-f="wecomWatch" data-k="talentUploadMax" value="' + esc(w.talentUploadMax != null ? w.talentUploadMax : 10) + '" placeholder="10"></div></div>' +
          '<div class="f-row"><label>值班连通性</label><div style="padding-top:9px"><button type="button" class="mini" id="wecomWatchProbeBtn">检测 Gemini</button> <button type="button" class="mini" id="wecomWatchRunBtn">立即扫描</button><span id="wecomWatchProbeMsg" style="margin-left:8px;font-size:12px;color:#7c88a1"></span></div></div>' +
        '</div>' +
        '<h3 style="margin:22px 0 10px;font-size:14px">意图分析（TypeSafe）' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (intent.hasKey ? 'var(--ok)' : 'var(--bad)') + '">● ' + (intent.hasKey ? '密钥已配置' : '未配置密钥') + '</span></h3>' +
        '<p style="margin:-4px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">会话拆条 / 意图识别走 TypeSafe System One，与值班判断的 Gemini 分开。密钥留空不改。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>Base URL</label><div class="inp"><input data-f="wecomIntent" data-k="baseUrl" value="' + esc(intent.baseUrl || '') + '" placeholder="https://api.typesafe.ai"></div></div>' +
          '<div class="f-row span2"><label>API Key（留空 = 不修改）</label><div class="inp"><input type="password" data-f="wecomIntent" data-k="apiKey" value="" placeholder="' + (intent.hasKey ? '••••••••（已配置）' : '填入 TypeSafe 密钥') + '"></div></div>' +
          '<div class="f-row"><label>模型</label><div class="inp"><input data-f="wecomIntent" data-k="model" value="' + esc(intent.model || '') + '" placeholder="jev-latest"></div></div>' +
          '<div class="f-row"><label>超时（秒）</label><div class="inp"><input data-f="wecomIntent" data-k="timeoutSec" value="' + esc(intent.timeoutSec || 60) + '" placeholder="60"></div></div>' +
          '<div class="f-row span2"><label>代理（国内访问 TypeSafe 需要）</label><div class="inp"><input data-f="wecomIntent" data-k="proxy" value="' + esc(intent.proxy || '') + '" placeholder="可空；Clash mixed-port 例 http://127.0.0.1:7897"><div style="font-size:11px;color:#8a93a6;margin-top:4px">留空则用系统 HTTPS_PROXY，或把 ALL_PROXY 的 socks5 转成同端口 HTTP。TypeSafe 会拒绝中国大陆直连（HTTP 451）。</div></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存聊天记录配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () { saveWecomConfig(box); });
    var hb = box.querySelector('#wecomHealthBtn');
    if (hb) hb.addEventListener('click', function () {
      var msg = box.querySelector('#wecomHealthMsg');
      if (msg) msg.textContent = '检测中…';
      fetch('/api/wecom/health').then(function (r) { return r.json(); }).then(function (j) {
        if (!msg) return;
        if (j.ok) msg.textContent = '✓ 服务与鉴权正常（' + (j.source_count || 0) + ' 台电脑）';
        else if (j.service_ok) msg.textContent = '服务可达；' + (j.error || '鉴权未通过');
        else msg.textContent = '✗ ' + (j.error || '不可达');
        msg.style.color = j.ok ? 'var(--ok)' : 'var(--bad)';
      }).catch(function (e) { if (msg) msg.textContent = '检测失败：' + (e.message || e); });
    });
    var pb = box.querySelector('#wecomWatchProbeBtn');
    if (pb) pb.addEventListener('click', function () {
      var msg = box.querySelector('#wecomWatchProbeMsg');
      if (msg) msg.textContent = '检测中…';
      fetch('/api/wecom/watch-probe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          var j = res.data || {};
          if (!msg) return;
          if (j.ok) msg.textContent = '✓ ' + (j.detail || '已连通') + ' · ' + (j.model || '') + ' · ' + (j.ms || 0) + 'ms';
          else msg.textContent = '✗ ' + (j.error || j.detail || '失败');
          msg.style.color = j.ok ? 'var(--ok)' : 'var(--bad)';
        }).catch(function (e) { if (msg) msg.textContent = '检测失败：' + (e.message || e); });
    });
    var rb = box.querySelector('#wecomWatchRunBtn');
    if (rb) rb.addEventListener('click', function () {
      var msg = box.querySelector('#wecomWatchProbeMsg');
      if (msg) msg.textContent = '扫描中…';
      fetch('/api/wecom/watch-run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          var j = res.data || {};
          if (!msg) return;
          if (j.ok) msg.textContent = '✓ 本轮完成' + (j.created && j.created.length ? '，提交 ' + j.created.length + ' 条' : '');
          else msg.textContent = '✗ ' + (j.detail || '扫描未执行');
          msg.style.color = j.ok ? 'var(--ok)' : 'var(--bad)';
        }).catch(function (e) { if (msg) msg.textContent = '扫描失败：' + (e.message || e); });
    });
  }

  function saveWecomConfig(card) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    var ed = cfgData.edit || {};
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    postConfig({ wecomChat: collectWecom(card) }).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var got = res.data.edit || {};
        for (var k in got) ed[k] = got[k];
        var p = ed.wecomChat || {};
        var dot = card.querySelector('.keydot');
        if (dot) { dot.style.color = p.hasKey ? 'var(--ok)' : 'var(--bad)'; dot.textContent = '● ' + (p.hasKey ? '密钥已配置' : '未配置密钥'); }
        if (window.loadWecomBoard) window.loadWecomBoard();
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function leadersText(rows) {
    return (rows || []).map(function (r) {
      return [r.department || '*', r.name || '', r.userid || ''].join(' | ');
    }).join('\n');
  }
  function parseLeaders(text) {
    var rows = [];
    String(text || '').split(/\n/).forEach(function (line) {
      var parts = line.split('|').map(function (s) { return s.trim(); });
      if (!parts[0] && parts.length < 2) return;
      var userid = parts.length >= 3 ? parts[2] : (parts[1] || '');
      if (!userid) return;
      rows.push({
        department: parts[0] || '*',
        name: parts.length >= 3 ? parts[1] : '',
        userid: userid
      });
    });
    return rows;
  }
  function collectAlertCfg(card) {
    var p = collectFields('alertCfg', card);
    p.leaders = parseLeaders(((card.querySelector('[data-f="alertLeaders"]') || {}).value) || '');
    p.dueBroadcast = collectFields('dueBcCfg', card);
    p.issueWatch = collectFields('issueCfg', card);
    if (p.issueWatch && p.issueWatch.keywords != null) {
      p.issueWatch.keywords = String(p.issueWatch.keywords || '').split(/\n/).map(function (s) { return s.trim(); }).filter(Boolean);
    }
    return p;
  }
  function collectScanPdf(card) {
    var p = collectFields('scanPdf', card);
    p.hybrid = collectFields('scanHybrid', card);
    return p;
  }
  function collectInbox(card) {
    var p = collectFields('inbox', card);
    return p.path || '';
  }
  function applySavedEdit(res) {
    var ed = cfgData.edit || {};
    var got = (res.data && res.data.edit) || {};
    for (var k in got) ed[k] = got[k];
    cfgData = res.data;
    return ed;
  }
  function saveHubCard(card, payload, rerender) {
    var btn = card.querySelector('.cfg-save-one');
    var msg = card.querySelector('.cfg-save-msg');
    if (btn) btn.disabled = true;
    cardMsg(msg, false, '');
    postConfig(payload).then(function (res) {
      if (res.ok && res.data && res.data.version) {
        cardMsg(msg, true, '✓ 已保存');
        var ed = applySavedEdit(res);
        if (rerender) rerender(ed);
      } else cardMsg(msg, false, (res.data && res.data.detail) || '保存失败');
      if (btn) btn.disabled = false;
    }).catch(function (e) { cardMsg(msg, false, '保存失败：' + (e.message || '')); if (btn) btn.disabled = false; });
  }

  function renderAlertCfgCard(ed) {
    var box = document.getElementById('alertCfgCard');
    if (!box) return;
    var a = ed.alert || {};
    var db = a.dueBroadcast || {};
    var iw = a.issueWatch || {};
    var kws = (iw.keywords || []).join('\n');
    box.innerHTML =
      '<div class="acard">' +
        '<h3>告警通知 ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (a.hasWebhook ? 'var(--ok)' : 'var(--bad)') + '">● ' + (a.hasWebhook ? 'Webhook 已配置' : '未配置 Webhook') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">任务失败、反馈、值班待审、群内催问会推到企业微信告警群。试发与发送日志仍在左侧「告警通知」。Webhook 留空不改。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row"><label>开启推送</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="alertCfg" data-k="enabled"' + (a.enabled ? ' checked' : '') + '> 有重要事件时发到告警群</div></div>' +
          '<div class="f-row"><label>最低级别</label><div class="inp"><select data-f="alertCfg" data-k="minLevel">' +
            [['P0','仅 P0'],['P1','P0 + P1'],['P2','全部']].map(function (v) {
              return '<option value="' + v[0] + '"' + ((a.minLevel || 'P1') === v[0] ? ' selected' : '') + '>' + v[1] + '</option>';
            }).join('') +
          '</select></div></div>' +
          '<div class="f-row span2"><label>Webhook（留空 = 不修改）</label><div class="inp"><input type="password" data-f="alertCfg" data-k="webhook" value="" placeholder="' + (a.hasWebhook ? '••••••••（已配置）' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…') + '"></div></div>' +
          '<div class="f-row"><label>去重（分钟）</label><div class="inp"><input data-f="alertCfg" data-k="dedupeMinutes" value="' + esc(a.dedupeMinutes != null ? a.dedupeMinutes : 15) + '"></div></div>' +
          '<div class="f-row"><label>公开访问前缀</label><div class="inp"><input data-f="alertCfg" data-k="publicBaseUrl" value="' + esc(a.publicBaseUrl || '') + '" placeholder="可空"></div></div>' +
          '<div class="f-row span2"><label>@ 组长（部门 | 姓名 | userid，一行一条）</label><div class="inp"><textarea data-f="alertLeaders" rows="3" placeholder="* | 默认 | ZhangSan">' + esc(leadersText(a.leaders)) + '</textarea></div></div>' +
          '<div class="f-row"><label>交付播报</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="dueBcCfg" data-k="enabled"' + (db.enabled ? ' checked' : '') + '> 每天按时刻自动播报</div></div>' +
          '<div class="f-row"><label>播报时刻</label><div class="inp"><input data-f="dueBcCfg" data-k="times" value="' + esc((db.times || []).join(',')) + '" placeholder="09:30,18:00"></div></div>' +
          '<div class="f-row"><label>SLA（工作日）</label><div class="inp"><input data-f="dueBcCfg" data-k="slaWorkdays" value="' + esc(db.slaWorkdays != null ? db.slaWorkdays : 1) + '"></div></div>' +
          '<div class="f-row"><label>只看最近（天）</label><div class="inp"><input data-f="dueBcCfg" data-k="recentDays" value="' + esc(db.recentDays != null ? db.recentDays : 14) + '"></div></div>' +
          '<div class="f-row"><label>列表上限</label><div class="inp"><input data-f="dueBcCfg" data-k="maxLines" value="' + esc(db.maxLines != null ? db.maxLines : 20) + '"></div></div>' +
          '<div class="f-row"><label>周末</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="dueBcCfg" data-k="skipWeekend"' + (db.skipWeekend !== false ? ' checked' : '') + '> 周六日不播报</div></div>' +
          '<div class="f-row"><label>范围</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="dueBcCfg" data-k="wecomOnly"' + (db.wecomOnly ? ' checked' : '') + '> 只播企业微信建的任务</div></div>' +
          '<div class="f-row"><label>空列表</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="dueBcCfg" data-k="sendIfEmpty"' + (db.sendIfEmpty ? ' checked' : '') + '> 没有待交付也发一条</div></div>' +
          '<div class="f-row"><label>重要问题</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="issueCfg" data-k="enabled"' + (iw.enabled !== false ? ' checked' : '') + '> 群内催问 / 质疑时通知组长</div></div>' +
          '<div class="f-row"><label>问题级别</label><div class="inp"><select data-f="issueCfg" data-k="level">' +
            [['P0','P0'],['P1','P1'],['P2','P2']].map(function (v) {
              return '<option value="' + v[0] + '"' + ((iw.level || 'P0') === v[0] ? ' selected' : '') + '>' + v[1] + '</option>';
            }).join('') +
          '</select></div></div>' +
          '<div class="f-row span2"><label>关键词（一行一条）</label><div class="inp"><textarea data-f="issueCfg" data-k="keywords" rows="5">' + esc(kws) + '</textarea></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存告警配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () {
      saveHubCard(box, { alert: collectAlertCfg(box) }, renderAlertCfgCard);
    });
  }

  function renderScanPdfCard(ed) {
    var box = document.getElementById('scanPdfCfgCard');
    if (!box) return;
    var s = ed.scanPdf || {};
    var kws = (s.criticalKeywords || []).join('\n');
    box.innerHTML =
      '<div class="acard">' +
        '<h3>扫描 PDF / OCR</h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">扫描件申报书走 docreconstruct + Gemini 混合识别。改完立即写入 config.json，下一份扫描件生效。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row"><label>转换器</label><div class="inp"><select data-f="scanPdf" data-k="converter">' +
            [['hybrid','混合（推荐）'],['docreconstruct','仅文档重建'],['gemini','仅 Gemini']].map(function (v) {
              return '<option value="' + v[0] + '"' + ((s.converter || 'hybrid') === v[0] ? ' selected' : '') + '>' + v[1] + '</option>';
            }).join('') +
          '</select></div></div>' +
          '<div class="f-row"><label>输出模式</label><div class="inp"><select data-f="scanPdf" data-k="outputMode">' +
            [['template','套模板'],['convert','仅转换']].map(function (v) {
              return '<option value="' + v[0] + '"' + ((s.outputMode || 'template') === v[0] ? ' selected' : '') + '>' + v[1] + '</option>';
            }).join('') +
          '</select></div></div>' +
          '<div class="f-row"><label>页最少字数</label><div class="inp"><input data-f="scanHybrid" data-k="pageMinChars" value="' + esc(s.pageMinChars != null ? s.pageMinChars : 80) + '"></div></div>' +
          '<div class="f-row"><label>质检分</label><div class="inp"><input data-f="scanHybrid" data-k="qaMinScore" value="' + esc(s.qaMinScore != null ? s.qaMinScore : 0.15) + '"></div></div>' +
          '<div class="f-row"><label>Gemini 最多页</label><div class="inp"><input data-f="scanHybrid" data-k="geminiMaxPages" value="' + esc(s.geminiMaxPages != null ? s.geminiMaxPages : 10) + '"></div></div>' +
          '<div class="f-row"><label>超时（秒）</label><div class="inp"><input data-f="scanHybrid" data-k="timeoutSec" value="' + esc(s.timeoutSec != null ? s.timeoutSec : 900) + '"></div></div>' +
          '<div class="f-row"><label>OCR 语言</label><div class="inp"><input data-f="scanHybrid" data-k="languages" value="' + esc(s.languages || 'chi_sim+eng') + '"></div></div>' +
          '<div class="f-row"><label>保留中间文件</label><div class="chk" style="padding-top:10px"><input type="checkbox" data-f="scanHybrid" data-k="keepIntermediates"' + (s.keepIntermediates !== false ? ' checked' : '') + '> 方便排查识别失败</div></div>' +
          '<div class="f-row span2"><label>Tesseract 路径</label><div class="inp"><input data-f="scanHybrid" data-k="tesseractCmd" value="' + esc(s.tesseractCmd || '') + '" placeholder="C:\\Program Files\\Tesseract-OCR\\tesseract.exe"></div></div>' +
          '<div class="f-row span2"><label>tessdata 目录</label><div class="inp"><input data-f="scanHybrid" data-k="tessdataPrefix" value="' + esc(s.tessdataPrefix || '') + '" placeholder="C:\\Program Files\\Tesseract-OCR\\tessdata"></div></div>' +
          '<div class="f-row span2"><label>关键字段关键词（一行一条）</label><div class="inp"><textarea data-f="scanHybrid" data-k="criticalKeywords" rows="4">' + esc(kws) + '</textarea></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存扫描 PDF 配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () {
      saveHubCard(box, { scanPdf: collectScanPdf(box) }, renderScanPdfCard);
    });
  }

  function renderPortalCard(ed) {
    var box = document.getElementById('portalCfgCard');
    if (!box) return;
    var p = ed.portal || {};
    box.innerHTML =
      '<div class="acard">' +
        '<h3>协同门户 ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (p.configured ? 'var(--ok)' : 'var(--bad)') + '">● ' + (p.configured ? '已配置 SDK' : '未配置') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">门户页加载协同中心 SDK，用外部程序 id 完成单点登录。改完刷新 /portal 生效。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>SDK 地址</label><div class="inp"><input data-f="portal" data-k="sdkUrl" value="' + esc(p.sdkUrl || '') + '" placeholder="http://192.168.2.23:8086/api/ext-research/plugin/sdk.js"></div></div>' +
          '<div class="f-row span2"><label>外部程序 ID</label><div class="inp"><input data-f="portal" data-k="portalId" value="' + esc(p.portalId || '') + '" placeholder="协同中心分配的 portalId"></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存门户配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () {
      saveHubCard(box, { portal: collectFields('portal', box) }, renderPortalCard);
    });
  }

  function renderMysqlCard(ed) {
    var box = document.getElementById('mysqlCfgCard');
    if (!box) return;
    var m = ed.mysql || {};
    box.innerHTML =
      '<div class="acard">' +
        '<h3>MySQL 数据库 ' +
          '<span class="keydot" style="margin-left:auto;font-size:12px;font-weight:600;color:' + (m.hasPassword ? 'var(--ok)' : 'var(--bad)') + '">● ' + (m.hasPassword ? '密码已配置' : '未配置密码') + '</span></h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">用户账号与企微入库库。密码留空不改。改主机 / 库名后建议重启服务再连。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row"><label>主机</label><div class="inp"><input data-f="mysql" data-k="host" value="' + esc(m.host || '127.0.0.1') + '"></div></div>' +
          '<div class="f-row"><label>端口</label><div class="inp"><input data-f="mysql" data-k="port" value="' + esc(m.port || 3306) + '"></div></div>' +
          '<div class="f-row"><label>用户</label><div class="inp"><input data-f="mysql" data-k="user" value="' + esc(m.user || 'root') + '"></div></div>' +
          '<div class="f-row"><label>密码（留空 = 不修改）</label><div class="inp"><input type="password" data-f="mysql" data-k="password" value="" placeholder="' + (m.hasPassword ? '••••••••（已配置）' : '填入密码') + '"></div></div>' +
          '<div class="f-row"><label>主库</label><div class="inp"><input data-f="mysql" data-k="database" value="' + esc(m.database || 'shenbaoshu') + '"></div></div>' +
          '<div class="f-row"><label>企微库</label><div class="inp"><input data-f="mysql" data-k="wecomDatabase" value="' + esc(m.wecomDatabase || 'shenbaoshu_wecom') + '"></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存数据库配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () {
      saveHubCard(box, { mysql: collectFields('mysql', box) }, renderMysqlCard);
    });
  }

  function renderInboxCard(ed) {
    var box = document.getElementById('inboxCfgCard');
    if (!box) return;
    var path = ed.clientInbox || '';
    box.innerHTML =
      '<div class="acard">' +
        '<h3>客户端收件箱</h3>' +
        '<p style="margin:-8px 0 14px;font-size:12.5px;color:#7c88a1;line-height:1.7">客户端投放材料的扫描目录。留空使用项目根目录下的 <code>client_inbox</code>。</p>' +
        '<div class="cfg-grid">' +
          '<div class="f-row span2"><label>目录路径</label><div class="inp"><input data-f="inbox" data-k="path" value="' + esc(path) + '" placeholder="可空，默认 ./client_inbox"></div></div>' +
        '</div>' +
        '<div class="cfg-save-row">' +
          '<button type="button" class="mini cfg-save-one">保存收件箱配置</button>' +
          '<span class="cfg-save-msg"></span>' +
        '</div>' +
      '</div>';
    var btn = box.querySelector('.cfg-save-one');
    if (btn) btn.addEventListener('click', function () {
      saveHubCard(box, { clientInbox: collectInbox(box) }, renderInboxCard);
    });
  }

  document.getElementById('cfgSave').addEventListener('click', function () {
    var btn = this;
    btn.disabled = true;
    var payload = {
      grok: collectFields('grok'),
      gemini: collectFields('gemini'),
      doubao: collectFields('doubao'),
      pool: collectFields('pool', document.getElementById('poolCfgCard')),
      papers: collectFields('papers', document.getElementById('papersCfgCard')),
      ps: collectFields('ps', document.getElementById('psCfgCard')),
      projectProof: collectProjectProof(document.getElementById('projectProofCfgCard')),
      wecomChat: collectWecom(document.getElementById('wecomCfgCard')),
      alert: collectAlertCfg(document.getElementById('alertCfgCard')),
      scanPdf: collectScanPdf(document.getElementById('scanPdfCfgCard')),
      portal: collectFields('portal', document.getElementById('portalCfgCard')),
      mysql: collectFields('mysql', document.getElementById('mysqlCfgCard')),
      clientInbox: collectInbox(document.getElementById('inboxCfgCard')),
      classifyModel: document.getElementById('classifyModel').value
    };
    postConfig(payload).then(function (res) {
      if (res.ok && res.data && res.data.version) { sayCfg(true, '✓ 配置已保存'); cfgData = res.data; renderConfig(res.data); }
      else sayCfg(false, (res.data && res.data.detail) || '保存失败');
      btn.disabled = false;
    }).catch(function (e) { sayCfg(false, '保存失败：' + (e.message || '')); btn.disabled = false; });
  });

  document.getElementById('cfgRestore').addEventListener('click', function () {
    if (!confirm('确定恢复为默认配置？当前修改将被覆盖。')) return;
    fetch('/api/config/restore-default', { method: 'POST' })
    .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
    .then(function (res) {
      if (res.ok && res.data && res.data.version) { sayCfg(true, '✓ 已恢复默认'); cfgData = res.data; renderConfig(res.data); }
      else sayCfg(false, (res.data && res.data.detail) || '恢复失败');
    });
  });

  document.getElementById('cfgProbe').addEventListener('click', function () {
    var btn = this;
    var box = document.getElementById('probeBox');
    btn.disabled = true;
    box.hidden = false;
    box.innerHTML = '<div class="p-row">正在检测 Grok / Gemini / 火山…</div>';
    fetch('/api/config/probe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        var data = res.data || {};
        if (!res.ok) {
          box.innerHTML = '<div class="p-row p-bad">' + esc((data.detail) || '检测失败') + '</div>';
          sayCfg(false, (data.detail) || '检测失败');
          btn.disabled = false;
          return;
        }
        var rows = data.results || [];
        box.innerHTML = rows.map(function (r) {
          var ms = r.ms ? (' · ' + (r.ms / 1000).toFixed(1) + 's') : '';
          if (r.ok) return '<div class="p-row p-ok">● ' + esc(r.label || r.id) + ' 已连通' + ms + (r.detail ? '（' + esc(r.detail) + '）' : '') + '</div>';
          return '<div class="p-row p-bad">● ' + esc(r.label || r.id) + ' 失败' + ms + '：' + esc(r.error || '未知错误') + '</div>';
        }).join('') || '<div class="p-row p-bad">' + esc(data.error || '没有可检测的模型') + '</div>';
        var n = rows.filter(function (r) { return r.ok; }).length;
        sayCfg(!!data.ok, n + '/' + rows.length + ' 个模型连通');
        btn.disabled = false;
      })
      .catch(function (e) {
        box.innerHTML = '<div class="p-row p-bad">' + esc(e.message || '检测失败') + '</div>';
        sayCfg(false, '检测失败：' + (e.message || ''));
        btn.disabled = false;
      });
  });
  global.loadConfig = loadConfig;
})(window);
