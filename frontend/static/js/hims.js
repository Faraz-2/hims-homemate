// HIMS: small progressive enhancements. No inline scripts (the Content-Security-Policy forbids them).
(function () {
  var body = document.body;

  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-toggle-sidebar]');
    if (toggle) { body.classList.toggle('nav-open'); return; }
    if (body.classList.contains('nav-open') && !e.target.closest('.sidebar')) body.classList.remove('nav-open');

    // close open dropdown menus when clicking elsewhere
    document.querySelectorAll('details.menu[open]').forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute('open');
    });

    var dismiss = e.target.closest('[data-dismiss]');
    if (dismiss) { var t = dismiss.closest('.flash'); if (t) t.remove(); }
  });

  // <form data-confirm="Are you sure?"> asks before submitting
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // <select data-autosubmit> submits its form when changed
  document.addEventListener('change', function (e) {
    if (e.target.matches('[data-autosubmit]') && e.target.form) e.target.form.submit();
  });

  // Show a chosen file name next to file inputs
  document.querySelectorAll('input[type=file]').forEach(function (input) {
    input.addEventListener('change', function () {
      var out = input.parentNode.querySelector('.file-name') || (input.form && input.form.querySelector('.file-name'));
      if (out) out.textContent = input.files.length ? input.files[0].name : '';
    });
  });

  // Flash messages fade out after a while (errors stay)
  document.querySelectorAll('.flash.success, .flash.info').forEach(function (el) {
    setTimeout(function () { el.classList.add('fade'); setTimeout(function () { el.remove(); }, 400); }, 7000);
  });

  // Live clock in the header
  var clock = document.querySelector('[data-clock]');
  if (clock) {
    var tick = function () {
      clock.textContent = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    };
    tick(); setInterval(tick, 20000);
  }

  // Chat on phones/tablets: size the chat box to exactly the visible screen (also when the on-screen keyboard opens or a
  // banner message pushes the page down), so the message box is always reachable and only the message list scrolls.
  var chatBox = document.querySelector('.chat');
  if (chatBox) {
    var fitChat = function () {
      if (window.innerWidth > 900) { chatBox.style.height = ''; return; }
      var vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
      var top = chatBox.getBoundingClientRect().top + window.pageYOffset;
      chatBox.style.height = Math.max(300, Math.round(vh - top - 10)) + 'px';
    };
    fitChat();
    window.addEventListener('resize', fitChat);
    window.addEventListener('orientationchange', fitChat);
    if (window.visualViewport) window.visualViewport.addEventListener('resize', fitChat);
  }

  // Chat: keep the newest message in view and fetch new ones every few seconds (no page reload)
  var log = document.querySelector('[data-chat]');
  if (log) {
    var last = parseInt(log.getAttribute('data-last') || '0', 10);
    var pollUrl = log.getAttribute('data-poll');
    var busy = false;
    log.scrollTop = log.scrollHeight;

    var buildRow = function (m) {
      var row = document.createElement('div'); row.className = 'msg' + (m.mine ? ' mine' : '');
      var av = document.createElement('span'); av.className = 'avatar sm'; av.textContent = m.initials;
      var bubble = document.createElement('div'); bubble.className = 'bubble';
      var who = document.createElement('div'); who.className = 'who';
      var name = document.createElement('b'); name.textContent = m.sender;
      var when = document.createElement('small'); when.textContent = m.time;
      who.appendChild(name); who.appendChild(when);
      var body = document.createElement('div'); body.className = 'prose'; body.textContent = m.body;   // textContent: never HTML
      bubble.appendChild(who); bubble.appendChild(body);
      if (m.file) {
        var a = document.createElement('a'); a.className = 'attach'; a.href = m.file.url; a.textContent = m.file.name;
        bubble.appendChild(a);
      }
      row.appendChild(av); row.appendChild(bubble);
      row.setAttribute('data-id', m.id);
      return row;
    };
    var addMessage = function (m) {
      if (m.id <= last) return;
      last = m.id;
      var stick = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
      var empty = log.querySelector('.chat-empty'); if (empty) empty.remove();
      log.appendChild(buildRow(m));
      if (stick || m.mine) log.scrollTop = log.scrollHeight;
    };

    // "Load earlier messages": fetch the 50 before the oldest one shown, keep the reading position where it was.
    var olderBtn = log.querySelector('[data-older-btn]');
    if (olderBtn) {
      var first = parseInt(log.getAttribute('data-first') || '0', 10);
      olderBtn.addEventListener('click', function () {
        olderBtn.disabled = true;
        fetch(log.getAttribute('data-older') + '?before=' + first, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) {
            if (!data) { olderBtn.disabled = false; return; }
            var prevHeight = log.scrollHeight, prevTop = log.scrollTop, anchor = olderBtn.nextSibling;
            data.messages.forEach(function (m) { log.insertBefore(buildRow(m), anchor); });
            if (data.messages.length) first = data.messages[0].id;
            log.scrollTop = prevTop + (log.scrollHeight - prevHeight);
            if (data.more) { olderBtn.disabled = false; } else { olderBtn.remove(); }
          })
          .catch(function () { olderBtn.disabled = false; });
      });
    }

    setInterval(function () {
      if (busy || document.hidden) return;
      busy = true;
      fetch(pollUrl + '?after=' + last, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) { if (data && data.messages) data.messages.forEach(addMessage); })
        .catch(function () { /* offline or server restarting: try again next tick */ })
        .then(function () { busy = false; });
    }, 5000);
  }

  // ---- 1.5: phones and tablets ------------------------------------------------------------------------------
  // Keep the page behind the slide-in menu from scrolling, close the menu on a link tap, Escape, or a wider screen.
  var syncLock = function () { body.classList.toggle('nav-lock', body.classList.contains('nav-open')); };
  new MutationObserver(syncLock).observe(body, { attributes: true, attributeFilter: ['class'] });
  document.addEventListener('click', function (e) {
    if (e.target.closest('.sidebar .nav-link')) body.classList.remove('nav-open');
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    body.classList.remove('nav-open');
    document.querySelectorAll('details.menu[open]').forEach(function (d) { d.removeAttribute('open'); });
  });
  window.addEventListener('resize', function () { if (window.innerWidth > 900) body.classList.remove('nav-open'); });

  // Opening one drop-down (notifications, profile) closes the other.
  document.querySelectorAll('details.menu').forEach(function (d) {
    d.addEventListener('toggle', function () {
      if (!d.open) return;
      document.querySelectorAll('details.menu[open]').forEach(function (o) { if (o !== d) o.removeAttribute('open'); });
    });
  });

  // On a phone each table row becomes a card. Give every cell its column name so the card can show it.
  document.querySelectorAll('table.data').forEach(function (table) {
    var heads = Array.prototype.map.call(table.querySelectorAll('thead th'), function (th) { return th.textContent.trim(); });
    if (!heads.length) return;
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      Array.prototype.forEach.call(tr.children, function (td, i) {
        if (td.tagName === 'TD' && !td.hasAttribute('colspan') && !td.hasAttribute('data-label')) td.setAttribute('data-label', heads[i] || '');
      });
    });
  });
})();
