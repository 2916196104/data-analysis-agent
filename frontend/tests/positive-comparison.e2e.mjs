// Real React/FastAPI/Worker + isolated synthetic SQLite and a local model stub.
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { createServer } from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { chromium } from 'playwright';

const root=path.resolve(import.meta.dirname,'../..');
await mkdir(path.join(root,'output/playwright'),{recursive:true});
const out=await mkdtemp(path.join(root,'output/playwright/positive-comparison-'));
const python=path.join(root,'.venv/Scripts/python.exe');
const children=[],logs=[],requests=[],errors=[],screenshots=[];
const report={source:'real UI/API/Worker; synthetic isolated SQLite; local unavailable model',out,requests,errors,screenshots};
let browser,model;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<200;i++){if(await check())return;await delay(200);}throw Error(`Timeout: ${label}`);}
async function port(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const value=server.address().port;await new Promise(resolve=>server.close(resolve));return value;}
function launch(command,args,label,env={}){const log=createWriteStream(path.join(out,`${label}.log`));logs.push(log);const child=spawn(command,args,{cwd:root,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','pipe']});child.stdout.pipe(log);child.stderr.pipe(log);children.push(child);return child;}
try{
  const seed=`import sys,sqlite3
from pathlib import Path
sys.path.insert(0,'tests')
from test_review_positive_trend import PositiveTrendTests
f=PositiveTrendTests();f.setUp()
try:
    with sqlite3.connect(f.db) as source,sqlite3.connect(sys.argv[1]) as target:
        source.backup(target)
        for rid,name in target.execute('SELECT id,file_path FROM import_runs').fetchall():
            path=Path(name)
            if path.is_file():
                copied=Path(sys.argv[1]).parent/(str(rid)+'-'+path.name);copied.write_bytes(path.read_bytes())
                target.execute('UPDATE import_runs SET file_path=? WHERE id=?',(str(copied),rid))
finally:f.doCleanups()
`;
  const db=path.join(out,'reviews.sqlite3');
  await promisify(execFile)(python,['-c',seed,db],{cwd:root,windowsHide:true});
  await promisify(execFile)(python,['-m','data_analysis_agent.cli.server','init','--db',db],{cwd:root,windowsHide:true});
  model=createServer(async(req,res)=>{
    let text='';for await(const part of req)text+=part;requests.push(JSON.parse(text));
    res.writeHead(503,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'SIMULATED unavailable'}}));
  });
  await new Promise(resolve=>model.listen(0,'127.0.0.1',resolve));
  const apiPort=await port(),webPort=await port(),api=`http://127.0.0.1:${apiPort}/api/v1`,web=`http://127.0.0.1:${webPort}`;
  launch(python,['-m','data_analysis_agent.cli.server','serve','--db',db,'--port',String(apiPort),
    '--env-file',path.join(out,'unused.env'),'--import-root',out,'--allow-origin',web],'api',{
    LLM_MODEL_ID:'SIMULATED-UNAVAILABLE',LLM_API_KEY:'SIMULATED-key',LLM_BASE_URL:`http://127.0.0.1:${model.address().port}/v1`,
    LLM_MAX_RETRIES:'0',AGENT_SEMANTIC_PARSING:'rules_only',
  });
  launch(process.execPath,['frontend/node_modules/vite/bin/vite.js','frontend','--host','127.0.0.1','--port',String(webPort),'--strictPort'],'vite',{VITE_API_BASE_URL:api});
  await until(async()=>{try{return(await fetch(api+'/health')).ok&&(await fetch(web)).ok;}catch{return false;}},'services');
  const options=(await(await fetch(api+'/options')).json()).result;
  const profile=options.profiles.find(row=>row.requested_model==='SIMULATED-model-A');assert.ok(profile);
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true});
  await context.addInitScript(({api,profile})=>localStorage.setItem(`review-workbench:${api}`,JSON.stringify({selected:null,notes:{},draft:{
    dataset:JSON.stringify(['reviews_csv','reviews-csv']),productIds:['100013588055','OTHER'],profileId:profile.profile_id,
    current:['2023-01-01','2023-02-28'],baseline:['2022-12-01','2022-12-31'],note:'',rangeConfirmed:true,
  }})),{api,profile});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(web+'/assistant');
  await page.getByLabel('问答模式').getByText('评论分析',{exact:true}).click();
  await page.getByText('查看支持的分析能力',{exact:true}).click();
  await page.getByText(/正向对比支持整体或一个维度/).waitFor();
  await page.getByText('查看支持的分析能力',{exact:true}).click();
  const lastTask=()=>page.evaluate(api=>{
    const store=JSON.parse(localStorage.getItem(`review-agent-chat:${api}`));
    return store?.sessions.find(row=>row.id===store.activeSessionId)?.turns.at(-1)?.taskId;
  },api);
  const completed=[];
  async function send(text,kind){
    const previous=await lastTask();
    await page.getByRole('textbox',{name:'评论分析问题',exact:true}).fill(text);
    await page.getByRole('button',{name:'提交问题',exact:true}).click();
    let task;
    await until(async()=>{
      const id=await lastTask();if(!id||id===previous)return false;
      task=(await(await fetch(api+'/tasks/'+id)).json()).result;
      if(task.status==='failed')throw Error(JSON.stringify(task.error));return task.status==='succeeded';
    },text);
    const result=task.result;
    assert.equal(result.status,'ok');assert.equal(result.validation.status,'passed');assert.equal(result.total_model_calls,0);
    assert.equal(result.public_answer.generation_status,'template');assert.equal(result.results.r1.kind,kind);
    const canonical=[result.public_answer.summary,...result.public_answer.highlights.map((row,index)=>`${index+1}. ${row.text}`),result.public_answer.sample_note,...result.public_answer.caveats].filter(Boolean).join('\n\n');
    assert.equal(result.answer,canonical);
    completed.push({question:text,kind,taskId:task.task_id,modelCalls:0});
    await until(async()=>await page.locator('.public-answer').count()===completed.length,'rendered new answer');
    return result;
  }
  const products=await send('商品对比：物流好评占比','comparison');
  assert.deepEqual(products.results.r1.data.products.map(row=>[row.product_id,row.comparison_count,row.comparison_denominator,row.comparison_rate]),
    [['100013588055',3,5,.6],['OTHER',1,1,1]]);
  await page.locator('.chat-report > summary').last().click();
  await page.getByRole('columnheader',{name:'好评占比',exact:true}).waitFor();
  assert.equal(await page.getByRole('columnheader',{name:'负面占比',exact:true}).count(),0);
  const [download]=await Promise.all([page.waitForEvent('download'),
    page.getByRole('button',{name:'导出分析报告',exact:true}).last().click()]);
  await download.saveAs(path.join(out,'positive-products.md'));
  const windowResult=await send('对比当前窗口与基线的物流好评占比','window_comparison');
  const data=windowResult.results.r1.data;
  assert.equal(data.current.comparison_rate,.666667);assert.equal(data.baseline.comparison_rate,1);
  assert.equal(data.changes.comparison_rate_change_pp,-33.333333);
  await page.locator('.chat-report > summary').last().click();
  await page.getByText('当前较基线：-33.3 个百分点。',{exact:true}).waitFor();
  for(const width of [1440,1024,768]){
    await page.setViewportSize({width,height:900});
    await page.locator('.chat-report').last().locator('.ant-table-tbody tr').first().scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,`comparison-${width}.png`),animations:'disabled'});
    const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      messages:document.querySelector('.chat-messages').scrollWidth,client:document.querySelector('.chat-messages').clientWidth}));
    assert.ok(dimensions.scroll<=width+1&&dimensions.messages<=dimensions.client+1,JSON.stringify(dimensions));screenshots.push(dimensions);
  }
  await send('查看缺失正文、失败标注和覆盖率','quality');
  assert.equal(requests.length,0,'deterministic statistics must not contact the unavailable model');assert.deepEqual(errors,[]);
  report.status='passed';report.completed=completed;report.checks=['positive product SQL/UI/export','positive window rate and raw-count delta',
    'canonical legacy answer','quality with model unavailable','capability list','1440/1024/768 no horizontal overflow','zero model requests'];
}catch(error){report.status='failed';report.error=String(error);process.exitCode=1;}
finally{
  if(browser)await browser.close();
  for(const child of children.filter(child=>child.pid&&child.exitCode===null))await promisify(execFile)('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,timeout:5000}).catch(()=>{});
  if(model){model.closeAllConnections();await new Promise(resolve=>model.close(resolve));}
  logs.forEach(log=>log.end());await writeFile(path.join(out,'acceptance.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({status:report.status,error:report.error,out,checks:report.checks},null,2));
}
