/*
 * app.js: the views only the full version (index.html) has. Shared helpers, the slider state and
 * the Summary blocks live in js/shared.js.
 */

/* ---------- calculations ---------- */
const byRank = (a, b) => b.rpn - a.rpn || b.severity - a.severity || a.id.localeCompare(b.id);
const agentOnly = (risks) => risks.filter((r) => r.is_agent);

dash.calc('kpi', {inputs: ['risks'], fn: (risks) => {
  const agent = agentOnly(risks);
  const run = risks.filter((r) => r.result !== 'Not run');
  return [{
    total: risks.length, agent: agent.length, control: risks.length - agent.length,
    high_impact: agent.filter((r) => r.high_impact).length,
    avg_rpn: Math.round(d3.mean(agent, (r) => r.rpn) * 10) / 10,
    run: run.length, failed: run.filter((r) => r.result === 'Fail').length,
  }];
}});
dash.calc('by_area_tier', {inputs: ['risks'], fn: (risks) => {
  const agent = agentOnly(risks);
  return ERROR_TYPES.flatMap((area) => ['Critical', 'High', 'Medium', 'Low'].map((tier) => ({area, tier, n: agent.filter((r) => r.area === area && r.tier === tier).length})));
}});
dash.calc('by_result', {inputs: ['risks'], fn: (risks) => ['Fail', 'Partial', 'Pass', 'Not run'].map((result) => ({result, n: risks.filter((r) => r.result === result).length}))});
dash.calc('by_category', {inputs: ['risks'], fn: (risks) => {
  const agent = agentOnly(risks);
  return [...new Set(agent.map((r) => r.category))].map((category) => {
    const g = agent.filter((r) => r.category === category);
    const run = g.filter((r) => r.result !== 'Not run');
    return {
      category, tests: g.length, run: run.length, fail: run.filter((r) => r.result === 'Fail').length,
      pass_rate: run.length ? run.filter((r) => r.result === 'Pass').length / run.length : null,
      avg_rpn: Math.round(d3.mean(g, (r) => r.rpn) * 10) / 10, max_rpn: d3.max(g, (r) => r.rpn),
    };
  });
}});
dash.calc('top10', {inputs: ['risks'], fn: (risks) => [...agentOnly(risks)].sort(byRank).slice(0, 10).map((r, i) => ({rank: i + 1, ...r}))});

/* ---------- text values ---------- */
dash.draw('thr', (el) => { el.textContent = THRESHOLDS[el.dataset.k]; });
dash.draw('maxrpn', (el) => { el.textContent = MAX_RPN; });
dash.draw('kpi', (el) => {
  const r = rowsOf('kpi'); if (!r) return; done(el);
  const v = r[0][el.dataset.field]; el.textContent = v == null ? '—' : (el.dataset.field === 'avg_rpn' ? num1 : intf)(v);
});
dash.draw('hiRule', (el) => { const m = meta(); if (m) el.textContent = `agent failure types with ${hiLabel(m)}`; });
dash.draw('hiTypes', (el) => {
  const m = meta(), risks = rowsOf('risks'); if (!m || !risks) return;
  el.textContent = m.high_impact_types.map((id) => { const r = risks.find((x) => x.id === id); return `${id} ${r.test} (${r.area}, RPN ${r.rpn})`; }).join('; ');
});
dash.draw('metaVal', (el) => { const m = meta(); if (m) el.textContent = intf(m[el.dataset.k]); });

