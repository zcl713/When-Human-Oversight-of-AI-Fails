/* ---------- calculations ---------- */
dash.calc('kpi', {inputs: ['risks'], title: 'Headline counts', description: 'Totals, critical count, average RPN, failures and pass rate across all test cases.',
  fn: (risks) => {
    const run = risks.filter((r) => r.result !== 'Not run');
    const top = [...risks].sort((a, b) => b.rpn - a.rpn || b.severity - a.severity || a.id.localeCompare(b.id)).slice(0, 10);
    const sum = risks.reduce((s, r) => s + Number(r.rpn), 0);
    return [{
      total: risks.length,
      critical: risks.filter((r) => r.tier === 'Critical').length,
      avg_rpn: Math.round(sum / risks.length * 10) / 10,
      run: run.length,
      failed: run.filter((r) => r.result === 'Fail').length,
      pass_rate: run.length ? Math.round(run.filter((r) => r.result === 'Pass').length / run.length * 1000) / 1000 : null,
      top10_review: top.filter((r) => r.area === 'Human review').length,
    }];
  }});

dash.calc('by_area_tier', {inputs: ['risks'], title: 'Tier counts by workflow area', description: 'Number of failure modes in each workflow area and risk tier.',
  fn: (risks) => {
    const areas = ['Reasoning', 'Planning', 'System execution', 'Human review'];
    const tiers = ['Critical', 'High', 'Medium', 'Low'];
    return areas.flatMap((area) => tiers.map((tier) => ({key: area + ' / ' + tier, area, tier, n: risks.filter((r) => r.area === area && r.tier === tier).length})));
  }});

dash.calc('by_result', {inputs: ['risks'], title: 'Tests by result', description: 'Number of test cases with each latest result.',
  fn: (risks) => ['Fail', 'Partial', 'Pass', 'Not run'].map((result) => ({result, n: risks.filter((r) => r.result === result).length}))});

dash.calc('by_category', {inputs: ['risks'], title: 'Roll-up by category', description: 'Tests, runs, failures, pass rate, average and maximum RPN per category, as in the Excel dashboard.',
  fn: (risks) => {
    const cats = [...new Set(risks.map((r) => r.category))];
    return cats.map((category) => {
      const g = risks.filter((r) => r.category === category);
      const run = g.filter((r) => r.result !== 'Not run');
      return {
        category, tests: g.length, run: run.length,
        fail: run.filter((r) => r.result === 'Fail').length,
        pass_rate: run.length ? Math.round(run.filter((r) => r.result === 'Pass').length / run.length * 1000) / 1000 : null,
        avg_rpn: Math.round(g.reduce((s, r) => s + Number(r.rpn), 0) / g.length * 10) / 10,
        max_rpn: Math.max(...g.map((r) => Number(r.rpn))),
      };
    });
  }});

dash.calc('top10', {inputs: ['risks'], title: 'Top 10 risks', description: 'The ten highest-RPN failure modes; ties broken by severity, then ID.',
  fn: (risks) => [...risks].sort((a, b) => b.rpn - a.rpn || b.severity - a.severity || a.id.localeCompare(b.id)).slice(0, 10)
    .map((r, i) => ({rank: i + 1, id: r.id, test: r.test, area: r.area, rpn: r.rpn, tier: r.tier, result: r.result, mvp: r.mvp}))});

dash.calc('area_share', {inputs: ['strategy_by_area'], title: 'Catch share by area', description: 'Errors caught divided by errors present, per workflow area and strategy, at a 10% budget.',
  fn: (strategy_by_area) => strategy_by_area.map((r) => ({key: r.area + ' / ' + r.method, area: r.area, method: r.method, errors: r.errors, caught: r.caught, share: Math.round(r.caught / r.errors * 1000) / 1000}))});

/* ---------- shared helpers ---------- */
const THRESHOLDS = {critical: 60, high: 36, medium: 18};
const MAX_RPN = 125;
const AREAS = ['Reasoning', 'Planning', 'System execution', 'Human review'];
const TIER_FILL = {Critical: 'var(--tier-critical)', High: 'var(--tier-high)', Medium: 'var(--tier-medium)', Low: 'var(--tier-low)'};
const AREA_FILL = {'System execution': dash.colors[0], 'Reasoning': dash.colors[1], 'Planning': dash.colors[2], 'Human review': dash.colors[3]};
const METHOD_FILL = {'Risk-based': dash.colors[4], 'Random sampling': 'var(--cds-chart-muted)'};
document.querySelectorAll('[data-sw]').forEach((i) => { i.style.background = AREA_FILL[i.dataset.sw] || METHOD_FILL[i.dataset.sw]; });
document.querySelectorAll('[data-goto]').forEach((b) => b.addEventListener('click', () => openTab(b.dataset.goto, true)));
const pct = d3.format('.0%');
const pct1 = d3.format('.1%');
const num1 = d3.format(',.1f');
const intf = d3.format(',');
const signed = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + d3.format('.1f')(Math.abs(v));

