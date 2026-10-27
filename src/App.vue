<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';

const CRYPTO = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT'];
const FOREX = ['EUR_USD', 'GBP_USD', 'USD_JPY'];
const SYMBOLS = [...CRYPTO, ...FOREX];
const MARKETS = {
  BTCUSDT: { name: 'Bitcoin', label: 'BTC / USDT', icon: '₿' },
  ETHUSDT: { name: 'Ethereum', label: 'ETH / USDT', icon: '◆' },
  SOLUSDT: { name: 'Solana', label: 'SOL / USDT', icon: '◎' },
  XRPUSDT: { name: 'XRP', label: 'XRP / USDT', icon: '✕' },
  DOGEUSDT: { name: 'Dogecoin', label: 'DOGE / USDT', icon: 'Ð' },
  EUR_USD: { name: 'Euro / Dollar', label: 'EUR / USD', icon: '€' },
  GBP_USD: { name: 'Pound / Dollar', label: 'GBP / USD', icon: '£' },
  USD_JPY: { name: 'Dollar / Yen', label: 'USD / JPY', icon: '¥' },
};
const PAGES = {
  overview: ['Overview', 'Good to have you here', 'Track markets, manage risk, and stay on top of every move.'],
  markets: ['Markets', 'Market watch', 'Follow current crypto and forex quotes in one place.'],
  automation: ['Automation', 'Trade while you are away', 'A moving-average strategy runs through the same risk limits as manual orders.'],
  copy: ['Copy trading', 'Trade together, on your terms', 'Import trader profiles and control the signals you follow.'],
  risk: ['Risk controls', 'Set your boundaries', 'Know exactly how much this workspace can trade.'],
};

const state = ref(null);
const view = ref('overview');
const marketFilter = ref('all');
const marketSearch = ref('');
const chartSymbol = ref('BTCUSDT');
const chartCandles = ref([]);
const chartStatus = ref('Loading live candles…');
const orderSymbol = ref('BTCUSDT');
const orderAmount = ref(100);
const side = ref('BUY');
const riskDraft = ref(null);
const riskDirty = ref(false);
const autoDraft = ref(null);
const autoDirty = ref(false);
const autoConfirm = ref('');
const modal = ref(null);
const modeChoice = ref('paper');
const liveConfirm = ref('');
const profileName = ref('');
const profileUrl = ref('');
const fileProfile = ref(null);
const toastMessage = ref('');
const toastError = ref(false);
const busy = ref(false);
const now = ref(Date.now());
const orderAmountInput = ref(null);
let toastTimer;
let clockTimer;
let refreshTimer;
let marketStream;
let chartLoadedAt = 0;
let chartRequest = 0;
let refreshRequest = 0;

function money(value, digits = 2) {
  return Number.isFinite(Number(value)) ? '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
}
function number(value, digits = 4) {
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
}
function market(symbol) { return MARKETS[symbol] || { name: symbol, label: symbol, icon: '•' }; }
function iconClass(symbol) { return symbol.startsWith('BTC') ? 'btc' : symbol.startsWith('ETH') ? 'eth' : symbol.startsWith('SOL') ? 'sol' : FOREX.includes(symbol) ? 'fx' : 'eth'; }
function quote(symbol) { return state.value?.prices?.[symbol]; }
function isFresh(value) { return !!value && now.value - new Date(value.time).getTime() < 60000; }
function priceText(symbol, value = quote(symbol)) {
  if (!value) return '—';
  return symbol === 'USD_JPY' ? number(value.price, 3) : FOREX.includes(symbol) ? number(value.price, 5) : money(value.price, value.price < 1 ? 4 : 2);
}
function changeText(value) { return value?.changePercent == null ? '—' : `${value.changePercent >= 0 ? '+' : ''}${number(value.changePercent, 2)}%`; }
function tradeReady(symbol) {
  if (!state.value || !isFresh(quote(symbol)) || quote(symbol)?.tradeable === false) return false;
  if (state.value.mode === 'paper') return true;
  return FOREX.includes(symbol) ? state.value.connectors.oanda && state.value.connectors.oandaEnvironment === 'live' : state.value.connectors.coins;
}
function bar(value, limit) { return `${Math.min(100, Math.max(0, (value / limit) * 100))}%`; }
function danger(value, limit) { return value / limit >= 0.85; }
function showToast(message, error = false) {
  toastMessage.value = message;
  toastError.value = error;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastMessage.value = ''; }, 5000);
}
function showError(error) { showToast(error.message || String(error), true); }
async function api(path, method = 'GET', data) {
  const response = await fetch(path, { method, headers: data ? { 'Content-Type': 'application/json' } : {}, body: data ? JSON.stringify(data) : undefined });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Request failed');
  return payload;
}
async function refresh() {
  const request = ++refreshRequest;
  try {
    const result = await api('/api/state');
    if (request !== refreshRequest) return;
    state.value = result;
    if (!riskDirty.value) riskDraft.value = structuredClone(result.risk);
    if (!autoDirty.value) autoDraft.value = structuredClone(result.auto);
    if (view.value === 'overview' && now.value - chartLoadedAt > 60000) loadChart();
  } catch (error) { showError(error); }
}
async function loadChart() {
  const request = ++chartRequest;
  const symbol = chartSymbol.value;
  try {
    const result = await api(`/api/candles?symbol=${encodeURIComponent(symbol)}`);
    if (request !== chartRequest) return;
    chartCandles.value = result.candles || [];
    chartStatus.value = chartCandles.value.length ? '' : 'No candles available.';
  } catch {
    if (request !== chartRequest) return;
    chartCandles.value = [];
    chartStatus.value = 'Chart unavailable. Market quotes may still be live.';
  }
  chartLoadedAt = Date.now();
}