/* ---------- register ---------- */
dash.draw('areaTier', (el) => {
  if (failed('risks')) return showFail(el);
  const rows = rowsOf('by_area_tier'); el.replaceChildren(); if (!rows) return; done(el);
  const W = el.clientWidth, rowH = 34, H = ERROR_TYPES.length * rowH + 28;
  const svg = svgFor(el, W, H, 'Stacked bar chart: agent failure types by error type and risk tier');
  const left = Math.ceil(textWidth(svg, ERROR_TYPES, 12)) + 12;
  const totals = ERROR_TYPES.map((a) => d3.sum(rows.filter((r) => r.area === a), (r) => r.n));
  const x = d3.scaleLinear([0, d3.max(totals) || 1], [left, W - 28]);
  const y = d3.scaleBand(ERROR_TYPES, [0, ERROR_TYPES.length * rowH]).padding(0.3);
  axisStyle(svg.append('g').attr('transform', `translate(0,${ERROR_TYPES.length * rowH})`).call(d3.axisBottom(x).ticks(5).tickSize(-ERROR_TYPES.length * rowH).tickFormat(d3.format('d'))));
  svg.append('g').selectAll('text').data(ERROR_TYPES).join('text').attr('x', left - 10).attr('y', (a) => y(a) + y.bandwidth() / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).text((a) => a);
  const tip = tipFor(el);
  ERROR_TYPES.forEach((a, i) => {
    let acc = 0;
    ['Critical', 'High', 'Medium', 'Low'].forEach((t) => {
      const r = rows.find((d) => d.area === a && d.tier === t); if (!r || !r.n) return;
      svg.append('rect').attr('class', 'grow').attr('x', x(acc)).attr('y', y(a)).attr('width', Math.max(0, x(acc + r.n) - x(acc) - 2)).attr('height', y.bandwidth()).attr('rx', 2).style('fill', TIER_FILL[t]).style('stroke', t === 'Low' ? 'var(--tier-low-line)' : 'none')
        .on('mousemove', (e) => tip.show(e, a, [[TIER_FILL[t], `${t}: ${r.n} failure mode${r.n === 1 ? '' : 's'}`]])).on('mouseleave', () => tip.hide());
      acc += r.n;
    });
    svg.append('text').attr('class', 'muted').attr('x', x(totals[i]) + 6).attr('y', y(a) + y.bandwidth() / 2).attr('dy', '0.35em').attr('font-size', 12).text(totals[i]);
  });
  setSummary(el, ERROR_TYPES.map((a) => `${a}: ${totals[ERROR_TYPES.indexOf(a)]} failure types, ${rows.find((r) => r.area === a && r.tier === 'Critical').n} Critical`).join('; ') + '.');
});

dash.draw('results', (el) => {
  if (failed('risks')) return showFail(el);
  const rows = rowsOf('by_result'); el.replaceChildren(); if (!rows) return; done(el);
  const fill = {Fail: 'var(--cds-chart-status-critical)', Partial: 'var(--color-warn)', Pass: 'var(--color-ok)', 'Not run': 'var(--cds-chart-grid)'};
  const W = el.clientWidth, rowH = 30, H = rows.length * rowH + 6;
  const svg = svgFor(el, W, H, 'Bar chart: test cases by illustrative sample result');
  const left = Math.ceil(textWidth(svg, rows.map((r) => r.result), 12)) + 12;
  const x = d3.scaleLinear([0, d3.max(rows, (r) => r.n) || 1], [left, W - 30]);
  const y = d3.scaleBand(rows.map((r) => r.result), [0, rows.length * rowH]).padding(0.3);
  rows.forEach((r) => {
    svg.append('text').attr('x', left - 10).attr('y', y(r.result) + y.bandwidth() / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).text(r.result);
    svg.append('rect').attr('class', 'grow').attr('x', left).attr('y', y(r.result)).attr('width', Math.max(0, x(r.n) - left)).attr('height', y.bandwidth()).attr('rx', 2).style('fill', fill[r.result]);
    svg.append('text').attr('class', 'muted').attr('x', x(r.n) + 6).attr('y', y(r.result) + y.bandwidth() / 2).attr('dy', '0.35em').attr('font-size', 12).text(r.n);
  });
  setSummary(el, 'Illustrative sample results: ' + rows.map((r) => `${r.result} ${r.n}`).join(', ') + '.');
});

dash.draw('catTable', (el) => {
  const rows = rowsOf('by_category'); el.replaceChildren(); if (!rows) return;
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    const bar = document.createElement('span'); bar.className = 'qa-bar' + (r.avg_rpn >= THRESHOLDS.critical ? ' hot' : ''); bar.style.width = Math.round(r.avg_rpn / MAX_RPN * 60) + 'px';
    const avg = document.createElement('span'); avg.append(num1(r.avg_rpn), bar);
    tr.append(td(r.category), td(intf(r.tests), 'num'), td(intf(r.run), 'num'), td(intf(r.fail), 'num'), td(r.pass_rate == null ? '—' : pct(r.pass_rate), 'num'), td(avg, 'num'), td(intf(r.max_rpn), 'num'));
    el.appendChild(tr);
  });
});

