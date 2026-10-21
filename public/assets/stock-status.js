/* Consumer-facing status; no quote requests or credentials. */
(() => {
  const base = 'https://status.tockett.work/';
  const titles = {
    operational:['当前运行正常','Currently operating normally'], degraded:['服务有波动','Service disruptions'],
    outage:['服务故障','Service outage'], suspended:['服务已暂停','Service suspended'],
    discontinued:['服务已停止','Service discontinued'], unknown:['无法确认最新状态','Latest status unavailable']
  };
  const decision = value => ['suspended','discontinued'].includes(value.status);
  const valid = value => value && value.schemaVersion === 1 && Number.isSafeInteger(value.revision) && value.revision > 0 && titles[value.status] &&
    ['summary','detail','scope'].every(key => ['zh','en'].every(lang => typeof value[key]?.[lang] === 'string' && value[key][lang].length > 0 && value[key][lang].length <= 8000)) &&
    Number.isFinite(Date.parse(value.checkedAt)) && Number.isFinite(Date.parse(value.validUntil)) && Number.isFinite(Date.parse(value.updatedAt)) &&
    Date.parse(value.checkedAt) <= Date.now()+300000 && Date.parse(value.updatedAt) <= Date.now()+300000 && Date.parse(value.validUntil) >= Date.parse(value.checkedAt) && Date.parse(value.validUntil)-Date.parse(value.checkedAt) <= 900000;
  const time = (value,lang) => new Date(value).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', {timeZoneName:'short'});
  let record = null, failed = false, refreshing = false;
  try { const value = JSON.parse(localStorage.getItem('tockett-stock-status-v1')); if(valid(value))record=value; } catch {}
  const read = async path => {
    const response = await fetch(base+path+'?v='+Date.now(), {cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(8000)});
    if(!response.ok)throw Error('status unavailable');
    const raw = await response.text(); if(raw.length>65536)throw Error('status too large');
    return JSON.parse(raw);
  };
  const render = () => {
    const state = record && (decision(record) || (!failed && Date.now()<Date.parse(record.validUntil))) ? record.status : 'unknown';
    document.querySelectorAll('[data-stock-status]').forEach(root => {
      const lang = root.dataset.language || 'en', en = lang === 'en';
      const set = (selector,value) => { const node=root.querySelector(selector); if(node)node.textContent=value; };
      root.querySelector('.stock-status-dot')?.setAttribute('data-state',state);
      set('[data-status-title]', titles[state][en?1:0]);
      set('[data-status-time]', record ? (decision(record)?(en?'Announcement effective: ':'公告生效：'):(en?'Last checked: ':'最后核实：'))+time(record.checkedAt,lang) : (en?'No service status received yet':'尚未获取服务状态'));
      set('[data-status-summary]',state==='unknown' ? (en?'The latest status could not be confirmed. This does not mean normal operation or discontinued service.':'暂时无法确认最新状态，不代表服务正常或已停止。') : record?.summary[lang]);
      set('[data-status-detail]',record ? (state==='unknown'?(en?'Previous announcement: ':'上次公告：'):'')+record.detail[lang] : '');
      set('[data-status-scope]',record ? (en?'Affected: ':'影响范围：')+record.scope[lang] : '');
      const button=root.querySelector('.stock-status-refresh');
      if(button){button.disabled=refreshing;button.textContent=refreshing?(en?'Refreshing…':'正在刷新…'):(en?'Refresh status':'刷新状态');}
    });
  };
  const history = async () => {
    const roots=document.querySelectorAll('[data-stock-history]'); if(!roots.length)return;
    try {
      const values=await read('history.json'); if(!Array.isArray(values)||values.length>30||!values.every(valid))throw Error('invalid history');
      roots.forEach(root => {
        const lang=root.dataset.language,en=lang==='en';root.replaceChildren();
        if(!values.length){root.textContent=en?'No status changes recorded yet.':'暂无状态变化记录。';return;}
        values.slice().reverse().forEach(value => {
          const article=document.createElement('article'), heading=document.createElement('h3'), date=document.createElement('time'), detail=document.createElement('p');
          heading.textContent=value.summary[lang];date.dateTime=value.updatedAt;date.textContent=time(value.updatedAt,lang);detail.textContent=value.detail[lang];
          article.append(heading,date,detail);root.append(article);
        });
      });
    } catch { roots.forEach(root => {root.textContent=root.dataset.language==='en'?'Status history is temporarily unavailable.':'暂时无法读取状态变化记录。';}); }
  };
  const refresh = async () => {
    if(refreshing)return;refreshing=true;render();
    try {
      const value=await read('status.json');
      if(!valid(value)||value.revision<(record?.revision||0)||value.revision===record?.revision&&JSON.stringify(value)!==JSON.stringify(record))throw Error('invalid or older status');
      record=value;failed=false;try{localStorage.setItem('tockett-stock-status-v1',JSON.stringify(value));}catch{}
    } catch { failed=true; }
    refreshing=false;render();await history();
  };
  document.querySelectorAll('.stock-status-refresh').forEach(button => button.addEventListener('click',refresh));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  setInterval(()=>{if(!document.hidden)render();},30000);
  render();refresh();
})();
