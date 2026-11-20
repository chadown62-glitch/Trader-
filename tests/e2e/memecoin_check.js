// Browser regression on a disposable demo. Simulated inputs never reach a public market.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const prefix = '/api/dmh/memecoin';
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM ? {executablePath:process.env.CHROMIUM} : {})});
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport:{width,height:900}});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      let socket, active = false, revision = 0;
      await page.routeWebSocket('**/api/ws', ws => {
        socket = ws;
        ws.send(JSON.stringify({topic:'hello', data:{}}));
      });
      const session = {asset_domain:'memecoin', initial_capital:30, deposit:{amount:30,currency:'USD'}, equity:30,
        tracked_wallet_count:2, activity:[], errors:[], token_ranking:[], closed_trades:0, win_rate:null, mean_closed_pnl:null,
        strategies:Object.fromEntries(['sniper','momentum','dip_hunter','whale_tail'].map(k=>[k,{pnl:0,trades:0}])),
        equity_curve:[]};
      const funnel = {asset_domain:'memecoin', tokens:16, accepted:1, median_liquidity_usd:356974,
        refused_by_stage:{liquidity:0, age:0, activity:2, round_trip:1, guardian:4},
        thresholds:{min_liquidity_usd:20000, min_age_minutes:60, min_volume_h1_usd:5000, min_trades_h1:10, max_round_trip_loss:0.1},
        candidates:[{mint:'GATOmint', name:'GATO / SOL', dex_id:'pumpswap', liquidity_usd:212230, volume_h1_usd:57844, round_trip_loss:0.0178}],
        fleet:{counts:{}, kept:0, recent:[]}};
      await page.route('**/api/dmh/memecoin/**', async route => {
        const url = new URL(route.request().url()).pathname;
        if (route.request().method() === 'POST') {
          assert.equal(route.request().headers()['x-sentinelle'],'1');
          assert.equal(route.request().postDataJSON().mode,'paper');
          active = url.endsWith('/start');
        }
        if (url === prefix+'/funnel') return route.fulfill({json:funnel});
        return route.fulfill({json:url === prefix+'/session' || url.endsWith('/tick') ? {...session,revision} :
          {asset_domain:'memecoin',active,cycles:revision,interval_seconds:15}});
      });
      await page.goto(process.env.BASE);
      const fields = page.locator('input[type=password]');
      await fields.first().waitFor();
      for (const field of await fields.all()) await field.fill('disposable-memecoin-ui-test');
      await page.locator('button.primary').click();
      await page.locator('.shell').waitFor();
      if (width < 600) await page.locator('.bottom-nav a').filter({hasText:'Plus'}).click();
      await page.locator((width < 600 ? '.more-sheet' : '.nav')+' a[href="#/memecoin"]').click();
      await page.getByText('Le premier cycle alimentera la courbe.').waitFor();
      const card = page.locator('section.mc-funnel');
      await card.getByText('GATO / SOL').waitFor();  // pool établi retenu, avec sa liquidité et son aller-retour mesuré
      await card.getByText('Refusés · Veto anti-arnaque').waitFor();
      await card.getByText('Aucune entrée examinée par la flotte.').waitFor();
      await page.getByRole('button',{name:'Démarrer'}).click();
      await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Pause').disabled);
      revision = 1;
      session.equity_curve = [{ts:10,equity:30},{ts:20,equity:28},{ts:30,equity:32}];
      session.equity = 32;
      session.activity.push({mint:'Token de test',ts:30,side:'sell',strategy:'whale_tail',reason:'take_profit',pnl:2});
      session.token_ranking = [['Token de test',2]];
      funnel.fleet.recent = [{ts:30, mint:'GATOmint', decision:'PROPOSITION_SIMULÉE', why:['aucune objection']},
        {ts:29, mint:'TTWOmint', decision:'VETO', why:['Rug Pull Guardian : Mutable metadata']}];
      for (const topic of ['memecoin_fill','memecoin_session']) socket.send(JSON.stringify({topic,data:{asset_domain:'memecoin',strategy:'whale_tail'}}));
      await page.locator('.mc-chart path.up').waitFor();
      assert.equal(await page.locator('.mc-chart path.down').count(), 1);
      await page.locator('article.mc-flash').filter({hasText:'Whale Tail'}).waitFor();
      await card.getByText('Entrée fictive autorisée').waitFor();  // décisions de la flotte rafraîchies par l'événement de session
      await card.getByText('Rug Pull Guardian : Mutable metadata').waitFor();
      await page.getByRole('button',{name:'Pause',exact:true}).click();
      await page.getByRole('button',{name:'Cycle manuel'}).click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth),false);
      assert.deepEqual(errors,[]);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => {console.error(e);process.exitCode=1;});
