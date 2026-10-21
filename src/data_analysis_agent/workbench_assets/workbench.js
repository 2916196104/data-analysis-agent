"use strict";
const $ = id => document.getElementById(id);
const state = {options:null, page:1, pages:1, detail:null, request:0, detailRequest:0, saving:false};
function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}
function notify(message, error=false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
}
async function api(path, options={}) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请重试。");
  return body;
}
function choices(select, mapping, allLabel) {
  select.replaceChildren();
  if (allLabel) select.append(new Option(allLabel, ""));
  for (const [value,label] of Object.entries(mapping)) select.append(new Option(label,value));
}
function profileParams() {
  const params = new URLSearchParams();
  if ($("profile").value) params.set("profile_id", $("profile").value);
  return params;
}
function badge(value, mapping) { return el("span", mapping[value] || value, "badge " + value); }
function renderOpinions(container, opinions, onQuote) {
  if (!opinions.length) { container.append(el("p", "无观点", "muted")); return; }
  for (const opinion of opinions) {
    const row = el("div", null, "opinion");
    row.append(badge(opinion.aspect, state.options.aspects), document.createTextNode(" "), badge(opinion.sentiment,state.options.sentiments));
    row.append(el("span", opinion.quote, "quote"));
    if (onQuote) {
      const locate = el("button", "定位原文");
      locate.type = "button";
      locate.onclick = () => onQuote(opinion);
      row.append(locate);
    }
    container.append(row);
  }
}
async function loadOptions() {
  const oldProduct = $("product").value;
  const options = await api("/api/options?" + profileParams());
  state.options = options;
  $("profile").replaceChildren();
  for (const p of options.profiles) $("profile").append(new Option(
    `${p.requested_model} · ${p.prompt_version.slice(0,31)}… · 配置 ${p.profile_id}`, p.profile_id));
  if (!options.profiles.length) $("profile").append(new Option("暂无模型标注，先使用标注命令处理评论", ""));
  $("profile").value = options.selected_profile_id ?? "";
  const profile = options.profiles.find(p => p.profile_id === options.selected_profile_id);
  $("profile-description").textContent = profile ? `${profile.base_url} ｜ ${profile.prompt_version}` : "尚无模型调用记录，所有非空评论显示为待标注。";
  $("product").replaceChildren(new Option("全部商品", ""));
  $("progress").replaceChildren();
  let total=0, success=0, pending=0, reviewed=0;
  for (const p of options.products) {
    $("product").append(new Option(`${p.product_id} · ${p.product_title || "商品名称缺失"}`, p.key));
    const row = el("tr");
    const name = el("td", p.product_id); name.append(el("small", p.product_title || "商品名称缺失"));
    row.append(name);
    for (const value of [p.review_count, `${p.success_count} / ${p.eligible_count}`, p.pending_count,p.failed_count,p.empty_count,p.reviewed_count]) row.append(el("td",value));
    const cell = el("td"); const progress = el("progress");
    progress.max = Math.max(1,p.eligible_count); progress.value=p.success_count;
    progress.setAttribute("aria-label", p.product_id + " 标注完成率");
    cell.append(progress,el("small",p.eligible_count ? (100*p.success_count/p.eligible_count).toFixed(1)+"%" : "无有效正文"));
    row.append(cell); $("progress").append(row);
    total+=p.review_count; success+=p.success_count; pending+=p.pending_count; reviewed+=p.reviewed_count;
  }
  if ([...$("product").options].some(o => o.value===oldProduct)) $("product").value=oldProduct;
  $("totals").replaceChildren();
  for (const [label,value] of [["评论总数",total],["标注成功",success],["待标注",pending],["人工复核",reviewed]]) {
    const box=el("div",null,"metric"); box.append(el("strong",value),el("span",label)); $("totals").append(box);
  }
}
async function loadReviews() {
  const request = ++state.request;
  ++state.detailRequest; state.detail=null;
  $("detail").replaceChildren(el("p","选择评论后，可查看原文并进行复核。","empty"));
  const params=profileParams();
  for (const [key,value] of new FormData($("filters"))) if (value) params.set(key,value);
  params.set("page",state.page);
  $("previous").disabled=true; $("next").disabled=true;
  $("reviews").replaceChildren(el("p","正在读取评论…","empty"));
  try {
    const result=await api("/api/reviews?"+params);
    if (request!==state.request) return;
    state.page=result.page; state.pages=result.pages;
    $("result-count").textContent=`共 ${result.total} 条`;
    $("page-description").textContent=`第 ${result.page} / ${result.pages} 页`;
    $("reviews").replaceChildren();
    if (!result.items.length) $("reviews").append(el("p","没有符合条件的评论。","empty"));
    for (const item of result.items) {
      const card=el("article",null,"review-card"); card.dataset.id=item.id;
      const heading=el("div",null,"card-heading"); heading.append(el("strong",`#${item.id} · ${item.product_id}`),badge(item.processing_status,state.options.processing_statuses),badge(item.human_status,state.options.human_statuses));
      card.append(heading,el("p",`${item.published_at.slice(0,10)} UTC ｜ 评分 ${item.rating ?? "缺失"}`,"muted"),el("div",item.text || "（空正文）","body-text"));
      renderOpinions(card,item.opinions);
      if (item.error_code) card.append(el("p",`失败原因：${item.error_code}`,"muted"));
      const open=el("button","查看原文与复核"); open.type="button";
      open.onclick=()=>openDetail(item.id).catch(error=>notify(error.message,true)); card.append(open);
      $("reviews").append(card);
    }
    $("previous").disabled=state.page<=1; $("next").disabled=state.page>=state.pages;
  } catch(error) { if(request===state.request) { $("reviews").replaceChildren(); notify(error.message,true); } }
}
function highlight(opinion) {
  const body=Array.from(state.detail.review.text), target=$("full-body");
  target.replaceChildren(document.createTextNode(body.slice(0,opinion.quote_start).join("")),el("mark",body.slice(opinion.quote_start,opinion.quote_end).join("")),document.createTextNode(body.slice(opinion.quote_end).join("")));
  target.querySelector("mark").scrollIntoView({block:"nearest",behavior:"smooth"});
}
function addEditorRow(opinion={aspect:"quality",sentiment:"negative",quote:""}) {
  const row=el("div",null,"editor-row"), controls=el("div",null,"editor-controls");
  for (const [field,title,mapping] of [["aspect","维度",state.options.aspects],["sentiment","情感",state.options.sentiments]]) {
    const label=el("label",title), select=el("select"); select.dataset.field=field;
    choices(select,mapping); select.value=opinion[field]; label.append(select); controls.append(label);
  }
  const remove=el("button","删除观点","danger"); remove.type="button"; remove.onclick=()=>row.remove(); controls.append(remove);
  const label=el("label","原文片段（须逐字一致）"), quote=el("textarea"); quote.dataset.field="quote"; quote.maxLength=4000; quote.value=opinion.quote;
  label.append(quote); row.append(controls,label); $("editor-rows").append(row);
}
async function saveHuman(action) {
  if (state.saving || !state.detail) return;
  const detail=state.detail, review=detail.review;
  const body={annotation_id:detail.annotation.id, expected_revision_id:review.revision_id, action,
              reviewer:$("reviewer").value, note:$("review-note").value};
  if (action==="corrected") body.opinions=[...$("editor-rows").children].map(row=>Object.fromEntries([...row.querySelectorAll("[data-field]")].map(input=>[input.dataset.field,input.value])));
  state.saving=true; document.querySelectorAll(".save-actions button").forEach(button=>button.disabled=true);
  try {
    await api(`/api/reviews/${review.id}/human-reviews`, {method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":state.options.csrf_token},body:JSON.stringify(body)});
    if (state.detail===detail) {
      await loadOptions(); await loadReviews(); await openDetail(review.id);
    }
    notify("复核已保存，模型原始记录保留。若当前筛选不再匹配，该评论会从列表中移出。");
  } catch(error) { notify(error.message,true); }
  finally {state.saving=false;document.querySelectorAll(".save-actions button").forEach(button=>button.disabled=false);}
}
async function openDetail(id) {
  const request=++state.detailRequest, profile=$("profile").value;
  const detail=await api(`/api/reviews/${id}?`+profileParams());
  if(request!==state.detailRequest || profile!==$("profile").value) return;
  state.detail=detail;
  document.querySelectorAll(".review-card").forEach(card=>card.classList.toggle("selected",Number(card.dataset.id)===id));
  const target=$("detail"), review=detail.review; target.replaceChildren();
  target.append(el("h3",`#${id} · ${review.product_id}`),el("p",review.product_title || "商品名称缺失","muted"),el("p",review.published_at+" ｜ "+state.options.processing_statuses[review.processing_status],"muted"));
  const original=el("div",review.text || "（空正文）","body-text"); original.id="full-body"; target.append(original);
  target.append(el("h3","当前有效观点")); renderOpinions(target,detail.effective_opinions,highlight);
  const evidence=el("details"), evidenceTitle=el("summary","原文来源与模型记录"); evidence.append(evidenceTitle,el("p",`${review.file_path} · 第 ${review.line_number} 行`,"muted wrap"),el("p",review.evidence_ref,"muted wrap"));
  if(detail.annotation) {evidence.append(el("h3","所选版本的模型原始观点"));renderOpinions(evidence,detail.annotation.opinions,highlight);}
  evidence.append(el("pre",JSON.stringify(detail.model_history,null,2))); target.append(evidence);
  if(review.processing_status==="success") {
    target.append(el("h3","人工确认与纠错"),el("p","编辑下方完整观点列表后保存。删除全部观点表示“人工判断无可支持观点”。","muted"));
    const fields=el("div",null,"reviewer-fields");
    for(const [field,labelText,max] of [["reviewer","复核人（必填）",80],["review-note","备注",2000]]) {
      const label=el("label",labelText),input=el(field==="reviewer"?"input":"textarea"); input.id=field;input.maxLength=max;
      if(field==="reviewer") input.value=sessionStorage.getItem("reviewer") || "";
      label.append(input);fields.append(label);
    }
    target.append(fields);
    const rows=el("div");rows.id="editor-rows";target.append(rows);
    detail.effective_opinions.forEach(addEditorRow);
    const actions=el("div",null,"editor-actions");
    const add=el("button","添加观点");add.onclick=()=>addEditorRow();
    const selected=el("button","用所选原文添加观点");selected.onclick=()=>{
      const selection=window.getSelection(), quote=selection.toString();
      if(!quote || !original.contains(selection.anchorNode) || !original.contains(selection.focusNode)) {notify("请先在上方评论原文中选中连续文字。",true);return;}
      addEditorRow({aspect:"quality",sentiment:"negative",quote});
    };
    actions.append(add,selected);target.append(actions,el("p","编辑后需点击保存。确认模型原始结果会以模型观点作为当前结果，并保留之前的纠正历史。","muted edit-note"));
    const saves=el("div",null,"save-actions");
    for(const [action,label] of [["corrected","保存人工纠正"],["confirmed","确认模型原始结果"]]) {
      const button=el("button",label,action==="corrected"?"primary":"");
      button.onclick=()=>{sessionStorage.setItem("reviewer",$("reviewer").value);saveHuman(action);}; saves.append(button);
    }
    target.append(saves);
  } else {target.append(el("p","先完成该版本的模型标注，再进行人工确认或纠错。","muted"));}
  const history=el("details");history.append(el("summary",`人工复核历史（${detail.human_reviews.length} 次，最新在前）`));
  for(const event of detail.human_reviews) {
    const item=el("div",null,"history-item");item.append(el("strong",`${state.options.human_statuses[event.action]} · ${event.reviewer}`),el("p",`${event.created_at} ｜ 记录 ${event.id}`,"muted"),el("p",event.note));
    renderOpinions(item,event.opinions);history.append(item);
  }
  target.append(history);
}
async function refresh() {await loadOptions();await loadReviews();}
$("filters").onsubmit=event=>{event.preventDefault();state.page=1;loadReviews();};
$("filters").onreset=()=>{state.page=1;setTimeout(()=>loadReviews(),0);};
$("previous").onclick=()=>{state.page--;loadReviews();};
$("next").onclick=()=>{state.page++;loadReviews();};
$("profile").onchange=()=>{state.page=1;refresh().catch(error=>notify(error.message,true));};
$("refresh").onclick=()=>refresh().catch(error=>notify(error.message,true));
(async()=>{
  await loadOptions();
  choices($("aspect"),state.options.aspects,"全部维度"); choices($("sentiment"),state.options.sentiments,"全部情感");
  choices($("processing-status"),state.options.processing_statuses,"全部状态");choices($("human-status"),state.options.human_statuses,"全部复核状态");
  await loadReviews();
})().catch(error=>notify(error.message,true));
