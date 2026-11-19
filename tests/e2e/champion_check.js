// Bounded browser QA against a disposable synthetic demo, never a user installation.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM?{executablePath:process.env.CHROMIUM}:{})});
 try{
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:900}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(process.env.BASE||'http://127.0.0.1:8773');
   const fields=page.locator('input[type=password]');await fields.first().waitFor();
   for(let i=0;i<await fields.count();i++)await fields.nth(i).fill('disposable-champion-test-password');
   await page.locator('button.primary').click();await page.locator('.shell').waitFor();
   const navigate=async id=>{
    if(width<600 && id==='apprentissage'){
     await page.locator('.bottom-nav a').filter({hasText:'Plus'}).click();
     await page.locator('.more-sheet a[href="#/apprentissage"]').click();
    }else if(width<600)await page.locator('.bottom-nav a[href="#/performance"]').click();
    else await page.locator('.nav a[href="#/'+id+'"]').click();
   };
   await navigate('apprentissage');
   await page.getByRole('heading',{name:'Apprentissage',exact:true}).waitFor();
   await page.getByText('Aucune comparaison prospective enregistrée. Aucun résultat inventé.').waitFor();
   await page.locator('select').selectOption('refusé');
   await page.getByText('Aucune décision enregistrée.',{exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:(process.env.OUTPUT||'/tmp')+'/apprentissage-'+width+'.png',fullPage:true});
   await navigate('performance');
   await page.getByText('Validation scientifique :',{exact:false}).waitFor();
   const report=await page.evaluate(async()=>await(await fetch('/api/performance')).json());
   assert.equal(report.capital.initial,30);assert.equal(report.capital.current,30);
   assert.equal(report.result.realized,0);assert.equal(report.result.net,0);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.deepEqual(errors,[]);
   await page.screenshot({path:(process.env.OUTPUT||'/tmp')+'/performance-'+width+'.png',fullPage:true});
   await page.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