const resultPill = (r) => pill(r.result, 'r-' + r.result.split(' ')[0]);
dash.draw('top10', (el) => {
  const rows = rowsOf('top10'); el.replaceChildren(); if (!rows) return;
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.append(td(intf(r.rank), 'num'), td(r.id, 'id'), td(r.test), td(r.area, 'hide-sm'), td(intf(r.rpn), 'num'), td(pill(r.tier, 't-' + r.tier)), td(resultPill(r)), td(r.mvp ? pill(r.mvp, 'mvp') : '', 'hide-sm'));
    el.appendChild(tr);
  });
});
dash.draw('hcases', (el) => {
  const rows = rowsOf('risks'); el.replaceChildren(); if (!rows) return;
  rows.filter((r) => !r.is_agent).forEach((r) => {
    const tr = document.createElement('tr');
    tr.append(td(r.id, 'id'), td(r.test), td(intf(r.rpn), 'num'), td(r.what, 'wrap hide-sm'), td(resultPill(r)));
    el.appendChild(tr);
  });
});

/* register: search, filter, sort (page-local state) */
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
      tr.append(td(r.id, 'id'), td(r.test), td(r.area, 'hide-sm'), td(String(r.severity), 'num hide-sm'), td(String(r.likelihood), 'num hide-sm'), td(String(r.detectability), 'num hide-sm'),
        td(intf(r.rpn), 'num'), td(pill(r.tier, 't-' + r.tier)), td(resultPill(r)), td(r.what, 'wrap hide-sm'), td(r.evidence, 'wrap hide-sm'));
      el.appendChild(tr);
    });
});

/* ---------- strategy comparison ---------- */
dash.draw('burden', (el) => {
  const c = current(), m = meta(); el.replaceChildren(); if (!c) return; done(el);
  const need = (method) => m.reviews_needed_50.find((r) => r.method === method && r.catch_rate === c.catch);
  const rows = [
    {label: 'Chosen budget', v: c.risk.reviews_per_day, note: `${c.budget}% of ${m.outputs_per_day} answers a day`, fill: 'var(--color-fg-muted)'},
    ...['Risk-based', 'Random sampling'].map((meth) => { const n = need(meth);
      return {label: `Needed: ${meth}`, v: n.reviews_per_day, fill: METHOD_FILL[meth],
        note: n.reachable ? `to catch half (${n.budget_pct}% of answers)` : `not reachable: reviewing every answer catches at most ${pct(n.best_share)}`}; }),
  ];
  // label above each bar so the bars get the full width on a phone
  const W = el.clientWidth, rowH = 62, top = 20, H = rows.length * rowH + top;
  const svg = svgFor(el, W, H, 'Bar chart: daily reviews at the chosen budget and needed to catch half of high-impact errors, against team capacity');
  const left = 4;
  const x = d3.scaleLinear([0, Math.max(m.team_capacity, d3.max(rows, (r) => r.v || 0)) * 1.08], [left, W - 12]);
  rows.forEach((r, i) => {
    const yy = top + i * rowH;
    svg.append('text').attr('x', left).attr('y', yy + 10).attr('font-size', 12).attr('font-weight', 600).text(r.label);
    const by = yy + 16;
    if (r.v != null) {
      svg.append('rect').attr('class', 'grow').attr('x', left).attr('y', by).attr('width', Math.max(2, x(r.v) - left)).attr('height', 18).attr('rx', 3).style('fill', r.fill);
      const inside = x(r.v) > W - 70;
      svg.append('text').attr('x', inside ? x(r.v) - 6 : x(r.v) + 6).attr('y', by + 9).attr('dy', '0.35em').attr('text-anchor', inside ? 'end' : 'start').attr('font-size', 12).attr('font-weight', 600).style('fill', inside ? 'var(--color-panel)' : null).text(intf(r.v) + '/day');
    }
    const note = svg.append('text').attr('class', 'muted').attr('x', left).attr('y', r.v != null ? by + 32 : by + 10).attr('font-size', 11);
    // wrap the note onto a second line on narrow screens
    const words = r.note.split(' '); let line = [], ln = 0;
    let t = note.append('tspan').attr('x', left);
    words.forEach((w) => { line.push(w); t.text(line.join(' ')); if (t.node().getComputedTextLength() > W - 16 && line.length > 1) { line.pop(); t.text(line.join(' ')); line = [w]; ln += 1; t = note.append('tspan').attr('x', left).attr('dy', 13).text(w); } });
  });
  const cx = x(m.team_capacity);
  svg.append('line').attr('x1', cx).attr('x2', cx).attr('y1', top - 6).attr('y2', H - 4).style('stroke', 'var(--color-bad)').attr('stroke-dasharray', '4 3').attr('stroke-width', 1.5);
  svg.append('text').attr('x', cx).attr('y', top - 8).attr('text-anchor', cx > W - 90 ? 'end' : cx < 90 ? 'start' : 'middle').attr('font-size', 11).style('fill', 'var(--color-bad)').text(`team capacity ${m.team_capacity}/day`);
  const nk = need('Risk-based'), nr = need('Random sampling');
  const say = (n, name) => n.reachable ? `${name} would need ${intf(n.reviews_per_day)} reviews a day` : `${name} cannot reach half even reviewing every answer (at most ${pct(n.best_share)})`;
  setSummary(el, `At ${pct(c.catch)} reviewer catch rate, ${say(nk, 'risk-based review')} and ${say(nr, 'random sampling')}, against a team capacity of ${m.team_capacity}. The chosen budget is ${c.risk.reviews_per_day} reviews a day.`);
});
dash.draw('splitTable', (el) => {
  const c = current(); el.replaceChildren(); if (!c) return;
  [['Random sampling', c.random], ['Risk-based', c.risk]].forEach(([name, cell]) => {
    [['High-impact', cell.hi], ['All errors', cell.all]].forEach(([g, b]) => {
      const tr = document.createElement('tr');
      const n = (t) => { const x = td(intf(t[0]), 'num'); x.title = rangeText(t); return x; };
      tr.append(td(name), td(g), n(b.injected), n(b.caught), n(b.never_selected), n(b.reviewed_not_caught), n(b.missed));
      el.appendChild(tr);
    });
  });
});