const page = computed(() => PAGES[view.value]);
const paper = computed(() => state.value?.mode !== 'live');
const freshFeed = computed(() => SYMBOLS.some(symbol => isFresh(quote(symbol))));
const clock = computed(() => new Intl.DateTimeFormat('en', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }).format(new Date(now.value)));
const accountMetrics = computed(() => {
  const snapshot = state.value;
  if (!snapshot) return { equity: 10000, balance: 10000, positions: 0, note: 'Paper USD cash' };
  if (paper.value) return { equity: snapshot.paperEquity, balance: snapshot.paperAvailable, positions: Object.keys(snapshot.paper.positions).length, note: 'Paper USD cash' };
  const coins = snapshot.live?.coins;
  const oanda = snapshot.live?.oanda;
  if (coins && oanda) return { equity: null, balance: null, positions: Object.keys(coins.holdings).length + (oanda.environment === 'live' ? Number(oanda.openPositionCount || 0) : 0), note: 'Multiple venue accounts' };
  if (coins) return { equity: coins.equityUsd, balance: coins.usdt + coins.php / (snapshot.prices.USDTPHP?.price || 1), positions: Object.keys(coins.holdings).length, note: 'Coins.ph USDT + PHP, USD equivalent' };
  if (oanda) return { equity: oanda.nav, balance: oanda.balance, positions: Number(oanda.openPositionCount || 0), note: `OANDA ${oanda.currency}` };
  return { equity: null, balance: null, positions: 0, note: 'Account unavailable' };
});
const accountChange = computed(() => paper.value ? (state.value?.daily.paperChangeUsd ?? 0) : (state.value?.daily.liveChangeUsd ?? state.value?.daily.realizedPnlUsd ?? 0));
const dailyLoss = computed(() => state.value?.mode === 'live' && state.value.daily.liveLossUsd != null ? state.value.daily.liveLossUsd : Math.max(0, -accountChange.value));
const recentTrades = computed(() => (paper.value ? state.value?.paper.trades : state.value?.liveTrades)?.slice(0, 4) || []);
const filteredMarkets = computed(() => SYMBOLS.filter(symbol =>
  (marketFilter.value === 'all' || (marketFilter.value === 'crypto' ? CRYPTO : FOREX).includes(symbol)) &&
  `${symbol} ${market(symbol).name} ${market(symbol).label}`.toLowerCase().includes(marketSearch.value.trim().toLowerCase())
));
const orderQuantity = computed(() => {
  const price = quote(orderSymbol.value)?.price;
  const usd = Number(orderAmount.value);
  if (!price || !Number.isFinite(usd) || usd <= 0) return null;
  return orderSymbol.value === 'USD_JPY' ? Math.floor(usd) : FOREX.includes(orderSymbol.value) ? Math.floor(usd / price) : usd / price;
});
const copyDescription = computed(() => 'Automatic copy trading is paused while a signal source is being chosen. Imported signals can still be copied manually in paper mode.');
const connectorsText = computed(() => !state.value ? '' : `Configured: ${state.value.connectors.coins ? 'Coins.ph' : 'no Coins.ph'} · ${state.value.connectors.oanda ? `OANDA ${state.value.connectors.oandaEnvironment}` : 'no OANDA'}`);
const chartDrawing = computed(() => {
  const closes = chartCandles.value.map(candle => Number(candle.close)).filter(Number.isFinite);
  if (closes.length < 2) return null;
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const spread = max - min || min * 0.001 || 1;
  const points = closes.map((value, index) => [index / (closes.length - 1) * 1000, 188 - ((value - min) / spread) * 156]);
  const line = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  return { line, area: `${line} L1000 205 L0 205 Z`, last: points.at(-1), color: closes.at(-1) >= closes[0] ? '#50d9c2' : '#ed9589' };
});

function navigate(target) { view.value = target; }
function tradeMarket(symbol) { orderSymbol.value = symbol; navigate('overview'); nextTick(() => orderAmountInput.value?.focus()); }
function openMode() { modeChoice.value = state.value.mode; liveConfirm.value = ''; modal.value = 'mode'; }
function openImport() { profileName.value = ''; profileUrl.value = ''; fileProfile.value = null; modal.value = 'import'; }
function closeModal() { modal.value = null; }
function onKeydown(event) { if (event.key === 'Escape') closeModal(); }

