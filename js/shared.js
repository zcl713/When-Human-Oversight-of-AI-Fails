/*
 * shared.js
 * Everything both drafts use: number formatting, Wilson intervals, chart helpers, the slider
 * state, and the Summary blocks (verdict, headline tiles, budget curve, misses by error type,
 * snowball over time, quality over time). Draft A (lean/index.html) is the Summary on its own;
 * Draft B (index.html) shows the same blocks, condensed, on its first tab. Because both pages draw
 * from these functions, their numbers cannot drift apart.
 *
 * Definitions such as the high-impact threshold come from data/sim_meta.json, which the generator
 * writes (tools/generate_sample_data.py). Nothing here hard-codes a result.
 *
 * Percentages: every displayed percentage is a displayed integer count / the displayed denominator
 * (share()). Plotted curves may use unrounded means; text quoting them uses the counts.
 */

/* ---------- formatting ---------- */
const pct = d3.format('.0%');
const pct1 = d3.format('.1%');
const num1 = d3.format(',.1f');
const intf = d3.format(',');
/** The one way to show a percentage of counts: displayed count / displayed denominator. */
function share(count, den) { return den ? pct(count / den) : '—'; }
const METHODS = ['Random sampling', 'Risk-based'];
const ERROR_TYPES = ['Reasoning', 'Planning', 'System execution'];

/* RPN tiers for the register. The high-impact threshold is separate and comes from sim_meta.json. */
const THRESHOLDS = {critical: 60, high: 36, medium: 18};
const MAX_RPN = 125;

/** Wilson score interval for k successes in n trials (95% by default). Never the normal/CLT one. */
function wilson(k, n, z = 1.959964) {
  if (!n) return [0, 1];
  const p = k / n, den = 1 + z * z / n;
  const mid = (p + z * z / (2 * n)) / den;
  const half = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den;
  return [Math.max(0, mid - half), Math.min(1, mid + half)];
}
/** "95% range 3–9" for a [mean, lo, hi] triple from the simulation. */
function rangeText(t, unit = '') {
  if (!t) return '';
  const hi = t[2] == null ? 'no upper bound' : intf(t[2]) + unit;
  return `95% range ${intf(t[1])}${unit}–${hi}`;
}

/* ---------- colors ---------- */
const TIER_FILL = {Critical: 'var(--tier-critical)', High: 'var(--tier-high)', Medium: 'var(--tier-medium)', Low: 'var(--tier-low)'};
const METHOD_FILL = {'Risk-based': 'var(--series-1)', 'Random sampling': 'var(--cds-chart-muted)'};
const METHOD_DASH = {'Risk-based': null, 'Random sampling': '6 4'};
const OUTCOME_FILL = {caught: 'var(--outcome-caught)', reviewed_not_caught: 'var(--outcome-rnc)', never_selected: 'var(--outcome-never)'};
const OUTCOME_SW = {caught: 'o-caught', reviewed_not_caught: 'o-rnc', never_selected: 'o-never'};
const OUTCOME_LABEL = {caught: 'Caught', reviewed_not_caught: 'Reviewed but not caught', never_selected: 'Never selected for review'};
const AREA_FILL = {'System execution': 'var(--series-1)', 'Reasoning': 'var(--series-2)', 'Planning': 'var(--series-3)', 'Control layer (human review)': 'var(--series-4)'};

/* ---------- DOM and chart helpers ---------- */
function rowsOf(id) { const d = dash.data(id); return d.status === 'ok' ? d.data : null; }
function failed(id) { return dash.data(id).status === 'error'; }
function showFail(el) { const f = document.createElement('div'); f.className = 'qa-fail'; f.textContent = 'Failed to load'; el.replaceChildren(f); }
function done(el) { el.classList.remove('dash-skeleton'); }
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
function badge() { const s = document.createElement('span'); s.className = 'qa-badge'; s.textContent = 'Illustrative'; return s; }
function td(content, cls) { const c = document.createElement('td'); if (cls) c.className = cls; if (content instanceof Node) c.appendChild(content); else c.textContent = content; return c; }
function svgFor(el, W, H, label) {
  return d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', label);
}
/** Writes the plain-language text summary that sits under each chart. */
function setSummary(el, text) {
  const card = el.closest('.qa-card'); const s = card && card.querySelector('.qa-chart-summary');
  if (s) s.textContent = text;
}
function axisStyle(g) { return g.attr('font-size', null).attr('font-family', null); }
/** Shows the card's fallback legend only when the chart is too narrow for its direct labels. */
function fallbackLegend(el, show) {
  const lg = el.closest('.qa-card') && el.closest('.qa-card').querySelector('.qa-legend[data-fallback]');
  if (lg) lg.hidden = !show;
}

/* "i" info popovers are native <details>: click, Enter or Space toggles them. Escape or a click
   elsewhere closes them, and focus returns to the "i" button. */
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('details.qa-info[open]').forEach((d) => {
    const had = d.contains(document.activeElement); d.open = false; if (had) d.querySelector('summary').focus();
  });
});
document.addEventListener('click', (e) => {
  document.querySelectorAll('details.qa-info[open]').forEach((d) => { if (!d.contains(e.target)) d.open = false; });
});

/* ---------- data access and the slider state ---------- */
dash.calc('grid_index', {inputs: ['sim_grid'], fn: (grid) => new Map(grid.map((c) => [`${c.method}|${c.budget}|${c.catch_rate}`, c]))});

const view = {budget: null, catch: null};
function meta() { return rowsOf('sim_meta'); }
function settings() {
  const m = meta(); if (!m) return null;
  if (view.budget == null) { view.budget = m.default_budget; view.catch = m.default_catch_rate; }
  return view;
}
function cellFor(method, budget, rate) { const idx = rowsOf('grid_index'); return idx ? idx.get(`${method}|${budget}|${rate}`) : null; }
function current() {
  const s = settings(); if (!s) return null;
  const r = cellFor('Random sampling', s.budget, s.catch), k = cellFor('Risk-based', s.budget, s.catch);
  return r && k ? {random: r, risk: k, budget: s.budget, catch: s.catch} : null;
}
function hiLabel(m) {
  const tierNote = m.high_impact_rpn === THRESHOLDS.critical ? ', the Critical tier' : '';
  return `RPN ≥ ${m.high_impact_rpn}${tierNote}`;
}
/** Error type with the largest share of high-impact errors missed under risk-based review. */
function weakSpot(cell) {
  return ERROR_TYPES.map((t) => { const b = cell.by_type[t].hi; return {type: t, missed: b.missed[0], injected: b.injected[0], share: b.injected[0] ? b.missed[0] / b.injected[0] : 0}; })
    .sort((a, b) => b.share - a.share)[0];
}

