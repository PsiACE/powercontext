/* Connect server-rendered fragments to Tabler; no client-side business state. */
let evidenceOpener;
document.addEventListener('htmx:beforeRequest', (event) => {
  if (event.detail.elt.matches('[data-evidence]')) evidenceOpener = event.detail.elt;
  document.getElementById('network-error')?.setAttribute('hidden', '');
});
document.addEventListener('htmx:beforeSwap', (event) => {
  if ([404, 503].includes(event.detail.xhr.status) && event.detail.xhr.getResponseHeader('X-Dashboard-HTML') === '1') {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});
document.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target.id === 'evidence-content') {
    document.getElementById('workspace').classList.add('evidence-open');
    tabler.Offcanvas.getOrCreateInstance(document.getElementById('evidence'), {
      scroll: window.matchMedia('(min-width: 1300px)').matches,
    }).show();
  } else if (event.detail.target.id === 'workspace') {
    const main = document.getElementById('main');
    document.title = main.dataset.title;
    main.focus({ preventScroll: true });
  }
});
document.addEventListener('hidden.bs.offcanvas', (event) => {
  if (event.target.id !== 'evidence') return;
  document.getElementById('workspace').classList.remove('evidence-open');
  if (evidenceOpener?.isConnected) evidenceOpener.focus({ preventScroll: true });
});
for (const name of ['htmx:sendError', 'htmx:responseError']) {
  document.addEventListener(name, () => document.getElementById('network-error')?.removeAttribute('hidden'));
}

const charts = new Map();
function renderCharts() {
  document.querySelectorAll('[data-comparison-chart]').forEach(element => {
    if (charts.has(element)) return;
    const days = JSON.parse(element.dataset.days);
    const labels = JSON.parse(element.dataset.labels);
    const compact = element.dataset.compact === 'true';
    const style = getComputedStyle(document.documentElement);
    const chart = new ApexCharts(element, {
      chart: {
        type: 'bar', height: compact ? 155 : 300, fontFamily: 'inherit',
        toolbar: { show: false }, zoom: { enabled: false }, animations: { enabled: false },
        parentHeightOffset: 0,
      },
      series: ['baseline_tokens', 'recalled_tokens'].map((field, index) => ({
        name: labels[index], data: days.map(day => day[field]),
      })),
      colors: [style.getPropertyValue('--tblr-gray-400').trim(), style.getPropertyValue('--tblr-primary').trim()],
      plotOptions: { bar: { columnWidth: '55%', borderRadius: 0, dataLabels: { position: 'top' } } },
      dataLabels: {
        enabled: days.length <= 7, offsetY: -16,
        formatter: value => `${Number((value / 1000).toFixed(1))}k`,
        style: { fontSize: '11px', fontWeight: 400, colors: ['#111b36'] },
      },
      legend: { show: false },
      grid: { show: !compact, borderColor: style.getPropertyValue('--tblr-border-color').trim(), strokeDashArray: 4 },
      xaxis: {
        categories: days.map(day => day.label),
        axisTicks: { show: false }, labels: { rotate: 0, hideOverlappingLabels: true },
      },
      yaxis: { show: !compact, min: 0, labels: { formatter: value => `${Number((value / 1000).toFixed(1))}k` } },
      tooltip: { y: { formatter: value => `${value.toLocaleString()} tokens` } },
    });
    charts.set(element, chart);
    chart.render();
  });
}
document.addEventListener('DOMContentLoaded', renderCharts);
document.addEventListener('htmx:afterSwap', renderCharts);
document.addEventListener('htmx:beforeCleanupElement', (event) => {
  for (const [element, chart] of charts) {
    if (event.detail.elt === element || event.detail.elt.contains(element)) {
      chart.destroy();
      charts.delete(element);
    }
  }
});
