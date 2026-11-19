// Run on a disposable demo instance, never on a user's installation.
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
   for(let i=0;i<await fields.count();i++)await fields.nth(i).fill('disposable-macro-test-password');
   await page.locator('button.primary').click();await page.locator('.shell').waitFor();
   if(width<600){await page.locator('.bottom-nav a').filter({hasText:'Plus'}).click();await page.locator('.more-sheet a[href="#/macro"]').click();}
   else await page.locator('.nav a[href="#/macro"]').click();
   await page.getByRole('heading',{name:'Contexte macro et actualités'}).waitFor();
   await page.getByText('Données insuffisantes',{exact:true}).waitFor();
   await page.getByRole('button',{name:'Relire le journal local'}).click();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.equal(await page.locator('table').count()>=1,true);
   assert.deepEqual(errors,[]);
   await page.screenshot({path:`/tmp/macro-${width}.png`,fullPage:true});
   await page.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