/* ---------- snowball ---------- */
dash.draw('snowLede', (el) => {
  const sb = rowsOf('snowball'); if (!sb) return;
  const r = sb.outcome.find((o) => o.method === 'Random sampling'), k = sb.outcome.find((o) => o.method === 'Risk-based');
  el.textContent = `One stale fund price feeds ${intf(r.affected)} answers: ${intf(r.clients_reached[0])} reach clients under random sampling, ${intf(k.clients_reached[0])} under risk-based review.`;
});
let sbMethod = 'Random sampling';
document.querySelectorAll('[data-sb]').forEach((b) => b.addEventListener('click', () => {
  sbMethod = b.dataset.sb;
  document.querySelectorAll('[data-sb]').forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
  dash.draw('graph');
}));
dash.draw('graph', (el) => {
  if (failed('snowball')) return showFail(el);
  const sb = rowsOf('snowball'); el.replaceChildren(); if (!sb) return; done(el);
  const o = sb.outcome.find((r) => r.method === sbMethod);
  const nodes = sb.nodes.map((n) => {
    if (n.node === 'review') return {...n, value: o.caught[0], unit: `caught of ${o.reviewed[0]} reviewed`};
    if (n.node === 'client') return {...n, value: o.clients_reached[0], unit: 'clients reached',
      note: o.root_cause_found ? `Root cause found after about ${o.detection_min} min; ${intf(o.blocked[0])} later answers blocked at the source.` : 'Each catch fixed as a one-off; the root cause is never found, so nothing is blocked.'};
    return {...n, value: n.affected};
  });
  const W = el.clientWidth, horizontal = W >= 760;
  const nw = horizontal ? Math.min(140, (W - 8 - (nodes.length - 1) * 22) / nodes.length) : W - 8, nh = horizontal ? 84 : 54, gap = horizontal ? 22 : 24;
  const H = horizontal ? nh + 8 : nodes.length * (nh + gap);
  const pos = (i) => horizontal ? {x: 4 + i * (nw + gap), y: 4} : {x: 4, y: i * (nh + gap)};
  const svg = svgFor(el, W, H, `Flow diagram of the stale-price error under ${sbMethod}`);
  nodes.forEach((n, i) => {
    if (i === 0) return;
    const a = pos(i - 1), b = pos(i);
    const stopped = sbMethod === 'Risk-based' && n.node === 'client';
    const stroke = stopped ? 'var(--cds-chart-muted)' : 'var(--cds-chart-status-critical)';
    const p = horizontal ? `M${a.x + nw},${a.y + nh / 2} L${b.x},${b.y + nh / 2}` : `M${a.x + 24},${a.y + nh} L${b.x + 24},${b.y}`;
    svg.append('path').attr('d', p).style('stroke', stroke).style('stroke-opacity', 0.6).attr('stroke-width', 3);
  });
  const tip = tipFor(el);
  nodes.forEach((n, i) => {
    const p = pos(i);
    const g = svg.append('g').attr('transform', `translate(${p.x},${p.y})`).attr('tabindex', 0).attr('role', 'img').attr('aria-label', `${n.label}: ${intf(n.value)} ${n.unit}. ${n.note}`);
    const harm = n.state === 'root' || n.state === 'harm';
    g.append('rect').attr('width', nw).attr('height', nh).attr('rx', 6).style('fill', 'var(--color-bg)')
      .style('stroke', harm ? 'var(--cds-chart-status-critical)' : 'var(--color-border-line)').attr('stroke-width', harm ? 1.5 : 1);
    g.append('rect').attr('width', 4).attr('height', nh - 12).attr('x', 0).attr('y', 6).attr('rx', 2).style('fill', AREA_FILL[n.area]);
    if (horizontal) {
      g.append('text').attr('x', 12).attr('y', 20).attr('font-size', 12).attr('font-weight', 600).text(n.label);
      g.append('text').attr('x', 12).attr('y', 52).attr('font-size', 18).attr('font-family', 'var(--font-anthropic-serif)').text(intf(n.value));
      const unit = n.unit.length > 22 ? n.unit.slice(0, 21) + '…' : n.unit;
      g.append('text').attr('class', 'muted').attr('x', 12).attr('y', 70).attr('font-size', 10).text(unit);
    } else {
      g.append('text').attr('x', 12).attr('y', 22).attr('font-size', 13).attr('font-weight', 600).text(n.label);
      g.append('text').attr('class', 'muted').attr('x', 12).attr('y', 40).attr('font-size', 11).text(n.unit);
      g.append('text').attr('x', nw - 10).attr('y', 34).attr('text-anchor', 'end').attr('font-size', 20).attr('font-family', 'var(--font-anthropic-serif)').text(intf(n.value));
    }
    g.on('mousemove', (e) => tip.show(e, n.label, [[AREA_FILL[n.area], n.area], [null, n.note]])).on('mouseleave', () => tip.hide());
  });
  setSummary(el, `${sbMethod}: ` + nodes.map((n) => `${n.label} ${intf(n.value)} ${n.unit}`).join(' → ') + '. ' + nodes[nodes.length - 1].note);
});
dash.draw('snowTable', (el) => {
  const sb = rowsOf('snowball'); el.replaceChildren(); if (!sb) return;
  sb.outcome.forEach((o) => {
    const tr = document.createElement('tr');
    const n = (t) => { const x = td(intf(t[0]), 'num'); x.title = rangeText(t); return x; };
    tr.append(td(o.method), td(intf(o.affected), 'num'), n(o.reviewed), n(o.caught), n(o.blocked), n(o.clients_reached),
      td(o.detection_min == null ? 'none' : `${o.detection_kind}, about ${o.detection_min} min (median)`));
    el.appendChild(tr);
  });
});

