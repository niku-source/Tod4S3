const TELEGRAM_HANDLE = 'Niku_Blade';

function requestUrl(planName) {
  const message = `Hola, deseo adquirir el plan ${planName}.`;
  return `https://t.me/${TELEGRAM_HANDLE}?text=${encodeURIComponent(message)}`;
}

document.querySelectorAll('.plan-button').forEach((button) => {
  const planName = button.dataset.plan;
  button.href = requestUrl(planName);
  button.target = '_blank';
  button.rel = 'noopener';
});