/* Sliders: every [data-slider] input on the page shares one state. Budget is 2–30%; the catch rate
   steps through the rates precomputed in sim_grid.json. */
document.addEventListener('input', (e) => {
  const inp = e.target.closest && e.target.closest('[data-slider]'); if (!inp) return;
  const m = meta(); if (!m) return;
  if (inp.dataset.slider === 'budget') view.budget = Number(inp.value);
  else view.catch = m.catch_rates[Number(inp.value)];
  dash.refresh();
});
document.addEventListener('click', (e) => {
  const b = e.target.closest && e.target.closest('[data-reset]'); if (!b) return;
  const m = meta(); if (!m) return;
  view.budget = m.default_budget; view.catch = m.default_catch_rate; dash.refresh();
});
dash.draw('slider', (el) => {
  const m = meta(), s = settings(); if (!m) return;
  const out = el.closest('label').querySelector('output');
  if (el.dataset.slider === 'budget') {
    el.min = m.budgets[0]; el.max = m.budgets[m.budgets.length - 1]; el.step = 1; el.value = s.budget;
    const perDay = Math.round(m.outputs_per_day * s.budget / 100);
    out.textContent = `${s.budget}% (${perDay}/day)`;
    el.setAttribute('aria-valuetext', `${s.budget} percent of answers, ${perDay} reviews a day`);
  } else {
    el.min = 0; el.max = m.catch_rates.length - 1; el.step = 1; el.value = m.catch_rates.indexOf(s.catch);
    out.textContent = pct(s.catch);
    el.setAttribute('aria-valuetext', `${pct(s.catch)} reviewer catch rate`);
  }
  el.disabled = false;
});
function isDefault() { const m = meta(), s = settings(); return !!m && s.budget === m.default_budget && s.catch === m.default_catch_rate; }
/* Shown next to the snowball and quality charts only when the sliders differ from their fixed settings. */
dash.draw('defaultTag', (el) => {
  if (!meta()) return;
  el.textContent = 'Snowball and quality charts: default settings';
  el.classList.toggle('is-off', isDefault());
});
dash.draw('hiRuleText', (el) => { const m = meta(); if (m) el.textContent = hiLabel(m); });

/* ---------- verdict ---------- */
/* One sentence, at most 25 words. The weak spot and the snowball result are shown by their own blocks. */
dash.draw('verdict', (el) => {
  const c = current(); if (!c) return;
  done(el);
  const n = c.risk.hi.injected[0], rk = c.risk.hi.caught[0], rr = c.random.hi.caught[0];
  const most = c.risk.hi.missed[0] > n / 2 ? 'both miss most' : 'risk-based misses fewer than half';
  el.textContent = `At a ${c.budget}% review budget, risk-based review catches ${share(rk, n)} of high-impact errors versus ${share(rr, n)} for random sampling; ${most}.`;
});

/* ---------- headline tiles ---------- */
const TILE = {
  caught: (cell) => ({v: cell.hi.caught[0], p: share(cell.hi.caught[0], cell.hi.injected[0]), r: rangeText(cell.hi.caught)}),
  missed: (cell) => ({v: cell.hi.missed[0], p: share(cell.hi.missed[0], cell.hi.injected[0]), r: `${cell.hi.never_selected[0]} never selected, ${cell.hi.reviewed_not_caught[0]} reviewed but not caught · ${rangeText(cell.hi.missed)}`}),
  effort: (cell) => ({v: cell.minutes_per_hi_caught[0], p: '', r: rangeText(cell.minutes_per_hi_caught, ' min')}),
};
dash.draw('tile', (el) => {
  const c = current(); if (!c) return;
  const method = el.dataset.m, cell = method === 'Risk-based' ? c.risk : c.random;
  let v, p, r;
  if (el.dataset.tile === 'snowball') {
    const sb = rowsOf('snowball'); if (!sb) return;
    const o = sb.outcome.find((x) => x.method === method);
    v = o.clients_reached[0]; p = share(v, o.affected); r = rangeText(o.clients_reached);
  } else ({v, p, r} = TILE[el.dataset.tile](cell));
  done(el);
  el.querySelector('.v').textContent = v == null ? '—' : intf(v);
  el.querySelector('.p').textContent = p;
  el.querySelector('.r').textContent = r;
  el.title = `${method}: ${r}`;
});
/* Tile sub-lines: at most 5 words; the longer description is in the title (hover) text. */
dash.draw('hiN', (el) => {
  const c = current(), m = meta(); if (!c) return;
  el.textContent = `of ${c.risk.hi.injected[0]} injected`;
  el.title = `of ${c.risk.hi.injected[0]} injected high-impact errors (${hiLabel(m)})`;
});
dash.draw('sbN', (el) => {
  const sb = rowsOf('snowball'); if (!meta() || !sb) return;
  const n = intf(sb.outcome[0].affected);
  el.textContent = isDefault() ? `of ${n} affected answers` : `of ${n}, default settings`;
  el.title = `of ${n} affected answers, stale-price case at ${sb.budget_pct}% budget`;
});
dash.draw('effortN', (el) => {
  const c = current(), m = meta(); if (!c) return;
  el.textContent = 'lower is better';
  el.title = `${intf(c.risk.reviewed)} reviews at ${m.min_per_review} min each, same for both strategies`;
});

