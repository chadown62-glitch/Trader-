// Real browser assertions against a disposable demo only. No viewport result is synthesized.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({headless:true,...(process.env.CHROMIUM?{executablePath:process.env.CHROMIUM}:{})});
  const results=[];
  try {
    for(const width of [1440,390]) {
      const ctx=await browser.newContext({viewport:{width,height:900}});
      const page=await ctx.newPage();const errors=[];
      page.on('pageerror', e=>errors.push(e.message));
      await page.goto(process.env.BASE||'http://127.0.0.1:8765');
      const fields=page.locator('input[type=password]');await fields.first().waitFor();
      for(let i=0;i<await fields.count();i++)await fields.nth(i).fill('mot-de-passe-jetable-e2e');
      await page.locator('button.primary').click();await page.locator('.shell').waitFor();
      await page.evaluate(()=>{location.hash='#/mission-control';});
      await page.locator('.mission-control').waitFor();
      await page.getByText('Fixtures synthétiques',{exact:true}).waitFor();
      await page.getByText('Prospective prouvée',{exact:true}).waitFor();
      await page.getByText('Historiques authentiques',{exact:true}).waitFor();
      const response=await ctx.request.get((process.env.BASE||'http://127.0.0.1:8765')+'/api/evidence');
      assert.equal(response.status(),200);const data=await response.json();
      assert.equal(data.wallet.initial_eur,30);assert.equal(data.wallet.current_mtm_eur,null);
      assert.equal(data.independent_advantage,false);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert.deepEqual(errors,[]);
      if(process.env.OUTPUT)await page.screenshot({path:path.join(process.env.OUTPUT,`mission-control-${width}.png`),fullPage:true});
      results.push({width,passed:true});await ctx.close();
    }
    if(process.env.OUTPUT)fs.writeFileSync(path.join(process.env.OUTPUT,'mission-control-browser.json'),JSON.stringify({schema:1,results}));
    console.log(JSON.stringify({results}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