async function submitOrder() {
  if (!state.value || busy.value) return;
  const notionalUsd = Number(orderAmount.value);
  if (state.value.mode === 'live' && !window.confirm(`Send a REAL ${side.value} market order for about ${money(notionalUsd)} of ${market(orderSymbol.value).label}?`)) return;
  busy.value = true;
  try {
    const trade = await api('/api/order', 'POST', { symbol: orderSymbol.value, side: side.value, notionalUsd });
    showToast(`${trade.mode === 'live' ? 'Live' : 'Paper'} ${side.value.toLowerCase()} order ${trade.status}.`);
    await refresh();
  } catch (error) { showError(error); }
  finally { busy.value = false; }
}
async function saveMode() {
  if (busy.value) return;
  busy.value = true;
  try {
    await api('/api/mode', 'POST', { mode: modeChoice.value, confirm: liveConfirm.value });
    closeModal(); showToast('Trading mode updated. Automatic copying is disarmed.'); await refresh();
  } catch (error) { showError(error); }
  finally { busy.value = false; }
}
async function loadProfileFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const profile = JSON.parse(await file.text());
    fileProfile.value = profile;
    profileName.value = profile.name || '';
    profileUrl.value = profile.sourceUrl || '';
    showToast('Profile file loaded. Review and import it.');
  } catch { showError(new Error('The file is not valid JSON')); }
}
async function importProfile() {
  if (busy.value) return;
  busy.value = true;
  try {
    const profile = { ...(fileProfile.value || {}), name: profileName.value.trim(), sourceUrl: profileUrl.value.trim() || null };
    const result = await api('/api/profiles', 'POST', profile);
    closeModal(); showToast(`${result.name} imported.`); await refresh();
  } catch (error) { showError(error); }
  finally { busy.value = false; }
}
async function toggleProfile(profile) {
  try { await api('/api/profiles/toggle', 'POST', { id: profile.id, enabled: !profile.enabled }); await refresh(); }
  catch (error) { showError(error); }
}
async function copySignal(profile, signal) {
  try {
    await api('/api/profiles/order', 'POST', { profileId: profile.id, signalId: signal.id });
    showToast('Signal copied through your risk limits.'); await refresh();
  } catch (error) { showError(error); }
}
async function toggleCopy(event) {
  const enabled = event.target.checked;
  try { await api('/api/copy', 'POST', { enabled }); showToast(enabled ? 'Paper copying enabled.' : 'Automatic copying paused.'); await refresh(); }
  catch (error) { event.target.checked = !enabled; showError(error); }
}
async function saveRisk() {
  if (!riskDraft.value || busy.value) return;
  busy.value = true;
  try { await api('/api/risk', 'POST', riskDraft.value); riskDirty.value = false; showToast('Risk settings saved.'); await refresh(); }
  catch (error) { showError(error); }
  finally { busy.value = false; }
}
async function saveAutoConfig() {
  if (!autoDraft.value || busy.value) return;
  busy.value = true;
  try {
    await api('/api/auto/config', 'POST', {
      symbols: autoDraft.value.symbols,
      fastPeriod: autoDraft.value.fastPeriod,
      slowPeriod: autoDraft.value.slowPeriod,
      orderUsd: autoDraft.value.orderUsd,
    });
    autoDirty.value = false;
    showToast('Strategy settings saved. The first completed candle will anchor the strategy.');
    await refresh();
  } catch (error) { showError(error); }
  finally { busy.value = false; }
}
async function toggleAuto() {
  if (!state.value || busy.value) return;
  const enabled = !state.value.auto.enabled;
  busy.value = true;
  try {
    await api('/api/auto', 'POST', { enabled, confirm: autoConfirm.value });
    autoConfirm.value = '';
    showToast(enabled ? `${state.value.mode === 'live' ? 'Live' : 'Paper'} automation armed.` : 'Automation paused.');
    await refresh();
  } catch (error) { showError(error); }
  finally { busy.value = false; }
}

