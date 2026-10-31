/*
 * dash-shim.js
 * A tiny runtime that gives js/app.js the same `dash` helpers it had inside Claude:
 *   dash.calc(id, {inputs, fn})  derived tables, computed from data files or other calcs
 *   dash.data(id)                {status, data} for a data file or a calc
 *   dash.draw(name, fn)          runs fn(el) for every visible [data-draw="name"] element,
 *                                again whenever data loads, the tab changes or the width changes
 *   dash.colors                  series colours (CSS variables, so they follow light/dark)
 *   dash.setLink(name)           puts the tab name in the URL hash and redraws
 * Data files are listed in DATASETS and loaded from data/<id>.json.
 */
(function () {
  const DATASETS = ['risks', 'strategy_curve', 'strategy_summary', 'strategy_by_area', 'snowball_nodes', 'snowball_edges', 'snowball_outcome'];
  const state = {};
  const calcs = [];
  const draws = {};
  let ready = false;

  function drawAll(only) {
    if (!ready) return;
    Object.keys(draws).forEach((name) => {
      if (only && name !== only) return;
      document.querySelectorAll('[data-draw="' + name + '"]').forEach((el) => {
        if (el.closest('[hidden]')) return;
        try { draws[name](el); } catch (err) { console.error('draw "' + name + '" failed:', err); }
      });
    });
  }

  window.dash = {
    colors: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => 'var(--series-' + i + ')'),
    data(id) { return state[id] || {status: 'loading', data: [], message: ''}; },
    calc(id, spec) { calcs.push({id, inputs: spec.inputs, fn: spec.fn}); },
    draw(name, fn) { if (fn) draws[name] = fn; drawAll(name); },
    setLink(name) { try { history.replaceState(null, '', '#' + name); } catch (e) { /* file:// or sandbox */ } drawAll(); },
    params() { return {}; },
    setParams() {},
    resetParams() {},
    refresh() {},
  };

  async function load() {
    await Promise.all(DATASETS.map(async (id) => {
      try {
        const res = await fetch('data/' + id + '.json');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        state[id] = {status: 'ok', data: await res.json(), message: ''};
      } catch (err) {
        state[id] = {status: 'error', data: [], message: String(err)};
      }
    }));
    calcs.forEach((c) => {
      const ins = c.inputs.map((id) => state[id]);
      if (ins.some((s) => !s || s.status !== 'ok')) { state[c.id] = {status: 'error', data: [], message: 'An input did not load'}; return; }
      try { state[c.id] = {status: 'ok', data: c.fn(...ins.map((s) => s.data)), message: ''}; }
      catch (err) { console.error('calc "' + c.id + '" failed:', err); state[c.id] = {status: 'error', data: [], message: String(err)}; }
    });
    ready = true;
    drawAll();
    if (location.protocol === 'file:' && DATASETS.some((id) => state[id].status === 'error')) {
      const note = document.createElement('p');
      note.style.cssText = 'padding:12px 16px;border:1px solid var(--color-border-line);border-radius:8px;margin:0 0 16px;background:var(--color-panel)';
      note.textContent = 'The data files could not be read from a local file. Serve the folder instead (for example: python3 -m http.server) or open the GitHub Pages link.';
      document.getElementById('dash-root').prepend(note);
    }
  }

  let lastWidth = 0, timer = null;
  new ResizeObserver((entries) => {
    const w = Math.round(entries[0].contentRect.width);
    if (w === lastWidth) return;
    lastWidth = w; clearTimeout(timer); timer = setTimeout(() => drawAll(), 120);
  }).observe(document.documentElement);

  window.addEventListener('load', load);
})();
