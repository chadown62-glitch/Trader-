// Run against a disposable demo instance. No financial account is used.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM ? {executablePath:process.env.CHROMIUM} : {})});
  try {
    for (const width of [1440,390]) {
      const page = await browser.newPage({viewport:{width,height:900}});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto((process.env.BASE || 'http://127.0.0.1:8771') + '/');
      const fields=page.locator('input[type=password]');
      await fields.first().waitFor();
      for(let i=0;i<await fields.count();i++) await fields.nth(i).fill('disposable-test-password-123');
      await page.locator('button.primary').click();
      await page.locator('.shell').waitFor();
      await page.goto((process.env.BASE || 'http://127.0.0.1:8771')+'/#/performance');
      await page.getByText('Validation scientifique :',{exact:false}).waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      assert.deepEqual(errors,[]);
      await page.screenshot({path:`/tmp/performance-${width}.png`,fullPage:true});
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