watch(chartSymbol, () => { chartLoadedAt = 0; loadChart(); });
watch(view, target => { if (target === 'overview' && Date.now() - chartLoadedAt > 60000) loadChart(); });
onMounted(() => {
  window.addEventListener('keydown', onKeydown);
  clockTimer = setInterval(() => { now.value = Date.now(); }, 1000);
  refreshTimer = setInterval(refresh, 15000);
  marketStream = new EventSource('/api/events');
  marketStream.onmessage = refresh;
  refresh();
});
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown);
  clearTimeout(toastTimer); clearInterval(clockTimer); clearInterval(refreshTimer);
  marketStream?.close();
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark"><span></span><span></span><span></span></span><span>trade<span class="brand-accent">pilot</span><small>TRADING COCKPIT</small></span></div>
      <div class="sidebar-label">WORKSPACE</div>
      <nav class="nav" aria-label="Main navigation">
        <button v-for="item in ['overview', 'markets', 'automation', 'copy', 'risk']" :key="item" class="nav-link" :class="{ active: view === item }" @click="navigate(item)">
          <span class="nav-icon">{{ { overview: '◫', markets: '⌁', automation: '✦', copy: '◎', risk: '▤' }[item] }}</span>{{ PAGES[item][0] }}
        </button>
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="sidebar-card"><div class="sidebar-card-icon">✦</div><strong>Stay in control</strong><p>Every order passes your limits before it reaches a venue.</p><button class="text-action" @click="navigate('risk')">Review limits <span>↗</span></button></div>
      <div class="sidebar-footer"><span class="status-pulse"></span> Local workspace <span class="version">v0.1</span></div>
    </aside>

    <main class="main">
      <header class="topbar"><div class="crumb"><span>Workspace</span><span class="crumb-arrow">/</span><strong>{{ page[0] }}</strong></div><div class="top-actions"><div class="feed-state"><span class="status-pulse" :class="{ error: !freshFeed }"></span><span>{{ freshFeed ? 'Market feed live' : 'Market feed unavailable' }}</span></div><div class="top-divider"></div><a class="api-docs-link" href="/docs" target="_blank" rel="noopener noreferrer">API docs ↗</a><span class="current-time">{{ clock }}</span><div class="avatar">TP</div></div></header>

      <div class="page-content">
        <section class="page-heading"><div><div class="eyebrow">YOUR TRADING DESK</div><h1>{{ page[1] }} <span class="heading-spark">✳</span></h1><p>{{ page[2] }}</p></div><div class="heading-actions"><span class="mode-badge" :class="paper ? 'paper' : 'live'"><span class="badge-dot"></span>{{ paper ? 'PAPER MODE' : 'LIVE MODE' }}</span><button class="button button-outline" @click="openMode">Switch mode <span>⌄</span></button></div></section>
        <div v-if="state?.marketError" class="warning-banner">Market feed: {{ state.marketError }}. Stale quotes cannot be traded.</div>

        <section v-show="view === 'overview'" class="view active">
          <div class="metric-grid">
            <article class="metric-card"><div class="metric-top"><span>{{ paper ? 'Portfolio value' : 'Tracked portfolio value' }}</span><span class="metric-icon violet">◈</span></div><div class="metric-value">{{ accountMetrics.equity == null ? 'See venue accounts' : money(accountMetrics.equity) }}</div><div class="metric-foot"><span class="soft-label">Current account value</span></div></article>
            <article class="metric-card"><div class="metric-top"><span>Available balance</span><span class="metric-icon teal">▰</span></div><div class="metric-value">{{ accountMetrics.balance == null ? 'See venue accounts' : money(accountMetrics.balance) }}</div><div class="metric-foot"><span class="soft-label">{{ accountMetrics.note }}</span></div></article>
            <article class="metric-card"><div class="metric-top"><span>Today's P&amp;L</span><span class="metric-icon amber">↗</span></div><div class="metric-value" :class="accountChange < 0 ? 'negative' : accountChange > 0 ? 'positive' : ''">{{ accountChange >= 0 ? '+' : '−' }}{{ money(Math.abs(accountChange)) }}</div><div class="metric-foot"><span class="soft-label">Account change since UTC baseline</span></div></article>
            <article class="metric-card"><div class="metric-top"><span>Open positions</span><span class="metric-icon blue">◧</span></div><div class="metric-value">{{ accountMetrics.positions }}</div><div class="metric-foot"><span class="soft-label">of {{ state?.risk.maxOpenPositions ?? 4 }} allowed</span></div></article>
          </div>

          <div class="dashboard-grid">
            <article class="panel chart-panel"><div class="panel-head"><div><div class="panel-kicker">MARKET PULSE</div><h2>Price overview</h2></div><div class="chart-tools"><select v-model="chartSymbol" aria-label="Chart symbol"><option v-for="symbol in CRYPTO" :key="symbol" :value="symbol">{{ market(symbol).label }}</option></select><span class="time-pill">1H</span></div></div><div class="chart-price-line"><strong>{{ priceText(chartSymbol) }}</strong><span class="change-tag" :class="{ negative: (quote(chartSymbol)?.changePercent ?? 0) < 0 }">{{ changeText(quote(chartSymbol)) }}</span></div>
              <div class="chart-wrap"><svg v-if="chartDrawing" viewBox="0 0 1000 205" preserveAspectRatio="none" role="img" aria-label="Recent price chart"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop :stop-color="chartDrawing.color" stop-opacity=".22"/><stop offset="1" :stop-color="chartDrawing.color" stop-opacity="0"/></linearGradient></defs><g stroke="#28364a" stroke-width="1" stroke-dasharray="4 6"><path d="M0 42 H1000 M0 91 H1000 M0 140 H1000 M0 189 H1000"/></g><path :d="chartDrawing.area" fill="url(#chart-fill)"/><path :d="chartDrawing.line" fill="none" :stroke="chartDrawing.color" stroke-width="2.5" vector-effect="non-scaling-stroke"/><circle :cx="chartDrawing.last[0]" :cy="chartDrawing.last[1]" r="5" :fill="chartDrawing.color" stroke="#152237" stroke-width="3" vector-effect="non-scaling-stroke"/></svg><div v-else class="chart-empty">{{ chartStatus }}</div></div><div class="chart-axis"><span>80h ago</span><span>60h ago</span><span>40h ago</span><span>20h ago</span><span>Now</span></div>
            </article>

            <article class="panel ticket-panel"><div class="panel-head"><div><div class="panel-kicker">QUICK ACTION</div><h2>Place an order</h2></div><span class="panel-glyph">↗</span></div><div class="segmented" role="group" aria-label="Order side"><button type="button" :class="{ active: side === 'BUY' }" data-side="BUY" @click="side = 'BUY'">Buy</button><button type="button" :class="{ active: side === 'SELL' }" data-side="SELL" @click="side = 'SELL'">Sell</button></div><form @submit.prevent="submitOrder"><label class="field-label" for="order-symbol">Market</label><select id="order-symbol" v-model="orderSymbol" required><option v-for="symbol in SYMBOLS" :key="symbol" :value="symbol">{{ market(symbol).label }}</option></select><div class="ticket-quote"><span>Market price</span><strong>{{ priceText(orderSymbol) }}</strong></div><label class="field-label" for="order-amount">Order size (USD)</label><div class="input-wrap"><span>$</span><input id="order-amount" ref="orderAmountInput" v-model.number="orderAmount" type="number" min="1" step="0.01" required><span>USD</span></div><div class="ticket-estimate"><span>Estimated quantity</span><strong>{{ orderQuantity == null ? '—' : `${number(orderQuantity, FOREX.includes(orderSymbol) ? 0 : 8)} ${orderSymbol.replace(/USDT$|_USD$|_JPY$/, '')}` }}</strong></div><button class="button button-primary" type="submit" :disabled="busy || !tradeReady(orderSymbol)">Place {{ state?.mode || 'paper' }} {{ side.toLowerCase() }} order <span>↗</span></button><p class="form-note">Market orders use the latest quote. Risk-reducing exits can pass opening limits.</p></form></article>
          </div>

          <div class="bottom-grid"><article class="panel"><div class="panel-head"><div><div class="panel-kicker">ON YOUR RADAR</div><h2>Watchlist</h2></div><button class="text-action" @click="navigate('markets')">View all <span>↗</span></button></div><div class="watchlist"><div v-for="symbol in CRYPTO.slice(0, 4)" :key="symbol" class="watch-item"><span class="asset-icon" :class="iconClass(symbol)">{{ market(symbol).icon }}</span><span class="asset-name"><strong>{{ market(symbol).name }}</strong><small>{{ market(symbol).label }}</small></span><span class="item-right"><strong>{{ priceText(symbol) }}</strong><small :class="(quote(symbol)?.changePercent ?? 0) < 0 ? 'down' : 'up'">{{ changeText(quote(symbol)) }}</small></span></div></div></article><article class="panel"><div class="panel-head"><div><div class="panel-kicker">LATEST ACTIVITY</div><h2>Recent trades</h2></div><span class="subtle-icon">↗</span></div><div class="activity-list"><div v-if="!recentTrades.length" class="empty">No trades yet. Your activity will appear here.</div><div v-for="trade in recentTrades" :key="trade.id" class="activity-item"><span class="asset-icon" :class="iconClass(trade.symbol)">{{ market(trade.symbol).icon }}</span><span class="asset-name"><strong>{{ trade.side }} {{ market(trade.symbol).label }}</strong><small>{{ trade.source === 'copy' ? 'Copied signal' : 'Manual order' }} · {{ new Date(trade.time).toLocaleString() }}</small></span><span class="item-right"><strong>{{ money(trade.notionalUsd) }}</strong><small :class="trade.side === 'BUY' ? 'up' : 'down'">{{ trade.status }}</small></span></div></div></article></div>
        </section>

        <section v-show="view === 'markets'" class="view active"><div class="section-intro"><h2>Explore markets</h2><p>Current crypto quotes from Coins.ph and forex quotes from your OANDA account.</p></div><div class="panel market-panel"><div class="market-toolbar"><div class="segmented small" role="group" aria-label="Market type"><button v-for="filter in ['all', 'crypto', 'forex']" :key="filter" :class="{ active: marketFilter === filter }" @click="marketFilter = filter">{{ { all: 'All markets', crypto: 'Crypto', forex: 'Forex' }[filter] }}</button></div><div class="market-search"><span>⌕</span><input v-model="marketSearch" placeholder="Search market" aria-label="Search market"></div></div><div class="table-scroll"><table><thead><tr><th>Market</th><th>Last price</th><th>24h change</th><th>24h volume</th><th>Status</th><th></th></tr></thead><tbody><tr v-for="symbol in filteredMarkets" :key="symbol"><td><div class="market-asset"><span class="asset-icon" :class="iconClass(symbol)">{{ market(symbol).icon }}</span><span><strong>{{ market(symbol).label }}</strong><small>{{ market(symbol).name }}</small></span></div></td><td><strong>{{ priceText(symbol) }}</strong></td><td :class="(quote(symbol)?.changePercent ?? 0) < 0 ? 'down' : 'up'">{{ changeText(quote(symbol)) }}</td><td>{{ quote(symbol)?.volume ? money(quote(symbol).volume, 0) : '—' }}</td><td><span class="market-status" :class="{ offline: !isFresh(quote(symbol)) || quote(symbol)?.tradeable === false }">● {{ isFresh(quote(symbol)) ? quote(symbol)?.tradeable === false ? 'Closed' : 'Live' : 'Unavailable' }}</span></td><td><button class="table-button" :disabled="!tradeReady(symbol)" @click="tradeMarket(symbol)">Trade ↗</button></td></tr><tr v-if="!filteredMarkets.length"><td colspan="6" class="empty">No matching markets.</td></tr></tbody></table></div></div></section>

        <section v-show="view === 'automation'" class="view active">
          <div class="section-intro"><h2>Unattended trading</h2><p>The server checks completed 1-hour candles every minute. It must stay running while you are away.</p></div>
          <div class="auto-grid">
            <form v-if="autoDraft" class="panel risk-form" @submit.prevent="saveAutoConfig">
              <div class="panel-head"><div><div class="panel-kicker">STRATEGY</div><h2>Moving-average crossover</h2></div><span class="panel-glyph">✦</span></div>
              <p class="auto-explainer">A buy signal occurs when the fast average crosses above the slow average. A sell signal occurs on the reverse crossing. The first completed candle after arming sets a starting point; it does not place a historical trade.</p>
              <div class="risk-fields">
                <label>Fast average <span>Completed hourly candles</span><div class="input-wrap"><input v-model.number="autoDraft.fastPeriod" type="number" min="2" max="100" step="1" required :disabled="state?.auto.enabled" @input="autoDirty = true"></div></label>
                <label>Slow average <span>Must exceed the fast average</span><div class="input-wrap"><input v-model.number="autoDraft.slowPeriod" type="number" min="3" max="200" step="1" required :disabled="state?.auto.enabled" @input="autoDirty = true"></div></label>
                <label>Order size <span>USD per crossover</span><div class="input-wrap"><span>$</span><input v-model.number="autoDraft.orderUsd" type="number" min="1" step="0.01" required :disabled="state?.auto.enabled" @input="autoDirty = true"></div></label>
              </div>
              <div class="field-label symbol-title">Strategy markets</div>
              <div class="symbol-options"><label v-for="symbol in SYMBOLS" :key="symbol" class="symbol-chip"><input v-model="autoDraft.symbols" type="checkbox" :value="symbol" :disabled="state?.auto.enabled" @change="autoDirty = true"><span>{{ market(symbol).label }}</span></label></div>
              <button class="button button-primary save-risk" type="submit" :disabled="busy || state?.auto.enabled">Save strategy <span>↗</span></button>
            </form>
            <aside class="risk-side">
              <div class="panel auto-control"><div class="panel-kicker">ENGINE STATUS</div><h2>{{ state?.auto.enabled ? 'Running' : 'Paused' }} · {{ state?.mode === 'live' ? 'Live' : 'Paper' }}</h2><p>Last check: {{ state?.auto.lastRunAt ? new Date(state.auto.lastRunAt).toLocaleString() : 'Waiting to start' }}</p><p>Last trade: {{ state?.auto.lastTradeAt ? new Date(state.auto.lastTradeAt).toLocaleString() : 'None yet' }}</p><p v-if="state?.auto.lastError" class="profile-error">{{ state.auto.lastError }}</p><label v-if="state?.mode === 'live' && !state?.auto.enabled" class="field-label" for="auto-confirm">Type ENABLE AUTO LIVE TRADING</label><input v-if="state?.mode === 'live' && !state?.auto.enabled" id="auto-confirm" v-model="autoConfirm" class="modal-input" autocomplete="off" placeholder="ENABLE AUTO LIVE TRADING"><button class="button" :class="state?.auto.enabled ? 'button-outline' : 'button-primary'" :disabled="busy || autoDirty" @click="toggleAuto">{{ state?.auto.enabled ? 'Pause automation' : 'Arm automation' }}</button><p class="auto-hint">{{ autoDirty ? 'Save your strategy before arming it.' : state?.mode === 'live' ? 'Live automation can send real orders without another prompt while this server stays running.' : 'Paper automation uses simulated funds.' }}</p></div>
              <div class="panel"><div class="panel-kicker">RECENT DECISIONS</div><h2>Engine log</h2><div v-if="!state?.auto.recentDecisions.length" class="empty">No decisions yet. Arm the engine to begin.</div><div v-for="(decision, index) in state?.auto.recentDecisions.slice(0, 8) || []" :key="`${decision.time}-${index}`" class="auto-decision"><strong>{{ market(decision.symbol).label }} · {{ decision.outcome }}</strong><small>{{ new Date(decision.time).toLocaleString() }}</small><p>{{ decision.detail }}</p></div></div>
            </aside>
          </div>
        </section>

        <section v-show="view === 'copy'" class="view active"><div class="section-intro with-action"><div><h2>Copy trading</h2><p>Import trader profiles and review their signals in paper mode.</p></div><button class="button button-primary" @click="openImport">+ Import profile</button></div><div class="copy-status panel"><div class="copy-status-icon">◎</div><div><strong>Automatic copying</strong><p>{{ copyDescription }}</p></div><label class="switch"><input type="checkbox" :checked="!!state?.copyEnabled" disabled><span class="slider"></span></label></div><div class="copy-grid"><article class="panel"><div class="panel-head"><div><div class="panel-kicker">YOUR SOURCES</div><h2>Trader profiles</h2></div><span class="count-pill">{{ state?.profiles.length || 0 }} profile{{ state?.profiles.length === 1 ? '' : 's' }}</span></div><div class="profiles-list"><div v-if="!state?.profiles.length" class="empty">No trader profiles yet. Import a JSON profile to start.</div><div v-for="profile in state?.profiles || []" :key="profile.id" class="profile-card"><div class="profile-main"><span class="profile-avatar">{{ profile.name.slice(0, 2).toUpperCase() }}</span><span><strong>{{ profile.name }}</strong><small>{{ profile.sourceUrl || 'Uploaded profile · no live feed' }}</small></span><button class="profile-toggle" :class="{ paused: !profile.enabled }" @click="toggleProfile(profile)">{{ profile.enabled ? 'Active' : 'Paused' }}</button></div><div class="profile-meta"><span>{{ profile.signalCount }} signals</span><span>Imported {{ new Date(profile.importedAt).toLocaleDateString() }}</span><span v-if="profile.lastFetchedAt">Synced {{ new Date(profile.lastFetchedAt).toLocaleTimeString() }}</span></div><div v-if="profile.signals?.length" class="profile-signals"><div v-for="signal in profile.signals.slice(0, 3)" :key="signal.id"><span>{{ signal.side }} {{ market(signal.symbol).label }} · {{ money(signal.notionalUsd) }}</span><button :disabled="!profile.enabled || state?.mode !== 'paper'" @click="copySignal(profile, signal)">Copy ↗</button></div></div><div v-if="profile.lastError" class="profile-error">{{ profile.lastError }}</div></div></div></article><article class="panel copy-guide"><div class="panel-kicker">HOW IT WORKS</div><h2>Review signals first.</h2><div class="guide-step"><span>01</span><div><strong>Import a profile</strong><p>Use a JSON file with a name and optional saved signals.</p></div></div><div class="guide-step"><span>02</span><div><strong>Set your limits</strong><p>Choose the assets, order cap, daily cap, and copy multiplier.</p></div></div><div class="guide-step"><span>03</span><div><strong>Copy manually in paper mode</strong><p>Automatic feed integration is deferred until a signal source is chosen.</p></div></div><button class="text-action" @click="navigate('risk')">Configure risk <span>↗</span></button></article></div></section>

        <section v-show="view === 'risk'" class="view active"><div class="section-intro"><h2>Risk controls</h2><p>These limits are checked before each order that increases exposure.</p></div><div class="risk-grid"><form v-if="riskDraft" class="panel risk-form" @submit.prevent="saveRisk"><div class="panel-head"><div><div class="panel-kicker">TRADING LIMITS</div><h2>Configure your guardrails</h2></div><span class="panel-glyph">▤</span></div><div class="risk-fields"><label>Maximum order size <span>USD per order</span><div class="input-wrap"><span>$</span><input v-model.number="riskDraft.maxOrderUsd" type="number" min="1" step="0.01" required @input="riskDirty = true"></div></label><label>Daily loss limit <span>Paper or live account drawdown</span><div class="input-wrap"><span>$</span><input v-model.number="riskDraft.maxDailyLossUsd" type="number" min="1" step="0.01" required @input="riskDirty = true"></div></label><label>Daily traded notional <span>USD per UTC day</span><div class="input-wrap"><span>$</span><input v-model.number="riskDraft.maxDailyNotionalUsd" type="number" min="1" step="0.01" required @input="riskDirty = true"></div></label><label>Open positions <span>Maximum at once</span><div class="input-wrap"><input v-model.number="riskDraft.maxOpenPositions" type="number" min="1" step="1" required @input="riskDirty = true"></div></label><label>Copy multiplier <span>Signal size × multiplier</span><div class="input-wrap"><input v-model.number="riskDraft.copyMultiplier" type="number" min="0.01" max="10" step="0.01" required @input="riskDirty = true"><span>×</span></div></label></div><div class="field-label symbol-title">Allowed markets</div><div class="symbol-options"><label v-for="symbol in SYMBOLS" :key="symbol" class="symbol-chip"><input v-model="riskDraft.allowedSymbols" type="checkbox" :value="symbol" @change="riskDirty = true"><span>{{ market(symbol).label }}</span></label></div><button class="button button-primary save-risk" type="submit" :disabled="busy">Save risk settings <span>↗</span></button></form><aside class="risk-side"><div class="panel limit-panel"><div class="panel-kicker">TODAY'S USAGE</div><h2>Limit monitor</h2><div class="limit-row"><div><span>Traded notional</span><strong>{{ money(state?.daily.tradedNotionalUsd || 0, 0) }} / {{ money(state?.risk.maxDailyNotionalUsd || 1500, 0) }}</strong></div><div class="progress"><i :style="{ width: bar(state?.daily.tradedNotionalUsd || 0, state?.risk.maxDailyNotionalUsd || 1500) }" :class="{ danger: danger(state?.daily.tradedNotionalUsd || 0, state?.risk.maxDailyNotionalUsd || 1500) }"></i></div></div><div class="limit-row"><div><span>Account drawdown</span><strong>{{ money(dailyLoss, 0) }}</strong></div><div class="progress"><i :style="{ width: bar(dailyLoss, state?.risk.maxDailyLossUsd || 500) }" :class="{ danger: danger(dailyLoss, state?.risk.maxDailyLossUsd || 500) }"></i></div></div><div class="limit-row"><div><span>Open positions</span><strong>{{ accountMetrics.positions }} / {{ state?.risk.maxOpenPositions || 4 }}</strong></div><div class="progress"><i :style="{ width: bar(accountMetrics.positions, state?.risk.maxOpenPositions || 4) }" :class="{ danger: danger(accountMetrics.positions, state?.risk.maxOpenPositions || 4) }"></i></div></div></div><div class="panel note-panel"><span class="note-icon">ⓘ</span><strong>About live risk</strong><p>Limits apply to orders placed by this app. Existing positions, external trades, fees, and deposits can affect the true account risk. Check your venue before arming live mode.</p></div></aside></div></section>
      </div>
    </main>
  </div>

  <div v-if="modal === 'mode'" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="mode-modal-title" @click.self="closeModal"><div class="modal"><button class="modal-close" aria-label="Close" @click="closeModal">×</button><div class="modal-symbol">⇄</div><h2 id="mode-modal-title">Trading mode</h2><p>Paper trades use a local $10,000 account. Live orders are sent to your configured venue.</p><div class="mode-options"><label class="mode-option"><input v-model="modeChoice" type="radio" value="paper"><span><strong>Paper trading</strong><small>Practice with simulated funds</small></span></label><label class="mode-option"><input v-model="modeChoice" type="radio" value="live"><span><strong>Live trading</strong><small>Send real orders to Coins.ph or OANDA</small></span></label></div><div v-if="modeChoice === 'live'"><label class="field-label" for="live-confirm">Type ENABLE LIVE TRADING to arm live orders</label><input id="live-confirm" v-model="liveConfirm" class="modal-input" autocomplete="off" placeholder="ENABLE LIVE TRADING"></div><p class="modal-hint">{{ connectorsText }}</p><button class="button button-primary modal-submit" :disabled="busy" @click="saveMode">Apply mode</button></div></div>
  <div v-if="modal === 'import'" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="import-modal-title" @click.self="closeModal"><div class="modal"><button class="modal-close" aria-label="Close" @click="closeModal">×</button><div class="modal-symbol">◎</div><h2 id="import-modal-title">Import trader profile</h2><p>Upload a JSON profile or enter its details below. A profile URL alone cannot supply trade signals; the feed must return signal JSON.</p><input id="profile-file" type="file" accept="application/json,.json" class="file-input" @change="loadProfileFile"><label class="upload-area" for="profile-file"><span>⇧</span><strong>Choose JSON file</strong><small>or enter the fields below</small></label><label class="field-label" for="profile-name">Trader name</label><input id="profile-name" v-model="profileName" class="modal-input" placeholder="e.g. Macro Momentum"><label class="field-label" for="profile-url">HTTPS signal feed URL</label><input id="profile-url" v-model="profileUrl" class="modal-input" type="url" placeholder="https://example.com/signals.json"><p class="modal-hint">Feed format: <code>{"signals":[{"id":"unique-1","symbol":"BTCUSDT","side":"BUY","notionalUsd":100,"timestamp":"..."}]}</code></p><button class="button button-primary modal-submit" :disabled="busy" @click="importProfile">Import profile</button></div></div>
  <div v-if="toastMessage" class="toast" :class="{ error: toastError }" role="status">{{ toastMessage }}</div>
</template>
