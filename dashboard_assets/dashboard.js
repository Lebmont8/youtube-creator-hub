'use strict';
const P='/youtube-hub', $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(Number(v));
const cash=(v,c='USD')=>v===null||v===undefined?'—':new Intl.NumberFormat('ru-RU',{style:'currency',currency:c,maximumFractionDigits:2}).format(v);
const time=v=>v?new Date(v*1000).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'ещё не обновлялись';
const date=v=>v?new Date(v).toLocaleDateString('ru-RU'):'—';
const errors={reconnect_required:'Нужно переподключить канал',not_authorized_or_unavailable:'YouTube не предоставил этот отчёт. Возможны ограничения разрешений или канала.',identity_mismatch:'Аккаунт Google вернул другой канал. Нужно переподключить этот канал.',provider_unavailable:'Источник временно недоступен',rate_limited:'Лимит запросов. Попробуй обновить позже.',channel_not_connected:'Канал не подключён',not_found:'Видео или канал недоступны'};
const labels={overview:['Главная','Твои каналы и последние видео.'],analytics:['Аналитика','Просмотры, время просмотра и рост аудитории.'],channels:['Каналы','Каждый канал — своя динамика и своя аудитория.'],videos:['Контент','Все ролики подключённых каналов.'],money:['Доход','Доход YouTube, реклама и выплаты — раздельно.'],queue:['Загрузки','Очередь и результаты загрузок через этот сервис.'],connections:['Подключения','Управляй доступом к своим площадкам.'],video:['Видео','Статистика и просмотр выбранного ролика.']};
function icon(name){return '<svg class="icon" aria-hidden="true"><use href="#i-'+name+'"></use></svg>';}
const symbolNames={'▷':'eye','◷':'clock','↗':'users','$':'money','◎':'instagram','−':'money','≈':'clock','◫':'chart'};
function symbol(value){return icon(symbolNames[value]||value||'chart');}
const query=new URLSearchParams(location.search);
const state={tab:labels[query.get('tab')]?query.get('tab'):'overview',channel:query.get('channel')||'',video:query.get('video')||'',data:null,detail:null,videoQuery:'',videoPage:0,sort:'published',privacy:'',chartMetric:'views',loading:false};
let timer=null,loadGeneration=0;
async function api(path,body){
 const options={cache:'no-store',credentials:'same-origin',headers:{}};
 if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':$('meta[name="csrf-token"]').content};options.body=JSON.stringify(body);}
 const r=await fetch(P+path,options);
 if(r.status===401){location.assign(P+'/');throw new Error('Сессия истекла. Войди заново.');}
 const data=await r.json();
 if(!r.ok)throw new Error(data.error==='invalid_request'?'Не удалось выполнить запрос. Проверь даты и заполненные поля.':'Не удалось выполнить запрос. Обнови страницу и попробуй снова.');
 return data;
}
function notice(text,type='info'){$('#notice').innerHTML=text?'<div class="banner '+type+'">'+esc(text)+'</div>':'';}
function selected(){return (state.data?.channels||[]).filter(c=>!state.channel||c.id===state.channel);}
function reportValue(c,which,metric){
 const r=c.data?.[which];if(!r?.available||!r.rows?.length)return null;
 return (r.rows||[]).reduce((a,x)=>a+(Number(x[metric])||0),0);
}
function total(which,metric){
 const vals=selected().map(c=>reportValue(c,which,metric)).filter(x=>x!==null);
 return {value:vals.length?vals.reduce((a,b)=>a+b,0):null,count:vals.length};
}
function lifetime(metric){
 const vals=selected().map(c=>c.data?.channel?.totals?.[metric]).filter(x=>x!==null&&x!==undefined);
 return {value:vals.length?vals.reduce((a,b)=>a+b,0):null,count:vals.length};
}
function coverage(n){return 'Данные: '+n+' из '+selected().length+' каналов';}
function metric(label,value,sub,symbol='',highlight=false){return '<article class="metric'+(highlight?' highlight':'')+'"><div class="label">'+esc(label)+'<span class="symbol">'+icon(symbolNames[symbol]||symbol||'chart')+'</span></div><div class="value">'+value+'</div><div class="sub">'+esc(sub)+'</div></article>';}
function metrics(){
 const views=total('activity','views'),watch=total('activity','estimatedMinutesWatched'),gain=total('activity','subscribersGained'),lost=total('activity','subscribersLost'),rev=total('revenue','estimatedRevenue');
 return '<section class="cards" aria-label="Показатели за период">'+metric('Просмотры',fmt(views.value),coverage(views.count),'▷')+metric('Время просмотра',watch.value===null?'—':fmt(watch.value/60)+'<small> ч</small>',coverage(watch.count),'◷')+metric('Подписчики за период',gain.value===null?'—':(gain.value-lost.value>=0?'+':'')+fmt(gain.value-lost.value),'Пришли: '+fmt(gain.value)+' · Ушли: '+fmt(lost.value),'↗')+metric('Оценочный доход YouTube',cash(rev.value),coverage(rev.count)+' · Не фактическая выплата','$',true)+'</section>';
}
function empty(title,description,icon='◫'){return '<div class="empty"><span class="empty-icon">'+symbol(icon)+'</span><strong>'+esc(title)+'</strong><span>'+esc(description)+'</span></div>';}
function series(metric=state.chartMetric){
 const values=new Map();
 selected().forEach(c=>{if(c.data?.activity?.available)(c.data.activity.rows||[]).forEach(r=>values.set(r.day,(values.get(r.day)||0)+(metric==='watch'?(Number(r.estimatedMinutesWatched)||0)/60:metric==='subs'?(Number(r.subscribersGained)||0)-(Number(r.subscribersLost)||0):(Number(r.views)||0))));});
 return [...values].sort((a,b)=>a[0].localeCompare(b[0])).map(([x,y])=>({x,y}));
}
function chart(points,label='Просмотры',retention=false){
 if(!points.length)return empty('Пока нет точек на графике','У источника нет строк отчёта за выбранный период.');
 const pageWidth=$('.page').clientWidth-(innerWidth<=760?36:innerWidth>=1700?88:innerWidth<=1250?56:64);
 const W=Math.max(280,Math.round(state.tab==='overview'&&innerWidth>1250?(pageWidth-24)*1.8/2.8-48:pageWidth-40)),H=210,L=58,R=16,T=20,B=36,max=Math.max(0,...points.map(x=>Number(x.y)||0))||(points.every(x=>Number(x.y)===0)?1:0),min=Math.min(0,...points.map(x=>Number(x.y)||0));
 const xy=points.map((x,i)=>[L+i*(W-L-R)/Math.max(1,points.length-1),T+(H-T-B)*(1-(x.y-min)/(max-min))]);
 const path=xy.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
 const area=path+' L'+xy.at(-1)[0]+','+(H-B)+' L'+L+','+(H-B)+' Z';
 let grid='';for(let i=0;i<4;i++){const y=T+i*(H-T-B)/3;grid+='<line class="grid" x1="'+L+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'"/><text x="'+(L-10)+'" y="'+(y+3)+'" text-anchor="end">'+esc(new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(max-(max-min)*i/3))+'</text>';}
 const marks=[0,Math.floor((points.length-1)/2),points.length-1].filter((v,i,a)=>a.indexOf(v)===i);
 grid+=marks.map(i=>'<text x="'+xy[i][0]+'" y="'+(H-6)+'" text-anchor="middle">'+esc(retention?Math.round(Number(points[i].x)*100)+'% видео':points[i].x.slice(5).split('-').reverse().join('.'))+'</text>').join('');
 return '<svg class="chart" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="'+esc(label)+'"><defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#75bfff" stop-opacity=".21"/><stop offset="100%" stop-color="#789cf0" stop-opacity=".015"/></linearGradient></defs>'+grid+'<path class="area" d="'+area+'"/><path class="line" d="'+path+'"/>'+xy.map(([x,y])=>'<circle cx="'+x+'" cy="'+y+'" r="2.3" fill="#75bfff"/>').join('')+'</svg>';
}
function chartsPanel(){
 const title={views:'Просмотры',watch:'Время просмотра, ч',subs:'Изменение подписчиков'}[state.chartMetric];
 return '<section class="panel"><div class="panel-head"><div><h2>Аналитика за период</h2><p>'+coverage(total('activity','views').count)+'</p></div></div><div class="chart-tabs" role="group" aria-label="Показатель на графике">'+[['views','Просмотры'],['watch','Время просмотра'],['subs','Подписчики']].map(([key,label])=>'<button data-chart="'+key+'" aria-pressed="'+(key===state.chartMetric)+'" class="'+(key===state.chartMetric?'active':'')+'">'+label+'</button>').join('')+'</div>'+chart(series(),title)+'<p class="chart-note">Нет строк отчёта — нет точки на графике. Это не нулевые просмотры.</p></section>';
}
function latestVideo(){
 const v=allVideos().sort((a,b)=>(b.published_at||'').localeCompare(a.published_at||''))[0];
 if(!v)return '<section class="panel"><h2>Последнее видео</h2>'+empty('Видео ещё нет','Обнови данные подключённых каналов.','video')+'</section>';
 return '<section class="panel"><div class="panel-head"><h2>Последнее видео</h2></div><div class="latest-video"><button class="text-button" aria-label="Открыть последнее видео" data-video="'+esc(v.id)+'" data-channel="'+esc(v.channel_id)+'">'+thumb(v)+'</button><div><small>'+esc(v.channel_name)+' · '+date(v.published_at)+'</small><h3><button class="text-button" data-video="'+esc(v.id)+'" data-channel="'+esc(v.channel_id)+'">'+esc(v.title)+'</button></h3><span class="tag">'+icon(v.privacy==='private'?'lock':v.privacy==='public'?'globe':'link')+esc(({private:'Личное',public:'Открытое',unlisted:'По ссылке'})[v.privacy]||'Видео')+'</span><div class="inline-stats"><div><small>Просмотры всего</small>'+fmt(v.statistics.viewCount)+'</div><div><small>Лайки</small>'+fmt(v.statistics.likeCount)+'</div></div><div class="actions"><button class="quiet" data-video="'+esc(v.id)+'" data-channel="'+esc(v.channel_id)+'">'+icon('chart')+' Аналитика видео</button><button class="text-button" data-tab="videos">Весь контент '+icon('arrow')+'</button></div></div></div></section>';
}
function analyticsPage(){return metrics()+chartsPanel()+'<section class="panel"><div class="panel-head"><div><h2>Сравнение каналов</h2><p>Просмотры и доход — за период. Подписчики — текущий счётчик.</p></div></div>'+channelTable()+'</section>';}
function channelTable(){
 return '<div class="table-wrap"><table><thead><tr><th>Канал</th><th class="num">Просмотры за период</th><th class="num">Подписчики всего</th><th class="num">Оценочный доход</th><th>Данные</th></tr></thead><tbody>'+selected().map(c=>'<tr><td><button class="text-button" data-open-channel="'+esc(c.id)+'"><span class="badge-dot"></span>'+esc(c.name)+'</button></td><td class="num">'+fmt(reportValue(c,'activity','views'))+'</td><td class="num">'+fmt(c.data?.channel?.totals?.subscriberCount)+'</td><td class="num">'+cash(reportValue(c,'revenue','estimatedRevenue'))+'</td><td><span class="tag '+(c.error?'red':c.data?'green':'')+'">'+(c.error?'Не обновлены':c.data?'Обновлены':'Ожидают обновления')+'</span></td></tr>').join('')+'</tbody></table></div>';
}
function channelSide(){
 return '<section class="panel"><div class="panel-head"><div><h2>Подключённые каналы</h2><p>'+state.data.channels.length+' YouTube-каналов в кабинете</p></div></div><div class="channel-list">'+selected().map(c=>'<div class="channel-row"><div class="channel-logo">'+esc(c.name.slice(0,1).toUpperCase())+'</div><div><button class="title" data-open-channel="'+esc(c.id)+'">'+esc(c.name)+'</button><small><span class="badge-dot"></span>YouTube</small></div><div class="right">'+(c.data?fmt((c.data.videos||[]).length):'—')+'<small>в библиотеке</small></div></div>').join('')+'</div></section>';
}
function overview(){
 return '<div class="section-heading"><h2>Результаты за период</h2><button class="text-button jump-link" data-tab="analytics">Вся аналитика '+icon('arrow')+'</button></div>'+metrics()+'<div class="split"><div>'+chartsPanel()+'</div>'+latestVideo()+'</div><section class="panel summary-panel"><div class="panel-head"><h2>Текущее состояние каналов</h2><button class="text-button" data-tab="channels">Все каналы '+icon('arrow')+'</button></div><div class="inline-stats"><div><small>Подписчики всего</small>'+fmt(lifetime('subscriberCount').value)+'</div><div><small>Просмотры всего</small>'+fmt(lifetime('viewCount').value)+'</div><div><small>Открытые видео</small>'+fmt(lifetime('videoCount').value)+'</div><div><small>Видео в библиотеке</small>'+fmt(allVideos().length)+'</div></div><p class="chart-note">Счётчики за всё время. Библиотека также включает личные видео.</p></section>'+channelSide();
}
function channelsPage(){
 if(!state.channel)return metrics()+'<section class="panel"><div class="panel-head"><div><h2>Все каналы</h2><p>Выбери название, чтобы открыть канал и его видео</p></div></div>'+channelTable()+'</section>';
 const c=selected()[0];if(!c)return empty('Канал не найден','Выбери канал из списка.');
 return '<div class="channel-hero"><div class="channel-logo">'+esc(c.name.slice(0,1))+'</div><div><h2>'+esc(c.name)+'</h2><p>YouTube · '+esc(c.id)+'</p></div><button class="quiet" data-tab="videos">Видео канала →</button></div>'+metrics()+chartsPanel()+'<section class="panel"><h2>За всё время</h2><div class="inline-stats"><div><small>Просмотры</small>'+fmt(c.data?.channel?.totals?.viewCount)+'</div><div><small>Подписчики</small>'+fmt(c.data?.channel?.totals?.subscriberCount)+'</div><div><small>Открытые видео</small>'+fmt(c.data?.channel?.totals?.videoCount)+'</div><div><small>Средняя длительность за период</small>'+average(c)+'</div></div></section>';
}
function average(c){const report=c.data?.activity;if(!report?.available)return '—';const rows=(report.rows||[]).filter(r=>Number.isFinite(Number(r.averageViewDuration))&&Number(r.views)>0);const views=rows.reduce((n,r)=>n+Number(r.views),0);return views?fmt(rows.reduce((n,r)=>n+Number(r.averageViewDuration)*Number(r.views),0)/views)+' с':'—';}
function allVideos(){
 const rows=[];selected().forEach(c=>{(c.data?.videos||[]).forEach(v=>rows.push({...v,channel_name:c.name,period:(c.data.video_activity?.rows||[]).find(x=>x.video===v.id)}));});
 return rows;
}
function thumb(v){return v.thumbnail?'<img class="thumb" alt="" loading="lazy" src="'+esc(v.thumbnail)+'">':'<span class="thumb placeholder">'+icon('video')+'</span>';}
function videosPage(){
 const incomplete=selected().some(c=>c.data&&!c.data.videos_complete);
 return '<section class="panel"><div class="content-toolbar"><input id="video-search" type="search" placeholder="Поиск по названию…" aria-label="Найти видео" value="'+esc(state.videoQuery)+'"><select id="video-privacy" aria-label="Видимость видео">'+[['','Любая видимость'],['public','Открытые'],['private','Личные'],['unlisted','По ссылке']].map(([k,v])=>'<option value="'+k+'" '+(state.privacy===k?'selected':'')+'>'+v+'</option>').join('')+'</select><select id="video-sort" aria-label="Сортировка видео">'+[['published','Сначала новые'],['views','По просмотрам'],['title','По названию']].map(([k,v])=>'<option value="'+k+'" '+(state.sort===k?'selected':'')+'>'+v+'</option>').join('')+'</select><span id="library-count" class="library-count"></span></div>'+(incomplete?'<div class="banner">Часть библиотеки недоступна. Показаны доступные видео, не более 500 на канал.</div>':'')+'<div id="video-rows"></div><div id="video-pages" class="actions"></div><p class="chart-note">Общие счётчики роликов — за всё время. Столбец «За период» — отдельный отчёт выбранных дат.</p></section>';
}
function videoRows(){
 const container=$('#video-rows');if(!container)return;
 const videos=allVideos().filter(v=>(!state.privacy||v.privacy===state.privacy)&&(v.title+' '+v.channel_name).toLowerCase().includes(state.videoQuery.toLowerCase())).sort((a,b)=>state.sort==='views'?(Number(b.statistics.viewCount)||0)-(Number(a.statistics.viewCount)||0):state.sort==='title'?a.title.localeCompare(b.title,'ru'):(b.published_at||'').localeCompare(a.published_at||''));
 $('#library-count').textContent='Видео: '+fmt(videos.length);
 const pages=Math.ceil(videos.length/25);state.videoPage=Math.min(state.videoPage,Math.max(0,pages-1));
 const rows=videos.slice(state.videoPage*25,state.videoPage*25+25);
 container.innerHTML=rows.length?'<div class="table-wrap"><table><thead><tr><th>Видео</th><th>Канал / дата</th><th class="num">Просмотры всего</th><th class="num">За период</th><th class="num">Лайки</th><th class="num">Комментарии</th></tr></thead><tbody>'+rows.map(v=>'<tr><td class="video-title"><button class="text-button" data-video="'+esc(v.id)+'" data-channel="'+esc(v.channel_id)+'">'+thumb(v)+'<span>'+esc(v.title)+'</span></button><div class="muted"><span class="tag">'+esc(({private:'Личное',public:'Открытое',unlisted:'По ссылке'})[v.privacy]||'Видео')+'</span></div></td><td>'+esc(v.channel_name)+'<div class="muted">'+date(v.published_at)+'</div></td><td class="num">'+fmt(v.statistics.viewCount)+'</td><td class="num">'+fmt(v.period?.views)+'</td><td class="num">'+fmt(v.statistics.likeCount)+'</td><td class="num">'+fmt(v.statistics.commentCount)+'</td></tr>').join('')+'</tbody></table></div>':empty('Видео пока не найдены','Обнови данные канала или измени поисковый запрос.','▷');
 $('#video-pages').innerHTML=pages>1?'<button class="quiet" data-video-page="-1" '+(state.videoPage===0?'disabled':'')+'>← Назад</button><span class="muted">'+(state.videoPage+1)+' / '+pages+'</span><button class="quiet" data-video-page="1" '+(state.videoPage+1>=pages?'disabled':'')+'>Далее →</button>':'';
}

