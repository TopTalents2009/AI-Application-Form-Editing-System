/* 管理后台：人员信息 */
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
  var deptCache = [];
  function deptOptions(cur) {
    var names = deptCache.map(function (d) { return d.name; });
    if (cur && names.indexOf(cur) < 0) names.unshift(cur);
    var html = '<option value="">请选择部门</option>';
    names.forEach(function (n) {
      html += '<option value="' + escAttr(n) + '"' + (cur === n ? ' selected' : '') + '>' + esc(n) + '</option>';
    });
    return html;
  }
  function fillNewDeptSelect() {
    var sel = document.getElementById('newDept');
    if (!sel) return;
    var cur = sel.value;
    sel.innerHTML = deptOptions(cur);
    if (cur) sel.value = cur;
  }
  function sayDept(ok, t) {
    var el = document.getElementById('deptMsg');
    if (!el) return;
    el.className = 'msg ' + (ok ? 'ok' : 'bad');
    el.textContent = t;
    setTimeout(function () { if (el.textContent === t) el.textContent = ''; }, 4000);
  }
  function renderDeptTable() {
    var body = document.getElementById('deptBody');
    if (!body) return;
    if (!deptCache.length) {
      body.innerHTML = '<tr><td colspan="3" style="color:#8a93a6">暂无部门，请先添加</td></tr>';
      return;
    }
    body.innerHTML = deptCache.map(function (d) {
      return '<tr><td>' + esc(d.name) + '</td><td>' + (d.userCount || 0) + '</td><td class="ops">' +
        '<button type="button" class="mini" data-dept-del="' + d.id + '">删除</button></td></tr>';
    }).join('');
  }
  function loadUsers() {
    Promise.all([
      fetch('/api/admin/users', { cache: 'no-store' }).then(function (r) { return r.json(); }),
      fetch('/api/admin/departments', { cache: 'no-store' }).then(function (r) { return r.json(); })
    ]).then(function (pair) {
      var j = pair[0], dj = pair[1];
      if (dj && dj.ok) deptCache = dj.items || [];
      fillNewDeptSelect();
      renderDeptTable();
      if (!j.ok) { say(false, j.detail || '加载失败'); return; }
      AdminStats.userCount = (j.users || []).length;
      renderUsers(j.users || []);
      if (global.renderDataCard) global.renderDataCard();
    }).catch(function (e) { say(false, '加载失败：' + e.message); });
  }
  function renderUsers(users) {
    var tbody = document.getElementById('tbody');
    tbody.innerHTML = '';
    users.forEach(function (u) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + u.id + '</td>' +
        '<td class="td-clip" title="' + esc(u.username) + '">' + esc(u.username) +
          (u.mustSetCredentials ? ' <span class="me-tag">待改密</span>' : '') + '</td>' +
        '<td class="td-name" title="' + esc(u.realName) + '">' + esc(u.realName) + '</td>' +
        '<td><select class="dept-sel" data-id="' + u.id + '">' + deptOptions(u.department) + '</select></td>' +
        '<td><select class="role-sel" data-id="' + u.id + '">' + roleOptions(u.role) + '</select></td>' +
        '<td><select class="status-sel" data-id="' + u.id + '">' +
          '<option value="active"' + (u.status === 'active' ? ' selected' : '') + '>启用</option>' +
          '<option value="disabled"' + (u.status === 'disabled' ? ' selected' : '') + '>停用</option>' +
        '</select></td>' +
        '<td>' + esc(u.createdAt) + '</td>' +
        '<td>' + esc(u.lastLoginAt || '—') + '</td>' +
        '<td class="ops">' +
          '<input type="password" class="reset-pw" placeholder="新密码" data-id="' + u.id + '">' +
          '<button type="button" class="mini" data-act="pw" data-id="' + u.id + '">重置</button>' +
          '<button type="button" class="mini" data-act="del" data-id="' + u.id + '">删除</button>' +
        '</td>';
      var me = global.adminMe;
      if (me && u.id === me.id) {
        var tag = document.createElement('span');
        tag.className = 'me-tag';
        tag.textContent = '(我)';
        tr.children[1].appendChild(tag);
      }
      tbody.appendChild(tr);
    });
    if (global.fillApiOwnerSelect) global.fillApiOwnerSelect(users);
  }
  document.getElementById('userCreate').addEventListener('click', function () {
    var btn = document.getElementById('userCreate');
    var body = {
      username: (document.getElementById('newUserName').value || '').trim(),
      realName: (document.getElementById('newRealName').value || '').trim(),
      department: (document.getElementById('newDept').value || '').trim(),
      password: document.getElementById('newPassword').value || '',
      role: document.getElementById('newRole').value || 'user'
    };
    btn.disabled = true;
    fetch('/api/admin/users', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
    .then(function (res) {
      btn.disabled = false;
      if (res.ok && res.data.ok) {
        say(true, '已添加账号 ' + ((res.data.user && res.data.user.username) || ''));
        document.getElementById('newUserName').value = '';
        document.getElementById('newRealName').value = '';
        document.getElementById('newDept').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('newRole').value = 'user';
        loadUsers();
      } else say(false, (res.data && res.data.detail) || '添加失败');
    }).catch(function (e) {
      btn.disabled = false;
      say(false, '添加失败：' + (e.message || ''));
    });
  });
  document.getElementById('tbody').addEventListener('change', function (e) {
    var sel = e.target;
    if (!sel.classList.contains('role-sel') && !sel.classList.contains('status-sel') && !sel.classList.contains('dept-sel')) return;
    var id = sel.getAttribute('data-id');
    var body = sel.classList.contains('role-sel') ? { role: sel.value }
      : (sel.classList.contains('dept-sel') ? { department: sel.value } : { status: sel.value });
    fetch('/api/admin/users/' + id, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
    .then(function (res) {
      if (res.ok && res.data.ok) { say(true, '已更新'); loadUsers(); }
      else { say(false, (res.data.detail) || '更新失败'); loadUsers(); }
    });
  });
  document.getElementById('tbody').addEventListener('click', function (e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    var id = btn.getAttribute('data-id');
    var act = btn.getAttribute('data-act');
    if (act === 'pw') {
      var input = document.querySelector('input.reset-pw[data-id="' + id + '"]');
      var pw = (input && input.value) || '';
      if (!pw) { say(false, '请先输入新密码'); return; }
      var selfReset = global.adminMe && String(global.adminMe.id) === String(id);
      var ok = selfReset
        ? confirm('确定修改自己的密码？修改后请用新密码重新登录。')
        : confirm('确定重置该用户密码？对方需用此临时密码登录，并立即重新设置密码。');
      if (!ok) return;
      fetch('/api/admin/users/' + id, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        if (res.ok && res.data.ok) {
          say(true, selfReset ? '密码已修改，请重新登录' : '密码已重置，对方下次登录需重新设置密码');
          input.value = '';
          loadUsers();
        }
        else say(false, (res.data.detail) || '重置失败');
      });
    } else if (act === 'del') {
      if (!confirm('确定删除该用户？此操作不可恢复。')) return;
      fetch('/api/admin/users/' + id, { method: 'DELETE' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        if (res.ok && res.data.ok) { say(true, '已删除'); loadUsers(); }
        else say(false, (res.data.detail) || '删除失败');
      });
    }
  });
  var deptCreateBtn = document.getElementById('deptCreate');
  if (deptCreateBtn) {
    deptCreateBtn.addEventListener('click', function () {
      var inp = document.getElementById('deptNewName');
      var name = ((inp && inp.value) || '').trim();
      if (!name) { sayDept(false, '请填写部门名称'); return; }
      deptCreateBtn.disabled = true;
      fetch('/api/admin/departments', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) {
        if (res.ok && res.data.ok) {
          sayDept(true, '已添加');
          if (inp) inp.value = '';
          loadUsers();
        } else sayDept(false, (res.data && res.data.detail) || '添加失败');
      }).catch(function (e) {
        sayDept(false, e.message || '添加失败');
      }).finally(function () { deptCreateBtn.disabled = false; });
    });
  }
  var deptBody = document.getElementById('deptBody');
  if (deptBody) {
    deptBody.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-dept-del]') : null;
      if (!btn) return;
      if (!confirm('确定删除该部门？')) return;
      fetch('/api/admin/departments/' + btn.getAttribute('data-dept-del'), { method: 'DELETE' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          if (res.ok && res.data.ok) { sayDept(true, '已删除'); loadUsers(); }
          else sayDept(false, (res.data && res.data.detail) || '删除失败');
        });
    });
  }
  global.loadUsers = loadUsers;
})(window);
