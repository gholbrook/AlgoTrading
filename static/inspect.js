/* Offline, dependency-free candle inspector for Gridline run detail points. */
(() => {
  'use strict';

  const mounted = new WeakMap();

  function utc(ts) {
    if (!Number.isFinite(Number(ts))) return 'Timestamp unavailable';
    const date = new Date(Number(ts) * 1000);
    if (Number.isNaN(date.getTime())) return 'Timestamp unavailable';
    return (
      new Intl.DateTimeFormat('en-GB', {
        timeZone: 'UTC',
        year: 'numeric',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hourCycle: 'h23',
      }).format(date) + ' UTC'
    );
  }

  function quoteDigits(run) {
    const market = String(run?.market || '');
    const quote = market.includes('/') ? market.split('/').pop() : '';
    return quote.toUpperCase() === 'BTC'
      ? 8
      : ['USD', 'USDT', 'USDC'].includes(quote.toUpperCase())
        ? 4
        : 8;
  }

  function price(value, run) {
    if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value)))
      return '—';
    const digits = quoteDigits(run);
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits === 8 ? 6 : 2,
    }).format(Number(value));
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function identity(run) {
    if (run && run.id !== undefined && run.id !== null) return String(run.id);
    return run ? `${run.market || ''}|${run.timeframe || ''}|${run.created_at || ''}` : '';
  }

  function mount(container, points, run) {
    if (!container || typeof container.replaceChildren !== 'function') return;
    const list = Array.isArray(points) ? points : [];
    const runId = identity(run);
    const prior = mounted.get(container);
    if (prior && prior.runId !== runId && prior.timer) clearInterval(prior.timer);
    const resumePlayback = Boolean(prior && prior.runId === runId && prior.playing);
    if (prior && prior.runId === runId && prior.timer) clearInterval(prior.timer);
    let index = list.length - 1;
    if (prior && prior.runId === runId && (prior.playing || !prior.following)) {
      const found = list.findIndex((p) => String(p.ts) === String(prior.ts));
      if (!prior.following) {
        index = found >= 0 ? found : Math.min(prior.index, list.length - 1);
        if (found < 0 && list.length && Number(prior.ts) < Number(list[0].ts)) index = 0;
      }
    }
    index = Math.max(0, index);
    const playback = prior && prior.runId === runId
      ? prior
      : { runId, playing: false, timer: null };
    playback.timer = null;
    playback.playing = false;
    playback.points = list;
    playback.run = run;
    playback.index = index;
    playback.ts = list[index]?.ts ?? null;
    playback.following = index === list.length - 1;
    mounted.set(container, playback);

    const root = make('section', 'gridline-inspector');
    root.setAttribute('role', 'region');
    root.setAttribute('aria-label', 'Candle inspector');
    const heading = make('h3', 'gridline-inspector-title', 'Candle inspector');
    const controls = make('div', 'gridline-inspector-controls');
    const previous = make('button', 'gridline-inspector-button', 'Previous');
    previous.type = 'button';
    previous.setAttribute('aria-label', 'Inspect previous candle');
    const next = make('button', 'gridline-inspector-button', 'Next');
    next.type = 'button';
    next.setAttribute('aria-label', 'Inspect next candle');
    const play = make('button', 'gridline-inspector-button gridline-inspector-play');
    play.type = 'button';
    const slider = make('input', 'gridline-inspector-slider');
    slider.type = 'range';
    slider.min = '0';
    slider.max = String(Math.max(0, list.length - 1));
    slider.step = '1';
    slider.setAttribute('aria-label', 'Candle position');
    const position = make('span', 'gridline-inspector-position');
    position.setAttribute('aria-live', 'polite');
    controls.append(previous, play, slider, next, position);
    const timestamp = make('time', 'gridline-inspector-timestamp');
    const candle = make('dl', 'gridline-inspector-candle');
    const status = make('p', 'gridline-inspector-status');
    const queued = make('p', 'gridline-inspector-queued');
    const orderDetails = make('details', 'gridline-inspector-orders');
    const fillsHeading = make('h4', 'gridline-inspector-fills-title', 'Fills in this candle');
    const fills = make('ul', 'gridline-inspector-fills');
    root.append(heading, controls, timestamp, candle, status, queued, orderDetails, fillsHeading, fills);
    container.replaceChildren(root);

    function render(nextIndex) {
      if (!list.length) {
        stopPlayback(playback);
        slider.disabled = true;
        previous.disabled = true;
        next.disabled = true;
        position.textContent = '0 / 0';
        timestamp.textContent = 'No captured candles';
        candle.replaceChildren();
        status.textContent = 'Candle details are unavailable for this run.';
        queued.textContent = '';
        fills.replaceChildren(make('li', 'gridline-inspector-empty', 'No candle events available.'));
        mounted.set(container, { runId, index: 0, ts: null, following: true });
        return;
      }
      index = Math.max(0, Math.min(list.length - 1, Number(nextIndex) || 0));
      const point = list[index] || {};
      slider.disabled = list.length < 2;
      slider.value = String(index);
      slider.setAttribute('aria-valuetext', `Candle ${index + 1} of ${list.length}, ${utc(point.ts)}`);
      previous.disabled = index === 0;
      next.disabled = index === list.length - 1;
      position.textContent = `${index + 1} / ${list.length}`;
      timestamp.dateTime = Number.isFinite(Number(point.ts))
        ? new Date(Number(point.ts) * 1000).toISOString()
        : '';
      timestamp.textContent = utc(point.ts);

      candle.replaceChildren();
      [
        ['Open', point.open],
        ['High', point.high],
        ['Low', point.low],
        ['Close', point.close],
      ].forEach(([label, value]) => {
        const term = make('dt', '', label);
        const detail = make('dd', '', price(value, run));
        candle.append(term, detail);
      });

      const warmup = point.warmup === true;
      const signal = ['long', 'short'].includes(point.signal) ? point.signal : null;
      const config = run?.config || {};
      const model = config.signal_model === 'custom' ? 'custom rule' : config.signal_model === 'sma-cross' ? 'SMA crossover' : 'wick';
      const routing = config.direction || run?.direction || 'legacy-long';
      const regime = point.regime === 'rising' || point.regime === 'non-rising' ? point.regime : null;
      if (warmup) {
        status.textContent = `Warm-up candle${regime ? ` · ${regime} SMA regime` : ''}. Signals are not traded during warm-up.`;
      } else if (!regime) {
        status.textContent = `Regime unavailable${signal ? ` · observed ${signal} signal` : ''}. Eligibility cannot be confirmed from this candle.`;
      } else if (signal) {
        const routed = routing === 'long-short' ? signal : 'long';
        status.textContent = `${signal} ${model} signal · ${regime} regime · routed ${routed} (${routing}).`;
      } else {
        status.textContent = `No eligible ${model} signal · ${regime} regime.`;
      }
      if (signal && !warmup && regime) {
        queued.textContent = `This completed candle queues a ${routing === 'long-short' ? signal : 'long'} market entry for the next candle’s open. The next candle and available funds determine whether a fill is recorded.`;
      } else {
        queued.textContent = '';
      }

      orderDetails.replaceChildren();
      orderDetails.hidden = true;
      if (config.strategy_file) {
        status.textContent = `${config.strategy_file.filename} · ${warmup || point.strategy_ready === false ? 'indicator warmup' : 'strategy evaluated on this candle close'}`;
        const orders = point.orders || [];
        queued.textContent = warmup || point.strategy_ready === false ? '' : `${orders.length} orders for the next candle.`;
        if (orders.length) {
          orderDetails.hidden = false;
          orderDetails.append(make('summary', '', 'View orders placed at close'));
          const orderList = make('ul','gridline-inspector-fills');
          for (const order of orders) {
            const action = order.action === 'exit' ? 'Close lot ' + order.lot_id : 'Open ' + order.direction;
            orderList.append(make('li','gridline-inspector-fill',
              action + (order.label ? ' · ' + order.label : '') + ' · ' + (order.type === 'limit' ? 'limit ' + price(order.price,run) : 'market at next open')));
          }
          orderDetails.append(orderList);
        }
      } else if (point.grid) {
        const g = point.grid;
        queued.textContent = `Basket #${g.basket_id} · ${g.layers} of ${g.max_layers} lines used · realized ${price(g.realized_net, run)} · estimated remaining stop risk ${price(g.stop_risk, run)} · ${g.risk_covered ? 'recovery exit policy active' : 'initial exits'}. ${g.layers < g.max_layers && g.next_add_price ? 'Next grid level ' + price(g.next_add_price, run) + '.' : ''}`;
      } else if (config.execution_model === 'single-position' && signal) {
        queued.textContent = 'This signal can open a trade only when the strategy is flat.';
      }

      fills.replaceChildren();
      const events = Array.isArray(point.events) ? point.events : [];
      if (!events.length) {
        fills.append(make('li', 'gridline-inspector-empty', 'No fills recorded in this candle.'));
      } else {
        events.forEach((event) => {
          const direction = event.direction || 'direction unavailable';
          const kind = event.kind || 'event';
          const side = event.side ? ` ${event.side}` : '';
          const reason = event.label ? ' · ' + event.label : event.reason ? ` · ${event.reason}` : '';
          const qty = Number.isFinite(Number(event.qty))
            ? ` · qty ${new Intl.NumberFormat(undefined, { maximumFractionDigits: 8 }).format(Number(event.qty))}`
            : '';
          fills.append(
            make(
              'li',
              'gridline-inspector-fill',
              `${kind}${side} ${direction} at ${price(event.price, run)}${qty}${reason}`,
            ),
          );
        });
      }
      playback.index = index;
      playback.ts = point.ts;
      playback.following = index === list.length - 1;
      playback.points = list;
      playback.run = run;
      playback.emit = emit;
      playback.render = render;
      playback.playButton = play;
      playback.position = position;
      mounted.set(container, playback);
      play.textContent = playback.playing ? 'Pause' : (index === list.length - 1 && list.length > 1 ? 'Replay from start' : 'Play');
      play.setAttribute('aria-label', playback.playing ? 'Pause candle replay' : (index === list.length - 1 ? 'Replay from start' : 'Play candle replay'));
      play.setAttribute('aria-pressed', String(playback.playing));
      previous.disabled = index === 0 || playback.playing;
      next.disabled = index === list.length - 1 || playback.playing;
      emit(index, point);
    }

    function emit(currentIndex, point) {
      container.dispatchEvent(new CustomEvent('gridline:inspect', {
        bubbles: true, detail: { index: currentIndex, point, run },
      }));
    }
    function stopPlayback(context) {
      if (context.timer) clearInterval(context.timer);
      context.timer = null;
      context.playing = false;
    }
    function startPlayback() {
      if (playback.playing || !list.length) return;
      if (index >= list.length - 1) render(0);
      playback.playing = true;
      playback.timer = setInterval(() => {
        if (index >= list.length - 1) { stopPlayback(playback); render(index); return; }
        render(index + 1);
        if (index >= list.length - 1) { stopPlayback(playback); render(index); }
      }, 450);
      render(index);
    }
    play.addEventListener('click', () => {
      if (playback.playing) { stopPlayback(playback); render(index); }
      else startPlayback();
    });
    previous.addEventListener('click', () => render(index - 1));
    next.addEventListener('click', () => render(index + 1));
    slider.addEventListener('input', () => render(Number(slider.value)));
    render(index);
    if (resumePlayback) startPlayback();
  }

  function seek(container, index) {
    const context = mounted.get(container);
    if (context?.render) context.render(index);
  }

  window.GridlineInspect = Object.freeze({ mount, seek });
})();