function rowsOf(id) { const d = dash.data(id); return d.status === 'ok' ? d.data : null; }
function failed(id) { return dash.data(id).status === 'error'; }
function showFail(el) { const f = document.createElement('div'); f.className = 'qa-fail'; f.textContent = 'Failed to load'; el.replaceChildren(f); }
function textWidth(svg, strings, size) {
  const t = svg.append('text').attr('font-size', size || 12).style('visibility', 'hidden');
  let w = 0; strings.forEach((s) => { t.text(s); w = Math.max(w, t.node().getComputedTextLength()); });
  t.remove(); return w;
}
function tipFor(el) {
  const tip = document.createElement('div'); tip.className = 'qa-tip'; tip.hidden = true; el.appendChild(tip);
  return {
    show(event, head, lines) {
      const h = document.createElement('div'); h.className = 't-head'; h.textContent = head;
      const rows = lines.map(([sw, text]) => { const r = document.createElement('div'); r.className = 't-row';
        if (sw) { const s = document.createElement('i'); s.className = 'qa-sw'; s.style.background = sw; r.appendChild(s); }
        r.appendChild(document.createTextNode(text)); return r; });
      tip.replaceChildren(h, ...rows); tip.hidden = false;
      const box = el.getBoundingClientRect(); const tw = tip.offsetWidth, th = tip.offsetHeight;
      let x = event.clientX - box.left + 12, y = event.clientY - box.top + 12;
      if (x + tw > el.clientWidth) x = Math.max(0, event.clientX - box.left - tw - 12);
      if (y + th > el.clientHeight + 40) y = Math.max(0, event.clientY - box.top - th - 12);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    },
    hide() { tip.hidden = true; },
  };
}
function pill(text, cls) { const s = document.createElement('span'); s.className = 'qa-pill ' + cls; s.textContent = text; return s; }
function td(content, cls) { const c = document.createElement('td'); if (cls) c.className = cls; if (content instanceof Node) c.appendChild(content); else c.textContent = content; return c; }
function done(el) { el.classList.remove('dash-skeleton'); }

/* ---------- text values ---------- */
dash.draw('thr', (el) => { el.textContent = THRESHOLDS[el.dataset.k]; });
dash.draw('maxrpn', (el) => { el.textContent = MAX_RPN; });

