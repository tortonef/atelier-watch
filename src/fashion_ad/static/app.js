/* Atelier Watch dashboard: vanilla JS + ECharts, hash-routed, one render function per view. */
const $ = (s, el = document) => el.querySelector(s);
const main = $('#main');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const C = Object.fromEntries(['ink', 'ink-2', 'muted', 'grid', 'axis', 'series', 'surface', 'surface-2', 'good', 'warning', 'serious', 'critical']
  .map(n => [n, cssVar('--' + n)]));

const SEV = {
  critical: { label: 'Critical', color: C.critical },
  high: { label: 'High', color: C.serious },
  medium: { label: 'Medium', color: C.warning },
  low: { label: 'Low', color: C.muted },
};
const SEV_ORDER = ['critical', 'high', 'medium', 'low'];
const METRIC = {
  revenue: 'Revenue', units: 'Units sold', avg_discount: 'Average discount', return_rate: 'Return rate',
  unit_price: 'Unit price', quantity: 'Bulk quantity', hour: 'Off-hours sale', combination: 'Unusual combination',
};
const DETECTOR = { zscore: 'Robust z-score', isolation_forest: 'Isolation Forest' };
const JOB_COLORS = { ingest: '#3987e5', quality: '#d95926', transform: '#199e70', detect: '#c98500', notify: '#d55181' };
const JOB_NOTE = {
  ingest: 'CSV · JSON · Parquet → bronze', quality: '20 rules → quarantine', transform: 'silver → gold aggregates',
  detect: 'z-score + Isolation Forest', notify: 'LLM summary → Slack',
};

const nf = new Intl.NumberFormat('en-US');
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
const eur = v => '€' + nf.format(Math.round(v));
const pct = (v, d = 1) => (v * 100).toFixed(d) + '%';
const fmtMetric = (metric, v) => v == null ? '—'
  : metric === 'revenue' ? eur(v) : metric === 'units' ? nf.format(Math.round(v))
  : metric === 'avg_discount' ? v.toFixed(1) + '%' : metric === 'return_rate' ? pct(v)
  : metric === 'unit_price' ? '€' + v.toFixed(2) : eur(v);
const fmtDay = d => new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
const fmtTs = t => new Date(t).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
const fmtMs = ms => ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms';
const badge = (color, label) => `<span class="badge"><i style="background:${color}"></i>${esc(label)}</span>`;
const sevBadge = s => badge(SEV[s].color, SEV[s].label);
const statusBadge = s => badge({ success: C.good, sent: C.good, failed: C.critical, running: C.warning }[s] || C.muted,
  { success: 'Success', failed: 'Failed', running: 'Running', skipped: 'Skipped', sent: 'Sent to Slack', dry_run: 'Dry run (no webhook)', suppressed: 'No alert (below threshold)' }[s] || s);
const mrkdwn = t => esc(t).replace(/[*][*](.+?)[*][*]/g, '<b>$1</b>').replace(/[*](.+?)[*]/g, '<b>$1</b>').replace(/\n/g, '<br>');

async function api(path, opts) {
  const r = await fetch('/api/' + path, opts);
  if (!r.ok) throw Object.assign(new Error((await r.json().catch(() => ({}))).detail || r.statusText), { status: r.status });
  return r.json();
}

/* ---------- charts ---------- */
let charts = [];
function chart(el, option) {
  const c = echarts.init(el, null, { renderer: 'svg' });
  c.setOption({ animationDuration: 300, textStyle: { fontFamily: 'system-ui, "Segoe UI", sans-serif' }, ...option });
  charts.push(c);
  return c;
}
addEventListener('resize', () => charts.forEach(c => c.resize()));
const axis = { axisLine: { lineStyle: { color: C.axis } }, axisTick: { show: false }, axisLabel: { color: C.muted, fontSize: 11 }, splitLine: { lineStyle: { color: C.grid } } };
const tooltip = { backgroundColor: C['surface-2'], borderColor: 'rgba(255,255,255,.12)', textStyle: { color: C.ink, fontSize: 12 }, extraCssText: 'box-shadow:0 6px 20px rgba(0,0,0,.5)' };
const crosshair = { ...tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: C.muted, type: 'dashed' } } };