/* ---------- reviewer reliability ---------- */
dash.draw('reviewers', (el) => {
  if (failed('oversight')) return showFail(el);
  const rows = rowsOf('oversight'); el.replaceChildren(); if (!rows) return; done(el);
  const data = rows.map((r) => { const [lo, hi] = wilson(r.caught, r.shown); return {...r, p: r.caught / r.shown, lo, hi}; });
  const W = el.clientWidth, rowH = 46, top = 8, bottom = 26, H = data.length * rowH + top + bottom;
  const svg = svgFor(el, W, H, 'Dot plot: reviewer catch rate by condition with 95% Wilson intervals');
  const x = d3.scaleLinear([0, 1], [12, W - 16]);
  axisStyle(svg.append('g').attr('transform', `translate(0,${H - bottom})`).call(d3.axisBottom(x).ticks(5).tickFormat(pct).tickSize(-(H - bottom - top))));
  const base = data[0];
  svg.append('line').attr('x1', x(base.p)).attr('x2', x(base.p)).attr('y1', top).attr('y2', H - bottom).style('stroke', 'var(--cds-chart-reference)').attr('stroke-dasharray', '4 3');
  const tip = tipFor(el);
  data.forEach((d, i) => {
    const yy = top + i * rowH;
    svg.append('text').attr('x', 12).attr('y', yy + 12).attr('font-size', 12).text(`${d.label} (${d.linked})`);
    const cy = yy + 28;
    svg.append('line').attr('x1', x(d.lo)).attr('x2', x(d.hi)).attr('y1', cy).attr('y2', cy).style('stroke', 'var(--series-1)').attr('stroke-width', 3).attr('stroke-opacity', 0.45).attr('stroke-linecap', 'round');
    svg.append('circle').attr('cx', x(d.p)).attr('cy', cy).attr('r', 5.5).style('fill', 'var(--series-1)').style('stroke', 'var(--color-panel)').attr('stroke-width', 2);
    const right = x(d.hi) + 8 + 34 < W;
    svg.append('text').attr('class', 'muted').attr('x', right ? x(d.hi) + 8 : x(d.lo) - 8).attr('text-anchor', right ? 'start' : 'end').attr('y', cy).attr('dy', '0.35em').attr('font-size', 11).text(pct(d.p));
    svg.append('rect').attr('x', 0).attr('y', yy).attr('width', W).attr('height', rowH).style('fill', 'transparent')
      .on('mousemove', (e) => tip.show(e, d.label, [['var(--series-1)', `${d.caught} of ${d.shown} caught (${pct(d.p)})`], [null, `95% Wilson ${pct(d.lo)}–${pct(d.hi)}`]])).on('mouseleave', () => tip.hide());
  });
  const best = data.reduce((a, b) => (b.p > a.p ? b : a)), worst = data.reduce((a, b) => (b.p < a.p ? b : a));
  setSummary(el, `Baseline reviewers catch ${pct(base.p)} of planted errors. The best condition is "${best.label}" (${pct(best.p)}); the worst is "${worst.label}" (${pct(worst.p)}). Each condition has ${base.shown} planted errors, so intervals are wide.`);
});
dash.draw('reviewerTable', (el) => {
  const rows = rowsOf('oversight'); el.replaceChildren(); if (!rows) return;
  rows.forEach((r) => {
    const [lo, hi] = wilson(r.caught, r.shown);
    const tr = document.createElement('tr');
    tr.append(td(r.label), td(r.linked), td(intf(r.reviewers), 'num'), td(`${r.caught} / ${r.shown}`, 'num'), td(pct(r.caught / r.shown), 'num'), td(`${pct(lo)}–${pct(hi)}`, 'num'));
    el.appendChild(tr);
  });
});