const KPI_FMT = {avg_rpn: num1, pass_rate: pct};
dash.draw('kpi', (el) => {
  const r = rowsOf('kpi'); const f = el.dataset.field;
  if (!r) { el.textContent = '—'; return; }
  done(el); const v = r[0][f]; el.textContent = v == null ? '—' : (KPI_FMT[f] || intf)(v);
});
dash.draw('sum', (el) => {
  const r = rowsOf('strategy_summary'); if (!r) { el.textContent = '—'; return; }
  const m = el.dataset.where.split('=')[1]; const row = r.find((x) => x.method === m); const f = el.dataset.field;
  const v = row ? row[f] : null;
  el.textContent = v == null ? '—' : (['errors_caught', 'critical_caught', 'errors_missed', 'review_hours'].includes(f) ? num1(v) : intf(v));
});
dash.draw('outcome', (el) => {
  const r = rowsOf('snowball_outcome'); if (!r) { el.textContent = '—'; return; }
  const m = el.dataset.where.split('=')[1]; const row = r.find((x) => x.method === m);
  el.textContent = row ? intf(row[el.dataset.field]) : '—';
});
/* ---------- risk register charts ---------- */
dash.draw('areaTier', (el) => {
  if (failed('by_area_tier')) return showFail(el);
  const rows = rowsOf('by_area_tier'); el.replaceChildren(); if (!rows) return; done(el);
  const W = el.clientWidth, rowH = 34, H = AREAS.length * rowH + 28;
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
  const left = Math.ceil(textWidth(svg, AREAS, 12)) + 12;
  const totals = AREAS.map((a) => d3.sum(rows.filter((r) => r.area === a), (r) => r.n));
  const x = d3.scaleLinear([0, d3.max(totals) || 1], [left, W - 28]);
  const y = d3.scaleBand(AREAS, [0, AREAS.length * rowH]).padding(0.3);
  svg.append('g').attr('transform', `translate(0,${AREAS.length * rowH})`).call(d3.axisBottom(x).ticks(5).tickSize(-AREAS.length * rowH).tickFormat(d3.format('d'))).attr('font-size', null).attr('font-family', null);
  svg.append('g').selectAll('text').data(AREAS).join('text').attr('x', left - 10).attr('y', (a) => y(a) + y.bandwidth() / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).text((a) => a);
  const tip = tipFor(el);
  AREAS.forEach((a, i) => {
    let acc = 0;
    ['Critical', 'High', 'Medium', 'Low'].forEach((t) => {
      const r = rows.find((d) => d.area === a && d.tier === t); if (!r || !r.n) return;
      svg.append('rect').attr('class', 'grow').attr('x', x(acc)).attr('y', y(a)).attr('width', Math.max(0, x(acc + r.n) - x(acc) - 1)).attr('height', y.bandwidth()).attr('rx', 2).style('fill', TIER_FILL[t]).style('stroke', t === 'Low' ? 'var(--tier-low-line)' : 'none')
        .on('mousemove', (e) => tip.show(e, a, [[TIER_FILL[t], `${t}: ${r.n} failure mode${r.n === 1 ? '' : 's'}`]])).on('mouseleave', () => tip.hide());
      acc += r.n;
    });
    svg.append('text').attr('class', 'muted').attr('x', x(totals[i]) + 6).attr('y', y(a) + y.bandwidth() / 2).attr('dy', '0.35em').attr('font-size', 12).text(totals[i]);
  });
});

dash.draw('results', (el) => {
  if (failed('by_result')) return showFail(el);
  const rows = rowsOf('by_result'); el.replaceChildren(); if (!rows) return; done(el);
  const fill = {Fail: 'var(--cds-chart-status-critical)', Partial: 'var(--color-warn)', Pass: 'var(--color-ok)', 'Not run': 'var(--cds-chart-grid)'};
  const W = el.clientWidth, rowH = 30, H = rows.length * rowH + 6;
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
  const left = Math.ceil(textWidth(svg, rows.map((r) => r.result), 12)) + 12;
  const x = d3.scaleLinear([0, d3.max(rows, (r) => r.n) || 1], [left, W - 30]);
  const y = d3.scaleBand(rows.map((r) => r.result), [0, rows.length * rowH]).padding(0.3);
  rows.forEach((r) => {
    svg.append('text').attr('x', left - 10).attr('y', y(r.result) + y.bandwidth() / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).text(r.result);
    svg.append('rect').attr('class', 'grow').attr('x', left).attr('y', y(r.result)).attr('width', Math.max(0, x(r.n) - left)).attr('height', y.bandwidth()).attr('rx', 2).style('fill', fill[r.result]);
    svg.append('text').attr('class', 'muted').attr('x', x(r.n) + 6).attr('y', y(r.result) + y.bandwidth() / 2).attr('dy', '0.35em').attr('font-size', 12).text(r.n);
  });
});

dash.draw('catTable', (el) => {
  const rows = rowsOf('by_category'); el.replaceChildren(); if (!rows) return;
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    const bar = document.createElement('span'); bar.className = 'qa-bar' + (r.avg_rpn >= THRESHOLDS.critical ? ' hot' : ''); bar.style.width = Math.round(r.avg_rpn / 125 * 60) + 'px';
    const avg = document.createElement('span'); avg.append(num1(r.avg_rpn), bar);
    tr.append(td(r.category), td(intf(r.tests), 'num'), td(intf(r.run), 'num'), td(intf(r.fail), 'num'), td(r.pass_rate == null ? '—' : pct(r.pass_rate), 'num'), td(avg, 'num'), td(intf(r.max_rpn), 'num'));
    el.appendChild(tr);
  });
});