/* ---------- detection vs budget ---------- */
dash.draw('curve', (el) => {
  if (failed('sim_grid')) return showFail(el);
  const s = settings(), m = meta(), idx = rowsOf('grid_index'); el.replaceChildren(); if (!idx || !m) return; done(el);
  const W = el.clientWidth, H = el.classList.contains('tall') ? 300 : 260, top = 22, bottom = 30;
  const series = ['Risk-based', 'Random sampling'].map((name) => ({name, color: METHOD_FILL[name], dash: METHOD_DASH[name],
    pts: m.budgets.map((b) => { const c = idx.get(`${name}|${b}|${s.catch}`); return {budget: b, v: c.hi.share_caught[0], lo: c.hi.share_caught[1], hi: c.hi.share_caught[2], caught: c.hi.caught, n: c.hi.injected[0]}; })}));
  const svg = svgFor(el, W, H, 'Line chart: share of high-impact errors caught by review budget, for each strategy');
  const ymax = Math.min(1, Math.ceil((d3.max(series, (x) => d3.max(x.pts, (p) => p.hi)) || 0.1) * 10) / 10);
  const left = Math.ceil(textWidth(svg, [pct(ymax), '0%'], 11)) + 10;
  const right = W < 480 ? 12 : Math.ceil(textWidth(svg, ['Random sampling', 'Risk-based'], 12)) + 14;
  fallbackLegend(el, W < 480);
  const x = d3.scaleLinear([m.budgets[0], m.budgets[m.budgets.length - 1]], [left, W - right]);
  const y = d3.scaleLinear([0, ymax], [H - bottom, top]);
  axisStyle(svg.append('g').attr('transform', `translate(0,${H - bottom})`).call(d3.axisBottom(x).tickValues([2, 10, 20, 30]).tickFormat((d) => d + '%').tickSize(0).tickPadding(8)));
  axisStyle(svg.append('g').attr('transform', `translate(${left},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(pct).tickSize(-(W - right - left))));
  // team capacity
  const capB = m.team_capacity / m.outputs_per_day * 100;
  if (capB <= x.domain()[1]) {
    svg.append('line').attr('x1', x(capB)).attr('x2', x(capB)).attr('y1', top).attr('y2', H - bottom).style('stroke', 'var(--cds-chart-reference)').attr('stroke-dasharray', '2 3');
    svg.append('text').attr('class', 'muted').attr('x', x(capB) + 4).attr('y', H - bottom - 6).attr('font-size', 11).text(`team capacity (${m.team_capacity}/day)`);
  }
  const area = d3.area().x((d) => x(d.budget)).y0((d) => y(d.lo)).y1((d) => y(d.hi));
  const line = d3.line().x((d) => x(d.budget)).y((d) => y(d.v));
  series.forEach((sr) => {
    svg.append('path').datum(sr.pts).attr('d', area).style('fill', sr.color).style('fill-opacity', 0.13);
    svg.append('path').datum(sr.pts).attr('d', line).style('fill', 'none').style('stroke', sr.color).attr('stroke-width', 2).attr('stroke-dasharray', sr.dash).attr('stroke-linecap', 'round');
  });
  // current budget marker
  svg.append('line').attr('x1', x(s.budget)).attr('x2', x(s.budget)).attr('y1', top).attr('y2', H - bottom).style('stroke', 'var(--color-fg)').attr('stroke-width', 1);
  svg.append('text').attr('x', x(s.budget)).attr('y', top - 8).attr('text-anchor', x(s.budget) > W - 120 ? 'end' : 'middle').attr('font-size', 11).text(`current: ${s.budget}%`);
  series.forEach((sr) => { const p = sr.pts.find((q) => q.budget === s.budget);
    svg.append('circle').attr('cx', x(s.budget)).attr('cy', y(p.v)).attr('r', 4.5).style('fill', sr.color).style('stroke', 'var(--color-panel)').attr('stroke-width', 2); });
  if (W >= 480) {
    const ends = series.map((sr) => ({y: y(sr.pts[sr.pts.length - 1].v), text: sr.name}));
    if (Math.abs(ends[0].y - ends[1].y) < 16) ends[1].y = ends[0].y + 16;
    ends.forEach((e) => svg.append('text').attr('x', W - right + 8).attr('y', e.y).attr('dy', '0.35em').attr('font-size', 12).text(e.text));
  }
  const tip = tipFor(el);
  const dots = series.map((sr) => svg.append('circle').attr('r', 4).style('fill', sr.color).style('display', 'none'));
  svg.append('rect').attr('x', left).attr('y', top).attr('width', W - right - left).attr('height', H - bottom - top).style('fill', 'transparent')
    .on('mousemove', (e) => {
      const [mx] = d3.pointer(e); const b = Math.round(Math.max(x.domain()[0], Math.min(x.domain()[1], x.invert(mx))));
      series.forEach((sr, i) => { const p = sr.pts.find((q) => q.budget === b); dots[i].attr('cx', x(b)).attr('cy', y(p.v)).style('display', null); });
      tip.show(e, `Review ${b}% of answers (${Math.round(m.outputs_per_day * b / 100)} a day)`, series.map((sr) => { const p = sr.pts.find((q) => q.budget === b);
        return [sr.color, `${sr.name}: ${share(p.caught[0], p.n)} (${p.caught[0]} of ${p.n}; 95% range ${pct(p.lo)}–${pct(p.hi)})`]; }));
    })
    .on('mouseleave', () => { tip.hide(); dots.forEach((d) => d.style('display', 'none')); });
  const at = (name) => series.find((q) => q.name === name).pts.find((q) => q.budget === s.budget);
  const peak = series[0].pts.reduce((a, b) => (b.caught[0] > a.caught[0] ? b : a));
  const atS = (name) => { const p = at(name); return `${share(p.caught[0], p.n)} (${p.caught[0]} of ${p.n})`; };
  setSummary(el, `At ${s.budget}% budget, risk-based review catches ${atS('Risk-based')} of high-impact errors and random sampling ${atS('Random sampling')}. ` +
    `Risk-based review peaks at ${share(peak.caught[0], peak.n)} (${peak.caught[0]} of ${peak.n}) near ${peak.budget}%; above the team's capacity of ${m.team_capacity} reviews a day (${num1(capB)}% budget) each review gets less careful, so more budget stops helping. Shaded bands are the middle 95% of ${m.n_runs} simulated runs.`);
});

/* ---------- misses by error type ---------- */
let hatchSeq = 0;
dash.draw('byType', (el) => {
  const c = current(); el.replaceChildren(); if (!c) return; done(el);
  const ws = weakSpot(c.risk);
  const segs = ['caught', 'reviewed_not_caught', 'never_selected'];
  const W = el.clientWidth, barH = 18, gap = 4, headH = 22, groupGap = 18;
  const groupH = headH + 2 * barH + gap;
  const H = ERROR_TYPES.length * (groupH + groupGap) + 4;
  const svg = svgFor(el, W, H, 'Stacked bar chart: high-impact errors by error type, split into caught, reviewed but not caught, and never selected, for each strategy');
  const left = Math.ceil(textWidth(svg, METHODS, 12)) + 10;
  const right = Math.ceil(textWidth(svg, ['missed 00 of 00'], 12)) + 10;
  // diagonal hatch for "reviewed, not caught" (id unique per drawing: hidden tabs keep their SVGs)
  const hatchId = `qa-hatch-${++hatchSeq}`;
  const pat = svg.append('defs').append('pattern').attr('id', hatchId).attr('patternUnits', 'userSpaceOnUse').attr('width', 6).attr('height', 6).attr('patternTransform', 'rotate(45)');
  pat.append('rect').attr('width', 6).attr('height', 6).style('fill', OUTCOME_FILL.reviewed_not_caught);
  pat.append('line').attr('x1', 0).attr('y1', 0).attr('x2', 0).attr('y2', 6).style('stroke', 'var(--outcome-hatch)').attr('stroke-width', 2.5);
  const segFill = (sg) => (sg === 'reviewed_not_caught' ? `url(#${hatchId})` : OUTCOME_FILL[sg]);
  const maxN = d3.max(ERROR_TYPES, (t) => c.risk.by_type[t].hi.injected[0]) || 1;
  const x = d3.scaleLinear([0, maxN], [left, W - right]);
  const tip = tipFor(el);
  ERROR_TYPES.forEach((t, gi) => {
    const gy = gi * (groupH + groupGap);
    const isWeak = t === ws.type;
    if (isWeak) svg.append('rect').attr('x', 0.5).attr('y', gy - 4).attr('width', W - 1).attr('height', groupH + 8).attr('rx', 6).style('fill', 'none').style('stroke', 'var(--color-weak)').attr('stroke-width', 1.5);
    const head = svg.append('text').attr('x', 6).attr('y', gy + 14).attr('font-size', 13).attr('font-weight', 600).text(t);
    if (isWeak) head.append('tspan').style('fill', 'var(--color-weak)').attr('font-weight', 600).text('  ⚠ Weak spot');
    METHODS.forEach((mth, mi) => {
      const b = (mth === 'Risk-based' ? c.risk : c.random).by_type[t].hi;
      const yy = gy + headH + mi * (barH + gap);
      svg.append('text').attr('x', left - 8).attr('y', yy + barH / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).text(mth);
      let acc = 0;
      segs.forEach((sg) => {
        const v = b[sg][0]; if (!v) return;
        svg.append('rect').attr('class', 'grow').attr('x', x(acc)).attr('y', yy).attr('width', Math.max(0, x(acc + v) - x(acc) - 2)).attr('height', barH).attr('rx', 3).style('fill', segFill(sg))
          .on('mousemove', (e) => tip.show(e, `${t} · ${mth}`, segs.map((q) => [OUTCOME_FILL[q], `${OUTCOME_LABEL[q]}: ${b[q][0]} (${rangeText(b[q])})`]))).on('mouseleave', () => tip.hide());
        acc += v;
      });
      svg.append('text').attr('class', 'muted').attr('x', x(acc) + 6).attr('y', yy + barH / 2).attr('dy', '0.35em').attr('font-size', 12).text(`missed ${b.missed[0]} of ${b.injected[0]}`);
    });
  });
  const lines = ERROR_TYPES.map((t) => { const r = c.random.by_type[t].hi, k = c.risk.by_type[t].hi;
    return `${t}: risk-based misses ${k.missed[0]} of ${k.injected[0]} (${k.never_selected[0]} never selected, ${k.reviewed_not_caught[0]} reviewed but not caught); random misses ${r.missed[0]}.`; });
  setSummary(el, `${ws.type} is the weak spot. ` + lines.join(' '));
});

/* ---------- snowball over time ---------- */
/** Minutes after which both strategies act (the mean of their medians, which the generator keeps close). */
function actMinutes(sb) {
  const t = sb.outcome.map((o) => o.detection_min).filter((v) => v != null);
  return t.length ? Math.round(d3.mean(t)) : null;
}
function clock(min) { const h = Math.floor(min / 60), m = Math.round(min % 60); return `${h}h${m ? String(m).padStart(2, '0') : ''}`; }
dash.draw('snowTime', (el) => {
  if (failed('snowball')) return showFail(el);
  const sb = rowsOf('snowball'); el.replaceChildren(); if (!sb) return; done(el);
  const W = el.clientWidth, H = el.classList.contains('tall') ? 340 : 260, top = 16, bottom = 34;
  const svg = svgFor(el, W, H, 'Line chart: cumulative affected answers and clients reached over the day, for each strategy');
  const ts = sb.series, tmax = ts[ts.length - 1].t;
  const left = Math.ceil(textWidth(svg, [intf(ts[ts.length - 1].affected)], 11)) + 10;
  const labels = ['Affected answers', 'Random sampling', 'Risk-based'];
  const right = W < 480 ? 12 : Math.ceil(textWidth(svg, labels, 12)) + 14;
  fallbackLegend(el, W < 480);
  const x = d3.scaleLinear([0, tmax], [left, W - right]);
  const y = d3.scaleLinear([0, ts[ts.length - 1].affected], [H - bottom, top]).nice();
  axisStyle(svg.append('g').attr('transform', `translate(0,${H - bottom})`).call(d3.axisBottom(x).tickValues(d3.range(0, tmax + 1, W < 480 ? 120 : 60)).tickFormat(clock).tickSize(0).tickPadding(8)));
  svg.append('text').attr('class', 'muted').attr('x', W - right).attr('y', H - 2).attr('text-anchor', 'end').attr('font-size', 11).text('time since the stale feed started');
  axisStyle(svg.append('g').attr('transform', `translate(${left},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - right - left))));
  const lines = [
    {key: 'affected', name: 'Affected answers', color: 'var(--color-fg-muted)', dash: '1 3', width: 1.5, get: (d) => d.affected},
    {key: 'random', name: 'Random sampling', color: METHOD_FILL['Random sampling'], dash: METHOD_DASH['Random sampling'], width: 2, get: (d) => d.random[0], band: (d) => d.random},
    {key: 'risk', name: 'Risk-based', color: METHOD_FILL['Risk-based'], dash: null, width: 2.5, get: (d) => d.risk[0], band: (d) => d.risk},
  ];
  lines.forEach((l) => {
    if (l.band) svg.append('path').datum(ts).attr('d', d3.area().x((d) => x(d.t)).y0((d) => y(l.band(d)[1])).y1((d) => y(l.band(d)[2]))).style('fill', l.color).style('fill-opacity', 0.12);
    svg.append('path').datum(ts).attr('d', d3.line().x((d) => x(d.t)).y((d) => y(l.get(d)))).style('fill', 'none').style('stroke', l.color).attr('stroke-width', l.width).attr('stroke-dasharray', l.dash);
  });
  const oR = sb.outcome.find((o) => o.method === 'Random sampling'), oK = sb.outcome.find((o) => o.method === 'Risk-based');
  // Both strategies act at about the same time; only risk-based finds the root cause. One marker.
  const tAct = actMinutes(sb);
  if (tAct != null) {
    svg.append('line').attr('x1', x(tAct)).attr('x2', x(tAct)).attr('y1', top).attr('y2', H - bottom).style('stroke', 'var(--color-fg-muted)').attr('stroke-dasharray', '3 3');
    const notes = W < 480 ? [`both act ≈${clock(tAct)}`, 'only risk-based blocks the feed']
      : [`both strategies act at about ${clock(tAct)}`, 'only risk-based review finds the root cause and blocks later answers'];
    notes.forEach((t, i) => svg.append('text').attr('class', 'halo').attr('x', x(tAct) + 6).attr('y', top + 10 + i * 15).attr('font-size', 11).text(t));
  }
  if (W >= 480) {
    const last = ts[ts.length - 1];
    const ends = lines.map((l) => ({y: y(l.get(last)), text: `${l.name} ${intf(l.get(last))}`}));
    ends.sort((a, b) => a.y - b.y); for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
    ends.forEach((e) => svg.append('text').attr('x', W - right + 8).attr('y', e.y).attr('dy', '0.35em').attr('font-size', 12).text(e.text));
  }
  const tip = tipFor(el);
  svg.append('rect').attr('x', left).attr('y', top).attr('width', W - right - left).attr('height', H - bottom - top).style('fill', 'transparent')
    .on('mousemove', (e) => {
      const [mx] = d3.pointer(e); const d = ts[d3.leastIndex(ts, (q) => Math.abs(x(q.t) - mx))];
      tip.show(e, `${clock(d.t)} after the feed went stale`, [
        [null, `Affected answers so far: ${intf(d.affected)}`],
        [METHOD_FILL['Random sampling'], `Random: ${intf(d.random[0])} reached clients (${rangeText(d.random)})`],
        [METHOD_FILL['Risk-based'], `Risk-based: ${intf(d.risk[0])} reached clients (${rangeText(d.risk)})`]]);
    }).on('mouseleave', () => tip.hide());
  setSummary(el, `Over one trading day, ${intf(oR.affected)} answers used the stale price. Both strategies act at about ${tAct} minutes. ` +
    `Random sampling fixes each caught answer as a one-off (${oR.caught[0]} caught) and never finds the shared cause, so ${intf(oR.clients_reached[0])} of ${intf(oR.affected)} (${share(oR.clients_reached[0], oR.affected)}) reached clients. ` +
    `Only risk-based review identifies the root cause and blocks later answers (${intf(oK.blocked[0])} blocked), so ${intf(oK.clients_reached[0])} of ${intf(oK.affected)} (${share(oK.clients_reached[0], oK.affected)}) reached clients.`);
});

/* ---------- quality over time ---------- */
/* Main marker and line: the estimate corrected for reviewer misses (sensitivity measured on seeded
   canaries), with its 95% interval. Hollow dot: the raw observed rate. */
dash.draw('qVal', (el) => { const q = rowsOf('quality'); if (q) el.textContent = intf(q[el.dataset.k]); });
/** "days 12–14" / "day 5" / "days 3, 7–9": alert days compressed into ranges. */
function dayList(days) {
  const runs = [];
  days.forEach((d) => { const r = runs[runs.length - 1]; if (r && d === r[1] + 1) r[1] = d; else runs.push([d, d]); });
  return (days.length > 1 ? 'days ' : 'day ') + runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(', ');
}
/** The monitor evaluation across simulated runs, for one rule. Percentages are runs / total runs. */
function evalSentence(e) {
  const med = e.median_delay_days == null ? 'never alerted'
    : `alerted in a median of ${e.median_delay_days} day${e.median_delay_days === 1 ? '' : 's'} after it starts (in the ${e.alerted_runs} runs that alert)`;
  return `false alarm before drift in ${share(e.false_alarm_runs, e.runs)} (${e.false_alarm_runs} of ${e.runs}); drift ${med}; missed within the window in ${share(e.missed_runs, e.runs)} (${e.missed_runs} of ${e.runs})`;
}
function evalParagraph(q) {
  const e = q.evaluation, c = e.calibration, ch = e.check_no_drift, old = q.previous_rules.old_2day;
  return `The limit (${pct1(q.alert_limit)}) is calibrated to a ${pct(q.false_alarm_budget)} false-alarm budget: the smallest limit at which at most ${c.allowed_runs} of ${c.runs} simulated runs without drift raise any alert (${c.alert_runs} did). ` +
    `On ${e.drift.runs} fresh runs with drift: ${evalSentence(e.drift.calibrated)}. ` +
    `A second fresh set without drift raised an alert in ${share(ch.alert_runs, ch.runs)} (${ch.alert_runs} of ${ch.runs}). ` +
    `Previous rules on the same drift runs: 3-day pooled at ${pct(q.fixed_limit)}: ${evalSentence(e.drift.pooled_fixed)}; ${old.alert_days} days in a row at ${pct(q.fixed_limit)}: ${evalSentence(e.drift.old_2day)}.`;
}
function alertSentence(q) {
  const lim = pct1(q.alert_limit), d0 = q.drift_start_day;
  const head = `On this illustrative run (fixed seed), alerts are possible from day ${q.warmup_day}, once ${q.min_canaries_for_alert} canaries have been seen. `;
  if (!q.alerts.length) return head + `The pooled ${q.window_days}-day estimate never goes above the calibrated ${lim} limit, so no alert fires: the drift is not alerted on this run.`;
  const early = q.alerts.filter((d) => d < d0);
  return head + `The pooled ${q.window_days}-day estimate is above the calibrated ${lim} limit on ${dayList(q.alerts)}` +
    (early.length ? `, including ${dayList(early)} before drift starts (a false alarm)` : '') + '; ' +
    (q.drift_alert_day ? `the drift is alerted on day ${q.drift_alert_day}, ${q.drift_alert_day - d0} days after it starts on day ${d0}.` : `the drift is not alerted on this run.`);
}
dash.draw('qLimit', (el) => { const q = rowsOf('quality'); if (q) el.textContent = pct1(q.alert_limit); });
dash.draw('qBudget', (el) => { const q = rowsOf('quality'); if (q) el.textContent = pct(q.false_alarm_budget); });
dash.draw('qEval', (el) => { const q = rowsOf('quality'); if (q) el.textContent = evalParagraph(q); });
dash.draw('quality', (el) => {
  if (failed('quality')) return showFail(el);
  const q = rowsOf('quality'); el.replaceChildren(); if (!q) return; done(el);
  const days = q.days;
  const W = el.clientWidth, H = el.classList.contains('tall') ? 300 : 250, top = 16, bottom = 30;
  const svg = svgFor(el, W, H, 'Chart: daily error rate corrected for reviewer misses with 95% intervals, the raw observed rate, the true error rate, and the alert limit');
  const ymax = Math.ceil(Math.max(d3.max(days, (d) => d.corrected_hi), d3.max(days, (d) => d.true_rate), q.alert_limit * 1.1) * 10) / 10;
  const left = Math.ceil(textWidth(svg, [pct(ymax)], 11)) + 10;
  const labels = ['Corrected estimate', 'True error rate', 'Raw rate', `${q.window_days}-day pooled`];
  const right = W < 480 ? 12 : Math.ceil(textWidth(svg, labels, 12)) + 14;
  fallbackLegend(el, W < 480);
  const x = d3.scaleLinear([0.5, days.length + 0.5], [left, W - right]);
  const y = d3.scaleLinear([0, ymax], [H - bottom, top]);
  svg.append('rect').attr('x', x(q.drift_start_day - 0.5)).attr('y', top).attr('width', x(days.length + 0.5) - x(q.drift_start_day - 0.5)).attr('height', H - bottom - top).style('fill', 'var(--cds-chart-grid)').style('fill-opacity', 0.6);
  svg.append('text').attr('class', 'muted').attr('x', x(q.drift_start_day - 0.5) + 4).attr('y', top + 12).attr('font-size', 11).text('drift starts');
  axisStyle(svg.append('g').attr('transform', `translate(0,${H - bottom})`).call(d3.axisBottom(x).tickValues(days.map((d) => d.day)).tickFormat((d) => (W >= 600 ? 'Day ' + d : String(d))).tickSize(0).tickPadding(8)));
  axisStyle(svg.append('g').attr('transform', `translate(${left},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(pct).tickSize(-(W - right - left))));
  svg.append('line').attr('x1', left).attr('x2', W - right).attr('y1', y(q.alert_limit)).attr('y2', y(q.alert_limit)).style('stroke', 'var(--color-bad)').attr('stroke-dasharray', '5 3').attr('stroke-width', 1.5);
  svg.append('text').attr('x', left + 4).attr('y', y(q.alert_limit) - 5).attr('font-size', 11).style('fill', 'var(--color-bad)').attr('class', 'halo qa-limit-label').text(`alert limit (calibrated) ${pct1(q.alert_limit)}`);
  svg.append('path').datum(days).attr('d', d3.line().x((d) => x(d.day)).y((d) => y(d.true_rate))).style('fill', 'none').style('stroke', 'var(--color-fg)').attr('stroke-width', 1.5).attr('stroke-dasharray', '6 4');
  days.forEach((d) => svg.append('line').attr('x1', x(d.day)).attr('x2', x(d.day)).attr('y1', y(d.corrected_lo)).attr('y2', y(d.corrected_hi)).style('stroke', 'var(--series-1)').attr('stroke-width', 2).attr('stroke-opacity', 0.45));
  svg.append('path').datum(days).attr('d', d3.line().x((d) => x(d.day)).y((d) => y(d.corrected))).style('fill', 'none').style('stroke', 'var(--series-1)').attr('stroke-width', 2);
  const pooled = days.filter((d) => d.pooled);
  svg.append('path').datum(pooled).attr('d', d3.line().x((d) => x(d.day)).y((d) => y(d.pooled.corrected))).style('fill', 'none').style('stroke', 'var(--series-3)').attr('stroke-width', 1.25);
  days.forEach((d) => {
    svg.append('circle').attr('cx', x(d.day) + 7).attr('cy', y(d.raw_rate)).attr('r', 3).style('fill', 'var(--color-panel)').style('stroke', 'var(--color-fg-muted)').attr('stroke-width', 1.25);
    svg.append('circle').attr('cx', x(d.day)).attr('cy', y(d.corrected)).attr('r', 4.5).style('fill', 'var(--series-1)').style('stroke', 'var(--color-panel)').attr('stroke-width', 1.5);
  });
  q.alerts.forEach((day) => svg.append('line').attr('x1', x(day)).attr('x2', x(day)).attr('y1', top).attr('y2', H - bottom).style('stroke', 'var(--color-bad)').attr('stroke-width', 1.5));
  if (q.alerts.length) {
    const ax = x(q.alerts[0]), before = ax > left + 120;
    svg.append('text').attr('x', before ? ax - 4 : ax + 4).attr('y', top + 30).attr('text-anchor', before ? 'end' : 'start').attr('font-size', 11).attr('font-weight', 600).style('fill', 'var(--color-bad)').attr('class', 'halo qa-alert-label').text(`⚠ alert: ${dayList(q.alerts)}`);
  }
  svg.append('text').attr('class', 'muted halo').attr('x', left + 4).attr('y', top + 12).attr('font-size', 11).text('one illustrative run (fixed seed)');
  if (W >= 480) {
    const last = days[days.length - 1];
    const ends = [{y: y(last.corrected), text: 'Corrected estimate'}, {y: y(last.true_rate), text: 'True error rate'}, {y: y(last.raw_rate), text: 'Raw rate'}];
    if (last.pooled) ends.push({y: y(last.pooled.corrected), text: `${q.window_days}-day pooled`});
    ends.sort((a, b) => a.y - b.y); for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
    ends.forEach((e) => svg.append('text').attr('x', W - right + 8).attr('y', e.y).attr('dy', '0.35em').attr('font-size', 12).text(e.text));
  }
  const tip = tipFor(el);
  svg.append('rect').attr('x', left).attr('y', top).attr('width', W - right - left).attr('height', H - bottom - top).style('fill', 'transparent')
    .on('mousemove', (e) => {
      const [mx] = d3.pointer(e); const d = days[Math.max(0, Math.min(days.length - 1, Math.round(x.invert(mx)) - 1))];
      tip.show(e, `Day ${d.day}`, [
        ['var(--series-1)', `Corrected ${pct1(d.corrected)} (95% interval ${pct1(d.corrected_lo)}–${pct1(d.corrected_hi)})`],
        [null, `Raw: ${d.errors_found} of ${d.reviewed} = ${pct1(d.errors_found / d.reviewed)}`],
        [null, `Canaries caught: ${d.canaries_caught} of ${d.canaries_seen} to date (${share(d.canaries_caught, d.canaries_seen)})`],
        ['var(--series-3)', d.pooled ? `${q.window_days}-day pooled ${pct1(d.pooled.corrected)} (${d.pooled.found} of ${d.pooled.reviewed} found, days ${d.pooled.first_day}–${d.day})` : 'Warm-up: too few canaries for an alert'],
        [null, `True rate ${pct1(d.true_rate)}`]]);
    }).on('mouseleave', () => tip.hide());
  const last = days[days.length - 1];
  setSummary(el, `Each day ${q.slice} answers are reviewed at random; ${q.canary_per_day} of them are seeded known errors (canaries). ` +
    `The estimate is corrected for reviewer misses: the error rate in the other ${q.real_per_day} reviews divided by the share of canaries caught so far (${last.canaries_caught} of ${last.canaries_seen}, ${share(last.canaries_caught, last.canaries_seen)}, by day ${last.day}). ` +
    `The true rate rises from day ${q.drift_start_day}. ${alertSentence(q)} Early-day intervals are wide because few canaries have been seen. ${evalParagraph(q)}`);
});
dash.draw('qualityTable', (el) => {
  const q = rowsOf('quality'); el.replaceChildren(); if (!q) return;
  q.days.forEach((d) => {
    const status = q.alerts.includes(d.day) ? (d.day < q.drift_start_day ? '⚠ Alert (before drift)' : '⚠ Alert') : d.pooled ? '' : 'Warm-up';
    const pooled = d.pooled ? `${pct1(d.pooled.corrected)} (${pct1(d.pooled.lo)}–${pct1(d.pooled.hi)})` : '—';
    const tr = document.createElement('tr');
    tr.append(td(String(d.day), 'num'), td(intf(d.reviewed), 'num'), td(intf(d.errors_found), 'num'), td(pct1(d.errors_found / d.reviewed), 'num'),
      td(`${d.canaries_caught} / ${d.canaries_seen}`, 'num'), td(pct1(d.corrected), 'num'), td(`${pct1(d.corrected_lo)}–${pct1(d.corrected_hi)}`, 'num'), td(pooled, 'num'), td(pct1(d.true_rate), 'num'), td(status));
    el.appendChild(tr);
  });
});
const QUALITY_HEAD = '<thead><tr><th class="num">Day</th><th class="num">Reviewed</th><th class="num">Errors found</th><th class="num">Raw rate</th><th class="num">Canaries caught / seen (to date)</th><th class="num">Corrected estimate</th><th class="num">95% interval</th><th class="num">3-day pooled (95% interval)</th><th class="num">True rate</th><th>Status</th></tr></thead>';

/* ---------- the Summary markup (static text only; every number is filled in by the draws above) ---------- */
/** "i" button that opens the method and caveat text for one chart. */
function infoHTML(title, html) {
  return `<details class="qa-info"><summary aria-label="How to read this: ${title}" title="How to read this">i</summary><div class="qa-info-pop">${html}</div></details>`;
}
const QUALITY_INFO = `<p>Each day <b data-draw="qVal" data-k="slice">—</b> answers are reviewed at random. <b data-draw="qVal" data-k="canary_per_day">—</b> of them are seeded known errors (canaries), drawn from the same error mix and reviewed the same way, so reviewers cannot tell them apart.</p>
    <p>The estimate is corrected for reviewer misses: the error rate in the other <b data-draw="qVal" data-k="real_per_day">—</b> reviews divided by the share of canaries caught so far, capped at 100%. Lines are 95% intervals; early-day intervals are wide because few canaries have been seen. Hollow dots are the raw observed rate.</p>
    <p>Trade-off: this many canaries is more than the suite's 1 in 50, so the reviewer catch rate can be estimated, but fewer real answers get reviewed.</p>
    <p>Alert rule: once at least <b data-draw="qVal" data-k="min_canaries_for_alert">—</b> canaries have been seen, errors found over the last <b data-draw="qVal" data-k="window_days">—</b> days are pooled and corrected the same way (thin line). An alert fires on any day this pooled estimate is above the calibrated limit. The dashed line is the true error rate, known only in the simulation.</p>
    <p>The chart shows one illustrative run (fixed seed). <span data-draw="qEval">—</span></p>`;
const SUMMARY_INFO = {
  curve: `<p>Share of injected high-impact errors that reviewers catch. High-impact means <b data-draw="hiRuleText">—</b>.</p>
    <p>Bands show the middle 95% of simulated runs; the vertical line marks the budget set above.</p>
    <p>The thin dotted line is the team's daily capacity; past it, reviews get less careful, so more budget stops helping.</p>`,
  byType: `<p>Each bar splits the high-impact errors of one type into caught, reviewed but not caught, and never selected for review. Errors missed = never selected + reviewed but not caught.</p>
    <p>The weak spot (red outline) is the type risk-based review misses most often.</p>`,
  snowTime: `<p>One stale fund price (test D1) feeds every answer about that fund. Both strategies act at about the same time. Random sampling fixes each catch as a one-off; only risk-based review links the clustered freshness flags to the feed, identifies the root cause and blocks later answers.</p>
    <p>The stale feed starts at 0h. Lines are averages over simulated days; bands are the middle 95%.</p>`,
  quality: QUALITY_INFO,
};
const OUTCOME_LEGEND = `<div class="qa-legend">${['caught', 'reviewed_not_caught', 'never_selected'].map((k) => `<span><i class="qa-sw ${OUTCOME_SW[k]}"></i>${{caught: 'Caught', reviewed_not_caught: 'Reviewed, not caught', never_selected: 'Never selected'}[k]}</span>`).join('')}</div>`;
function summaryHTML(mode) {
  const lean = mode === 'lean';
  const tile = (label, key, sub) => `
    <div class="qa-kpi qa-tile">
      <span class="qa-kpi-label">${label}</span>
      <dl class="qa-tile-vals">
        ${METHODS.map((m) => `<div class="qa-tile-row dash-skeleton" data-draw="tile" data-tile="${key}" data-m="${m}"><dt><i class="qa-sw thin" style="background:${METHOD_FILL[m]}"></i>${m === 'Random sampling' ? 'Random' : m}</dt><dd class="v">—</dd><dd class="p"></dd><dd class="r sr-only"></dd></div>`).join('')}
      </dl>
      <span class="qa-kpi-sub" data-draw="${sub}">—</span>
    </div>`;
  const sliders = `
    <div class="qa-sliders" role="group" aria-label="What-if settings">
      <label class="qa-slider"><span>Review budget <output>—</output></span><input type="range" data-draw="slider" data-slider="budget" disabled></label>
      <label class="qa-slider"><span>Reviewer catch rate <output>—</output></span><input type="range" data-draw="slider" data-slider="catch" disabled></label>
      <button class="qa-chip" type="button" data-reset>Reset</button>
    </div>`;
  const card = (id, title, draw, legend = '', tall = lean) => `
    <div class="qa-card" id="${id}">
      <div class="qa-titlerow"><h2>${title}</h2>${infoHTML(title, SUMMARY_INFO[draw])}</div>
      ${legend}
      <div class="qa-chart dash-skeleton${tall ? ' tall' : ''}" data-draw="${draw}"${draw === 'byType' ? ' style="min-height:0"' : ''}></div>
      <p class="qa-chart-summary"></p>
      ${draw === 'quality' ? `<div class="sr-only"><table><caption>Daily quality estimate</caption>${QUALITY_HEAD}<tbody data-draw="qualityTable"></tbody></table></div>` : ''}
    </div>`;
  // Fallback legends appear only when the chart is too narrow for its direct labels.
  const legendM = `<div class="qa-legend" data-fallback hidden><span><i class="qa-sw thin" style="background:${METHOD_FILL['Risk-based']}"></i>Risk-based (solid)</span><span><i class="qa-sw thin" style="background:${METHOD_FILL['Random sampling']}"></i>Random sampling (dashed)</span></div>`;
  const legendO = OUTCOME_LEGEND;
  const legendS = `<div class="qa-legend" data-fallback hidden><span><i class="qa-sw thin" style="background:var(--color-fg-muted)"></i>Affected answers (dotted)</span><span><i class="qa-sw thin" style="background:${METHOD_FILL['Random sampling']}"></i>Random sampling (dashed)</span><span><i class="qa-sw thin" style="background:${METHOD_FILL['Risk-based']}"></i>Risk-based (solid)</span></div>`;
  const legendQ = `<div class="qa-legend" data-fallback hidden><span><i class="qa-sw" style="background:var(--series-1);border-radius:50%"></i>Corrected estimate</span><span><i class="qa-sw" style="border:1.25px solid var(--color-fg-muted);border-radius:50%"></i>Raw rate</span><span><i class="qa-sw thin" style="background:var(--color-fg)"></i>True rate (dashed)</span><span><i class="qa-sw thin" style="background:var(--series-3)"></i>Pooled</span></div>`;
  return `
    <p class="qa-verdict dash-skeleton" data-draw="verdict">—</p>
    ${sliders}
    <div class="qa-kpis qa-tiles">
      ${tile('High-impact errors caught', 'caught', 'hiN')}
      ${tile('High-impact errors missed', 'missed', 'hiN')}
      ${tile('Review minutes per high-impact error caught', 'effort', 'effortN')}
      ${tile('Clients reached before containment', 'snowball', 'sbN')}
    </div>
    <div class="${lean ? 'qa-stack' : 'qa-grid'}">
      ${card('s-curve', 'High-impact errors caught vs budget', 'curve', legendM)}
      ${card('s-type', 'Missed high-impact errors by type', 'byType', legendO, false)}
    </div>
    <p class="qa-tag is-off" data-draw="defaultTag"></p>
    <div class="${lean ? 'qa-stack' : 'qa-grid'}">
      ${card('s-snow', 'Stale-price case: clients reached', 'snowTime', legendS)}
      ${card('s-quality', 'Corrected error rate vs true rate', 'quality', legendQ)}
    </div>`;
}
document.querySelectorAll('[data-summary-root]').forEach((root) => { root.innerHTML = summaryHTML(root.dataset.mode); });
