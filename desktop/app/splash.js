// The starting page: says what the app is doing, and when the stack cannot come up, what was seen and the fix.
/* global splash */
const status = document.getElementById('status');
const title = document.getElementById('problem-title');
const detail = document.getElementById('problem-detail');
const log = document.getElementById('log');

splash.onState((state) => {
  if (state.phase === 'starting') {
    document.body.classList.remove('problem-shown');
    status.textContent = state.title;
    return;
  }
  title.textContent = state.title;
  detail.textContent = state.detail;
  log.hidden = !state.log;
  document.body.classList.add('problem-shown');
  document.getElementById('retry').focus();
});
document.getElementById('retry').addEventListener('click', () => splash.retry());
log.addEventListener('click', () => splash.showLog());
