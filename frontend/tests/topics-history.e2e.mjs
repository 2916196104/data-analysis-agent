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
const out=await mkdtemp(path.join(root,'output/playwright/topics-history-'));
const python=path.join(root,'.venv/Scripts/python.exe');
const children=[],logs=[],requests=[],errors=[],screenshots=[];
const report={source:'real UI/API/Worker; synthetic isolated SQLite; local simulated model',out,requests,errors,screenshots};
let browser,model;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<200;i++){if(await check())return;await delay(200);}throw Error(`Timeout: ${label}`);}
async function port(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const value=server.address().port;await new Promise(resolve=>server.close(resolve));return value;}
function launch(command,args,label,env={}){const log=createWriteStream(path.join(out,`${label}.log`));logs.push(log);const child=spawn(command,args,{cwd:root,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','pipe']});child.stdout.pipe(log);child.stderr.pipe(log);children.push(child);return child;}
try{
  const seed=`import sys,sqlite3
from pathlib import Path
sys.path.insert(0,'tests')
from test_review_dimension_content import DimensionContentTests
f=DimensionContentTests('test_scope_binding_and_simple_ranking_are_deterministic');f.setUp()
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
    let text='';for await(const part of req)text+=part;
    const body=JSON.parse(text);requests.push(body.messages);
    res.writeHead(200,{'Content-Type':'application/json'});
    res.end(JSON.stringify({id:'SIMULATED',object:'chat.completion',created:0,model:body.model,
      choices:[{index:0,finish_reason:'stop',message:{role:'assistant',content:'这是本机模拟回答：通过调整运行频率适应室温。'}}]}));
  });
  await new Promise(resolve=>model.listen(0,'127.0.0.1',resolve));
  const apiPort=await port(),webPort=await port(),api=`http://127.0.0.1:${apiPort}/api/v1`,web=`http://127.0.0.1:${webPort}`;
  launch(python,['-m','data_analysis_agent.cli.server','serve','--db',db,'--port',String(apiPort),
    '--env-file',path.join(out,'unused.env'),'--import-root',out,'--allow-origin',web],'api',{
    LLM_MODEL_ID:'SIMULATED-CHAT',LLM_API_KEY:'SIMULATED-key',LLM_BASE_URL:`http://127.0.0.1:${model.address().port}/v1`,
    LLM_MAX_RETRIES:'0',AGENT_SEMANTIC_PARSING:'rules_only',
  });
  launch(process.execPath,['frontend/node_modules/vite/bin/vite.js','frontend','--host','127.0.0.1','--port',String(webPort),'--strictPort'],'vite',{VITE_API_BASE_URL:api});
  await until(async()=>{try{return(await fetch(api+'/health')).ok&&(await fetch(web)).ok;}catch{return false;}},'services');
  const options=(await(await fetch(api+'/options')).json()).result;
  const profile=options.profiles.find(row=>row.requested_model==='SIMULATED-model-A');assert.ok(profile);
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(({api,profile})=>localStorage.setItem(`review-workbench:${api}`,JSON.stringify({selected:null,notes:{},draft:{
    dataset:JSON.stringify(['reviews_csv','reviews-csv']),productIds:['DIM-A'],profileId:profile.profile_id,
    current:['2023-02-01','2023-02-28'],baseline:['2023-01-01','2023-01-31'],note:'',rangeConfirmed:true,
  }})),{api,profile});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(web+'/assistant');
  async function send(label,text){await page.getByRole('textbox',{name:label,exact:true}).fill(text);await page.getByRole('button',{name:'提交问题',exact:true}).click();}
  const basicDone=()=>page.evaluate(api=>{
    const store=JSON.parse(localStorage.getItem(`review-agent-chat:${api}`));
    return store?.sessions.find(row=>row.id===store.activeSessionId)?.turns.at(-1)?.status==='succeeded';
  },api);
  await send('基础问答问题','解释变频空调');
  await page.getByText('这是本机模拟回答：通过调整运行频率适应室温。',{exact:true}).waitFor();
  await send('基础问答问题','把刚才的解释举个例子');
  await until(()=>requests.length===2,'second model request');
  assert.deepEqual(requests[1].map(row=>row.role),['system','user','assistant','user']);
  assert.equal(requests[1][1].content,'解释变频空调');
  assert.equal(requests[1].at(-1).content,'把刚才的解释举个例子');
  await until(basicDone,'answer completed');
  await page.getByRole('button',{name:'新建会话',exact:true}).click();
  await send('基础问答问题','新会话问题');
  await until(()=>requests.length===3,'new session request');assert.equal(requests[2].length,2);
  await until(basicDone,'new session answer completed');
  await page.getByLabel('问答模式').getByText('评论分析',{exact:true}).click();
  await send('评论分析问题','按细分主题统计差评');
  await page.locator('.public-answer-summary').filter({hasText:'固定版本规则'}).waitFor({timeout:45000});
  assert.match(await page.locator('.public-answer').last().innerText(),/未判定|部分归类/);
  await page.locator('.chat-report > summary').last().click();
  await page.getByRole('columnheader',{name:'细分主题',exact:true}).waitFor();
  await page.getByRole('columnheader',{name:'已确认占比',exact:true}).scrollIntoViewIfNeeded();
  const lastTask=()=>page.evaluate(api=>{
    const store=JSON.parse(localStorage.getItem(`review-agent-chat:${api}`));
    return store.sessions.find(row=>row.id===store.activeSessionId).turns.at(-1)?.taskId;
  },api);
  const originalTask=await lastTask();
  await send('评论分析问题','那好评呢');
  await until(async()=>{
    const id=await lastTask();if(!id||id===originalTask)return false;
    const task=(await(await fetch(api+'/tasks/'+id)).json()).result;
    if(task.status==='failed')throw Error(JSON.stringify(task.error));
    return task.status==='succeeded';
  },'structured topic followup');
  const followup=(await(await fetch(api+'/tasks/'+await lastTask())).json()).result;
  assert.equal(followup.parameters.context_task_id,originalTask);
  assert.equal(followup.result.results.r1.kind,'topic_statistics');
  assert.equal(followup.result.results.r1.data.sentiment,'positive');
  assert.equal(followup.result.total_model_calls,0);
  report.followup={taskId:followup.task_id,parent:originalTask,sentiment:'positive'};
  await page.locator('.public-answer-summary').filter({hasText:'固定版本规则'}).nth(1).waitFor({timeout:45000});
  await page.locator('.chat-report > summary').last().click();
  await page.getByRole('columnheader',{name:'已确认占比',exact:true}).last().scrollIntoViewIfNeeded();
  for(const width of [1440,1024,768]){
    await page.setViewportSize({width,height:900});
    await page.locator('.chat-report').last().locator('.ant-table-tbody tr').first().scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,`topics-${width}.png`),animations:'disabled'});
    const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      messages:document.querySelector('.chat-messages').scrollWidth,client:document.querySelector('.chat-messages').clientWidth}));
    assert.ok(dimensions.scroll<=width+1&&dimensions.messages<=dimensions.client+1,JSON.stringify(dimensions));screenshots.push(dimensions);
  }
  assert.equal(requests.length,3,'topic statistics must not call the model');assert.deepEqual(errors,[]);
  report.status='passed';report.checks=['bounded multi-turn HTTP messages','new session isolation','native versioned topic query','structured topic sentiment followup','no topic model calls','1440/1024/768 no horizontal overflow'];
}catch(error){report.status='failed';report.error=String(error);process.exitCode=1;}
finally{
  if(browser)await browser.close();
  for(const child of children.filter(child=>child.pid&&child.exitCode===null))await promisify(execFile)('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,timeout:5000}).catch(()=>{});
  if(model){model.closeAllConnections();await new Promise(resolve=>model.close(resolve));}
  logs.forEach(log=>log.end());await writeFile(path.join(out,'acceptance.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({status:report.status,error:report.error,out,checks:report.checks},null,2));
}