function hbar(el, items) {
  el.style.height = Math.max(120, items.length * 30 + 12) + 'px';
  chart(el, {
    grid: { left: 4, right: 52, top: 4, bottom: 4, containLabel: true },
    tooltip: { ...tooltip, trigger: 'item', formatter: p => `${esc(p.name)}<br><b>${nf.format(p.value)}</b>` },
    xAxis: { type: 'value', show: false },
    yAxis: { type: 'category', inverse: true, data: items.map(i => i.label), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C['ink-2'], fontSize: 12 } },
    series: [{
      type: 'bar', barWidth: 12, cursor: 'default',
      data: items.map(i => ({ value: i.value, itemStyle: { color: i.color || C.series, borderRadius: [0, 4, 4, 0] } })),
      label: { show: true, position: 'right', color: C['ink-2'], fontSize: 12, formatter: p => nf.format(p.value) },
    }],
  });
}

const page = (title, sub, right = '') =>
  `<header class="page"><div><h1>${title}</h1><p>${sub}</p></div>${right}</header>`;
const tile = (label, value, sub = '') =>
  `<div class="card tile"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;

/* ---------- views ---------- */
const views = {
  async overview() {
    const [o, list] = await Promise.all([api('overview'), api('anomalies')]);
    const k = o.kpi;
    main.innerHTML = page('Overview', 'Sales health across 10 stores and 8 product categories',
      `<span class="range">${fmtDay(k.first_day)} – ${fmtDay(k.last_day)}</span>`) + `
      <div class="stack">
        <div class="grid cols-5">
          ${tile('Rows ingested', compact.format(k.rows_ingested), `${compact.format(k.sales_rows)} clean sales in silver`)}
          ${tile('Quality rules passed', `${k.rules_passed}/${k.rules_total}`, `${nf.format(k.rows_quarantined)} rows quarantined`)}
          ${tile('Anomalies detected', nf.format(k.anomalies), `${k.recent} in the last 14 days`)}
          ${tile('Critical', nf.format(k.critical), badge(C.critical, 'needs review'))}
          ${tile('Detector recall', pct(o.eval.recall, 0), `precision ${pct(o.eval.precision, 0)} on ${o.eval.injected} seeded cases`)}
        </div>
        <div class="card">
          <h3>Daily revenue with detected anomalies <small>all stores · drag the slider to zoom</small></h3>
          <div class="legend"><span><i class="line" style="background:${C.series}"></i>Revenue</span>
            ${SEV_ORDER.map(s => `<span><i style="background:${SEV[s].color}"></i>${SEV[s].label} anomaly day</span>`).join('')}</div>
          <div class="chart" id="rev"></div>
        </div>
        <div class="grid cols-3">
          <div class="card"><h3>By severity</h3><div id="by-sev"></div></div>
          <div class="card"><h3>By signal</h3><div id="by-metric"></div></div>
          <div class="card"><h3>By store <small>store-level anomalies</small></h3><div id="by-store"></div></div>
        </div>
        <div class="card"><h3>Most recent anomalies</h3>
          <table><thead><tr><th>Date</th><th>Severity</th><th>Where</th><th>Signal</th><th>What happened</th></tr></thead>
          <tbody>${list.slice(0, 7).map(a => `<tr class="click" tabindex="0" data-id="${a.anomaly_id}"><td class="nowrap">${fmtDay(a.day)}</td>
            <td>${sevBadge(a.severity)}</td><td>${esc(a.entity_name)}</td><td>${METRIC[a.metric]}</td><td>${esc(a.description)}</td></tr>`).join('')}</tbody></table>
        </div>
      </div>`;
    main.querySelectorAll('tr.click').forEach(tr => tr.onclick = () => { selectedId = +tr.dataset.id; location.hash = 'anomalies'; });

    const rev = new Map(o.revenue.map(r => [r.day, r.revenue]));
    const days = new Map(o.anomaly_days.map(d => [d.day, d]));
    chart($('#rev'), {
      grid: { left: 8, right: 16, top: 12, bottom: 56, containLabel: true },
      tooltip: {
        ...crosshair, formatter: ps => {
          const day = ps[0].value[0], a = days.get(day);
          return `${fmtDay(day)}<br>Revenue <b>${eur(rev.get(day))}</b>` +
            (a ? `<br>${a.n} anomal${a.n > 1 ? 'ies' : 'y'} · worst: ${SEV[SEV_ORDER[a.rank]].label}` : '');
        },
      },
      xAxis: { type: 'time', ...axis, splitLine: { show: false } },
      yAxis: { type: 'value', ...axis, axisLine: { show: false }, axisLabel: { ...axis.axisLabel, formatter: v => '€' + compact.format(v) } },
      dataZoom: [{ type: 'inside', startValue: o.revenue[Math.max(0, o.revenue.length - 200)].day },
        { type: 'slider', height: 22, bottom: 10, borderColor: C.axis, backgroundColor: 'transparent', fillerColor: 'rgba(57,135,229,.15)',
          dataBackground: { lineStyle: { color: C.axis }, areaStyle: { color: C.grid } }, textStyle: { color: C.muted, fontSize: 11 },
          handleStyle: { color: C.muted }, moveHandleStyle: { color: C.axis } }],
      series: [
        { type: 'line', data: o.revenue.map(r => [r.day, r.revenue]), showSymbol: false, lineStyle: { width: 2, color: C.series }, itemStyle: { color: C.series }, z: 2 },
        ...SEV_ORDER.map((s, rank) => ({
          type: 'scatter', symbolSize: 10, z: 5 - rank, itemStyle: { color: SEV[s].color, borderColor: C.surface, borderWidth: 2 },
          data: o.anomaly_days.filter(d => d.rank === rank).map(d => [d.day, rev.get(d.day)]),
        })),
      ],
    });
    hbar($('#by-sev'), o.by_severity.map(s => ({ label: SEV[s.severity].label, value: s.n, color: SEV[s.severity].color })));
    hbar($('#by-metric'), o.by_metric.map(m => ({ label: METRIC[m.metric], value: m.n })));
    hbar($('#by-store'), o.by_store.map(s => ({ label: s.name, value: s.n })));
  },

  async anomalies() {
    const list = await api('anomalies');
    const opts = (vals, labels = {}) => vals.map(v => `<option value="${esc(v)}">${esc(labels[v]?.label || labels[v] || v)}</option>`).join('');
    main.innerHTML = page('Anomalies', 'Every signal flagged by the two detectors in the latest run') + `
      <div class="filters">
        <select id="f-sev" aria-label="Severity"><option value="">All severities</option>${opts(SEV_ORDER, SEV)}</select>
        <select id="f-det" aria-label="Detector"><option value="">All detectors</option>${opts(Object.keys(DETECTOR), DETECTOR)}</select>
        <select id="f-metric" aria-label="Signal"><option value="">All signals</option>${opts([...new Set(list.map(a => a.metric))], METRIC)}</select>
        <input id="f-q" type="search" placeholder="Search store, category, text…" aria-label="Search">
        <span class="count" id="f-count"></span>
      </div>
      <div class="grid split">
        <div class="card" style="padding:6px 8px"><div class="scroll"><table>
          <thead><tr><th>Date</th><th>Severity</th><th>Where</th><th>Signal</th><th class="num">Score</th></tr></thead><tbody id="rows"></tbody></table></div></div>
        <div class="card" id="detail" style="position:sticky;top:20px"></div>
      </div>`;
    let detailChart;
    const showDetail = async a => {
      const isTxn = a.detector === 'isolation_forest';
      const metric = isTxn ? 'revenue' : a.metric;
      const change = a.expected ? ((a.observed - a.expected) / a.expected * 100) : null;
      $('#detail').innerHTML = `
        <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
          <div><h2 style="font-size:17px">${esc(a.entity_name)} · ${METRIC[a.metric]}</h2>
          <p class="muted">${fmtDay(a.day)} · ${DETECTOR[a.detector]}${a.sale_id ? ` · sale #${a.sale_id}` : ''}</p></div>${sevBadge(a.severity)}
        </div>
        <p style="margin-top:10px">${esc(a.description)}</p>
        <div class="facts">
          <div><small>Observed</small><b>${fmtMetric(a.metric, a.observed)}</b></div>
          <div><small>Expected</small><b>${fmtMetric(a.metric, a.expected)}</b></div>
          <div><small>${isTxn ? 'Isolation score' : 'Robust z-score'}</small><b>${a.score.toFixed(2)}${change != null && !isTxn ? ` <span class="muted" style="font-size:12px;font-weight:400">(${change > 0 ? '+' : ''}${change.toFixed(0)}%)</span>` : ''}</b></div>
        </div>
        <h3 style="margin-top:14px">${isTxn ? 'Store revenue around the sale' : METRIC[metric] + ' vs expected range'} <small>±45 days</small></h3>
        <div class="legend"><span><i class="line" style="background:${C.series}"></i>Observed</span>
          <span><i class="line" style="background:${C.muted}"></i>Expected (same-weekday median)</span>
          <span><i style="background:rgba(57,135,229,.25);border-radius:2px"></i>±3σ band</span>
          <span><i style="background:${SEV[a.severity].color}"></i>Anomaly</span></div>
        <div class="chart" id="detail-chart" style="height:280px"></div>`;
      const s = await api(`series?entity=${encodeURIComponent(a.entity)}&metric=${metric}`);
      const at = s.findIndex(p => p.day === a.day);
      const win = s.slice(Math.max(0, at - 45), at + 46);
      const lower = win.map(p => p.expected == null ? null : Math.max(0, p.expected - 3 * p.sigma));
      const hidden = { type: 'line', symbol: 'none', lineStyle: { opacity: 0 }, stack: 'band', silent: true };
      if (detailChart) { detailChart.dispose(); charts = charts.filter(c => c !== detailChart); }
      detailChart = chart($('#detail-chart'), {
        grid: { left: 8, right: 16, top: 12, bottom: 8, containLabel: true },
        tooltip: {
          ...crosshair, formatter: ps => {
            const p = win[ps[0].dataIndex];
            return `${fmtDay(p.day)}<br>Observed <b>${fmtMetric(metric, p.value)}</b><br>Expected ${fmtMetric(metric, p.expected)}` +
              (p.z != null ? `<br>z-score ${p.z.toFixed(1)}` : '');
          },
        },
        xAxis: { type: 'category', data: win.map(p => p.day), ...axis, splitLine: { show: false }, boundaryGap: false,
          axisLabel: { ...axis.axisLabel, formatter: d => new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) } },
        yAxis: { type: 'value', ...axis, axisLine: { show: false }, axisLabel: { ...axis.axisLabel, formatter: v => metric === 'revenue' ? '€' + compact.format(v) : metric === 'return_rate' ? pct(v, 0) : metric === 'avg_discount' ? v + '%' : compact.format(v) } },
        series: [
          { ...hidden, data: lower },
          { ...hidden, data: win.map((p, i) => lower[i] == null ? null : p.expected + 3 * p.sigma - lower[i]), areaStyle: { color: C.series, opacity: .16 } },
          { type: 'line', data: win.map(p => p.expected), symbol: 'none', lineStyle: { width: 1.5, color: C.muted, type: 'dashed' } },
          { type: 'line', data: win.map(p => p.value), showSymbol: false, lineStyle: { width: 2, color: C.series }, itemStyle: { color: C.series },
            markPoint: { symbol: 'circle', symbolSize: 14, label: { show: false }, data: [{ coord: [a.day, win[Math.min(at, 45)].value] }],
              itemStyle: { color: SEV[a.severity].color, borderColor: C.surface, borderWidth: 2 } } },
        ],
      });
    };
    const render = () => {
      const sev = $('#f-sev').value, det = $('#f-det').value, met = $('#f-metric').value, text = $('#f-q').value.toLowerCase();
      const rows = list.filter(a => (!sev || a.severity === sev) && (!det || a.detector === det) && (!met || a.metric === met)
        && (!text || `${a.entity_name} ${a.entity} ${a.description} ${a.day}`.toLowerCase().includes(text)));
      $('#f-count').textContent = `${rows.length} of ${list.length} anomalies`;
      $('#rows').innerHTML = rows.map(a => `<tr class="click" tabindex="0" data-id="${a.anomaly_id}"><td class="nowrap">${fmtDay(a.day)}</td>
        <td>${sevBadge(a.severity)}</td><td>${esc(a.entity_name)}</td><td>${METRIC[a.metric]}</td><td class="num">${a.score.toFixed(2)}</td></tr>`).join('')
        || '<tr><td colspan="5" class="muted">No anomalies match these filters.</td></tr>';
      const pick = id => {
        const a = rows.find(r => r.anomaly_id === id) || rows[0];
        if (!a) { $('#detail').innerHTML = '<p class="muted">Nothing selected.</p>'; return; }
        selectedId = a.anomaly_id;
        $('#rows').querySelectorAll('tr').forEach(tr => tr.classList.toggle('selected', +tr.dataset.id === a.anomaly_id));
        showDetail(a);
      };
      $('#rows').querySelectorAll('tr.click').forEach(tr => {
        tr.onclick = () => pick(+tr.dataset.id);
        tr.onkeydown = e => { if (e.key === 'Enter') pick(+tr.dataset.id); };
      });
      pick(selectedId);
      $('#rows .selected')?.scrollIntoView({ block: 'nearest' });
    };
    ['f-sev', 'f-det', 'f-metric', 'f-q'].forEach(id => $('#' + id).oninput = render);
    render();
  },

  async quality() {
    const d = await api('quality');
    const failed = d.rules.filter(r => r.violations > 0);
    const quarantined = d.by_table.reduce((s, t) => s + t.n, 0);
    const checked = [...new Map(d.rules.map(r => [r.table_name, r.total_rows])).values()].reduce((a, b) => a + b, 0);
    main.innerHTML = page('Data quality', 'Integrity and quality rules evaluated on the bronze layer before anything reaches silver') + `
      <div class="stack">
        <div class="grid cols-4">
          ${tile('Rules passed', `${d.rules.length - failed.length}/${d.rules.length}`, `${failed.length} with violations`)}
          ${tile('Rows checked', compact.format(checked), 'across 4 bronze tables')}
          ${tile('Rows quarantined', nf.format(quarantined), 'blocked from the silver layer')}
          ${tile('Clean row rate', pct(1 - quarantined / checked, 3), 'rows passing every error rule')}
        </div>
        <div class="card"><h3>Rule scorecard</h3>
          <table><thead><tr><th>Rule</th><th>Type</th><th>Table</th><th>Expectation</th><th>On failure</th><th class="num">Violations</th><th>Pass rate</th><th>Status</th></tr></thead>
          <tbody>${d.rules.map(r => `<tr><td class="mono">${esc(r.rule)}</td><td><span class="chip">${r.category}</span></td>
            <td class="mono">${esc(r.table_name.replace('bronze_', ''))}</td><td>${esc(r.description)}</td>
            <td class="muted">${r.severity === 'error' ? 'quarantine' : 'warn only'}</td>
            <td class="num">${nf.format(r.violations)}</td>
            <td class="nowrap"><span class="meter"><i style="width:${Math.max(2, r.pass_rate * 100)}%"></i></span>${pct(r.pass_rate, 3)}</td>
            <td>${r.violations ? badge(r.severity === 'error' ? C.critical : C.warning, r.severity === 'error' ? '✕ Fail' : '! Warn') : badge(C.good, '✓ Pass')}</td></tr>`).join('')}</tbody></table>
        </div>
        <div class="grid cols-2">
          <div class="card"><h3>Quarantined sales rows by order month</h3><div class="chart sm" id="q-month"></div></div>
          <div class="card"><h3>Quarantined rows by source table</h3><div id="q-table"></div></div>
        </div>
        <div class="card"><h3>Quarantine sample <small>up to two rows per rule, with the original payload</small></h3>
          <div class="scroll" style="max-height:380px"><table><thead><tr><th>Table</th><th>Broken rule</th><th class="num">Row</th><th>Payload</th></tr></thead>
          <tbody>${d.sample.map(s => `<tr><td class="mono">${esc(s.table_name.replace('bronze_', ''))}</td><td class="mono">${esc(s.rule)}</td>
            <td class="num">${s.row_id}</td><td class="mono">${esc(s.payload)}</td></tr>`).join('')}</tbody></table></div>
        </div>
      </div>`;
    chart($('#q-month'), {
      grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
      tooltip: { ...tooltip, trigger: 'item', formatter: p => `${p.name}<br><b>${p.value}</b> rows quarantined` },
      xAxis: { type: 'category', data: d.by_month.map(m => m.month), ...axis, splitLine: { show: false } },
      yAxis: { type: 'value', ...axis, axisLine: { show: false }, minInterval: 1 },
      series: [{ type: 'bar', barMaxWidth: 18, data: d.by_month.map(m => m.n), itemStyle: { color: C.series, borderRadius: [4, 4, 0, 0] } }],
    });
    hbar($('#q-table'), d.by_table.map(t => ({ label: t.table_name.replace('bronze_', ''), value: t.n })));
  },

  async pipeline() {
    const runs = await api('pipeline');
    if (!runs.length) throw Object.assign(new Error('none'), { status: 404 });
    const last = runs[0], total = r => r.jobs.reduce((s, j) => s + j.duration_ms, 0);
    const maxTotal = Math.max(...runs.map(total), 1);
    main.innerHTML = page('Pipeline', 'Five jobs, run in order: a failure stops the run and skips what follows',
      `<span class="range">Run #${last.run_id} · ${fmtTs(last.started_at)} · ${fmtMs(total(last))}</span>`) + `
      <div class="stack">
        <div class="card"><h3>Latest run</h3>
          <div class="dag">${last.jobs.map((j, i) => `${i ? '<div class="arrow" aria-hidden="true">→</div>' : ''}
            <div class="node">${statusBadge(j.status)}<b>${esc(j.job)}</b><small>${JOB_NOTE[j.job]}</small>
              <div style="margin-top:10px;font-variant-numeric:tabular-nums">${fmtMs(j.duration_ms)}</div>
              <small>${j.rows_in == null ? '—' : `${compact.format(j.rows_in)} in → ${compact.format(j.rows_out)} out`}</small>
              ${j.error ? `<small style="color:${C.serious};margin-top:6px">${esc(j.error)}</small>` : ''}</div>`).join('')}</div>
        </div>
        <div class="card"><h3>Run history</h3>
          <div class="legend">${Object.entries(JOB_COLORS).map(([j, c]) => `<span><i style="background:${c};border-radius:2px"></i>${j}</span>`).join('')}</div>
          <table><thead><tr><th>Run</th><th>Started</th><th>Status</th><th class="num">Duration</th><th>Time per job</th><th class="num">Rows in</th><th class="num">Anomalies</th></tr></thead>
          <tbody>${runs.map(r => `<tr><td>#${r.run_id}</td><td class="nowrap">${fmtTs(r.started_at)}</td><td>${statusBadge(r.status)}</td>
            <td class="num">${fmtMs(total(r))}</td>
            <td><div class="jobbar" style="width:${Math.max(6, total(r) / maxTotal * 220)}px">${r.jobs.filter(j => j.duration_ms).map(j =>
              `<i title="${j.job}: ${fmtMs(j.duration_ms)}" style="flex:${j.duration_ms};background:${JOB_COLORS[j.job]}"></i>`).join('')}</div></td>
            <td class="num">${r.jobs[0]?.rows_in != null ? nf.format(r.jobs[0].rows_in) : '—'}</td>
            <td class="num">${r.jobs[3]?.rows_out != null ? nf.format(r.jobs[3].rows_out) : '—'}</td></tr>`).join('')}</tbody></table>
        </div>
      </div>`;
  },

  async alerts() {
    const { policy, items } = await api('alerts');
    const SOURCE = { template: 'Template fallback (no LLM)', rule: 'Alert rule', test: 'Manual test' };
    main.innerHTML = page('Alerts', 'What the LLM wrote for each run, exactly as posted to Slack') + `
      <div class="stack">
        <div class="card"><h3>Alert rule</h3>
          <div style="display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap">
            <div>Notify when the last <b>${policy.window_days} days</b> hold at least one anomaly of severity
              ${sevBadge(policy.min_severity)} or above.
              <div class="meta" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
                ${badge(policy.slack_configured ? C.good : C.muted, policy.slack_configured ? 'Slack webhook configured' : 'No Slack webhook: dry run')}
                <span class="chip">${policy.llm_model ? 'LLM: ' + esc(policy.llm_model) : 'No LLM: template'}</span>
                <span class="chip">ALERT_MIN_SEVERITY in .env</span></div></div>
            <div><button class="btn" id="test-alert" style="width:auto;margin:0;padding:9px 16px">Send test alert</button></div>
          </div>
        </div>
        ${items.map(n => `<div class="card"><div class="slack">
        <div class="avatar">AW</div>
        <div style="min-width:0"><div class="head"><b>Atelier Watch</b><span class="app">APP</span><span class="muted">${fmtTs(n.created_at)}</span></div>
          <div class="body">${mrkdwn(n.message)}</div>
          <div class="meta">${statusBadge(n.status)}<span class="chip">Run #${n.run_id}</span>
            <span class="chip">${n.source === 'llm' ? 'Written by ' + esc(n.model) : SOURCE[n.source] || esc(n.source)}</span><span class="chip">#${esc(n.channel)}</span></div>
        </div></div></div>`).join('') || '<div class="card empty">No alerts yet.</div>'}</div>`;
    $('#test-alert').onclick = async e => {
      e.target.disabled = true;
      await api('alerts/test', { method: 'POST' }).catch(() => {});
      route();
    };
  },

  async assistant() {
    const SUGGESTIONS = ['What happened in the last two weeks?', 'Which store has the most anomalies?',
      'Which data quality rules failed the most?', 'What should I investigate first?'];
    main.innerHTML = page('Assistant', 'Ask about the latest run: anomalies, stores, categories, data quality') + `
      <div class="card chat">
        <div id="chat-log" class="chat-log" aria-live="polite"></div>
        <div id="chat-suggest" class="chat-suggest">${SUGGESTIONS.map(q => `<button class="chip">${q}</button>`).join('')}</div>
        <form id="chat-form" class="chat-form">
          <input id="chat-input" placeholder="Ask a question about the data…" aria-label="Question" maxlength="2000" autocomplete="off">
          <button class="btn" style="width:auto;margin:0;padding:9px 18px">Send</button>
        </form>
      </div>`;
    const log = $('#chat-log'), input = $('#chat-input');
    const bubble = m => `<div class="msg ${m.role}">${m.role === 'user' ? '' : '<div class="avatar">AW</div>'}
      <div class="bubble${m.error ? ' muted' : ''}">${mrkdwn(m.content)}</div></div>`;
    const draw = pending => {
      log.innerHTML = chatHistory.map(bubble).join('') + (pending ? bubble({ role: 'assistant', content: 'Thinking…', error: true }) : '')
        || '<p class="muted" style="text-align:center;margin:40px 0">The assistant answers from the latest run: KPIs, recent anomalies and failed quality rules.</p>';
      $('#chat-suggest').style.display = chatHistory.length ? 'none' : '';  // suggestions only for an empty chat
      log.scrollTop = log.scrollHeight;
    };
    const ask = async text => {
      text = text.trim();
      if (!text) return;
      chatHistory.push({ role: 'user', content: text });
      input.value = ''; input.disabled = true; draw(true);
      try {
        const messages = chatHistory.filter(m => !m.error).map(({ role, content }) => ({ role, content }));
        const r = await api('chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages }) });
        chatHistory.push({ role: 'assistant', content: r.reply });
      } catch (e) {
        chatHistory.push({ role: 'assistant', content: e.message, error: true });
      }
      input.disabled = false; draw(); input.focus();
    };
    $('#chat-form').onsubmit = e => { e.preventDefault(); ask(input.value); };
    $('#chat-suggest').querySelectorAll('button').forEach(b => b.onclick = () => ask(b.textContent));
    draw();
  },
};

/* ---------- shell ---------- */
let selectedId = null;
let chatHistory = [];  // kept while the page is open, so switching views does not lose the conversation
async function route() {
  const name = location.hash.slice(1) in views ? location.hash.slice(1) : 'overview';
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.hash === '#' + name));
  charts.forEach(c => c.dispose()); charts = [];
  try {
    await views[name]();
  } catch (e) {
    main.innerHTML = e.status === 404
      ? `<div class="card empty"><h2>No pipeline run yet</h2><p style="margin:8px 0 16px">Generate the data, then run the pipeline to populate the dashboard.</p><button class="btn" onclick="runPipeline()">Run pipeline</button></div>`
      : `<div class="card empty"><h2>Something went wrong</h2><p class="mono" style="margin-top:8px">${esc(e.message)}</p></div>`;
  }
}

async function refreshStatus() {
  const runs = await api('pipeline').catch(() => []);
  $('#run-status').innerHTML = runs.length
    ? `${statusBadge(runs[0].status)}<div class="muted" style="font-size:12px;margin-top:2px">#${runs[0].run_id} · ${fmtTs(runs[0].started_at)}</div>` : 'Never';
}

async function runPipeline() {
  const btn = $('#run-btn');
  btn.disabled = true; btn.textContent = 'Running…';
  try {
    await api('run', { method: 'POST' });
    await refreshStatus();
    route();
  } catch (e) { $('#run-status').innerHTML = `<span style="color:${C.serious}">${esc(e.message)}</span>`; }
  btn.disabled = false; btn.textContent = 'Run pipeline';
}

$('#run-btn').onclick = runPipeline;
addEventListener('hashchange', route);
refreshStatus();
route();