/* ---------- quality over time ---------- */
dash.draw('qualityTable', (el) => {
  const q = rowsOf('quality'); el.replaceChildren(); if (!q) return;
  q.days.forEach((d) => {
    const [lo, hi] = wilson(d.errors_found, d.reviewed);
    const status = d.day === q.alert_day ? '⚠ Alert' : d.estimate > q.alert_limit ? 'Above limit' : '';
    const tr = document.createElement('tr');
    tr.append(td(String(d.day), 'num'), td(intf(d.reviewed), 'num'), td(intf(d.errors_found), 'num'), td(pct1(d.estimate), 'num'), td(`${pct1(lo)}–${pct1(hi)}`, 'num'), td(pct1(d.true_rate), 'num'), td(status));
    el.appendChild(tr);
  });
});

/* ---------- method ---------- */
dash.draw('assumptions', (el) => {
  const m = meta(); el.replaceChildren(); if (!m) return;
  const fmt = (v) => (typeof v === 'object' ? Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(', ') : String(v));
  m.assumptions.forEach((a) => {
    const tr = document.createElement('tr');
    tr.append(td(a.name, 'id'), td(fmt(a.value), 'num wrap-sm'), td(a.unit), td(a.comment.replace(/^ASSUMPTION[^:]*:\s*/, ''), 'wrap'));
    el.appendChild(tr);
  });
});

/* ---------- tabs ---------- */
const TAB_ALIASES = {strategy: 'strategies', reviewer: 'reviewers', oversight: 'reviewers'};
function openTab(name, link) {
  name = TAB_ALIASES[name] || name;
  let found = false;
  document.querySelectorAll('[data-panel]').forEach((p) => { const on = p.dataset.panel === name; p.hidden = !on; if (on) found = true; });
  if (!found) return;
  document.querySelectorAll('.qa-tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
  if (link) dash.setLink(name); else dash.refresh();
}
document.querySelectorAll('.qa-tab').forEach((t) => t.addEventListener('click', () => openTab(t.dataset.tab, true)));
document.querySelectorAll('[data-goto]').forEach((b) => b.addEventListener('click', () => openTab(b.dataset.goto, true)));
const startTab = location.hash.slice(1);
if (startTab) openTab(startTab, false);
window.addEventListener('hashchange', () => openTab(location.hash.slice(1), false));