dash.draw('top10', (el) => {
  const rows = rowsOf('top10'); el.replaceChildren(); if (!rows) return;
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.append(td(intf(r.rank), 'num'), td(r.id, 'id'), td(r.test), td(r.area, 'hide-sm'), td(intf(r.rpn), 'num'), td(pill(r.tier, 't-' + r.tier)), td(pill(r.result, 'r-' + r.result.split(' ')[0])), td(r.mvp ? pill(r.mvp, 'mvp') : '', 'hide-sm'));
    el.appendChild(tr);
  });
});

/* register: search, area filter, sort (page-local state) */
const reg = {q: '', area: '', sort: 'rpn', dir: -1};
const TIER_RANK = {Critical: 4, High: 3, Medium: 2, Low: 1};
document.querySelectorAll('#qa-search').forEach((inp) => inp.addEventListener('input', () => { reg.q = inp.value.trim().toLowerCase(); dash.draw('register'); }));
document.querySelectorAll('.qa-chip[data-area]').forEach((b) => b.addEventListener('click', () => {
  reg.area = b.dataset.area;
  document.querySelectorAll('.qa-chip[data-area]').forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
  dash.draw('register');
}));
document.querySelectorAll('.qa-sort').forEach((b) => b.addEventListener('click', () => {
  const k = b.dataset.sort;
  reg.dir = reg.sort === k ? -reg.dir : (['id', 'test', 'area'].includes(k) ? 1 : -1); reg.sort = k;
  document.querySelectorAll('.qa-sort').forEach((o) => { const th = o.parentElement; if (o === b) th.setAttribute('aria-sort', reg.dir > 0 ? 'ascending' : 'descending'); else th.removeAttribute('aria-sort'); });
  dash.draw('register');
}));
dash.draw('register', (el) => {
  const rows = rowsOf('risks'); el.replaceChildren(); if (!rows) return;
  const val = (r) => reg.sort === 'tier' ? TIER_RANK[r.tier] : r[reg.sort];
  rows.filter((r) => (!reg.area || r.area === reg.area) && (!reg.q || [r.id, r.test, r.what, r.category, r.control].join(' ').toLowerCase().includes(reg.q)))
    .sort((a, b) => { const va = val(a), vb = val(b); return (typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb))) * reg.dir || b.rpn - a.rpn; })
    .forEach((r) => {
      const tr = document.createElement('tr');
      tr.append(td(r.id, 'id'), td(r.test), td(r.area, 'hide-sm'), td(String(r.severity), 'num'), td(String(r.likelihood), 'num'), td(String(r.detectability), 'num'),
        td(intf(r.rpn), 'num'), td(pill(r.tier, 't-' + r.tier)), td(pill(r.result, 'r-' + r.result.split(' ')[0])), td(r.what, 'wrap'), td(r.evidence, 'wrap'));
      el.appendChild(tr);
    });
});

