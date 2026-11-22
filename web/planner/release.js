import {api} from './client.js';
import {button, esc, idPath, onSubmit, release, statusBox} from './ui.js';

export async function publicRelease(root, id) {
  if (!id) { location.hash = '#plan'; return; }
  const path = '/release/' + idPath(id);
  let plan;
  try { plan = await api(path); }
  catch (error) {
    if (error.status !== 404) throw error;
    root.innerHTML = '<p role="status">This plan expired on this server. Re-plan to get a current decision.</p><a class="button" href="#plan">Re-plan this trip</a>';
    return;
  }
  root.innerHTML = release(plan, 'Trip decision', false) +
    '<form class="sheet"><h2>Share with the driver</h2>' + button('Get driver message') + statusBox() + '</form><div class="message"></div>';
  const output = root.querySelector('.message');
  onSubmit(root.querySelector('form'), async () => {
    output.replaceChildren();
    let share;
    try { share = await api(path + '/share?audio=false'); }
    catch (error) {
      if (error.status === 404) {
        output.innerHTML = '<a href="#plan">Re-plan this trip</a>';
        throw new Error('Plan expired on this server.');
      }
      throw error;
    }
    output.innerHTML = (share.is_simulated ? '<p>(simulated)</p>' : '') +
      Object.entries(share.text || {}).map(([lang,message]) => '<p lang="' + esc(lang) + '">' + esc(message) + '</p>').join('') +
      '<label>Driver link<input readonly></label>';
    output.querySelector('input').value = location.href;
  }, 'Getting the driver message…');
}