function videoPage(){
 const cached=allVideos().find(x=>x.id===state.video&&x.channel_id===state.channel);
 const d=state.detail?.data,v=d?.video||cached;
 if(!v)return empty('Данные видео ещё не загружены','Нажми «Обновить данные» для этого ролика.');
 const fake={data:d},views=reportValue(fake,'activity','views'),watch=reportValue(fake,'activity','estimatedMinutesWatched'),rev=reportValue(fake,'revenue','estimatedRevenue');
 let content='<div class="subnav"><button class="text-button" data-tab="videos">← Все видео</button><span class="current">/ '+esc(v.title)+'</span></div><div class="video-layout"><div><iframe class="player" title="Просмотр видео" src="https://www.youtube-nocookie.com/embed/'+esc(v.id)+'" allow="fullscreen; picture-in-picture" allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe><p class="muted">Личное видео может потребовать входа в YouTube. <a target="_blank" rel="noopener noreferrer" href="https://www.youtube.com/watch?v='+esc(v.id)+'">Открыть на YouTube ↗</a></p></div><div class="video-info"><span class="tag blue">YouTube</span><h2>'+esc(v.title)+'</h2><div class="muted">'+esc(d?.channel.name||cached?.channel_name||'')+' · '+date(v.published_at)+'</div><p>'+esc(v.description||'Описание не указано.')+'</p><div class="inline-stats"><div><small>Просмотры всего</small>'+fmt(v.statistics.viewCount)+'</div><div><small>Лайки всего</small>'+fmt(v.statistics.likeCount)+'</div><div><small>Комментарии всего</small>'+fmt(v.statistics.commentCount)+'</div></div></div></div>';
 if(state.detail?.error)content+='<div class="banner">'+esc(errors[state.detail.error]||'Не удалось обновить видео')+'</div>';
 if(!d)return content+'<div class="banner info">Подробный отчёт этого ролика ещё не загружен. Нажми «Обновить данные»; общий список при этом сохранится.</div>';
 content+='<section class="cards">'+metric('Просмотры за период',fmt(views),'Из отдельного отчёта этого видео','▷')+metric('Время просмотра',watch===null?'—':fmt(watch/60)+' ч','За выбранный период','◷')+metric('Средняя длительность',average(fake),'Среднее из дневных отчётов YouTube','≈')+metric('Оценочный доход',cash(rev),'USD · Не фактическая выплата','$',true)+'</section>';
 const points=(d.activity?.rows||[]).map(r=>({x:r.day,y:Number(r.views)||0}));
 content+='<section class="panel"><div class="panel-head"><div><h2>Просмотры ролика по дням</h2><p>Обновлено '+time(d.fetched_at)+'</p></div></div>'+(d.activity?.available?chart(points):empty('Отчёт недоступен',errors[d.activity?.reason]||'YouTube не вернул данные'))+'</section>';
 const retention=(d.retention?.rows||[]).map(r=>({x:r.elapsedVideoTimeRatio,y:Number(r.audienceWatchRatio)*100}));
 return content+'<section class="panel"><div class="panel-head"><div><h2>Удержание аудитории</h2><p>По горизонтали — доля длины ролика; по вертикали — доля просмотров участка, %.</p></div></div>'+(d.retention?.available?chart(retention,'Удержание аудитории',true):empty('Удержание пока недоступно',errors[d.retention?.reason]||'Нет подходящих данных у источника.'))+'<p class="chart-note">Повторные просмотры участков могут давать значение выше 100%.</p></section>';
}
function financeRows(){return (state.data.finance||[]).filter(r=>!state.channel||r.channel===state.channel);}
const kinds={sponsorship:'Рекламная интеграция',payout:'Выплата платформы',expense:'Расход'};
function moneyPage(){
 const rev=total('revenue','estimatedRevenue'),entries=financeRows(),groups=new Map();
 entries.filter(x=>x.status==='received').forEach(r=>{const key=r.kind+'|'+r.currency;groups.set(key,(groups.get(key)||0)+r.amount_minor/100);});
 return '<section class="cards">'+metric('Доход YouTube за период',cash(rev.value),coverage(rev.count)+' · Оценка API, USD','$',true)+metric('Записи о выплатах',fmt(entries.filter(x=>x.kind==='payout').length),'Введены вручную, не получены из YouTube API','↗')+metric('Рекламные интеграции',fmt(entries.filter(x=>x.kind==='sponsorship').length),'Учитываются отдельно от дохода платформы','◎')+metric('Записи о расходах',fmt(entries.filter(x=>x.kind==='expense').length),'Не вычитаются из оценочного дохода автоматически','−')+'</section><section class="panel"><div class="panel-head"><div><h2>Доход от платформы</h2><p>Оценочный доход YouTube по каналам</p></div></div>'+channelTable()+'<p class="money-note">Оценочный доход — не выплата на банковский счёт. Отчёт может быть недоступен для немонетизированного канала или без выданного разрешения на доходы. Это не нулевой доход. Фактические выплаты и рекламу можно внести ниже.</p></section><section class="panel"><div class="panel-head"><div><h2>Реклама, выплаты и расходы</h2><p>Ручные записи за выбранные даты · валюты и типы дохода не смешиваются</p></div><button class="primary" data-new-finance>+ Добавить запись</button></div>'+(groups.size?'<div class="currency-totals">'+[...groups].map(([key,value])=>{const [kind,currency]=key.split('|');return '<div class="currency-card"><small>'+esc(kinds[kind])+' · подтверждённые</small><strong>'+cash(value,currency)+'</strong></div>';}).join('')+'</div>':'')+'<p class="money-note">Ожидаемые суммы не входят в подтверждённые итоги. Выплата платформы не прибавляется повторно к оценочному доходу YouTube.</p>'+(entries.length?'<div class="table-wrap"><table><thead><tr><th>Запись</th><th>Канал</th><th>Дата</th><th class="num">Сумма</th><th>Статус</th><th></th></tr></thead><tbody>'+entries.map(r=>'<tr><td>'+esc(kinds[r.kind])+'<div class="muted">'+esc(r.note)+'</div></td><td>'+esc(state.data.channels.find(c=>c.id===r.channel)?.name||'Общая запись')+'</td><td>'+date(r.date)+'</td><td class="num">'+cash(r.amount_minor/100,r.currency)+'</td><td><span class="tag '+(r.status==='received'?'green':'')+'">'+(r.status==='received'?'Получено / оплачено':'Ожидается')+'</span></td><td><div class="actions"><button class="text-button" data-edit-finance="'+r.id+'">Изменить</button><button class="text-button danger" data-delete-finance="'+r.id+'">Удалить</button></div></td></tr>').join('')+'</tbody></table></div>':empty('Пока нет ручных записей','Добавь рекламную интеграцию, выплату или расход. Введённые записи не переводят деньги.','$'))+'</section>';
}
function queuePage(){
 const jobs=state.data.jobs.filter(x=>!state.channel||x.channel_id===state.channel);
 const names={queued:'В очереди',uploading:'Загружается',uploaded:'Загружено',completed:'Завершено',failed:'Ошибка',needs_reconcile:'Нужна проверка результата',published:'Опубликовано'};
 return '<section class="panel"><div class="panel-head"><div><h2>Очередь загрузок</h2><p>Только задания этого сервиса. Видео, загруженные другими способами, находятся в разделе «Видео».</p></div></div>'+(jobs.length?'<div class="table-wrap"><table><thead><tr><th>Видео / канал</th><th>Статус</th><th>Готовность</th><th>Запланировано</th><th>Обновлено</th></tr></thead><tbody>'+jobs.map(j=>'<tr><td>'+esc(j.title)+'<div class="muted">'+esc(state.data.channels.find(c=>c.id===j.channel_id)?.name||'')+'</div></td><td><span class="tag">'+esc(names[j.status]||'Требует проверки')+'</span></td><td>'+fmt(j.progress)+'%</td><td>'+time(j.due)+'</td><td>'+time(j.updated)+'</td></tr>').join('')+'</tbody></table></div>':empty('Очередь пуста','Ни одного видео через этот сервис пока не загружено.','◷'))+'<p class="money-note">Текущий загрузчик допускает только личные видео. Открытие кабинета и обновление аналитики ничего не публикуют.</p></section>';
}
function connectionsPage(){
 const csrf=esc($('meta[name="csrf-token"]').content);
 return '<section class="panel"><div class="panel-head"><div><h2>Источники данных</h2><p>Подключения сохранены на сервере. Пароли аккаунтов для просмотра статистики не нужны.</p></div></div><div class="source-grid"><article class="source-card"><span class="source-mark">'+icon('youtube')+'</span><h3>YouTube</h3><span class="tag green">'+state.data.channels.length+' каналов подключено</span><p>Статистика каналов и видео, доступные отчёты о доходе. Для каждого нового канала владелец лично подтверждает разрешения Google.</p><form method="post" action="'+P+'/connect"><input type="hidden" name="csrf" value="'+csrf+'"><button class="primary">Подключить канал через Google</button></form></article><article class="source-card inactive"><span class="source-mark">'+icon('instagram')+'</span><h3>Instagram</h3><span class="tag">Не подключён к этому кабинету</span><p>Отдельного доступа Instagram у этого кабинета пока нет. Его данные не включены в сводку; повторно выдавать разрешения YouTube для этого не нужно.</p></article></div></section><section class="panel"><div class="panel-head"><div><h2>Состояние каналов</h2><p>«Подключён» означает сохранённое разрешение; свежесть данных показана отдельно.</p></div></div><div class="table-wrap"><table><thead><tr><th>Канал</th><th>Подключение</th><th>Последние данные</th><th></th></tr></thead><tbody>'+state.data.channels.map(c=>'<tr><td>'+esc(c.name)+'</td><td><span class="tag '+(c.status==='connected'?'green':'red')+'">'+(c.status==='connected'?'Подключён':'Нужно переподключить')+'</span></td><td>'+time(c.data?.fetched_at)+(c.error?'<div class="error-text">'+esc(errors[c.error]||'Данные не обновлены')+'</div>':'')+'</td><td><button class="text-button" data-open-channel="'+esc(c.id)+'">Открыть канал →</button></td></tr>').join('')+'</tbody></table></div><p class="money-note">Если Google сообщает о просроченной ссылке, вернись сюда и начни новое подключение. Уже сохранённые каналы от этого не исчезают.</p></section>';
}
function syncURL(){
 const params=new URLSearchParams({tab:state.tab});if(state.channel)params.set('channel',state.channel);if(state.video)params.set('video',state.video);
 history.replaceState(null,'',P+'/?'+params.toString());
}
function render(){
 if(!state.data)return;
 const [title,description]=labels[state.tab];
 $('#page-title').textContent=title;$('#crumb').textContent=title;$('#page-description').textContent=description;
 document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===(state.tab==='video'?'videos':state.tab)));
 $('#channel').value=state.channel;
 const samples=selected(),times=samples.map(c=>c.data?.fetched_at).filter(Boolean),available=samples.filter(c=>c.data).length;
 const failures=samples.filter(c=>c.error).length,active=state.data.running.length;
 $('#refresh').disabled=!!active;
 $('#refresh').innerHTML=icon('refresh')+(active?' Обновление…':' Обновить данные');
 const reported=samples.map(c=>c.data?.activity?.reported_through).filter(Boolean).sort()[0];
 $('#freshness').textContent=(times.length?'Последнее успешное обновление: '+time(Math.min(...times)):'Данные ещё не обновлялись')+' · Получены данные '+available+'/'+samples.length+' каналов'+(reported?' · Дневные отчёты доступны по '+date(reported):'')+(active?' · В работе: '+active:'');
 if(failures)notice('Не удалось обновить '+failures+' каналов. Если есть прежние данные за этот период, они сохранены; смотри дату обновления.','');
 else if(!available&&state.tab!=='connections'&&state.tab!=='queue')notice('Нажми «Обновить данные», чтобы получить статистику за выбранный период.');
 else if(total('activity','views').count<samples.length&&['overview','analytics','channels','money'].includes(state.tab))notice('У части каналов нет строк аналитики за выбранный период. В сводке учтены только полученные показатели; «—» означает отсутствие данных, не ноль.','');
 else notice('');
 const views={overview,analytics:analyticsPage,channels:channelsPage,videos:videosPage,video:videoPage,money:moneyPage,queue:queuePage,connections:connectionsPage};
 $('#content').innerHTML=views[state.tab]();videoRows();syncURL();
}
async function load(){
 const generation=++loadGeneration;
 try{
  const q=new URLSearchParams();if($('#start').value)q.set('start',$('#start').value);if($('#end').value)q.set('end',$('#end').value);
  const data=await api('/api/dashboard?'+q);
  if(generation!==loadGeneration)return;
  state.data=data;$('#start').value=data.start;$('#end').value=data.end;$('#end').max=new Date().toISOString().slice(0,10);
  if(state.channel&&!data.channels.some(c=>c.id===state.channel)){state.channel='';state.video='';if(state.tab==='video')state.tab='videos';}
  if(state.tab==='video'&&state.channel&&state.video){
   state.detail=await api('/api/video/'+encodeURIComponent(state.channel)+'/'+encodeURIComponent(state.video)+'?'+new URLSearchParams({start:data.start,end:data.end}));
   if(generation!==loadGeneration)return;
  }
  render();clearTimeout(timer);if(data.running.length)timer=setTimeout(load,2500);
 }catch(error){if(state.data){$('#start').value=state.data.start;$('#end').value=state.data.end;}notice(error.message,'');}
}
async function refresh(extra={}){
 if(!state.data)return;
 try{
  const body={start:$('#start').value,end:$('#end').value,channel:state.channel||undefined,...extra};
  if(state.tab==='video'&&state.video)body.video=state.video;
  await api('/api/refresh',body);await load();
 }catch(error){notice(error.message,'');}
}
function openFinance(id){
 const form=$('#finance-form');form.reset();$('#finance-error').textContent='';
 const row=state.data.finance.find(x=>x.id===id);
 if(row){for(const key of ['id','channel','kind','date','currency','status','note'])form.elements[key].value=row[key]||'';form.elements.amount.value=(row.amount_minor/100).toFixed(2);}
 else {form.elements.date.value=new Date().toISOString().slice(0,10);form.elements.channel.value=state.channel;}
 $('#finance-dialog').showModal();
}
document.addEventListener('click',async e=>{
 const button=e.target.closest('button');if(!button)return;
 if(button.dataset.chart){state.chartMetric=button.dataset.chart;render();}
 if(button.dataset.tab){state.tab=button.dataset.tab;state.video='';state.detail=null;render();$('.sidebar').classList.remove('open');$('#mobile-menu').setAttribute('aria-expanded','false');}
 if(button.dataset.openChannel){state.channel=button.dataset.openChannel;state.tab='channels';state.video='';state.detail=null;render();}
 if(button.dataset.video){state.channel=button.dataset.channel;state.video=button.dataset.video;state.tab='video';state.detail=null;await load();if(!state.detail?.data&&!state.data.running.length)await refresh();}
 if(button.dataset.videoPage){state.videoPage+=Number(button.dataset.videoPage);videoRows();}
 if(button.hasAttribute('data-new-finance'))openFinance();
 if(button.dataset.editFinance)openFinance(button.dataset.editFinance);
 if(button.hasAttribute('data-close-dialog'))$('#finance-dialog').close();
 if(button.dataset.deleteFinance&&confirm('Удалить эту ручную запись? Данные YouTube не изменятся.')){
  try{await api('/api/finance/'+button.dataset.deleteFinance+'/delete',{});await load();}catch(error){notice(error.message,'');}
 }
});
document.addEventListener('input',e=>{if(e.target.id==='video-search'){state.videoQuery=e.target.value;state.videoPage=0;videoRows();}});
$('#refresh').addEventListener('click',()=>refresh());
$('#mobile-menu').addEventListener('click',()=>{const open=$('.sidebar').classList.toggle('open');$('#mobile-menu').setAttribute('aria-expanded',String(open));});
$('#channel').addEventListener('change',async e=>{state.channel=e.target.value;state.video='';state.detail=null;if(state.tab==='video')state.tab='channels';state.videoPage=0;await load();});
$('#range').addEventListener('change',async e=>{
 if(e.target.value==='custom')return;
 const end=new Date($('#end').value+'T12:00:00Z');end.setUTCDate(end.getUTCDate()-Number(e.target.value)+1);$('#start').value=end.toISOString().slice(0,10);
 await load();if(selected().some(c=>!c.data))await refresh();
});
$('#apply-dates').addEventListener('click',async()=>{$('#range').value='custom';await load();if(selected().some(c=>!c.data))await refresh();});
$('#finance-form').addEventListener('submit',async e=>{
 e.preventDefault();const body=Object.fromEntries(new FormData(e.target));
 const save=e.target.querySelector('[type=submit]');save.disabled=true;
 try{await api('/api/finance',body);$('#finance-dialog').close();await load();}
 catch(error){$('#finance-error').textContent=error.message;}finally{save.disabled=false;}
});
$('#global-search').addEventListener('submit',e=>{e.preventDefault();state.videoQuery=$('#global-query').value;state.videoPage=0;state.tab='videos';state.video='';state.detail=null;render();});
document.addEventListener('change',e=>{if(e.target.id==='video-privacy'){state.privacy=e.target.value;state.videoPage=0;videoRows();}if(e.target.id==='video-sort'){state.sort=e.target.value;state.videoPage=0;videoRows();}});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)){e.preventDefault();$('#global-query').focus();}if(e.key==='Escape'){$('.sidebar').classList.remove('open');$('#mobile-menu').setAttribute('aria-expanded','false');}});
let resizeTimer,renderWidth=innerWidth;
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state.data&&renderWidth!==innerWidth){renderWidth=innerWidth;render();}},160);});
window.addEventListener('popstate',()=>location.reload());
load();
