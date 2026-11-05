(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    context: null,
    mode: 'research',
    studio: new URLSearchParams(location.search).get('view') === 'strategies',
    run: null,
    points: [],
    summary: null,
    chart: 'price',
    chartCursor: null,
    chartSpan: 200,
    trace: null,
    hoverPinned: false,
    chartRunId: null,
    chartDrag: null,
    pollTimer: null,
    toastTimer: null,
  };
  let selectionVersion = 0;
  const pendingReports = new Map();

  function loadReport(id) {
    if (!pendingReports.has(id)) {
      pendingReports.set(id, api(`/api/runs/${encodeURIComponent(id)}`)
        .finally(() => pendingReports.delete(id)));
    }
    return pendingReports.get(id);
  }

  function archiveStatus(message = '') {
    $('archive-status').textContent = message;
    $('archive-status').hidden = !message;
  }

  function revealReport() {
    $('report-title').focus({preventScroll: true});
    $('report').scrollIntoView({block: 'start', behavior: 'instant'});
  }
  const svgNS = 'http://www.w3.org/2000/svg';
  const labels = { equity: 'EQUITY' };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(body.error || body.detail || `Request failed (${response.status})`);
    return body;
  }

  function showToast(message, error = false) {
    const toast = $('toast');
    toast.textContent = message;
    toast.classList.toggle('error', error);
    toast.classList.add('visible');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('visible'), 4300);
  }

  function setConnection(ok, message) {
    $('connection-label').textContent = message;
    $('connection-label').classList.toggle('offline', !ok);
  }

  function fmtDate(value, withTime = false) {
    if (!value) return '—';
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return (
      new Intl.DateTimeFormat(
        undefined,
        withTime
          ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }
          : { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' },
      ).format(date) + (withTime ? ' UTC' : '')
    );
  }

  function num(value, digits = 2) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(Number(value));
  }

  function pct(value) {
    return value === null || value === undefined ? '—' : `${num(value, 2)}%`;
  }
  function money(value, currency = quoteCurrency()) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return `${num(value, ['BTC', 'ETH'].includes(currency) ? 8 : 2)} ${currency}`;
  }
  function quoteCurrency() {
    const market = state.run?.market || $('market').value || '';
    return market.includes('/') ? market.split('/').pop() : 'quote';
  }
  function byId(id) {
    return state.context?.datasets?.find((d) => String(d.id) === String(id));
  }
  function selectedDataset() {
    return byId($('dataset').value);
  }

  function datasetTitle(d) {
    return `${d.market} · ${d.timeframe}`;
  }

  function datasetOptionLabel(d) {
    const venue = d.venue ? `${d.venue} snapshot` : 'Local';
    const range = d.venue ? ` · ${String(d.start).slice(0, 10)}—${String(d.end).slice(0, 10)}` : '';
    return `${venue} · ${d.market} · ${d.timeframe}${range} · ${d.bars} bars`;
  }

  function rememberDataset() {
    localStorage.setItem('gridline.dataset', $('dataset').value);
    localStorage.setItem('gridline.quote', $('dataset-quote').value);
  }

  function refreshDatasetOptions(selectedId = $('dataset').value, selectRequested = false) {
    const datasets = state.context?.datasets || [];
    const quotes = [...new Set(datasets.map(d => quoteCurrencyFor(d.market)))];
    const rank = quote => quote === 'BTC' ? 0 : quote === 'ETH' ? 1 : 2;
    quotes.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const filter = $('dataset-quote');
    const requested = byId(selectedId);
    const preferred = selectRequested && requested ? quoteCurrencyFor(requested.market) : filter.value || localStorage.getItem('gridline.quote');
    fillSelect(filter, quotes);
    filter.value = quotes.includes(preferred) ? preferred : quotes[0] || '';
    const select = $('dataset');
    select.replaceChildren();
    datasets.filter(d => quoteCurrencyFor(d.market) === filter.value).forEach((d) => {
      const option = document.createElement('option');
      option.value = d.id;
      option.textContent = datasetOptionLabel(d);
      select.append(option);
    });
    if (requested && quoteCurrencyFor(requested.market) === filter.value) select.value = requested.id;
    rememberDataset();
  }

  async function refreshImportedDataset(dataset) {
    const context = await api('/api/context');
    state.context.datasets = context.datasets;
    const canonical = byId(dataset.id) || context.datasets.find(d =>
      d.market === dataset.market && d.timeframe === dataset.timeframe && d.venue === dataset.venue);
    refreshDatasetOptions(canonical?.id, true);
    updateDatasetMeta();
  }

  async function populateContext(context, requestedId) {
    const firstLoad = !state.context;
    const selectedId = firstLoad ? localStorage.getItem('gridline.dataset') : $('dataset').value;
    state.context = context;
    refreshDatasetOptions(selectedId);
    if (!firstLoad) updateDatasetMeta();
    if (firstLoad) {
      fillSelect($('market'), context.markets);
      fillSelect($('timeframe'), context.timeframes);
      applyConfig(context.defaults);
      await loadStrategies();
      if (state.studio && files.length) {
        $('strategy-list').value = fileValue((files.find(f=>f.filename === 'fixed_grid.py') || files[0]).filename);
        loadStrategy();
      }
      updateDatasetMeta();
    }
    renderArchive(context.runs);
    setConnection(true, 'LOCAL API READY');
    const saved = firstLoad ? Number(localStorage.getItem('gridline.selected')) : state.run?.id;
    const linked = firstLoad ? Number(new URLSearchParams(location.search).get('run')) : 0;
    const desired = requestedId ?? (linked || saved);
    const chosen = context.runs.find((r) => r.id === desired);
    const fallback = context.runs.find(r => r.id === context.sample_id) || context.runs.find(r => r.mode === 'backtest');
    if (chosen) await selectRun(chosen.id);
    else if (firstLoad && fallback) await selectRun(fallback.id);
    else if (state.run && !context.runs.some(r => r.id === state.run.id)) clearReport();
    updateModeUI();
  }

  function fillSelect(select, values) {
    select.replaceChildren();
    values.forEach((value) => {
      const o = document.createElement('option');
      o.value = value;
      o.textContent = value;
      select.append(o);
    });
  }

  function updateDatasetMeta() {
    const d = selectedDataset();
    if (!d) return;
    $('research-start-data').textContent = `${d.market} · ${d.timeframe} · ${num(d.bars, 0)} candles available · ${fmtDate(d.start)} — ${fmtDate(d.end)}`;
    const venue = d.venue ? `${d.venue} snapshot` : 'local CSV';
    $('dataset-meta').textContent =
      `${d.bars} candles · ${fmtDate(d.start)} — ${fmtDate(d.end)} · ${venue}`;
    for (const id of ['date-start', 'date-end']) {
      const input = $(id);
      input.min = d.start.slice(0, 10);
      input.max = d.end.slice(0, 10);
      if (input.value && (input.value < input.min || input.value > input.max)) input.value = '';
    }
    const live = state.mode === 'paper' && $('feed-choice').value === 'live';
    const currency = quoteCurrencyFor(live ? $('market').value : d.market);
    if (!selectedStrategy() && currency !== $('capital-unit').textContent) {
      if (fileMode()) $('file-capital').value = ['BTC', 'ETH'].includes(currency) ? 1 : state.context.defaults.capital;
      $('capital').value = ['BTC', 'ETH'].includes(currency) ? 1 : state.context.defaults.capital;
    }
    $('capital-unit').textContent = currency;
  }

  function quoteCurrencyFor(market) {
    return market?.includes('/') ? market.split('/').pop() : 'quote';
  }

  function updateModeUI() {
    const paper = state.mode === 'paper';
    $('strategy-studio').hidden = !state.studio;
    document.querySelector('.workspace').hidden = state.studio;
    for (const link of document.querySelectorAll('.jump-link')) link.hidden = state.studio;
    $('open-studio').classList.toggle('active', state.studio);
    $('studio-back').textContent = paper ? 'Back to Paper' : 'Back to Research';
    document
      .querySelectorAll('.mode-tab[data-mode]')
      .forEach((button) => button.classList.toggle('active', !state.studio && button.dataset.mode === state.mode));
    $('setup-title').textContent = paper ? 'Configure paper run' : 'Research a strategy';
    $('setup-copy').textContent = paper
      ? 'Monitor a paper feed or rehearse the strategy on recorded candles.'
      : 'Choose a strategy and a dataset to test it on.';
    const live = paper && $('feed-choice').value === 'live';
    $('dataset-group').hidden = live;
    $('history-download').hidden = live;
    $('market-group').hidden = !live;
    $('report').hidden = !state.run;
    $('research-start').hidden = paper || !!state.run;
    $('date-start').disabled = $('date-end').disabled = paper;
    updateDatasetMeta();
    $('feed-group').hidden = !paper;
    $('date-group').hidden = paper;
    $('run-button-label').textContent = paper
      ? $('feed-choice').value === 'recorded'
        ? 'Start rehearsal'
        : 'Start paper monitor'
      : 'Run backtest';
    $('form-footnote').textContent = paper
      ? $('feed-choice').value === 'recorded'
        ? 'Advance the session through captured candles at your own pace.'
        : 'Initial closed candles warm the strategy indicators. Later candles advance the paper account.'
      : 'Runs use the selected local history and configured execution costs.';
    $('paper-monitor').hidden = !paper;
    if (paper && state.run?.mode !== 'paper' && state.run?.mode !== 'rehearsal') renderMonitor(null);
  }

  async function refreshContext(requestedId) {
    try {
      const context = await api('/api/context');
      await populateContext(context, requestedId);
    } catch (error) {
      setConnection(false, 'API UNAVAILABLE');
      showToast(`Could not load Gridline data: ${error.message}`, true);
    }
  }

  function utcToday() {
    return new Date().toISOString().slice(0, 10);
  }

  function initHistoryDates() {
    const end = utcToday();
    const startDate = new Date(`${end}T00:00:00Z`);
    startDate.setUTCDate(startDate.getUTCDate() - 29);
    $('history-end').value = end;
    $('history-start').value = startDate.toISOString().slice(0, 10);
    $('history-start').max = end;
    $('history-end').max = end;
  }

  function historyCandleCount() {
    const start = $('history-start').value;
    const end = $('history-end').value;
    if (!start || !end) return null;
    const days = Math.floor((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000) + 1;
    if (!Number.isFinite(days) || days <= 0) return null;
    return days * ($('history-timeframe').value === '5m' ? 288 : 24);
  }

  function updateHistoryEstimate() {
    const days = $('history-timeframe').value === '5m' ? 104 : 1250;
    $('history-limit').textContent = `30,000 candles per snapshot · up to ${days.toLocaleString()} UTC dates. Choose an earlier end date to check older history.`;
    $('coverage-result').textContent = '';
    const count = historyCandleCount();
    const status = $('history-status');
    if (count === null) {
      status.textContent = 'Choose a valid UTC start and end date.';
      status.classList.add('error');
      $('history-button').disabled = true;
      return;
    }
    if (count > 30000) {
      status.textContent = `This range is about ${num(count, 0)} candles, over the 30,000 limit. Shorten the range or use a wider interval.`;
      status.classList.add('error');
      $('history-button').disabled = true;
      return;
    }
    status.textContent = `About ${num(count, 0)} candles · UTC dates include both endpoints.`;
    status.classList.remove('error', 'success', 'busy');
    $('history-button').disabled = false;
  }

  function selectedHistoryVenue() {
    return $('history-venue').value === 'Binance' ? 'Binance' : 'Coinbase';
  }

  function updateHistoryVenue() {
    const venue = selectedHistoryVenue();
    const marketInput = $('history-market');
    $('history-source-label').textContent = `${venue.toUpperCase()} · PUBLIC CANDLES`;
    marketInput.setAttribute('list', venue === 'Binance' ? 'binance-markets' : 'coinbase-markets');
    $('history-status').classList.remove('error', 'success', 'busy');
    $('history-status').textContent = `${venue} snapshot will be saved locally and available after reload.`;
    $('coverage-result').textContent = '';
  }

  async function downloadHistory() {
    const count = historyCandleCount();
    if (count === null || count > 30000) return updateHistoryEstimate();
    const market = $('history-market').value.trim().toUpperCase();
    const venue = selectedHistoryVenue();
    if (!/^[A-Z0-9]+\/[A-Z0-9]+$/.test(market)) {
      $('history-status').textContent = 'Enter a market as BASE/QUOTE, for example ETH/BTC.';
      $('history-status').classList.add('error');
      return;
    }
    const button = $('history-button');
    const status = $('history-status');
    button.disabled = true;
    for (const field of $('download-form').elements) field.disabled = true;
    button.classList.add('loading');
    $('history-button-label').textContent = `Fetching ${venue} candles…`;
    status.classList.remove('error', 'success');
    status.classList.add('busy');
    status.textContent = 'Request sent. Fetching the requested candles and writing a local snapshot…';
    try {
      const result = await api('/api/history', {
        method: 'POST',
        body: JSON.stringify({
          market,
          venue,
          timeframe: $('history-timeframe').value,
          start: $('history-start').value,
          end: $('history-end').value,
        }),
      });
      if (!result.dataset?.id) throw new Error('The service completed the request without returning dataset metadata. Refresh the dataset list.');
      const dataset = result.dataset;
      if (!dataset.venue) dataset.venue = venue;
      $('date-start').value = '';
      $('date-end').value = '';
      await refreshImportedDataset(dataset);
      status.classList.remove('busy', 'error');
      status.classList.add('success');
      status.textContent = `Saved ${dataset.bars} ${dataset.venue} candles · ${fmtDate(dataset.start)} — ${fmtDate(dataset.end)}. Selected for research.`;
      showToast(`${dataset.venue} snapshot saved and selected.`);
    } catch (error) {
      status.classList.remove('busy', 'success');
      status.classList.add('error');
      status.textContent = error.message === 'Unknown resource.'
        ? 'The local service does not have the historical download route available. Check that this Gridline build includes history support.'
        : error.message || 'Download failed. Check the market, dates, and interval, then try again.';
      showToast(`Historical download failed: ${error.message}`, true);
    } finally {
      for (const field of $('download-form').elements) field.disabled = false;
      button.classList.remove('loading');
      button.disabled = false;
      $('history-button-label').textContent = 'Download snapshot';
    }
  }

  let bulkRunning = false;
  let bulkStop = false;

  function lastArchiveMonth() {
    const now = new Date();
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1)).toISOString().slice(0, 7);
  }

  function bulkArchiveJobs() {
    const pairs = [...new Set($('bulk-pairs').value.trim().toUpperCase().split(/[\s,;]+/).filter(Boolean))];
    if (!pairs.length || pairs.length > 20 || pairs.some(pair => !/^[A-Z0-9]{1,20}\/[A-Z0-9]{1,20}$/.test(pair)))
      throw new Error('Enter 1–20 markets as BASE/QUOTE, for example ETH/BTC.');
    const start = $('bulk-start').value, end = $('bulk-end').value;
    const valid = value => /^\d{4}-(0[1-9]|1[0-2])$/.test(value) && value >= '2017-08';
    if (!valid(start) || !valid(end) || start > end || end > lastArchiveMonth())
      throw new Error('Choose an ordered month range from August 2017 through the last completed UTC month.');
    const index = value => Number(value.slice(0, 4)) * 12 + Number(value.slice(5)) - 1;
    const first = index(start), last = index(end);
    if (last - first >= 12) throw new Error('Choose at most 12 inclusive months per collection.');
    const jobs = [];
    for (let n = first; n <= last; n++) {
      const month = `${Math.floor(n / 12)}-${String(n % 12 + 1).padStart(2, '0')}`;
      pairs.forEach(market => jobs.push({market, month}));
    }
    return jobs;
  }

  async function collectBulkArchive(event) {
    event.preventDefault();
    if (bulkRunning) return;
    const status = $('bulk-status');
    let jobs;
    try { jobs = bulkArchiveJobs(); }
    catch (error) { status.textContent = error.message; status.className = 'history-status error'; return; }
    if (!state.context) { status.textContent = 'Wait for the local API to connect, then try again.'; return; }
    bulkRunning = true;
    bulkStop = false;
    for (const field of $('bulk-form').elements) field.disabled = true;
    $('bulk-stop').hidden = false;
    $('bulk-stop').disabled = false;
    status.className = 'history-status busy';
    const list = $('bulk-jobs');
    list.replaceChildren();
    const rows = jobs.map(job => {
      const row = document.createElement('li');
      row.textContent = `${job.market} · ${job.month} · queued`;
      list.append(row);
      return row;
    });
    let saved = 0, skipped = 0, failed = 0, completed = 0;
    try {
      for (const [i, job] of jobs.entries()) {
        if (bulkStop) break;
        status.textContent = `Collecting ${i + 1}/${jobs.length}: ${job.market} · ${job.month}…`;
        rows[i].textContent = `${job.market} · ${job.month} · downloading…`;
        try {
          const result = await api('/api/history/bulk-month', {method: 'POST', body: JSON.stringify(job)});
          const dataset = result.dataset;
          if (!dataset?.id) throw new Error('Service returned no dataset metadata.');
          await refreshImportedDataset(dataset);
          if (result.skipped) skipped++; else saved++;
          rows[i].textContent = `${job.market} · ${job.month} · ${result.skipped ? 'already saved' : 'saved'}`;
        } catch (error) {
          failed++;
          rows[i].className = 'error';
          rows[i].textContent = `${job.market} · ${job.month} · failed: ${error.message}`;
        }
        completed++;
      }
      const pending = jobs.length - completed;
      if (pending) rows.slice(completed).forEach(row => { row.textContent = row.textContent.replace('queued', 'not started'); });
      status.className = `history-status ${failed ? 'error' : 'success'}`;
      status.textContent = `${pending ? 'Stopped' : failed ? 'Finished with errors' : 'Complete'}: ${saved} saved, ${skipped} already saved, ${failed} failed${pending ? `, ${pending} not started` : ''}. Saved months are available in Dataset.`;
    } finally {
      bulkRunning = false;
      for (const field of $('bulk-form').elements) field.disabled = false;
      $('bulk-stop').hidden = true;
    }
  }

  function renderArchive(runs) {
    $('clear-backtests').disabled = !runs.some(r => r.mode === 'backtest');
    const list = $('run-list');
    list.replaceChildren();
    const sorted = [...runs].sort(
      (a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0),
    );
    $('run-count').textContent = `${sorted.length} ${sorted.length === 1 ? 'RUN' : 'RUNS'}`;
    if (!sorted.length) {
      const empty = document.createElement('div');
      empty.className = 'archive-empty';
      empty.textContent = 'No saved runs yet. Start a backtest or paper session to build your archive.';
      list.append(empty);
      return;
    }
    sorted.forEach((run) => {
      const button = document.createElement('button');
      button.className = 'archive-item';
      button.type = 'button';
      button.dataset.id = run.id;
      if (state.run && String(state.run.id) === String(run.id)) button.classList.add('selected');
      const top = document.createElement('span');
      top.className = 'archive-item-top';
      const market = document.createElement('strong');
      market.textContent = run.market || 'Unknown market';
      const mode = document.createElement('span');
      mode.className = `archive-mode ${run.mode || ''}`;
      mode.textContent =
        (run.mode === 'paper' ? 'PAPER' : run.mode === 'rehearsal' ? 'REHEARSAL' : 'BACKTEST') + ' #' + run.id;
      top.append(market, mode);
      const bottom = document.createElement('span');
      bottom.className = 'archive-item-bottom';
      const detail = document.createElement('span');
      detail.textContent = `${run.timeframe || '—'} · ${run.bars ?? '—'} bars`;
      const date = document.createElement('span');
      date.textContent = fmtDate(run.created_at);
      bottom.append(detail, date);
      button.append(top, bottom);
      button.addEventListener('click', () => selectRun(run.id, true));
      if (run.mode === 'backtest' || ['stopped','completed'].includes(run.status)) {
        const kind = run.mode === 'paper' ? 'paper session' : run.mode === 'rehearsal' ? 'rehearsal' : 'backtest';
        const card = document.createElement('div');
        card.className = 'archive-backtest';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'text-button';
        remove.textContent = `Delete #${run.id}…`;
        remove.setAttribute('aria-label', `Delete ${kind} #${run.id} · ${run.market}`);
        remove.addEventListener('click', async () => {
          if (!window.confirm(`Permanently delete ${kind} #${run.id} (${run.market}, ${run.timeframe}, ${fmtDate(run.created_at,true)}) and its fills and replay history?`)) return;
          remove.disabled = true;
          let nextId;
          try {
            await api(`/api/runs/${run.id}/delete`, {method:'POST',body:JSON.stringify({created_at:run.created_at})});
            if (state.run?.id === run.id) {
              resetDeletedSelection();
              nextId = sorted.find(other => (other.mode === 'backtest') === (run.mode === 'backtest') && other.id !== run.id)?.id;
            }
            showToast(`Deleted ${kind} #${run.id}.`);
          } catch (error) { showToast(error.message,true); }
          await refreshContext(nextId);
        });
        card.append(button, remove);
        list.append(card);
      } else list.append(button);
    });
  }

  function resetDeletedSelection() {
    clearReport();
    const url = new URL(location.href);
    url.searchParams.delete('run');
    history.replaceState(null, '', url);
    localStorage.removeItem('gridline.selected');
  }

  async function selectRun(id, fromArchive = false) {
    const version = ++selectionVersion;
    try {
      if (fromArchive && state.run?.id === id && state.run.mode === 'backtest') {
        archiveStatus();
        revealReport();
        return;
      }
      if (fromArchive) {
        clearInterval(state.pollTimer);
        archiveStatus(`Opening run #${id}…`);
      } else archiveStatus();
      const data = await loadReport(id);
      if (version !== selectionVersion) return;
      const changed = String(state.run?.id ?? '') !== String(id);
      state.run = data.run;
      state.points = data.points || [];
      loadUsdVolume(state.run);
      state.priorVolume = data.volume_before_window || [];
      if (changed || state.chartRunId !== String(state.run.id)) {
        state.trace = null;
        state.chartRunId = String(state.run.id);
        state.chartCursor = state.points.length - 1;
        state.chartSpan = Math.min(200, Math.max(20, state.points.length || 200));
      } else if (state.points.length) {
        state.chartCursor = state.chartCursor === null
          ? state.points.length - 1
          : Math.min(state.chartCursor, state.points.length - 1);
      }
      state.replayAccount = data.replay_account;
      state.summary = data.summary || {};
      state.positions = data.positions || [];
      if (changed) {
        state.mode = state.run.mode === 'backtest' ? 'research' : 'paper';
        if (state.mode === 'paper')
          $('feed-choice').value = state.run.mode === 'rehearsal' ? 'recorded' : 'live';
      }
      const saved = state.context.runs.findIndex((r) => r.id === id);
      if (saved >= 0) state.context.runs[saved] = state.run;
      const url = new URL(location.href);
      url.searchParams.set('run', id);
      history.replaceState(null, '', url);
      localStorage.setItem('gridline.selected', String(id));
      renderArchive(state.context?.runs || []);
      updateModeUI();
      renderReport();
      renderMonitor(state.run);
      archiveStatus();
      if (fromArchive) revealReport();
      schedulePoll();
    } catch (error) {
      if (version !== selectionVersion) return;
      const message = `Could not open run #${id}: ${error.message}`;
      if (fromArchive) archiveStatus(message);
      showToast(message, true);
      schedulePoll();
    }
  }

  function clearReport() {
    archiveStatus();
    ++selectionVersion;
    clearInterval(state.pollTimer);
    state.run = null;
    state.volumeKey = null;
    state.points = [];
    state.priorVolume = [];
    state.replayAccount = null;
    state.summary = null;
    state.positions = [];
    renderReport();
    renderMonitor(null);
  }

  function renderReport() {
    const run = state.run;
    $('report-title').textContent = run ? `${run.market} · ${run.timeframe}` : 'Select a run to inspect';
    $('report-subtitle').title = run?.source || '';
    const source = run?.source?.replace(
      /sha256 ([a-f0-9]{64})/,
      (_, hash) => 'sha256 ' + hash.slice(0, 10),
    );
    $('report-subtitle').textContent = run
      ? `${run.mode === 'paper' ? 'Paper monitor' : run.mode === 'rehearsal' ? 'Recorded candle rehearsal' : 'Historical backtest'} · ${source || 'local CSV'} · Started ${fmtDate(run.created_at, true)}`
      : 'The captured sample report will appear here when available.';
    $('report-id').textContent = run ? `#${run.id}` : '';
    const config = run?.config || {};
    $('run-config-line').textContent = run
      ? `${run.strategy_id ? (run.strategy_name || "Strategy #" + run.strategy_id) + " · v" + run.strategy_version : "Run settings"} · ${config.signal_model === "custom" ? "Custom entry rules" : config.signal_model === "sma-cross" ? "SMA crossover" : "Bollinger wick grid"} · ${executionLabel(config.execution_model)} · Capital ${money(config.capital)} · Size ${num(config.size_pct, 2)}% · Fee ${num(config.fee_bps, 2)} bps · Slip ${num(config.slip_bps, 2)} bps · TP/SL ${num(config.tp_pct, 2)}% / ${num(config.sl_pct, 2)}% · ${config.direction === 'long-short' ? 'Directional long & short' : 'Legacy long routing'}`
      : '';
    if (config.strategy_file) $('run-config-line').textContent = `Python · strategies/${config.strategy_file.filename} · Capital ${money(config.capital)} · Fees ${config.fee_bps} bps · Slippage ${config.slip_bps} bps`;
    renderAccount();
    renderChart();
    renderFills();
    if (window.GridlineInspect?.mount)
      window.GridlineInspect.mount($('candle-inspector'), state.points, state.run);
  }

  function renderAccount() {
    const run = state.run;
    const point = state.points[state.chartCursor];
    const replay = point && state.replayAccount;
    const summary = replay ? replay.summaries[state.chartCursor] : state.summary || {};
    $('account-as-of').textContent = replay ? 'Account at candle close · ' + fmtDate(point.ts + (run.timeframe === '1h' ? 3600 : 300), true) : '';
    const positions = replay ? replay.layers
      .filter(lot => lot.entry_ts <= point.ts && (lot.exit_ts === null || point.ts < lot.exit_ts))
      .map(lot => {
        const snapshot = {...lot, ...(lot.changes?.findLast(change => change.ts <= point.ts) || {})};
        snapshot.target = (lot.target_changes?.findLast(change => change.ts <= point.ts) || snapshot).target;
        snapshot.unrealized_pnl = (snapshot.direction === 'long' ? 1 : -1) *
          (point.close - snapshot.entry_price) * snapshot.qty - snapshot.entry_fee;
        return snapshot;
      }) : state.positions || [];
    renderPositions(positions);
    const metrics = [
      ['EQUITY', money(summary.equity)],
      ['RETURN', pct(summary.return_pct)],
      ['DRAWDOWN', pct(summary.drawdown_pct)],
      ['CLOSED TRADES', String(summary.closed_trades ?? 0)],
      ['WIN RATE', pct(summary.win_rate)],
      ['OPEN POSITIONS', String(summary.open_positions ?? 0)],
      ['FEES PAID', money(summary.fees)],
      ['CASH', money(summary.cash)],
      ['REALIZED P&L', money(summary.realized_pnl)],
      ['UNREALIZED P&L', money(summary.unrealized_pnl)],
    ];
    const primary = $('summary-strip');
    const secondary = $('secondary-strip');
    primary.replaceChildren();
    secondary.replaceChildren();
    if (!run) {
      const placeholder = document.createElement('div');
      placeholder.className = 'summary-placeholder';
      placeholder.textContent = 'Run metrics are loaded from the selected session.';
      primary.append(placeholder);
    } else {
      metrics.slice(0, 4).forEach(([label, value]) => appendMetric(primary, label, value));
      metrics.slice(4).forEach(([label, value]) => appendMetric(secondary, label, value));
    }
  }

  function appendMetric(parent, label, value) {
    const item = document.createElement('div');
    item.className = 'summary-metric';
    const l = document.createElement('span');
    l.textContent = label;
    const v = document.createElement('strong');
    v.textContent = value;
    item.append(l, v);
    parent.append(item);
    return item;
  }

  function svgEl(name, attrs = {}) {
    const el = document.createElementNS(svgNS, name);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
    return el;
  }
  // Name-based colors remain stable through replay, panning, and missing values.
  function plotColor(name) {
    let hash = 2166136261;
    for (const character of name) hash = Math.imul(hash ^ character.codePointAt(0), 16777619);
    return `hsl(${(hash >>> 0) % 360} 62% 48%)`;
  }
  function plotNames(points) {
    return [...new Set(points.flatMap(point => Object.keys(point.plots || {})))].sort();
  }
  function plotValue(point, name) {
    const value = point.plots?.[name];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
  function visibilityKey(name) {
    return JSON.stringify([state.run?.config.strategy_file?.filename || '', name]);
  }

  function indicatorVisible(name) {
    return localStorage.getItem('gridline.hidden.' + visibilityKey(name)) !== '1';
  }

  function configureIndicator(button, name) {
    const visible = indicatorVisible(name);
    button.setAttribute('aria-pressed', String(visible));
    button.title = (visible ? 'Hide ' : 'Show ') + name;
    button.onclick = () => {
      const key = 'gridline.hidden.' + visibilityKey(name);
      if (visible) localStorage.setItem(key, '1');
      else localStorage.removeItem(key);
      renderChart();
    };
  }

  const macdCache = new WeakMap();
  function macdSeries(points) {
    if (macdCache.has(points)) return macdCache.get(points);
    let fast = points[0].close, slow = fast, signal = 0;
    const series = points.map(({close}) => {
      fast += (2 / 13) * (close - fast);
      slow += (2 / 27) * (close - slow);
      const line = fast - slow;
      signal += 0.2 * (line - signal);
      return [line, signal, line - signal];
    });
    macdCache.set(points, series);
    return series;
  }

  function renderMacd(start, end, width) {
    const toggle = $('toggle-macd'), pane = $('macd-pane');
    const key = 'gridline.macd.' + visibilityKey('MACD');
    const saved = localStorage.getItem(key);
    const visible = saved === null ? state.run?.config.strategy_file?.filename === 'macd_recovery.py' : saved === '1';
    toggle.setAttribute('aria-pressed', String(visible));
    toggle.onclick = () => { localStorage.setItem(key, visible ? '0' : '1'); renderChart(); };
    pane.hidden = !visible || end < start || !state.points.length || state.chart === 'equity';
    if (pane.hidden) return;
    const svg = $('macd-chart');
    svg.replaceChildren();
    svg.setAttribute('viewBox', '0 0 ' + width + ' 100');
    const series = macdSeries(state.points);
    let bound = 0;
    for (let i = start; i <= end; i++) {
      for (const v of series[i]) bound = Math.max(bound, Math.abs(v));
    }
    const dx = (width - 68) / (end - start + 1);
    const x = i => (58 + (i - start + 0.5) * dx).toFixed(2);
    const y = v => (50 - 43 * v / (bound || 1)).toFixed(2);
    const paths = [[], [], [], []];
    for (let i = start; i <= end; i++) {
      const [line, signal, hist] = series[i];
      const cmd = i === start ? 'M' : 'L';
      paths[0].push(cmd + x(i) + ',' + y(line));
      paths[1].push(cmd + x(i) + ',' + y(signal));
      paths[hist >= 0 ? 2 : 3].push('M' + x(i) + ',50V' + y(hist));
    }
    const colors = ['var(--macd-line)', 'var(--macd-signal)', 'var(--candle-up)', 'var(--candle-down)'];
    svg.append(svgEl('line', {x1:58, x2:width-10, y1:50, y2:50, class:'chart-grid'}));
    for (const k of [2,3,0,1]) svg.append(svgEl('path', {d:paths[k].join(' '), fill:'none',
      stroke:colors[k], 'stroke-width':k < 2 ? 1.5 : Math.min(12, dx*0.8)}));
    for (const value of bound ? [bound,0,-bound] : [0]) {
      const label = svgEl('text', {x:52,y:Number(y(value))+3,'text-anchor':'end',class:'chart-tick'});
      label.textContent = value.toPrecision(2);
      svg.append(label);
    }
  }

  function renderChart() {
    const svg = $('main-chart');
    const width = Math.max(260, svg.clientWidth);
    const height = Math.max(160, svg.clientHeight);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.replaceChildren();
    resetHover();
    $('trade-trace').hidden = true;
    $('chart-y-label').textContent = state.chart === 'equity' ? labels.equity : 'PRICE';
    const allPoints = state.points;
    if (state.chartCursor === null && allPoints.length) state.chartCursor = allPoints.length - 1;
    const cursor = Math.max(-1, Math.min(allPoints.length - 1, state.chartCursor ?? -1));
    const visibleEnd = Math.max(0, cursor);
    const visibleStart = Math.max(0, visibleEnd - Math.max(20, state.chartSpan) + 1);
    const points = allPoints.slice(visibleStart, visibleEnd + 1);
    $('chart-range').textContent = points.length
      ? `${fmtDate(points[0].ts, true)}  —  ${fmtDate(points[points.length - 1].ts, true)}`
      : '—';
    $('chart-points').textContent = points.length
      ? `${visibleStart + 1}–${visibleEnd + 1} / ${allPoints.length} candles`
      : '—';
    $('chart-view-status').textContent = points.length ? `${points.length} visible · cursor ${cursor + 1}/${allPoints.length}` : 'No candles';
    $('chart-pan-earlier').disabled = cursor <= 0;
    $('chart-pan-later').disabled = cursor >= allPoints.length - 1;
    $('chart-zoom-in').disabled = state.chartSpan <= 20;
    $('chart-zoom-out').disabled = state.chartSpan >= Math.max(20, allPoints.length);
    renderMacd(visibleStart, cursor, width);
    const candles = state.chart === 'candles';
    $('price-series-label').textContent = candles ? 'OHLC' : 'Close';
    $('chart-legend').hidden = state.chart === 'equity';
    const fromFile = !!state.run?.config.strategy_file;
    $('file-exit-key').hidden = !fromFile;
    const bands = $('toggle-bands');
    bands.hidden = fromFile;
    configureIndicator(bands, 'Bollinger bands');
    const showBands = !fromFile && indicatorVisible('Bollinger bands');
    const overlays = fromFile ? plotNames(allPoints.slice(0, cursor + 1)) : [];
    const legend = $('chart-legend');
    const existing = new Map([...legend.querySelectorAll('.strategy-plot-key')].map(b => [b.dataset.plot, b]));
    for (const [name, item] of existing) if (!overlays.includes(name)) item.remove();
    for (const name of overlays) {
      const item = existing.get(name) || document.createElement('button');
      configureIndicator(item, name);
      if (existing.has(name)) continue;
      item.type = 'button';
      item.dataset.plot = name;
      item.className = 'strategy-plot-key indicator-toggle';
      const swatch = document.createElement('i');
      swatch.className = 'legend-line';
      swatch.style.borderColor = plotColor(name);
      item.append(swatch, document.createTextNode(' ' + name));
      $('chart-legend').append(item);
    }
    const visibleOverlays = overlays.filter(indicatorVisible);
    if (!points.length) {
      const t = svgEl('text', {
        x: width / 2,
        y: height / 2,
        'text-anchor': 'middle',
        class: 'chart-empty',
      });
      t.textContent = 'No plotted candles in this run';
      svg.append(t);
      return;
    }
    const key = state.chart === 'equity' ? 'equity' : 'close';
    const drawIndices = chartSampleIndices(points, key);
    const drawPoints = drawIndices.map((i) => points[i]);
    const vals = points.map((p) => p[key]).filter((v) => typeof v === 'number' && Number.isFinite(v));
    if (!vals.length) {
      const t = svgEl('text', {
        x: width / 2,
        y: height / 2,
        'text-anchor': 'middle',
        class: 'chart-empty',
      });
      t.textContent = 'Equity series is not available for this run';
      svg.append(t);
      return;
    }
    const rangeVals = [...vals];
    if (state.chart !== 'equity') {
      for (const point of points) {
        for (const name of visibleOverlays) {
          const value = plotValue(point, name);
          if (value !== null) rangeVals.push(value);
        }
      }
    }
    if (state.chart !== 'equity' && showBands)
      points.forEach((p) => {
        if (typeof p.upper === 'number' && Number.isFinite(p.upper)) rangeVals.push(p.upper);
        if (typeof p.lower === 'number' && Number.isFinite(p.lower)) rangeVals.push(p.lower);
      });
    if (state.chart !== 'equity') {
      for (const point of points) {
        if (candles) rangeVals.push(point.low, point.high);
        for (const fill of point.events) rangeVals.push(fill.price);
      }
    }
    let min = Infinity, max = -Infinity;
    for (const value of rangeVals) {
      if (Number.isFinite(value)) { min = Math.min(min, value); max = Math.max(max, value); }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) { min = 0; max = 1; }
    if (min === max) {
      const pad = Math.abs(min) * 0.01 || 1;
      min -= pad;
      max += pad;
    }
    const left = 58,
      right = width - 10,
      top = 36,
      bottom = height - 36;
    const barX = (i) => left + ((i + 0.5) * (right - left)) / points.length;
    const x = (i) => barX(drawIndices[i]);
    const y = (v) => bottom - ((v - min) * (bottom - top)) / (max - min);
    for (let i = 0; i < 5; i++) {
      const yy = top + (i * (bottom - top)) / 4;
      const line = svgEl('line', { x1: left, x2: right, y1: yy, y2: yy, class: 'chart-grid' });
      svg.append(line);
      const text = svgEl('text', { x: left - 8, y: yy + 4, 'text-anchor': 'end', class: 'chart-tick' });
      text.textContent = num(max - (i * (max - min)) / 4, max < 1 ? 6 : max >= 1000 ? 0 : 2);
      svg.append(text);
    }
    const tickDate = new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZone: 'UTC',
    });
    const ticks = Math.min(drawPoints.length, width < 440 ? 2 : 4);
    for (let t = 0; t < ticks; t++) {
      const i = Math.round((t * (drawPoints.length - 1)) / Math.max(ticks - 1, 1));
      const text = svgEl('text', {
        x: x(i),
        y: bottom + 24,
        class: 'chart-tick',
        'text-anchor': t === 0 ? 'start' : t === ticks - 1 ? 'end' : 'middle',
      });
      text.textContent = tickDate.format(new Date(drawPoints[i].ts * 1000));
      svg.append(text);
    }
    if (state.chart !== 'equity') {
      (showBands ? ['upper', 'lower'] : []).forEach((field) => {
        let d = '',
          drawing = false;
        drawPoints.forEach((p, i) => {
          const value = p[field];
          if (typeof value === 'number' && Number.isFinite(value)) {
            d += `${drawing ? 'L' : 'M'}${x(i).toFixed(1)},${y(value).toFixed(1)} `;
            drawing = true;
          } else drawing = false;
        });
        if (d)
          svg.append(
            svgEl('path', {
              d: d.trim(),
              class: field === 'upper' ? 'band-path upper-band' : 'band-path lower-band',
            }),
          );
      });
      if (candles) {
        const bodyWidth = Math.min(12, Math.max(0.5, (right - left) / points.length * 0.72));
        for (const rising of [true, false]) {
          const wicks = [], bodies = [];
          points.forEach((bar, i) => {
            if ((bar.close >= bar.open) !== rising) return;
            const cx = barX(i), cy = Math.min(y(bar.open), y(bar.close));
            const bodyHeight = Math.max(1, Math.abs(y(bar.open) - y(bar.close)));
            wicks.push(`M${cx},${y(bar.high)}V${y(bar.low)}`);
            bodies.push(`M${cx - bodyWidth / 2},${cy}h${bodyWidth}v${bodyHeight}h${-bodyWidth}Z`);
          });
          const color = rising ? 'var(--candle-up)' : 'var(--candle-down)';
          svg.append(svgEl('path', {d:wicks.join(' '), stroke:color, fill:'none', class:'candle-wicks'}),
                     svgEl('path', {d:bodies.join(' '), fill:color, class:'candle-bodies'}));
        }
      }
      const markers = points.flatMap((p, i) =>
        (p.events || [])
          .filter(
            (e) =>
              (e.kind === 'entry' || e.kind === 'exit') &&
              typeof e.price === 'number' &&
              Number.isFinite(e.price),
          )
          .map((e) => ({ e, i })),
      );
      const trace = state.trace;
      if (trace && trace.start >= visibleStart && trace.end <= visibleEnd) {
        const x1 = barX(trace.start - visibleStart), x2 = barX(trace.end - visibleStart);
        const y1 = y(trace.entry.price), y2 = y(trace.exit.price);
        svg.append(svgEl('line', {x1, y1, x2, y2, class: 'trade-connector'}));
        for (const [cx, cy] of [[x1, y1], [x2, y2]]) {
          svg.append(svgEl('circle', {cx, cy, r: 9, class: 'trade-endpoint'}));
        }
        $('trade-trace').hidden = false;
        $('trade-trace-label').textContent = `Open ${trace.entry.direction} · ${fmtDate(trace.entry.ts, true)} at ${money(trace.entry.price)} → ${fillLabel(trace.exit)} · ${fmtDate(trace.exit.ts, true)} at ${money(trace.exit.price)}`;
      }
      markers.forEach(({ e, i }) => {
        const type = fillType(e);
        const marker = svgEl('path', {
          d: markerPaths[type], transform: `translate(${barX(i)} ${y(e.price)})`,
          class: `trade-marker fill-${type}`, 'data-bar': visibleStart + i,
          'data-event': points[i].events.indexOf(e), 'aria-label': fillLabel(e),
        });
        svg.append(marker);
      });
    }
    let d = '',
      drawing = false;
    drawPoints.forEach((p, i) => {
      const value = p[key];
      if (typeof value === 'number' && Number.isFinite(value)) {
        d += `${drawing ? 'L' : 'M'}${x(i).toFixed(1)},${y(value).toFixed(1)} `;
        drawing = true;
      } else drawing = false;
    });
    if (d && !candles)
      svg.append(svgEl('path', { d, class: state.chart === 'equity' ? 'equity-path' : 'price-path' }));
    if (state.chart !== 'equity') {
      for (const name of visibleOverlays) {
        let path = '', connected = false;
        // Read every candle so sampling cannot bridge nulls or omit extrema.
        points.forEach((point, index) => {
          const value = plotValue(point, name);
          if (value === null) { connected = false; return; }
          path += `${connected ? 'L' : 'M'}${barX(index).toFixed(1)},${y(value).toFixed(1)} `;
          // Round caps make isolated, one-candle values visible too.
          if (!connected) path += 'l0.01,0 ';
          connected = true;
        });
        if (path) svg.append(svgEl('path', {
          d: path.trim(), class: 'strategy-plot-path', stroke: plotColor(name),
          fill: 'none', 'stroke-width': 1.6, 'stroke-linecap': 'round',
          'stroke-linejoin': 'round', 'pointer-events': 'none', 'aria-label': name,
        }));
      }
    }
    const cursorDrawIndex = drawIndices.indexOf(points.length - 1);
    if (cursorDrawIndex >= 0)
      svg.append(svgEl('line', { x1: x(cursorDrawIndex), x2: x(cursorDrawIndex), y1: top, y2: bottom, class: 'chart-cursor' }));
  }

  $('macd-chart').addEventListener('pointermove', describeHover);
  $('macd-chart').addEventListener('pointerleave', resetHover);

  const markerPaths = {
    long: 'M0,-6 L5,4 L-5,4 Z', short: 'M0,6 L5,-4 L-5,-4 Z',
    rule: 'M-4,-4 H4 V4 H-4 Z',
    target: 'M0,-5 L5,0 L0,5 L-5,0 Z',
    stop: 'M-5,-3 L-3,-5 L0,-2 L3,-5 L5,-3 L2,0 L5,3 L3,5 L0,2 L-3,5 L-5,3 L-2,0 Z',
  };
  function fillType(e) {
    if (e.kind === 'entry') return e.direction;
    if (e.reason === 'target' || e.reason.startsWith('tp')) return 'target';
    if (e.reason === 'stop' || e.reason.startsWith('sl')) return 'stop';
    return 'rule';
  }
  function fillLabel(e) {
    if (e.kind === 'entry') return `Open ${e.direction} · ${e.side}`;
    return `${({target:'Take profit',stop:'Stop loss',rule:'Strategy exit'})[fillType(e)]} · close ${e.direction} · ${e.side}${e.direction === 'short' ? ' to cover' : ''}`;
  }
  function matchingEntry(exit, index) {
    return state.points[index]?.events.find(f => f.kind === 'entry' &&
      (exit.lot_id == null || f.lot_id === exit.lot_id) &&
      (exit.position_id == null || f.position_id === exit.position_id) &&
      f.ts === exit.entry_ts && f.direction === exit.direction &&
      (exit.lot_id != null || exit.position_id != null || f.qty >= exit.qty));
  }
  function traceButton(exit, start, end, entry) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Show entry & exit';
    button.addEventListener('click', () => {
      state.trace = {start, end, entry, exit};
      state.chartSpan = Math.max(state.chartSpan, end - start + 6);
      if (state.chart === 'equity') document.querySelector('[data-chart="candles"]').click();
      seekInspector(end);
      renderChart();
    });
    return button;
  }
  $('clear-trace').addEventListener('click', () => { state.trace = null; renderChart(); });
  function resetHover() {
    state.hoverPinned = false;
    $('hover-readout').textContent = 'Hover to inspect. Click or tap a candle to pin its fills.';
    $('hover-guide')?.remove();
    $('chart-hover-label').hidden = true;
  }
  function describeHover(event) {
    if (!state.points.length) return;
    const svg = $('main-chart'), rect = svg.getBoundingClientRect();
    const end = state.chartCursor ?? state.points.length - 1;
    const start = Math.max(0, end - Math.max(20, state.chartSpan) + 1);
    const count = end - start + 1;
    const fraction = (event.clientX - rect.left - 58) / (rect.width - 68);
    const marker = event.target.closest?.('.trade-marker');
    const index = marker ? Number(marker.dataset.bar) : start + Math.min(count - 1, Math.max(0, Math.floor(fraction * count)));
    const bar = state.points[index];
    if (!bar) return;
    const x = 58 + ((index - start + 0.5) / count) * (rect.width - 68);
    $('hover-guide')?.remove();
    svg.append(svgEl('line', {id:'hover-guide', x1:x, x2:x, y1:32, y2:rect.height-36, class:'chart-cursor'}));
    const box = $('hover-readout');
    box.replaceChildren();
    function line(tag, value) {
      const node = document.createElement(tag); node.textContent = value; box.append(node);
    }
    line('strong', `${fmtDate(bar.ts, true)} · ${state.run.market} · ${bar.events.length} fills`);
    line('div', `Open ${money(bar.open)} · High ${money(bar.high)} · Low ${money(bar.low)} · Close ${money(bar.close)}`);
    if (!$('macd-pane').hidden) {
      const [macd, signal, hist] = macdSeries(state.points)[index];
      line('div', 'MACD ' + macd.toPrecision(5) + ' · Signal ' + signal.toPrecision(5) + ' · Histogram ' + hist.toPrecision(5));
    }
    if (!state.run.config.strategy_file) line('div', `Lower band ${money(bar.lower)} · Upper band ${money(bar.upper)} · Equity ${money(bar.equity)}`);
    if (state.run.config.strategy_file && state.chart !== 'equity') {
      for (const name of plotNames(state.points.slice(0, end + 1))) {
        const value = plotValue(bar, name);
        const item = document.createElement('div');
        const swatch = document.createElement('i');
        swatch.className = 'legend-line';
        swatch.style.borderColor = plotColor(name);
        item.append(swatch, document.createTextNode(` ${name}: ${value === null ? '—' : num(value, 6)}`));
        box.append(item);
      }
    }
    const events = [...bar.events];
    if (marker) events.unshift(...events.splice(Number(marker.dataset.event), 1));
    const badge = $('chart-hover-label');
    badge.hidden = false;
    badge.style.left = fraction > 0.5 ? '6px' : 'auto';
    badge.style.right = fraction > 0.5 ? 'auto' : '6px';
    badge.textContent = events.length
      ? `${fillLabel(events[0])} at ${money(events[0].price)}${events.length > 1 ? ' · ' + events.length + ' fills here' : ''}`
      : `${fmtDate(bar.ts, true)} · ${state.chart === 'equity' ? 'Equity ' + money(bar.equity) : 'Close ' + money(bar.close)}`;
    for (const e of events) {
      line('strong', `${fillLabel(e)} at ${money(e.price)}`);
      line('div', `Size ${num(e.qty, 8)} · Fee ${money(e.fee)}${e.kind === 'exit' ? ' · Net P&L ' + money(e.pnl) : ''}`);
      const entryIndex = e.kind === 'entry' ? index : state.points.findIndex(p => p.ts === e.entry_ts);
      const entry = e.kind === 'entry' ? e : matchingEntry(e, entryIndex);
      const signal = state.points[entryIndex - 1];
      if (e.kind === 'exit') line('div', `Layer opened ${fmtDate(e.entry_ts, true)}${entry ? ' at ' + money(entry.price) : ''}${e.reason.endsWith('_gap') ? ' · Gap exit at open' : ''}`);
      if (e.kind === 'exit' && entry) box.append(traceButton(e, entryIndex, index, entry));
      if (state.run.config.strategy_file) {
        line('div', `${state.run.config.strategy_file.filename} · ${e.label || e.reason}${e.lot_id == null ? '' : ' · lot ' + e.lot_id}`);
        if (entry && state.run.config.strategy_file.filename === 'macd_recovery.py') {
          const orderAt = i => state.points[i]?.orders?.find(o =>
            (o.action || 'entry') === 'entry' && o.label === entry.label);
          let origin = entryIndex - 1;
          const order = orderAt(origin);
          if (order) {
            while (origin > 0 && !state.points[origin].orders.some(o => o.type === 'market' && o.label === '1')) {
              const prior = orderAt(origin - 1);
              if (!prior || prior.price !== order.price || prior.type !== order.type) break;
              origin--;
            }
            line('div', order.type === 'limit' ? 'Ladder buy ' + entry.label + ' · resting limit at ' +
              money(order.price) : 'Ladder buy ' + entry.label + ' · next-open market order');
            if (state.points[origin].orders.some(o => o.type === 'market' && o.label === '1')) {
              line('div', 'Setup candle: ' + fmtDate(state.points[origin].ts, true) + ' (candle open time)');
              if (origin >= 2) {
                const values = macdSeries(state.points).slice(origin - 2, origin + 1);
                line('div', 'Setup histogram: ' + values.map(v => v[2].toPrecision(5)).join(' → '));
              }
              const jump = document.createElement('button');
              jump.type = 'button';
              jump.textContent = 'Show setup candle';
              jump.onclick = () => seekInspector(origin);
              box.append(jump);
            }
          }
        }
      } else if (entry?.reason.startsWith('grid_add')) {
        line('div', `Grid addition · ${entry.reason.replaceAll('_',' ')} · ${executionLabel('price-grid')}`);
      } else if (entry && signal && entry.reason.includes('sma_cross')) {
        line('div', `Signal candle ${fmtDate(signal.ts, true)} · close ${money(signal.close)} crossed ${entry.reason.includes('sma_cross_up') ? 'above' : 'below'} SMA ${money(signal.sma)} · ${state.run.config.direction} routing`);
      } else if (entry && signal && entry.reason.includes('custom_')) {
        const side = entry.reason.includes('custom_bull') ? 'bull' : 'bear', c = state.run.config;
        line('div', `Signal candle ${fmtDate(signal.ts, true)} · ${describeCondition(c, side)} · ${money(signal[c[side + '_price']])} ${c[side + '_op']} ${money(signal[c[side + '_level']])} · ${c.direction} routing`);
      } else if (entry && signal) {
        const upper = entry.reason.includes('upper_wick');
        line('div', `Signal candle ${fmtDate(signal.ts, true)} · ${upper ? 'high' : 'low'} ${money(upper ? signal.high : signal.low)} ${upper ? '≥ upper' : '≤ lower'} band ${money(upper ? signal.upper : signal.lower)} · ${signal.regime} SMA · ${entry.reason.endsWith('tier2') ? 'tier 2' : 'tier 1'} · ${state.run.config.direction} routing`);
      }
    }
  }

  function chartSampleIndices(points, key) {
    const maxPoints = 1800;
    if (points.length <= maxPoints) return points.map((_, i) => i);
    const bucketCount = Math.floor(maxPoints / 4);
    const selected = new Set([0, points.length - 1]);
    for (let bucket = 0; bucket < bucketCount; bucket++) {
      const start = Math.floor(bucket * points.length / bucketCount);
      const end = Math.max(start + 1, Math.floor((bucket + 1) * points.length / bucketCount));
      let minAt = -1, maxAt = -1, upperAt = -1, lowerAt = -1, eventAt = -1;
      for (let i = start; i < end; i++) {
        if (Number.isFinite(points[i][key]) && (minAt < 0 || points[i][key] < points[minAt][key])) minAt = i;
        if (Number.isFinite(points[i][key]) && (maxAt < 0 || points[i][key] > points[maxAt][key])) maxAt = i;
        if (Number.isFinite(points[i].upper) && (upperAt < 0 || points[i].upper > points[upperAt].upper)) upperAt = i;
        if (Number.isFinite(points[i].lower) && (lowerAt < 0 || points[i].lower < points[lowerAt].lower)) lowerAt = i;
        if (eventAt < 0 && (points[i].events || []).length) eventAt = i;
      }
      if (minAt >= 0) selected.add(minAt);
      if (maxAt >= 0) selected.add(maxAt);
      if (upperAt >= 0) selected.add(upperAt);
      if (lowerAt >= 0) selected.add(lowerAt);
      if (eventAt >= 0) selected.add(eventAt);
    }
    return [...selected].sort((a, b) => a - b);
  }

  function seekInspector(index) {
    if (!state.points.length) return;
    const target = Math.max(0, Math.min(state.points.length - 1, index));
    if (window.GridlineInspect?.seek) window.GridlineInspect.seek($('candle-inspector'), target);
    else { state.chartCursor = target; renderChart(); }
  }

  function loadUsdVolume(run) {
    const key = run.id + '/' + run.created_at + '/' + run.bars;
    if (state.volumeKey === key) return;
    state.volumeKey = key;
    state.usdVolume = null;
    api('/api/runs/' + run.id + '/usd-volume').then(data => {
      if (state.volumeKey !== key) return;
      state.usdVolume = data.cumulative;
      if (data.cumulative.at(-1)?.[2]) state.volumeKey = null;
      renderFills();
    }).catch(() => {
      if (state.volumeKey !== key) return;
      state.volumeKey = null;
      state.usdVolume = undefined;
      renderFills();
    });
  }

  function renderVolumeMetric(months) {
    const strip = $('secondary-strip');
    strip.querySelector('.volume-metric')?.remove();
    if (!months.size) return;
    const ts = state.points[state.chartCursor ?? state.points.length - 1]?.ts;
    const series = state.usdVolume;
    let display = series === null ? 'Loading…' : 'USD unavailable';
    let hint = 'Historical USD rates are unavailable.';
    if (series) {
      let lo = 0, hi = series.length;
      while (lo < hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (series[mid][0] <= ts) lo = mid + 1;
        else hi = mid;
      }
      const [, total, missing] = series[lo - 1] || [0, 0, 0];
      if (missing) hint = 'Missing historical USD price for ' + missing + ' fill(s).';
      else {
        display = money(total / months.size, 'USD');
        hint = 'Gross executed volume averaged over ' + months.size +
          ' UTC month(s), including partial and zero-volume months. Non-USD fills use the Coinbase five-minute opening price at the recorded fill time.';
      }
    }
    const metric = appendMetric(strip, 'AVG MONTHLY TRADE VOLUME', display);
    metric.classList.add('volume-metric');
    metric.title = hint;
  }

  function renderFills() {
    const tbody = $('fills-body');
    tbody.replaceChildren();
    const events = [];
    const months = new Set((state.priorVolume || []).map(row => row.month));
    let nextMonth = 0;
    for (const p of state.points.slice(0, (state.chartCursor ?? state.points.length - 1) + 1)) {
      if (p.warmup) continue;
      if (p.ts >= nextMonth) {
        const date = new Date(p.ts * 1000);
        months.add(date.toISOString().slice(0, 7));
        nextMonth = Date.UTC(date.getUTCFullYear(), date.getUTCMonth()+1, 1) / 1000;
      }
      for (const event of p.events || []) events.push(event);
    }
    renderVolumeMetric(months);
    events.sort(
      (a, b) => (Number(b.ts) || Date.parse(b.ts) || 0) - (Number(a.ts) || Date.parse(a.ts) || 0),
    );
    $('fills-count').textContent = `${events.length} ${events.length === 1 ? 'event' : 'events'}`;
    if (!events.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 7;
      cell.className = 'fills-empty';
      cell.textContent = state.run
        ? 'No fills recorded in this run.'
        : 'Fill details appear with the selected run.';
      row.append(cell);
      tbody.append(row);
      return;
    }
    events.slice(0, 16).forEach((event) => {
      const row = document.createElement('tr');
      const vals = [
        fmtDate(event.ts, true),
        event.side || '—',
        event.kind || '—',
        money(event.price),
        `${num(event.qty, 6)} ${state.run?.market?.split('/')[0] || ''}`.trim(),
        event.pnl === null || event.pnl === undefined ? '—' : money(event.pnl),
        event.reason || '—',
      ];
      vals.forEach((value, i) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        if (i === 1) cell.className = event.side === 'buy' ? 'side-buy' : 'side-sell';
        if (i === 2) cell.className = `kind-${event.kind || ''}`;
        if (i === 6 && event.kind === 'exit') {
          const start = state.points.findIndex(p => p.ts === event.entry_ts);
          const entry = matchingEntry(event, start);
          if (entry) cell.append(' ', traceButton(event, start, state.points.findIndex(p => p.ts === event.ts), entry));
        }
        row.append(cell);
      });
      tbody.append(row);
    });
  }

  function renderPositions(positions) {
    const body = $('positions-body');
    body.replaceChildren();
    $('positions-count').textContent =
      `${positions.length} ${positions.length === 1 ? 'layer' : 'layers'}`;
    if (!positions.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 7;
      cell.className = 'fills-empty';
      cell.textContent = 'No open layers.';
      row.append(cell);
      body.append(row);
      return;
    }
    positions.forEach((position) => {
      const row = document.createElement('tr');
      const values = [
        position.direction || '—',
        fmtDate(position.entry_ts, true),
        money(position.entry_price),
        `${num(position.qty, 6)} ${state.run?.market?.split('/')[0] || ''}`.trim(),
        money(position.stop),
        position.targets?.length
          ? position.targets.map(target => `${target.label || 'TP' + target.target_id}: ${money(target.price)} (${num(target.qty, 6)})`).join(' · ')
          : money(position.target),
        money(position.unrealized_pnl),
      ];
      values.forEach((value, i) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        if (i === 0) cell.className = position.direction === 'short' ? 'side-sell' : 'side-buy';
        row.append(cell);
      });
      body.append(row);
    });
  }

  function renderMonitor(run) {
    if (!run) {
      $('monitor-market').textContent = 'Awaiting paper run';
      $('monitor-status').replaceChildren();
      const idleDot = document.createElement('i');
      $('monitor-status').append(idleDot, document.createTextNode(' IDLE'));
      $('monitor-status').className = 'status-pill';
      $('monitor-latest').textContent = '—';
      $('monitor-poll').textContent = '—';
      $('monitor-feed').textContent = '—';
      $('monitor-message').textContent =
        'Choose a feed in Setup, then start the session. Kraken live advances automatically. Recorded candles advance when you press Advance 12 bars.';
      $('stop-button').disabled = true;
      $('audit-button').disabled = true;
      $('advance-button').hidden = true;
      $('audit-result').hidden = true;
      return;
    }
    const isPaper = run.mode === 'paper',
      isRehearsal = run.mode === 'rehearsal';
    $('monitor-market').textContent = `${run.market} · ${run.timeframe}`;
    const waiting = isPaper && run.status === 'running' && (!run.last_poll || !run.last_ts);
    const status = waiting ? 'CONNECTING' : String(run.status || 'unknown').toUpperCase();
    $('monitor-status').replaceChildren();
    const dot = document.createElement('i');
    $('monitor-status').append(dot, document.createTextNode(` ${status}`));
    $('monitor-status').className = `status-pill ${run.status || ''}`;
    $('monitor-latest').textContent = fmtDate(run.last_ts, true);
    $('monitor-poll').textContent = fmtDate(run.last_poll, true);
    $('monitor-feed').textContent = isRehearsal
      ? 'RECORDED CANDLES'
      : run.error
        ? 'ERROR'
        : waiting
          ? 'CONNECTING'
          : run.status === 'running'
            ? 'CONNECTED'
            : '—';
    $('monitor-feed').classList.toggle('feed-error', Boolean(run.error));
    $('monitor-message').textContent = run.error
      ? `Feed error: ${run.error}`
      : isRehearsal
        ? 'Offline rehearsal uses recorded local candles. Advance only as many bars as you choose.'
        : isPaper
          ? waiting
            ? 'Connecting to Kraken and loading the first closed candles…'
            : 'Paper monitor observes Kraken candles on a 30-second cadence. No exchange orders are placed.'
          : 'Select a paper session to monitor live feed details.';
    if (isPaper && run.status === 'running' && !waiting && !run.error) {
      const liveBars = run.bars - Math.max(1, run.warmup_count ?? 205);
      const latest = state.points.at(-1);
      const decision = liveBars === 0 ? 'Indicators are ready. Waiting for the first new closed candle.'
        : run.config.strategy_file ? `The file queued ${latest?.orders?.length ?? 0} orders at the latest close.`
        : latest?.signal ? 'The latest candle signaled an entry for the following open.'
        : 'The latest candle produced no entry signal.';
      const interval = run.timeframe === '1h' ? 3600 : 300;
      $('monitor-message').textContent = `${liveBars} live candles evaluated. ${decision} Next candle closes ${fmtDate(run.last_ts + 2 * interval, true)}. Checking Kraken every 30 seconds.`;
    }
    $('stop-button').disabled = run.status !== 'running';
    $('audit-button').disabled = !(isPaper || isRehearsal);
    $('advance-button').hidden = !(isRehearsal && run.status === 'running');
    $('audit-result').hidden = true;
  }

  const configFields = {
    capital:'capital', size_pct:'size', fee_bps:'fee', slip_bps:'slip', tp_pct:'tp', sl_pct:'sl',
    direction:'direction', signal_model:'signal_model', bb_period:'bb_period', bb_mult:'bb_mult',
    execution_model:'execution_model', grid_lines:'grid_lines', grid_spacing:'grid_spacing',
    grid_step_pct:'grid_step_pct', grid_growth:'grid_growth',
    grid_after_step_pct:'grid_after_step_pct', grid_after_growth:'grid_after_growth', grid_after_exits:'grid_after_exits',
    trend_period:'trend_period', trend_lookback:'trend_lookback', tier2_mult:'tier2_mult',
  };
  const ruleSides = [['bull','Bullish signal'], ['bear','Bearish signal']];
  const ruleFields = {
    price:['Candle price',['open','high','low','close']],
    op:['Comparison',['<=','>=']],
    level:['Indicator',['lower','upper','sma']],
    trend:['Trend filter',['any','rising','non-rising','disabled']],
  };
  const ruleLabels = {'<=':'At or below', '>=':'At or above', lower:'Lower band', upper:'Upper band', sma:'Trend SMA', any:'Any trend', disabled:'Disable signal'};
  for (const [side, title] of ruleSides) {
    const group = document.createElement('fieldset'), legend = document.createElement('legend');
    legend.textContent = title;
    group.append(legend);
    for (const [key, [caption, values]] of Object.entries(ruleFields)) {
      const label = document.createElement('label'), select = document.createElement('select');
      select.id = side + '_' + key;
      label.htmlFor = select.id;
      label.textContent = caption;
      for (const value of values) select.add(new Option(ruleLabels[value] || value, value));
      configFields[select.id] = select.id;
      group.append(label, select);
    }
    $('custom-rules').append(group);
  }
  function describeCondition(config, side) {
    const trend = config[side + '_trend'];
    if (trend === 'disabled') return 'disabled';
    const price = config[side + '_price'], op = config[side + '_op'], level = config[side + '_level'];
    return `${price} ${ruleLabels[op].toLowerCase()} the ${ruleLabels[level].toLowerCase()}${trend === 'any' ? '' : ' with a ' + trend + ' SMA'}`;
  }
  let files = [];
  let openedFile = null;
  const fileValue = filename => 'file:' + filename;
  const newFileValue = 'new-file';
  const emptyProgram = `"""Describe the strategy and when it enters and exits."""
NAME = "New strategy"
SETTINGS = {}

def on_bar(ctx):
    # Called on each closed candle. Return orders for the next candle.
    return []
`;
  function fileMode() {
    return $('strategy-list').value.startsWith('file:') || $('strategy-list').value === newFileValue;
  }
  function fileMessage(message, error = false) {
    $('file-status').textContent = message;
    $('file-status').classList.toggle('error', error);
  }
  function renderFileSettings(settings) {
    const container = $('file-settings');
    container.replaceChildren();
    for (const [key,value] of Object.entries(settings || {})) {
      const label = document.createElement('label'), input = document.createElement('input');
      label.textContent = key.replaceAll('_',' ');
      input.dataset.key = key;
      input.dataset.kind = value === null ? 'object' : typeof value;
      if (typeof value === 'boolean') { input.type = 'checkbox'; input.checked = value; }
      else {
        input.type = typeof value === 'number' ? 'number' : 'text';
        if (input.type === 'number') input.step = 'any';
        input.value = typeof value === 'object' ? JSON.stringify(value) : value;
      }
      label.append(input);
      container.append(label);
    }
  }
  function fileSettings() {
    const result = {};
    for (const input of $('file-settings').querySelectorAll('input')) {
      const kind = input.dataset.kind;
      if (kind === 'number' && !Number.isFinite(input.valueAsNumber)) throw Error('Enter a number for ' + input.dataset.key);
      result[input.dataset.key] = kind === 'boolean' ? input.checked : kind === 'number' ? input.valueAsNumber :
        kind === 'object' ? JSON.parse(input.value) : input.value;
    }
    return result;
  }
  function showFile(file, settings) {
    openedFile = file;
    $('file-title').textContent = file?.name || 'New Python strategy';
    $('file-description').textContent = file?.description || 'Define the rules in on_bar, or duplicate a working example to start.';
    $('file-name').value = file?.filename || 'my_strategy.py';
    $('file-source').value = file?.source ?? emptyProgram;
    renderFileSettings(settings || file?.settings);
    fileMessage(file?.error || (file ? 'Loaded strategies/' + file.filename : 'New draft. Save to add it to the directory.'), !!file?.error);
    $('file-capital').value = $('capital').value;
    $('file-fee').value = $('fee').value;
    $('file-slip').value = $('slip').value;
    $('strategy-status').textContent = file ? 'Python file · ' + file.hash.slice(0, 10) : 'Unsaved Python draft';
    $('strategy-summary').textContent = file ? 'Python · strategies/' + file.filename : 'Unsaved Python draft';
  }
  async function writeFile() {
    const filename = $('file-name').value.trim();
    const button = $('save-file');
    button.disabled = true;
    try {
      const body = {filename, source:$('file-source').value};
      if (openedFile?.filename === filename) body.previous_hash = openedFile.hash;
      await api('/api/strategy-files', {method:'POST', body:JSON.stringify(body)});
      await loadStrategies();
      $('strategy-list').value = fileValue(filename);
      loadStrategy();
      fileMessage('Saved strategies/' + filename + '. Ready for Research and Paper.');
    } catch (error) { fileMessage(error.message,true); }
    finally { button.disabled = false; }
  }
  function newFile(copy = false) {
    const file = copy ? {source:$('file-source').value, settings:fileSettings(), filename:$('file-name').value} : null;
    $('strategy-list').value = newFileValue;
    loadStrategy();
    if (file) {
      $('file-name').value = file.filename.replace(/\.py$/, '') + '_copy.py';
      $('file-source').value = file.source;
      renderFileSettings(file.settings);
    }
    $('file-name').focus();
  }
  let strategies = [];
  function configFromForm() {
    return Object.fromEntries(Object.entries(configFields).map(([key,id]) =>
      [key, $(id).type === 'number' ? Number($(id).value) : $(id).value]));
  }
  function applyConfig(config) {
    for (const [key,id] of Object.entries(configFields)) $(id).value = config[key] ?? state.context.defaults[key];
    explainStrategy();
  }
  function selectedStrategy() {
    return strategies.find(s => String(s.id) === $('strategy-list').value);
  }
  function unchanged(strategy) {
    const config = configFromForm();
    return Object.keys(config).every(key => config[key] === strategy.config[key]);
  }
  function explainStrategy() {
    const c = configFromForm(), saved = selectedStrategy();
    const route = c.direction === 'long-short' ? 'a short' : 'a long';
    $('custom-rules').hidden = c.signal_model !== 'custom';
    function customSummary(side, label, position) {
      return c[side + '_trend'] === 'disabled' ? `${label} entries are disabled.`
        : `${label} rule: ${describeCondition(c, side)} opens ${position}.`;
    }
    const signal = c.signal_model === 'custom'
      ? `${customSummary('bull','Bullish','a long')} ${customSummary('bear','Bearish',route)} If both match, the bullish rule takes priority.`
      : c.signal_model === 'sma-cross'
      ? `A close crossing above its ${c.trend_period}-bar SMA opens a long. Crossing below opens ${route}.`
      : `Bands sit ${c.bb_mult} standard deviations around the ${c.bb_period}-bar mean. When the ${c.trend_period}-bar SMA is above its value ${c.trend_lookback} bars ago, touching the lower band opens a long. Otherwise, touching the upper band opens ${route}.`;
    const grid = c.execution_model === 'price-grid';
    $('grid-controls').hidden = !grid;
    document.querySelector('.recovery-guidance').hidden = !grid;
    const policy = executionLabel(c.execution_model);
    const spacing = (method, step, growth) => method === 'band' ? `${step} × the Bollinger half-width`
      : method === 'geometric' ? `a ${step}% first interval, multiplied by ${growth} at each step` : `${step}% intervals`;
    const execution = grid
      ? `The first signal seeds a basket of up to ${c.grid_lines} lines. Additions use ${spacing(c.grid_spacing, c.grid_step_pct, c.grid_growth)}. After realized gains cover estimated remaining stop risk, ${c.grid_after_exits === 'unchanged' ? 'keep existing profit targets' : 'space remaining profit targets using ' + spacing(c.grid_after_exits,c.grid_after_step_pct,c.grid_after_growth)}.`
      : c.execution_model === 'single-position' ? 'Only enter while flat. Each trade runs independently to its exit.' : 'Each eligible signal can open an additional layer.';
    $('strategy-explanation').textContent = `${signal} ${execution} Size each entry at ${c.size_pct}% of available cash with a ${c.sl_pct}% stop.`;
    const summary = $('strategy-summary');
    if (summary) summary.textContent = `${policy}${grid ? ' · ' + c.grid_lines + ' lines' : ''} · ${c.size_pct}% per entry · ${c.sl_pct}% stop${saved && !unchanged(saved) ? ' · Edited draft' : ''}`;
    $('strategy-update').disabled = !saved;
    $('strategy-status').textContent = saved ? unchanged(saved) ? `Using saved version ${saved.version}.` : 'Edited draft. Save a version to use these changes in a named run.' : 'Draft settings can be run directly or saved under a name.';
  }
  function executionLabel(value) {
    return {'price-grid':'Price grid','single-position':'Single position','signal-layers':'Signal layers'}[value] || 'Signal layers';
  }
  function toggleStudio(open, browse = false) {
    if (open && !state.context) return;
    state.studio = open;
    const url = new URL(location.href);
    if (open) url.searchParams.set('view', 'strategies');
    else url.searchParams.delete('view');
    history.replaceState(null, '', url);
    updateModeUI();
    if (browse && !$('strategy-list').value && files.length && !Object.entries(configFromForm()).some(([key,value]) => key !== 'capital' && value !== state.context.defaults[key])) {
      $('strategy-list').value = fileValue((files.find(f => f.filename === 'fixed_grid.py') || files[0]).filename);
      loadStrategy();
    }
    if (open) $('strategy-studio').scrollIntoView({block:'start'});
  }
  async function loadStrategies() {
    const [presets, directory] = await Promise.all([api('/api/strategies'),api('/api/strategy-files')]);
    strategies = presets.strategies;
    files = directory.files;
    for (const id of ['strategy-list','studio-list']) {
      const select = $(id), old = select.value;
      select.replaceChildren();
      const group = document.createElement('optgroup');
      group.label = 'Python files in strategies/';
      for (const file of files) group.append(new Option((file.error ? 'Check file · ' : '') + (file.name || file.filename) + ' · ' + file.filename,fileValue(file.filename)));
      select.append(group, new Option('New Python file…',newFileValue), new Option('Built-in · Bollinger wick signals',''));
      for (const s of strategies) select.add(new Option(`${s.name} · v${s.version}`,s.id));
      select.value = old;
    }
  }
  function loadStrategy() {
    $('studio-list').value = $('strategy-list').value;
    const editingFile = fileMode();
    $('file-editor').hidden = !editingFile;
    $('preset-editor').hidden = editingFile;
    if (editingFile) {
      showFile(files.find(f => fileValue(f.filename) === $('strategy-list').value));
      return;
    }
    openedFile = null;
    const saved = selectedStrategy();
    $('strategy-name').value = saved?.name || '';
    applyConfig(saved?.config || state.context.defaults);
    if (!saved && ['BTC', 'ETH'].includes($('capital-unit').textContent)) $('capital').value = 1;
  }
  async function saveStrategy(update) {
    const saved = selectedStrategy();
    const name = $('strategy-name').value.trim();
    if (!name) { $('strategy-name').focus(); showToast('Enter a strategy name first.', true); return; }
    const button = $(update ? 'strategy-update' : 'strategy-create');
    button.disabled = true;
    try {
      const body = {name, config:configFromForm()};
      if (update) body.version = saved.version;
      const {strategy} = await api(update ? '/api/strategies/' + saved.id : '/api/strategies', {method:'POST', body:JSON.stringify(body)});
      await loadStrategies();
      $('strategy-list').value = String(strategy.id);
      loadStrategy();
      showToast(`Saved ${strategy.name} · v${strategy.version}.`);
    } catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; explainStrategy(); }
  }
  function strategyForRun() {
    if (fileMode()) {
      if (!openedFile || openedFile.source !== $('file-source').value || openedFile.filename !== $('file-name').value.trim()) throw Error('Save the Python draft in Strategies before starting the run.');
      if (openedFile.error) throw Error(openedFile.error);
      return {strategy_file:openedFile.filename, strategy_hash:openedFile.hash, strategy_settings:fileSettings(),
        config:{capital:$('file-capital').valueAsNumber, fee_bps:$('file-fee').valueAsNumber, slip_bps:$('file-slip').valueAsNumber}};
    }
    const saved = selectedStrategy();
    return saved && unchanged(saved)
      ? {strategy_id:saved.id, strategy_version:saved.version}
      : {config:configFromForm()};
  }

  async function submitRun(event) {
    event.preventDefault();
    const button = $('run-button');
    if (button.disabled) return;
    button.disabled = $('research-start-button').disabled = true;
    try {
      let result;
      if (state.mode === 'research') {
        const d = selectedDataset();
        if (!d) throw new Error('Choose an available dataset first.');
        const payload = { dataset: d.id, ...strategyForRun() };
        if ($('date-start').value) payload.start = $('date-start').value;
        if ($('date-end').value) payload.end = $('date-end').value;
        result = await api('/api/backtests', { method: 'POST', body: JSON.stringify(payload) });
      } else if ($('feed-choice').value === 'recorded') {
        const d = selectedDataset();
        if (!d) throw new Error('Choose a recorded dataset first.');
        result = await api('/api/rehearsals', {
          method: 'POST',
          body: JSON.stringify({ dataset: d.id, ...strategyForRun() }),
        });
      } else {
        result = await api('/api/paper', {
          method: 'POST',
          body: JSON.stringify({
            market: $('market').value,
            timeframe: $('timeframe').value,
            ...strategyForRun(),
          }),
        });
      }
      showToast(
        state.mode === 'research'
          ? 'Backtest added to the run archive.'
          : $('feed-choice').value === 'recorded'
            ? 'Recorded candle rehearsal started.'
            : 'Paper monitor started.',
      );
      await refreshContext(result.id);
      $(state.mode === 'paper' ? 'paper-monitor' : 'report').scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = $('research-start-button').disabled = false;
    }
  }

  async function stopRun() {
    if (!state.run) return;
    try {
      await api(`/api/runs/${encodeURIComponent(state.run.id)}/stop`, { method: 'POST', body: '{}' });
      await refreshContext();
      showToast('Run stopped.');
    } catch (error) {
      showToast(`Could not stop run: ${error.message}`, true);
    }
  }

  async function advanceRun() {
    if (!state.run) return;
    const button = $('advance-button');
    button.disabled = true;
    try {
      await api(`/api/runs/${encodeURIComponent(state.run.id)}/advance`, {
        method: 'POST',
        body: JSON.stringify({ count: 12, after: state.run.last_ts }),
      });
      await selectRun(state.run.id);
      showToast('Rehearsal advanced.');
    } catch (error) {
      showToast(`Could not advance rehearsal: ${error.message}`, true);
    } finally {
      button.disabled = false;
    }
  }

  async function auditRun() {
    if (!state.run) return;
    const auditedId = state.run.id;
    const button = $('audit-button');
    button.disabled = true;
    try {
      const result = await api(`/api/runs/${encodeURIComponent(state.run.id)}/audit`, {
        method: 'POST',
        body: '{}',
      });
      if (state.run?.id !== auditedId) return;
      const box = $('audit-result');
      box.replaceChildren();
      box.hidden = false;
      const mark = document.createElement('span');
      mark.className = `audit-mark ${result.match ? 'match' : 'mismatch'}`;
      mark.textContent = result.match ? 'MATCH' : 'DIFFERENCE';
      const detail = document.createElement('span');
      detail.textContent = `${result.bars} bars replayed · Decisions ${result.decisions_match ? 'match' : 'differ'} · Account ${result.summary_match ? 'matches' : 'differs'} · Checkpoint ${result.checkpoint_match ? 'matches' : 'differs'}`;
      box.append(mark, detail);
      showToast(result.match ? 'Replay matched.' : 'Replay found a difference.');
    } catch (error) {
      showToast(`Replay verification failed: ${error.message}`, true);
    } finally {
      if (state.run?.id === auditedId) button.disabled = false;
    }
  }

  async function pollSelected() {
    if (!state.run || state.run.status !== 'running' || state.run.mode !== 'paper') return;
    await selectRun(state.run.id);
  }
  function schedulePoll() {
    clearInterval(state.pollTimer);
    if (state.run?.mode === 'paper' && state.run.status === 'running')
      state.pollTimer = setInterval(pollSelected, 30000);
  }

  document.querySelectorAll('.mode-tab[data-mode]').forEach((button) =>
    button.addEventListener('click', async () => {
      if (!state.context) return;
      if (state.studio) toggleStudio(false);
      if (button.dataset.mode === state.mode) return;
      state.mode = button.dataset.mode;
      const candidate = state.context.runs.find(
        (r) => (r.mode === 'backtest') === (state.mode === 'research'),
      );
      clearReport();
      updateModeUI();
      if (candidate) await selectRun(candidate.id);
    }),
  );
  document.querySelectorAll('.chart-toggle-button').forEach((button) =>
    button.addEventListener('click', () => {
      state.chart = button.dataset.chart;
      document
        .querySelectorAll('.chart-toggle-button')
        .forEach((b) => b.classList.toggle('active', b === button));
      renderChart();
    }),
  );
  $('candle-inspector').addEventListener('gridline:inspect', (event) => {
    if (!state.run || String(event.detail?.run?.id ?? '') !== String(state.run.id)) return;
    state.chartCursor = event.detail.index;
    if (state.replayAccount) renderAccount();
    renderChart();
    renderFills();
  });
  $('chart-pan-earlier').addEventListener('click', () =>
    seekInspector((state.chartCursor ?? state.points.length - 1) - Math.max(1, Math.floor(state.chartSpan * 0.8))),
  );
  $('chart-pan-later').addEventListener('click', () =>
    seekInspector((state.chartCursor ?? state.points.length - 1) + Math.max(1, Math.floor(state.chartSpan * 0.8))),
  );
  $('chart-zoom-in').addEventListener('click', () => {
    state.chartSpan = Math.max(20, Math.floor(state.chartSpan / 2));
    renderChart();
  });
  $('chart-zoom-out').addEventListener('click', () => {
    state.chartSpan = Math.min(Math.max(20, state.points.length), state.chartSpan * 2);
    renderChart();
  });
  $('chart-zoom-reset').addEventListener('click', () => {
    state.chartSpan = Math.min(200, Math.max(20, state.points.length || 200));
    renderChart();
  });
  const chartSvg = $('main-chart');
  chartSvg.addEventListener('pointerleave', () => { $('chart-hover-label').hidden = true; });
  chartSvg.addEventListener('pointerdown', (event) => {
    if (!state.points.length || event.button !== 0) return;
    describeHover(event);
    state.hoverPinned = true;
    $('hover-readout').append(' · Pinned. Click another candle to inspect it, or ', Object.assign(document.createElement('button'), {type:'button', textContent:'Resume hover', onclick:resetHover}));
    state.chartDrag = { x: event.clientX, start: state.chartCursor ?? state.points.length - 1, moved: false };
    chartSvg.setPointerCapture(event.pointerId);
  });
  chartSvg.addEventListener('pointermove', (event) => {
    if (!state.chartDrag) { if (!state.hoverPinned) describeHover(event); return; }
    const rect = chartSvg.getBoundingClientRect();
    const dx = event.clientX - state.chartDrag.x;
    if (Math.abs(dx) < 8) return;
    state.chartDrag.moved = true;
    const bars = Math.round((dx / Math.max(1, rect.width)) * state.chartSpan);
    if (bars !== 0) seekInspector(state.chartDrag.start - bars);
  });
  const finishChartDrag = () => { state.chartDrag = null; };
  chartSvg.addEventListener('pointerup', finishChartDrag);
  chartSvg.addEventListener('pointercancel', finishChartDrag);
  chartSvg.addEventListener('wheel', (event) => {
    if (!state.points.length) return;
    event.preventDefault();
    const oldSpan = Math.min(state.chartSpan, Math.max(1, (state.chartCursor ?? 0) + 1));
    const nextSpan = Math.max(20, Math.min(state.points.length, Math.round(state.chartSpan * (event.deltaY < 0 ? 0.7 : 1.4))));
    if (nextSpan === state.chartSpan) return;
    const rect = chartSvg.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
    const oldStart = Math.max(0, (state.chartCursor ?? 0) - oldSpan + 1);
    const anchor = oldStart + fraction * (oldSpan - 1);
    const visibleNewSpan = Math.min(nextSpan, state.points.length);
    const target = Math.round(anchor + (1 - fraction) * (visibleNewSpan - 1));
    state.chartSpan = nextSpan;
    seekInspector(Math.min(state.points.length - 1, target));
  }, { passive: false });
  $('save-file').addEventListener('click',writeFile);
  $('new-file').addEventListener('click',() => newFile());
  $('copy-file').addEventListener('click',() => newFile(true));
  $('reload-files').addEventListener('click', async () => {
    const selected = $('strategy-list').value;
    const hasDraft = fileMode() && (!openedFile || openedFile.source !== $('file-source').value ||
      openedFile.filename !== $('file-name').value);
    try {
      await loadStrategies();
      if (hasDraft) {
        if (!$('strategy-list').value) $('strategy-list').value = $('studio-list').value = newFileValue;
        fileMessage('Directory refreshed. Your source draft is still open.');
      } else if (selected.startsWith('file:')) {
        const fresh = files.find(f => fileValue(f.filename) === selected);
        if (fresh?.hash !== openedFile?.hash) loadStrategy();
      }
      showToast('Strategy directory refreshed.');
    } catch (error) { showToast(error.message,true); }
  });
  $('file-source').addEventListener('input',() => fileMessage('Edited source. Save the file before running it.'));
  $('open-studio').addEventListener('click', () => toggleStudio(true,true));
  $('edit-strategy').addEventListener('click', () => toggleStudio(true));
  $('studio-back').addEventListener('click', () => toggleStudio(false));
  $('studio-list').addEventListener('change', () => {
    $('strategy-list').value = $('studio-list').value;
    loadStrategy();
  });
  $('strategy-list').addEventListener('change', () => {
    loadStrategy();
    if ($('strategy-list').value === newFileValue) toggleStudio(true);
  });
  $('strategy-create').addEventListener('click', () => saveStrategy(false));
  $('strategy-update').addEventListener('click', () => saveStrategy(true));
  $('strategy-from-run').addEventListener('click', () => {
    if (!state.run) return;
    if (state.run.config.strategy_file) {
      newFile();
      const c = state.run.config;
      $('file-source').value = c.strategy_file.source;
      $('file-name').value = c.strategy_file.filename.replace(/\.py$/, '_run_' + state.run.id + '.py');
      $('file-capital').value = c.capital;
      $('file-fee').value = c.fee_bps;
      $('file-slip').value = c.slip_bps;
      renderFileSettings(c.strategy_settings);
      fileMessage('Copied captured source from run #' + state.run.id + '. Save as a new file.');
      return;
    }
    $('strategy-list').value = $('studio-list').value = '';
    loadStrategy();
    $('strategy-name').value = '';
    applyConfig(state.run.config);
    showToast(`Copied run #${state.run.id} to an editable draft.`);
  });
  for (const id of Object.values(configFields)) $(id).addEventListener('input', explainStrategy);
  $('run-form').addEventListener('submit', submitRun);
  $('market').addEventListener('change', updateDatasetMeta);
  $('dataset').addEventListener('change', () => { rememberDataset(); updateDatasetMeta(); });
  $('dataset-quote').addEventListener('change', () => { refreshDatasetOptions(); updateDatasetMeta(); });
  $('feed-choice').addEventListener('change', updateModeUI);
  $('clear-backtests').addEventListener('click', async () => {
    const count = state.context.runs.filter(run => run.mode === 'backtest').length;
    if (!count || !window.confirm(`Permanently delete all ${count} saved backtest reports, including their fills and replay history? Paper sessions, datasets, and saved strategies are kept.`)) return;
    $('clear-backtests').disabled = true;
    try {
      const result = await api('/api/backtests/clear', {method:'POST',body:JSON.stringify({count})});
      if (state.run?.mode === 'backtest') {
        resetDeletedSelection();
      }
      showToast(`Deleted ${result.deleted} backtest reports.`);
    } catch (error) { showToast(error.message,true); }
    await refreshContext();
  });
  $('refresh-button').addEventListener('click', () => refreshContext());
  $('report-refresh').addEventListener('click', () => state.run && selectRun(state.run.id));
  $('stop-button').addEventListener('click', stopRun);
  $('advance-button').addEventListener('click', advanceRun);
  $('audit-button').addEventListener('click', auditRun);
  $('bulk-start').value = $('bulk-end').value = lastArchiveMonth();
  $('bulk-start').min = $('bulk-end').min = '2017-08';
  $('bulk-start').max = $('bulk-end').max = lastArchiveMonth();
  $('bulk-form').addEventListener('submit', collectBulkArchive);
  $('bulk-stop').addEventListener('click', () => {
    bulkStop = true;
    $('bulk-stop').disabled = true;
    $('bulk-status').textContent = 'Stopping after the current month finishes…';
  });
  initHistoryDates();
  $('history-start').addEventListener('change', updateHistoryEstimate);
  $('history-end').addEventListener('change', updateHistoryEstimate);
  $('history-timeframe').addEventListener('change', updateHistoryEstimate);
  $('history-venue').addEventListener('change', updateHistoryVenue);
  $('history-market').addEventListener('input', () => {
    $('history-status').classList.remove('error', 'success', 'busy');
    updateHistoryEstimate();
  });
  $('history-coverage').addEventListener('click', async () => {
    const market = $('history-market').value.trim().toUpperCase(), timeframe = $('history-timeframe').value, end = $('history-end').value;
    const result = $('coverage-result');
    for (const field of $('download-form').elements) field.disabled = true;
    const venue = selectedHistoryVenue();
    result.textContent = `Checking ${venue} backward from the end date, one page at a time…`;
    try {
      const info = await api('/api/history/coverage', {method:'POST', body:JSON.stringify({market,timeframe,end,venue})});
      if (info.start) {
        $('history-start').value = info.start;
        $('history-end').value = info.end;
      }
      updateHistoryEstimate();
      result.textContent = info.start
        ? `Verified ${market} ${timeframe} candles from ${info.start} through ${info.end} UTC.${info.skipped_gap ? ' End date moved back because candles were missing, including ' + info.skipped_gap + '.' : ''}`
        : `No continuous UTC date range found for ${market} on ${venue} at ${timeframe} in the ${info.days_limit} days through ${end}. Try the same interval on ${venue === 'Coinbase' ? 'Binance' : 'Coinbase'} or search an older window.`;
    } catch (error) { result.textContent = error.message; }
    finally {
      for (const field of $('download-form').elements) field.disabled = false;
      const count = historyCandleCount();
      $('history-button').disabled = count === null || count > 30000;
    }
  });
  $('history-button').addEventListener('click', downloadHistory);
  $('download-form').addEventListener('submit', (event) => {event.preventDefault(); if (!$('history-button').disabled) downloadHistory();});
  updateHistoryEstimate();

  new ResizeObserver(() => {
    if (state.run) renderChart();
  }).observe($('main-chart'));
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $('theme-switch').setAttribute('aria-pressed', String(theme === 'dark'));
    document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#111a1b' : '#f3f1eb';
  }
  applyTheme(document.documentElement.dataset.theme);
  $('theme-switch').addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(theme);
    localStorage.setItem('gridline.theme', theme);
  });
  refreshContext();
})();
