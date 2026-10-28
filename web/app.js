const TELEGRAM_HANDLE = 'Niku_Blade';
const modal = document.querySelector('#requestModal');
const form = document.querySelector('#requestForm');
const selectedPlan = document.querySelector('#selectedPlan');
const closeButton = document.querySelector('.modal-close');

function visitorId() {
  try {
    let id = localStorage.getItem('toduss3_visitor_id');
    if (!id) {
      id = (crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
      localStorage.setItem('toduss3_visitor_id', id);
    }
    return id;
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function formatUptime(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

async function refreshSiteStats() {
  try {
    const response = await fetch(`/api/site-stats?visitor=${encodeURIComponent(visitorId())}`, { cache: 'no-store' });
    if (!response.ok) return;
    const stats = await response.json();
    document.querySelector('#totalVisits').textContent = Number(stats.total_visits || 0).toLocaleString('es');
    document.querySelector('#onlineNow').textContent = Number(stats.online_now || 0).toLocaleString('es');
    document.querySelector('#siteUptime').textContent = formatUptime(stats.uptime_seconds);
  } catch {
    // El contador es informativo; no debe afectar la navegación ni las compras.
  }
}

refreshSiteStats();
window.setInterval(refreshSiteStats, 10000);

function openRequest(plan) {
  if (!modal || !selectedPlan) return;
  selectedPlan.value = plan;
  modal.hidden = false;
  document.body.classList.add('modal-open');
  window.setTimeout(() => modal.querySelector('input[name="name"]')?.focus(), 80);
}

function closeRequest() {
  if (!modal) return;
  modal.hidden = true;
  document.body.classList.remove('modal-open');
}

document.querySelectorAll('.plan-button').forEach((button) => {
  button.addEventListener('click', (event) => {
    event.preventDefault();
    openRequest(button.dataset.plan || 'plan solicitado');
  });
});

closeButton?.addEventListener('click', closeRequest);
modal?.addEventListener('click', (event) => {
  if (event.target === modal) closeRequest();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeRequest();
});

form?.addEventListener('submit', (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const plan = String(data.get('plan') || 'plan solicitado').trim();
  const name = String(data.get('name') || '').trim();
  const username = String(data.get('username') || '').trim() || 'No indicado';
  const message = [
    'Hola, deseo adquirir el plan ' + plan + '.',
    '',
    'Nombre: ' + name,
    'Usuario de Telegram: ' + username,
    '',
    'Pagaré/pagué en saldo móvil y enviaré la captura del comprobante por este chat para revisión y activación manual.',
  ].join('\n');
  window.open(`https://t.me/${TELEGRAM_HANDLE}?text=${encodeURIComponent(message)}`, '_blank', 'noopener');
  closeRequest();
});

const revealItems = document.querySelectorAll('.plan-card, .visual-card, .step, .limits-panel, .final-cta');
if ('IntersectionObserver' in window) {
  revealItems.forEach((item) => item.classList.add('reveal-ready'));
  const observer = new IntersectionObserver((entries, currentObserver) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      currentObserver.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -32px' });
  revealItems.forEach((item) => observer.observe(item));
}
