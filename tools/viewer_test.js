// Viewer lokal testen (Headless-Chromium). CDN-Bibliotheken werden aus node_modules bedient,
// weil cdn.jsdelivr.net in der Cloud-Umgebung gesperrt sein kann.
//   npm install --prefix /tmp/vt three@0.147.0 jszip@3.10.1 jspdf@2.5.2 jspdf-autotable@3.8.4
//   node tools/viewer_test.js projekte/hfw-trier /tmp/vt/node_modules /tmp/screens
const {chromium}=require('playwright'), fs=require('fs'), path=require('path'), http=require('http');
const [proj,NM,out]=process.argv.slice(2); fs.mkdirSync(out,{recursive:true});
const files={'/index.html':path.join(__dirname,'..','viewer','index.html')};
const walk=(d,pre)=>{for(const f of fs.readdirSync(d)){const q=path.join(d,f);if(fs.statSync(q).isDirectory())walk(q,pre+f+'/');else files['/'+pre+f]=q}};walk(path.join(proj,'viewer'),'');
const srv=http.createServer((q,r)=>{const f=files[q.url.split('?')[0]];if(!f){r.writeHead(404);return r.end()}r.end(fs.readFileSync(f))}).listen(8766);
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1500,height:900}});const errs=[];
  p.on('pageerror',e=>errs.push(e.message));p.on('console',m=>{if(m.type()==='error')errs.push(m.text())});
  await p.route('https://cdn.jsdelivr.net/npm/**',r=>{const u=new URL(r.request().url()).pathname.replace('/npm/','').replace(/@[\d.]+/,'');r.fulfill({body:fs.readFileSync(path.join(NM,u)),contentType:'application/javascript'})});
  await p.route('https://fonts.googleapis.com/**',r=>r.fulfill({body:'',contentType:'text/css'}));
  await p.goto('http://localhost:8766/index.html');await p.waitForSelector('#loading',{state:'hidden',timeout:60000});await p.waitForTimeout(1200);
  await p.screenshot({path:path.join(out,'3d.png')});
  await p.click('[data-t="info"]');await p.waitForTimeout(300);await p.screenshot({path:path.join(out,'planstand.png')});
  await p.click('[data-t="wt"]');await p.waitForTimeout(300);await p.click('#p-wt details.wtk:has([data-wtshow]) summary');await p.waitForTimeout(500);await p.screenshot({path:path.join(out,'wandtypen.png')});
  await p.click('#p-wt [data-wtshow]');await p.waitForTimeout(600);await p.click('#wtFarbe');await p.waitForTimeout(400);await p.screenshot({path:path.join(out,'wandtyp_farben.png')});
  await p.click('[data-t="tree"]');await p.fill('#q','W-EG-189');await p.waitForTimeout(400);await p.click('#tree .leaf');await p.waitForTimeout(500);await p.screenshot({path:path.join(out,'eigenschaften.png')});
  await p.click('[data-t="auf"]');await p.waitForTimeout(400);console.log('Aufmaß:',(await p.textContent('#p-auf .tot')).replace(/\s+/g,' '));
  console.log(errs.length?'FEHLER: '+errs.join(' | '):'keine JS-Fehler');await b.close();srv.close()})();