/* ---------- strategies ---------- */
let metric = 'recall';
document.querySelectorAll('[data-metric]').forEach((b) => b.addEventListener('click', () => {
  metric = b.dataset.metric;
  document.querySelectorAll('[data-metric]').forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
  dash.draw('curve');
}));
dash.draw('curve', (el) => {
  if (failed('strategy_curve')) return showFail(el);
  const rows = rowsOf('strategy_curve'); el.replaceChildren(); if (!rows) return; done(el);
  const W = el.clientWidth, H = 300, top = 16, bottom = 30;
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
  const series = [
    {name: 'Risk-based', color: METHOD_FILL['Risk-based'], pts: rows.filter((r) => r.method === 'Risk-based')},
    {name: 'Random sampling', color: METHOD_FILL['Random sampling'], pts: rows.filter((r) => r.method === 'Random sampling')},
  ];
  const ymax = Math.min(1, Math.ceil((d3.max(rows, (r) => r[metric]) || 0.1) * 10) / 10);
  const left = Math.ceil(textWidth(svg, [pct(ymax), '0%'], 11)) + 10;
  const endLabels = series.map((s) => `${s.name} ${pct(s.pts[s.pts.length - 1][metric])}`);
  const right = Math.ceil(textWidth(svg, endLabels, 12)) + 14;
  const x = d3.scaleLinear([2, 30], [left, W - right]);
  const y = d3.scaleLinear([0, ymax], [H - bottom, top]);
  svg.append('g').attr('transform', `translate(0,${H - bottom})`).call(d3.axisBottom(x).tickValues([2, 10, 20, 30]).tickFormat((d) => d + '%').tickSize(0).tickPadding(8)).attr('font-size', null).attr('font-family', null);
  svg.append('g').attr('transform', `translate(${left},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(pct).tickSize(-(W - right - left))).attr('font-size', null).attr('font-family', null);
  svg.append('line').attr('x1', x(10)).attr('x2', x(10)).attr('y1', top).attr('y2', H - bottom).style('stroke', 'var(--cds-chart-reference)').attr('stroke-dasharray', '4 3');
  svg.append('text').attr('class', 'muted').attr('x', x(10) + 5).attr('y', top + 4).attr('font-size', 11).text('10% budget (cards above)');
  const line = d3.line().x((d) => x(d.budget)).y((d) => y(d[metric]));
  series.forEach((s) => svg.append('path').datum(s.pts).attr('d', line).style('fill', 'none').style('stroke', s.color).attr('stroke-width', 2).attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round'));
  const ends = series.map((s, i) => ({y: y(s.pts[s.pts.length - 1][metric]), text: endLabels[i], color: s.color}));
  if (Math.abs(ends[0].y - ends[1].y) < 16) ends[1].y = ends[0].y + 16;
  ends.forEach((e) => svg.append('text').attr('x', W - right + 8).attr('y', e.y).attr('dy', '0.35em').attr('font-size', 12).text(e.text));
  const tip = tipFor(el);
  const dots = series.map((s) => svg.append('circle').attr('r', 4).style('fill', s.color).style('display', 'none'));
  const budgets = series[0].pts.map((p) => p.budget);
  svg.append('rect').attr('x', left).attr('y', top).attr('width', W - right - left).attr('height', H - bottom - top).style('fill', 'transparent')
    .on('mousemove', (e) => {
      const [mx] = d3.pointer(e); const b = budgets[d3.leastIndex(budgets, (v) => Math.abs(x(v) - mx))];
      series.forEach((s, i) => { const p = s.pts.find((q) => q.budget === b); dots[i].attr('cx', x(b)).attr('cy', y(p[metric])).style('display', null); });
      tip.show(e, `Review ${b}% of answers`, series.map((s) => [s.color, `${s.name}: ${pct1(s.pts.find((q) => q.budget === b)[metric])}`]));
    })
    .on('mouseleave', () => { tip.hide(); dots.forEach((d) => d.style('display', 'none')); });
});

dash.draw('byArea', (el) => {
  if (failed('area_share')) return showFail(el);
  const rows = rowsOf('area_share'); el.replaceChildren(); if (!rows) return; done(el);
  const areas = [...new Set(rows.map((r) => r.area))]; const methods = ['Risk-based', 'Random sampling'];
  const fill = METHOD_FILL;
  const W = el.clientWidth, groupH = 58, H = areas.length * groupH + 24;
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
  const left = Math.ceil(textWidth(svg, areas, 12)) + 12;
  const x = d3.scaleLinear([0, 1], [left, W - 44]);
  const y = d3.scaleBand(areas, [0, areas.length * groupH]).padding(0.18);
  const y2 = d3.scaleBand(methods, [0, y.bandwidth()]).padding(0.12);
  svg.append('g').attr('transform', `translate(0,${areas.length * groupH})`).call(d3.axisBottom(x).ticks(4).tickFormat(pct).tickSize(-areas.length * groupH)).attr('font-size', null).attr('font-family', null);
  const tip = tipFor(el);
  areas.forEach((a) => {
    svg.append('text').attr('x', left - 10).attr('y', y(a) + y.bandwidth() / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).text(a);
    methods.forEach((m) => {
      const r = rows.find((d) => d.area === a && d.method === m); if (!r) return;
      svg.append('rect').attr('class', 'grow').attr('x', left).attr('y', y(a) + y2(m)).attr('width', Math.max(0, x(r.share) - left)).attr('height', y2.bandwidth()).attr('rx', 2).style('fill', fill[m])
        .on('mousemove', (e) => tip.show(e, a, [[fill[m], `${m}: ${num1(r.caught)} of ${r.errors} caught`]])).on('mouseleave', () => tip.hide());
      svg.append('text').attr('class', 'muted').attr('x', x(r.share) + 5).attr('y', y(a) + y2(m) + y2.bandwidth() / 2).attr('dy', '0.35em').attr('font-size', 11).text(pct(r.share));
    });
  });
});

/* ---------- snowball ---------- */
let sbMethod = 'Random sampling';
document.querySelectorAll('[data-sb]').forEach((b) => b.addEventListener('click', () => {
  sbMethod = b.dataset.sb;
  document.querySelectorAll('[data-sb]').forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
  dash.draw('graph');
}));
dash.draw('graph', (el) => {
  if (failed('snowball_nodes') || failed('snowball_edges')) return showFail(el);
  const nodes = rowsOf('snowball_nodes'), edges = rowsOf('snowball_edges'), outc = rowsOf('snowball_outcome');
  el.replaceChildren(); if (!nodes || !edges || !outc) return; done(el);
  const o = outc.find((r) => r.method === sbMethod);
  const W = Math.max(900, el.clientWidth), nw = 112, nh = 80, H = 250;
  const stages = d3.max(nodes, (n) => n.stage);
  const sx = (s) => 4 + s * (W - nw - 8) / stages;
  const rowOf = (n) => (n.node === 'perf' ? 0 : 1);
  const pos = new Map(nodes.map((n) => [n.node, {x: sx(n.stage), y: 14 + rowOf(n) * (nh + 50)}]));
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
  const stopAt = sbMethod === 'Risk-based' ? 'review' : null;
  edges.forEach((e) => {
    const a = pos.get(e.source), b = pos.get(e.target);
    const x1 = a.x + nw, y1 = a.y + nh / 2, x2 = b.x, y2 = b.y + nh / 2, mx = (x1 + x2) / 2;
    svg.append('path').attr('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`).style('fill', 'none')
      .style('stroke', e.target === 'client' && stopAt ? 'var(--cds-chart-muted)' : 'var(--cds-chart-status-critical)').style('stroke-opacity', 0.45).attr('stroke-width', 4);
  });
  const tip = tipFor(el);
  const wrap = (g, text, x, y, maxW) => {
    const words = text.split(' '); const lines = []; let cur = '';
    const t = g.append('text').attr('font-size', 12).attr('font-weight', 600);
    words.forEach((w) => { const next = cur ? cur + ' ' + w : w; t.text(next); if (t.node().getComputedTextLength() > maxW && cur) { lines.push(cur); cur = w; } else cur = next; });
    lines.push(cur); t.text(null);
    lines.slice(0, 2).forEach((l, i) => t.append('tspan').attr('x', x).attr('y', y + i * 14).text(l));
  };
  nodes.forEach((n) => {
    const p = pos.get(n.node);
    let value = n.affected, unit = n.unit, note = n.note;
    if (n.node === 'client') { value = o.reached_clients; unit = 'clients reached'; note = o.root_cause_found; }
    if (n.node === 'review') { value = o.caught; unit = sbMethod === 'Risk-based' ? `caught at min ${o.first_catch_min}` : `caught (of ${o.inspected})`; }
    const g = svg.append('g').attr('transform', `translate(${p.x},${p.y})`);
    const harm = n.state === 'root' || n.state === 'harm';
    g.append('rect').attr('width', nw).attr('height', nh).attr('rx', 6).style('fill', 'var(--color-bg)')
      .style('stroke', harm ? 'var(--cds-chart-status-critical)' : 'var(--color-border-line)').attr('stroke-width', harm ? 1.5 : 1);
    g.append('rect').attr('width', 4).attr('height', nh - 12).attr('x', 0).attr('y', 6).attr('rx', 2).style('fill', AREA_FILL[n.area]);
    wrap(g, n.label, 12, 18, nw - 18);
    g.append('text').attr('x', 12).attr('y', 58).attr('font-size', 17).attr('font-family', 'var(--font-anthropic-serif)').text(intf(value));
    g.append('text').attr('class', 'muted').attr('x', 12).attr('y', 72).attr('font-size', 10).text(unit.length > 20 ? unit.slice(0, 19) + '…' : unit);
    g.on('mousemove', (e) => tip.show(e, n.label, [[AREA_FILL[n.area], n.area], [null, note]])).on('mouseleave', () => tip.hide());
  });
});
/* ---------- tabs ---------- */
function openTab(name, link) {
  let found = false;
  document.querySelectorAll('[data-panel]').forEach((p) => { const on = p.dataset.panel === name; p.hidden = !on; if (on) found = true; });
  if (!found) return;
  document.querySelectorAll('.qa-tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
  if (link) dash.setLink(name);
}
document.querySelectorAll('.qa-tab').forEach((t) => t.addEventListener('click', () => openTab(t.dataset.tab, true)));
const startTab = location.hash.slice(1);
if (startTab) openTab(startTab, false);
