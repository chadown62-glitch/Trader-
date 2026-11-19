// Hub de marché (mission 51). Instance de démonstration JETABLE uniquement, jamais une installation d'utilisateur.
// Ne clique sur aucun bouton qui lirait le réseau (« Relire ») : vérifie l'interface, le bandeau et les refus.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM?{executablePath:process.env.CHROMIUM}:{})});
 try{
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:900}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(process.env.BASE||'http://127.0.0.1:8772');
   const fields=page.locator('input[type=password]');await fields.first().waitFor();
   for(let i=0;i<await fields.count();i++)await fields.nth(i).fill('disposable-dmh-test-password');
   await page.locator('button.primary').click();await page.locator('.shell').waitFor();
   if(width<600){await page.locator('.bottom-nav a').filter({hasText:'Plus'}).click();await page.locator('.more-sheet a[href="#/dmh"]').click();}
   else await page.locator('.nav a[href="#/dmh"]').click();
   await page.getByRole('heading',{name:'Hub de marché'}).waitFor();
   const banner=page.locator('[data-sim-banner]');
   assert.match(await banner.innerText(),/SIMULATION — ARGENT FICTIF/);
   assert.equal(await banner.isVisible(),true);
   await page.getByRole('button',{name:'Stratégie'}).click();
   await page.getByRole('button',{name:/Prévisualiser/}).click();
   await page.getByText(/Indiquez l'avantage brut attendu/).first().waitFor();  // sans avantage annoncé : pas de prévisualisation
   await page.getByRole('button',{name:'Limites'}).click();
   await page.getByText(/plafond/).first().waitFor();
   await page.getByRole('button',{name:'Simulateur'}).click();
   await page.locator('[data-capital]').waitFor();  // capital disponible, frais payés et reliquats affichés séparément
   assert.match(await page.locator('[data-capital]').innerText(),/Capital disponible[\s\S]*frais payés[\s\S]*reliquats/);
   await page.getByRole('button',{name:'Observatoire'}).click();
   await page.getByText('Frais payés en simulation').waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'défilement horizontal');
   assert.deepEqual(errors,[]);
   await page.screenshot({path:`${process.env.OUTPUT||'/tmp'}/dmh-${width}.png`,fullPage:true});
   await page.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
