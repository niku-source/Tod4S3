const TELEGRAM_HANDLE = 'Niku_Blade';
const modal = document.querySelector('#requestModal');
const form = document.querySelector('#requestForm');
const selectedPlan = document.querySelector('#selectedPlan');
const closeButton = document.querySelector('.modal-close');

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
