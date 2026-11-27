'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('tradingview/alpha_market_radar.html', 'utf8');
const nodes = new Map();
function get(id) {
  if (!nodes.has(id)) nodes.set(id, {value:'', innerHTML:'', textContent:'', disabled:false});
  return nodes.get(id);
}
const context = vm.createContext({document:{getElementById:get},
  fetch:()=>new Promise(()=>{}), setTimeout:()=>{}, window:{confirm:()=>false,signalCards:{}}});
vm.runInContext(html.split('<script>')[1].split('</script>')[0], context);
const clock=1_800_000_000_000;
const score=(value,parts)=>({value,parts,reason:''});
const research_scores={version:'alt-evidence-score-research-v1',
  ae:score(73.5,{A:45,E:28.5}), core:score(60,{A:25,OB:10,C:15,E:10})};
const row=(symbol,ae)=>({symbol,name:symbol,pair:'ALPHA_'+symbol+'USDT',quote:'USDT',
  price:1,r1:.1,V:4,bar_ms:clock-300_000,scanned_ms:clock,fresh:true,active:true,
  ae_ready:true,core_ready:true,status:'正常观察',
  research_scores:{...research_scores,ae:score(ae,{A:ae*.6,E:ae*.4})}});
context.fixture={now:clock,threshold:.2,paused:false,error:'',
  markets:[row('LOW',20),row('HIGH',90),{...row('STALE',100),bar_ms:clock-900_000},
    {...row('MISSING',60),core_ready:false,research_scores:{...research_scores,core:{value:null,reason:'OI／已结算费率不足'}}}],
  alerts:[{display_symbol:'FROZEN',pair:'ALPHA_FROZENUSDT',quote:'USDT',model:'ae',bar_ms:clock-3_000_000,
    price:1,threshold:.2,evidence:{r1:.1,research_scores},outcomes:{}},
    {display_symbol:'OLD',pair:'ALPHA_OLDUSDT',quote:'USDT',model:'core',bar_ms:clock-3_000_000,
    price:1,threshold:.2,evidence:{r1:.1},outcomes:{}}]};
get('sort').value='ae_score';
vm.runInContext('state=fixture;render()',context);
let output=get('markets').innerHTML;
assert.ok(output.indexOf('<strong>HIGH')<output.indexOf('<strong>LOW'));
assert.ok(output.indexOf('<strong>LOW')<output.indexOf('<strong>STALE'));
assert.match(output,/90\.0/);
assert.match(output,/A 54\.0\/60 · E 36\.0\/40/);
assert.match(output,/行情过期或读取失败/);
assert.match(output,/OI／已结算费率不足/);
assert.match(get('alerts').innerHTML,/73\.5/);
assert.match(get('alerts').innerHTML,/触发时价量研究分/);
assert.match(get('alerts').innerHTML,/该预警未记录评分/);
get('sort').value='core_score'; get('sort').oninput();
output=get('markets').innerHTML;
assert.ok(output.indexOf('<strong>MISSING')>output.indexOf('<strong>LOW'));
get('query').value='does-not-exist';get('query').oninput();
assert.match(get('markets').innerHTML,/colspan="9"/);
assert.equal(get('alerts').innerHTML,'');
assert.match(html,/不是成功概率，不决定预警触发/);
assert.match(html,/未触发预警时也可显示分数/);
get('query').value='';
context.fixture.markets[0].observations={paired_symbol:'LOWUSDT',paired_fresh:true,
  rules:{both_volume:true,perp_discount:false},metrics:{spot_quote_1h:25000,perp_quote_1h:125000,
    spot_ratio:2.5,perp_ratio:3,volume_ratio:5}};
context.fixture.markets[2].observations=context.fixture.markets[0].observations;
get('observationFilter').value='both_volume';
get('observationFilter').oninput();
assert.match(get('markets').innerHTML,/<strong>LOW.*双边放量/s);
assert.doesNotMatch(get('markets').innerHTML,/<strong>(HIGH|STALE|MISSING)/);
assert.match(get('signalGroups').innerHTML,/data-signal-group="normal" open/);
vm.runInContext('renderObservations(fixture.markets[0])',context);
assert.match(get('observationFeedback').textContent,/当前命中 1 项 · LOWUSDT 已对齐/);
assert.match(get('observationRules').innerHTML,/期现比 5×.*已命中/s);
assert.match(get('observationRules').innerHTML,/永续明显折价.*未命中/s);
vm.runInContext('renderObservations(fixture.markets[2])',context);
assert.match(get('observationFeedback').textContent,/当前命中 0 项 · 现货缺成交或已过期/);
assert.doesNotMatch(get('observationRules').innerHTML,/已命中/);
get('observationFilter').value='';
context.fixture.markets.push({...row('BTC',30),pair:'BTCUSDT',source:'spot',coin:'SPOT:BTC'});
get('sourceFilter').value='spot';get('sourceFilter').onchange();
get('showMarkets').onclick();
assert.equal(get('marketTable').hidden,false);
assert.equal(get('signalGroups').hidden,true);
assert.match(get('markets').innerHTML,/\/coin#\/coin\/SPOT%3ABTC/);
assert.match(get('markets').innerHTML,/币安现货/);
assert.doesNotMatch(get('markets').innerHTML,/<strong>LOW/);
get('showAlerts').onclick();
assert.equal(get('alertPanel').hidden,false);
assert.equal(get('marketTable').hidden,true);
console.log('PASS: score sorting, stale/missing gating, component points, frozen/legacy alert scores and empty filters');
