/* Daily Job Tool: progressive enhancement only. Everything core works without this file
   (cards are links to /jobs/{id}, filters are GET forms). No inline script, CSP-safe. */
(function () {
  'use strict';
  var DESKTOP = window.matchMedia('(min-width: 980px)');
  var pending = null; // dismiss in flight: {id, wasSelected, next}

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function cards() { return $$('#rows > li.card:not(.card-gone)'); }
  function selected() { return $('#rows > li.card.is-selected'); }
  function detail() { return document.getElementById('detail'); }

  /* ---- popovers (<details data-pop>) ------------------------------------------------ */
  function placePop(d) {
    var pop = $('.pop', d), sum = $('summary', d);
    if (!pop || !sum) { return; }
    pop.classList.add('fx');
    var r = sum.getBoundingClientRect();
    var w = pop.offsetWidth;
    var left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    if (pop.classList.contains('right')) { left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)); }
    pop.style.left = left + 'px';
    pop.style.top = (r.bottom + 6) + 'px';
  }
  function closePops(except) {
    $$('details[data-pop][open]').forEach(function (d) { if (d !== except) { d.open = false; } });
  }
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (d && d.matches && d.matches('details[data-pop]') && d.open) {
      closePops(d);
      placePop(d);
      var s = $('input[type=search]', d);
      if (s) { s.focus(); }
    }
  }, true);
  window.addEventListener('resize', function () { closePops(null); });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('details[data-pop]')) { closePops(null); }
  });

  /* ---- All filters dialog ----------------------------------------------------------- */
  document.addEventListener('click', function (e) {
    var o = e.target.closest('[data-open-dialog]');
    if (o) {
      var dlg = document.getElementById(o.getAttribute('data-open-dialog'));
      if (dlg && dlg.showModal) { closePops(null); dlg.showModal(); }
      return;
    }
    if (e.target.closest('[data-close-dialog]')) {
      var dd = e.target.closest('dialog'); if (dd) { dd.close(); }
      return;
    }
    if (e.target.tagName === 'DIALOG') { e.target.close(); } // click on the backdrop
  });

  /* ---- selection + detail pane ------------------------------------------------------- */
  function markViewed(card) {
    if (!card || card.classList.contains('is-viewed')) { return; }
    card.classList.add('is-viewed');
    try { fetch('/jobs/' + card.getAttribute('data-id') + '/viewed', { method: 'POST', credentials: 'same-origin' }); } catch (err) { /* best effort */ }
  }
  function setSelected(card) {
    cards().forEach(function (c) { c.classList.remove('is-selected'); c.removeAttribute('aria-current'); });
    card.classList.add('is-selected');
    card.setAttribute('aria-current', 'true');
    card.scrollIntoView({ block: 'nearest' });
  }
  function openCard(card, opts) {
    opts = opts || {};
    setSelected(card);
    var pane = detail();
    if (!DESKTOP.matches || !pane || !window.htmx) { return; }
    card.classList.add('is-viewed');
    pane.classList.add('is-loading');
    var p = window.htmx.ajax('GET', '/jobs/' + card.getAttribute('data-id'), { target: '#detail', swap: 'innerHTML' });
    var done = function () { pane.classList.remove('is-loading'); pane.scrollTop = 0; if (opts.focusPane) { pane.focus(); } };
    if (p && p.then) { p.then(done, done); } else { done(); }
  }

  document.addEventListener('click', function (e) {
    var card = e.target.closest('#rows > li.card');
    if (!card || card.classList.contains('card-gone')) { return; }
    if (e.target.closest('.x')) { return; }                          // dismiss / undo forms
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) { return; }
    if (!DESKTOP.matches) {                                            // narrow: follow the link
      if (!e.target.closest('a')) { var a = $('a.job-title', card); if (a) { window.location.href = a.href; } }
      return;
    }
    e.preventDefault();
    openCard(card, { focusPane: e.detail === 0 });                     // detail 0 = keyboard Enter
  });

  /* ---- dismiss bookkeeping ------------------------------------------------------------ */
  document.addEventListener('htmx:beforeRequest', function (e) {
    var path = (e.detail && e.detail.requestConfig && e.detail.requestConfig.path) || '';
    if (!/\/jobs\/\d+\/dismiss$/.test(path)) { return; }
    var id = path.split('/')[2];
    var card = document.getElementById('card-' + id);
    var all = cards();
    var i = card ? all.indexOf(card) : -1;
    pending = { id: id, wasSelected: !!(card && card.classList.contains('is-selected')),
                next: i >= 0 ? (all[i + 1] || all[i - 1] || null) : null };
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    if (!pending || !e.detail || !e.detail.successful) { return; }
    var p = pending; pending = null;
    if (p.wasSelected && DESKTOP.matches) {
      var pane = detail();
      if (p.next) { openCard(p.next); }
      else if (pane) { pane.innerHTML = '<p class="placeholder muted">Select a job to read it here.</p>'; }
    }
  });
  function unload() { var p = detail(); if (p) { p.classList.remove('is-loading'); } }
  document.addEventListener('htmx:responseError', unload);
  document.addEventListener('htmx:sendError', unload);

  /* ---- first job auto-open counts as viewed (desktop only) ----------------------------- */
  function initBrowse() {
    if (!DESKTOP.matches) { return; }
    var s = selected();
    if (s && detail() && $('#detail article')) { markViewed(s); }
  }
  document.addEventListener('DOMContentLoaded', initBrowse);
  document.addEventListener('htmx:afterSettle', function (e) { if (e.target && e.target.id === 'browse') { initBrowse(); } });

  /* ---- keyboard ------------------------------------------------------------------------ */
  function move(delta, viaArrow) {
    var all = cards(); if (!all.length) { return; }
    var cur = selected(); var i = cur ? all.indexOf(cur) : -1;
    var n = all[Math.max(0, Math.min(all.length - 1, i + delta))];
    if (!n) { return; }
    if (DESKTOP.matches) { openCard(n); } else { setSelected(n); }
    var a = $('a.job-title', n); if (a && !viaArrow) { a.focus({ preventScroll: true }); }
  }
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) { return; }
    var t = e.target;
    if (t.closest && t.closest('input, textarea, select, [contenteditable], dialog[open]')) { return; }
    var inList = t === document.body || !!(t.closest && t.closest('#list-pane'));
    var k = e.key;
    if (k === 'Escape') { closePops(null); return; }
    if (k === 'j' || (k === 'ArrowDown' && inList)) { e.preventDefault(); move(1, k !== 'j'); }
    else if (k === 'k' || (k === 'ArrowUp' && inList)) { e.preventDefault(); move(-1, k !== 'k'); }
    else if (k === 'Enter' && t === document.body) { var p = detail(); if (p) { e.preventDefault(); p.focus(); } }
    else if (k === 'a') {
      var s = selected(); var url = s && s.getAttribute('data-apply');
      if (url) { e.preventDefault(); window.open(url, '_blank', 'noopener,noreferrer'); }
    } else if (k === 'x') {
      var c = selected(); var b = c && $('.x-btn', c); if (b) { e.preventDefault(); b.click(); }
    } else if (k === '/') { var q = document.getElementById('top-q'); if (q) { e.preventDefault(); q.focus(); } }
  });
})();
