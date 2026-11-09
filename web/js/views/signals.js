// Canaux Telegram de signaux : lecture en observation et fiabilité mesurée
import { api, safe } from '../api.js';
import { store, toast, dt, rr, plainPct, price, cls } from '../store.js';

const { ref, reactive, computed, onMounted, onBeforeUnmount } = Vue;

export default {
  setup() {
    const data = ref(null);
    const secrets = ref(null);
    const form = reactive({ telegram_api_id: '', telegram_api_hash: '', telegram_phone: '' });
    const code = ref('');
    const pwd = ref('');
    const needPassword = ref(false);
    const codeSent = ref(false);
    const newChannel = ref('');
    const load = async () => {
      data.value = await safe(api.get('/api/signals?limit=150'));
      secrets.value = await safe(api.get('/api/secrets'));
    };
    let timer;
    onMounted(() => { load(); timer = setInterval(load, 60000); });
    onBeforeUnmount(() => clearInterval(timer));
    const channels = computed(() => store.settings?.signals?.channels || []);
    const saveCreds = async () => {
      const values = {};
      Object.entries(form).forEach(([k, v]) => { if (v) values[k] = v.trim(); });
      if (!Object.keys(values).length) return;
      if (await safe(api.put('/api/secrets', { values }), 'Identifiants enregistrés sur votre ordinateur')) { Object.keys(form).forEach(k => { form[k] = ''; }); load(); }
    };
    const saveSignals = async (patch) => {
      const r = await safe(api.put('/api/settings', { signals: patch }));
      if (r) { store.settings = r; load(); }
      return r;
    };
    const enable = (on) => saveSignals({ enabled: on });
    const login = async () => {
      const r = await safe(api.post('/api/signals/login'));
      if (r?.authorized) toast('Compte Telegram déjà connecté', 'success');
      else if (r?.code_sent) { codeSent.value = true; toast('Code envoyé dans votre application Telegram', 'info'); }
    };
    const sendCode = async () => {
      const r = await safe(api.post('/api/signals/code', { code: code.value, password: pwd.value || null }));
      if (r?.need_password) { needPassword.value = true; toast('Votre compte a un mot de passe (validation en deux étapes) : saisissez-le', 'warn'); }
      else if (r?.authorized) { toast('Compte Telegram connecté', 'success'); codeSent.value = false; code.value = ''; pwd.value = ''; load(); }
    };
    const addChannel = async () => {
      const c = newChannel.value.trim();
      if (!c) return;
      if (await saveSignals({ channels: [...channels.value, c] })) newChannel.value = '';
    };
    const removeChannel = (c) => saveSignals({ channels: channels.value.filter(x => x !== c) });
    const resolve = async () => { const r = await safe(api.post('/api/signals/resolve')); if (r) { toast(`${r.resolved} signal(aux) évalué(s)`, 'success'); load(); } };
    return { store, data, secrets, form, code, pwd, needPassword, codeSent, newChannel, channels, saveCreds, enable, login, sendCode,
      addChannel, removeChannel, resolve, dt, rr, plainPct, price, cls };
  },
  template: `
  <div>
    <div class="page-head"><h1>Signaux Telegram</h1><span class="badge warn">OBSERVATION UNIQUEMENT</span><span class="spacer"></span>
      <button @click="resolve">↻ Évaluer les signaux</button></div>
    <div class="note info" style="margin-bottom:14px">
      Sentinelle lit les canaux de signaux que vous suivez, reconnaît chaque signal (crypto, sens, entrée, stop, objectifs) et calcule ce qu'il
      aurait <b>réellement</b> rapporté sur les prix du marché, frais compris. Beaucoup de canaux mettent en avant leurs gains et effacent leurs pertes :
      ici, c'est Sentinelle qui mesure. <b>Les signaux ne sont jamais tradés automatiquement.</b>
    </div>
    <div class="grid cols-2" style="margin-bottom:14px">
      <div class="card">
        <h2>Connexion de votre compte Telegram</h2>
        <ol class="small muted" style="padding-left:18px">
          <li>Sur <a href="https://my.telegram.org" target="_blank" rel="noopener">my.telegram.org</a>, connectez-vous, ouvrez « API development tools » et créez une application. Notez <b>api_id</b> et <b>api_hash</b>.</li>
          <li>Saisissez-les ci-dessous avec votre numéro (format +33…), puis enregistrez.</li>
          <li>Cliquez sur « Recevoir le code » : Telegram vous envoie un code dans l'application.</li>
        </ol>
        <div class="row">
          <div class="field"><label>api_id <span v-if="secrets?.telegram_api_id?.set" class="badge ok">défini</span></label><input v-model="form.telegram_api_id" inputmode="numeric"></div>
          <div class="field"><label>api_hash <span v-if="secrets?.telegram_api_hash?.set" class="badge ok">défini</span></label><input type="password" v-model="form.telegram_api_hash"></div>
        </div>
        <div class="field"><label>Numéro de téléphone <span v-if="secrets?.telegram_phone?.set" class="badge ok">défini</span></label><input v-model="form.telegram_phone" placeholder="+33612345678"></div>
        <div class="btn-row"><button class="primary" @click="saveCreds">Enregistrer</button><button @click="login" :disabled="!data?.status?.creds">Recevoir le code</button></div>
        <div v-if="codeSent || needPassword" class="card tight" style="margin-top:12px;background:var(--bg-2)">
          <div class="field"><label>Code reçu dans Telegram</label><input v-model="code" inputmode="numeric"></div>
          <div v-if="needPassword" class="field"><label>Mot de passe de validation en deux étapes</label><input type="password" v-model="pwd"></div>
          <button class="primary" @click="sendCode">Valider</button>
        </div>
        <div class="note small" :class="data?.status?.authorized ? 'ok' : ''" style="margin-top:12px">
          {{ data?.status?.authorized ? 'Compte connecté.' : 'Compte non connecté.' }}
          <span v-if="data?.status?.error" class="down"> {{ data.status.error }}</span>
        </div>
        <label class="check" style="margin-top:8px"><input type="checkbox" :checked="store.settings?.signals?.enabled" @change="enable($event.target.checked)"> Lire les canaux ci-contre</label>
      </div>
      <div class="card">
        <h2>Canaux suivis</h2>
        <div class="row" style="margin-bottom:10px"><input v-model="newChannel" placeholder="@nom_du_canal ou lien t.me/..."><button @click="addChannel">Ajouter</button></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Canal</th><th class="num">Messages</th><th class="num">Signaux</th><th class="num">Évalués</th><th class="num">Réussite</th><th class="num">Moyenne</th><th></th></tr></thead>
          <tbody><tr v-for="c in (data?.channels || [])" :key="c.channel">
            <td><b>{{ c.channel }}</b><div v-if="c.ok === false" class="down small">{{ c.error }}</div></td>
            <td class="num">{{ c.messages }}</td><td class="num">{{ c.signals }}</td><td class="num">{{ c.resolved }}</td>
            <td class="num">{{ c.win_rate !== null && c.win_rate !== undefined ? plainPct(c.win_rate) : '—' }}</td>
            <td class="num" :class="cls(c.mean_r)">{{ rr(c.mean_r) }}</td>
            <td><button v-if="channels.includes(c.channel)" class="small ghost" @click="removeChannel(c.channel)">Retirer</button></td></tr>
            <tr v-if="!(data?.channels || []).length"><td colspan="7" class="empty">Aucun canal suivi.</td></tr></tbody>
        </table></div>
        <p class="faint small">Un canal n'est intéressant que si son résultat moyen reste positif sur au moins 30 signaux évalués. Les 300 derniers messages sont analysés dès l'ajout.</p>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Derniers messages</h2></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Date</th><th>Canal</th><th>Signal reconnu</th><th class="num">Entrée</th><th class="num">Stop</th><th class="num">Objectifs</th><th>Issue</th></tr></thead>
        <tbody><tr v-for="s in (data?.signals || [])" :key="s.id">
          <td class="nowrap">{{ dt(s.ts) }}</td><td>{{ s.channel }}</td>
          <td><span v-if="s.parsed" class="badge" :class="s.side==='long' ? 'ok' : 'err'">{{ s.side==='long' ? 'Achat' : 'Vente' }} {{ s.symbol }}</span>
            <span v-else class="faint small" :title="s.text">{{ (s.text || '').slice(0, 60) }}…</span></td>
          <td class="num">{{ s.parsed ? price(s.entry) : '' }}</td><td class="num">{{ s.parsed ? price(s.stop) : '' }}</td>
          <td class="num small">{{ s.parsed ? s.targets.map(price).join(' / ') : '' }}</td>
          <td :class="cls(s.outcome_r)">{{ s.outcome_r !== null && s.outcome_r !== undefined ? rr(s.outcome_r) + ' (' + s.outcome + ')' : (s.outcome || (s.parsed ? 'en cours' : '')) }}</td></tr>
          <tr v-if="!(data?.signals || []).length"><td colspan="7" class="empty">Aucun message lu pour l'instant.</td></tr></tbody>
      </table></div>
    </div>
  </div>`,
};
