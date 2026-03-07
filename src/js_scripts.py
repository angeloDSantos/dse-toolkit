"""
src/js_scripts.py — All JavaScript injection strings.

Centralised here so they are easy to maintain and not scattered
across 5 different files.
"""

# ─── Auto-scroll: walk shadow DOM and fire scroll + wheel events ─────────────
SCROLL_ALL_JS = r"""
var amt = arguments[0];
function gs(el){return el.shadowRoot||null;}
function scrollAll(root, depth){
    if(depth>14) return;
    var all=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<all.length;i++){
        var el=all[i], sh=el.scrollHeight, ch=el.clientHeight;
        if(sh>ch+30 && ch>40){
            var ov=window.getComputedStyle(el).overflow+' '+window.getComputedStyle(el).overflowY;
            if(ov.indexOf('scroll')!==-1||ov.indexOf('auto')!==-1) el.scrollTop+=amt;
        }
        var sr=gs(el); if(sr) scrollAll(sr, depth+1);
    }
}
scrollAll(document, 0);
function fireWheel(root, depth){
    if(depth>14) return;
    var all=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<all.length;i++){
        var el=all[i], sh=el.scrollHeight, ch=el.clientHeight;
        if(sh>ch+30 && ch>40){
            var ov=window.getComputedStyle(el).overflow+' '+window.getComputedStyle(el).overflowY;
            if(ov.indexOf('scroll')!==-1||ov.indexOf('auto')!==-1)
                el.dispatchEvent(new WheelEvent('wheel',{deltaY:amt,bubbles:true,cancelable:true}));
        }
        var sr=el.shadowRoot; if(sr) fireWheel(sr, depth+1);
    }
}
fireWheel(document, 0);
"""

# ─── Get max scrollHeight of any scrollable container ────────────────────────
GET_SCROLL_HEIGHT_JS = r"""
function gs(el){return el.shadowRoot||null;}
function maxSH(root, depth){
    if(depth>14) return 0;
    var best=0, all=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<all.length;i++){
        var el=all[i], sh=el.scrollHeight, ch=el.clientHeight;
        if(sh>ch+30 && ch>40){
            var ov=window.getComputedStyle(el).overflow+' '+window.getComputedStyle(el).overflowY;
            if((ov.indexOf('scroll')!==-1||ov.indexOf('auto')!==-1) && sh>best) best=sh;
        }
        var sr=gs(el); if(sr){var v=maxSH(sr,depth+1); if(v>best) best=v;}
    }
    return best;
}
return maxSH(document, 0);
"""

# ─── Harvest all <a> links with text (shadow DOM walk) ───────────────────────
HARVEST_LINKS_WITH_TEXT_JS = r"""
try {
  function gs(el){return el.shadowRoot||el.$shadowRoot||null;}
  function walk(root, fn){
    var els=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<els.length;i++){fn(els[i]);var s=gs(els[i]);if(s)walk(s,fn);}
  }
  var out=[];
  walk(document, function(el){
    if(el.tagName==='A'){
      var h=(el.getAttribute('href')||'').trim();
      if(h.indexOf('/lightning/r/')!==-1 && h.indexOf('/view')!==-1){
        var txt=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
        out.push({href:h, text:txt});
      }
    }
  });
  return out;
} catch(e){ return []; }
"""

# ─── Harvest all links (shadow, flat DOM, and text regex) ────────────────────
HARVEST_SHADOW_JS = r"""
try{
  function gs(el){return el.shadowRoot||el.$shadowRoot||null;}
  function walk(root,fn){var els=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<els.length;i++){fn(els[i]);var s=gs(els[i]);if(s)walk(s,fn);}}
  var out=[];
  walk(document,function(el){if(el.tagName==='A'){
    var h=(el.getAttribute('href')||'').trim();
    if(h.indexOf('/lightning/r/')!==-1&&h.indexOf('/view')!==-1)out.push(h);}});
  return out;
}catch(e){return[];}
"""

HARVEST_FLAT_JS = r"""
try{var a=document.querySelectorAll('a'),out=[];
for(var i=0;i<a.length;i++){var h=(a[i].getAttribute('href')||'').trim();
  if(h.indexOf('/lightning/r/')!==-1&&h.indexOf('/view')!==-1)out.push(h);}
return out;}catch(e){return[];}
"""

HARVEST_TEXT_JS = r"""
try{var m=(document.documentElement.innerHTML||'')
    .match(/\/lightning\/r\/[a-zA-Z0-9]{3,18}\/view/g)||[];
var seen={},out=[];for(var i=0;i<m.length;i++)if(!seen[m[i]]){seen[m[i]]=1;out.push(m[i]);}
return out;}catch(e){return[];}
"""

# ─── Click the "Related" tab ─────────────────────────────────────────────────
CLICK_RELATED_JS = r"""
function gs(el){return el.shadowRoot||null;}
function walk(root,d){
    if(d>14) return false;
    var els=root.querySelectorAll?root.querySelectorAll('a,button,[role="tab"]'):[];
    for(var i=0;i<els.length;i++){
        var t=(els[i].innerText||els[i].textContent||els[i].getAttribute('title')||'').trim().toLowerCase();
        if(t==='related'){els[i].click(); return true;}
    }
    var all=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<all.length;i++){var sr=gs(all[i]); if(sr&&walk(sr,d+1)) return true;}
    return false;
}
return walk(document, 0);
"""

# ─── Find "Orders" link on record page ────────────────────────────────────────
FIND_ORDERS_JS = r"""
function gs(el){return el.shadowRoot||null;}
function walk(root,d){
    if(d>16) return null;
    var els=root.querySelectorAll?root.querySelectorAll('a'):[];
    for(var i=0;i<els.length;i++){
        var txt=(els[i].innerText||els[i].textContent||'').replace(/\s+/g,' ').trim();
        var href=(els[i].getAttribute('href')||'');
        if(/orders/i.test(txt) && href.indexOf('/related/')!==-1)
            return {href:href, text:txt};
    }
    for(var i=0;i<els.length;i++){
        var href=(els[i].getAttribute('href')||'');
        if(href.indexOf('/related/')!==-1 && /order/i.test(href))
            return {href:href, text:'Orders'};
    }
    var all=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<all.length;i++){var sr=gs(all[i]); if(sr){var v=walk(sr,d+1); if(v) return v;}}
    return null;
}
return walk(document, 0);
"""

# ─── Find the POC (Point of Contact) link on an Order page ───────────────────
FIND_POC_JS = r"""
try{
  function gs(el){return el.shadowRoot||el.$shadowRoot||null;}
  function norm(s){return(s||'').replace(/\s+/g,' ').trim().toLowerCase();}
  function walk(root,visit){var els=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<els.length;i++){visit(els[i]);var sh=gs(els[i]);if(sh)walk(sh,visit);}}
  function ctx(el,n){var cur=el,t='';
    for(var i=0;i<(n||18)&&cur;i++){t+=norm(cur.innerText||cur.textContent||'');cur=cur.parentElement;}
    return t;}
  var cands=[];
  walk(document,function(el){
    if(el.tagName==='A'){var href=el.getAttribute('href')||'';
      if(href.indexOf('/lightning/r/Contact/')!==-1){
        var txt=(el.innerText||el.textContent||'').trim();
        var c=ctx(el,18),score=10;
        if(c.indexOf('main poc')!==-1) score=0;
        else if(c.indexOf('poc')!==-1) score=2;
        var m=href.match(/\/lightning\/r\/Contact\/([a-zA-Z0-9]{15,18})\/view/);
        if(m&&m[1]&&m[1].startsWith('003')) score-=1;
        cands.push({href:href,text:txt,score:score});}}});
  cands.sort(function(a,b){
    return a.score!==b.score?a.score-b.score:(b.text||'').length-(a.text||'').length;});
  return cands.length?cands[0]:null;
}catch(e){return null;}
"""

# ─── Get record name from page header ────────────────────────────────────────
GET_RECORD_NAME_JS = r"""
var SF_FIELD_LABELS = {
    'phone':true,'mobile':true,'email':true,'name':true,'title':true,
    'account':true,'account name':true,'company':true,'industry':true,
    'ddi':true,'direct dial':true,'direct dial in':true,
    'secondary email':true,'personal email':true,
    'record type':true,'contact record type':true,
    'job function':true,'job function vertical':true,
    'relevant summits':true,'sub industry':true,'sub-industry':true,
    'warnings':true,'blacklist':true,'yellow card':true,
    'related':true,'details':true,'activity':true,'news':true
};
function isBadName(t){
    if(!t||t.length<3||t.length>200) return true;
    if(SF_FIELD_LABELS[t.toLowerCase().trim()]) return true;
    if(/^(phone|mobile|email|name|title|account|address|website|fax|owner)$/i.test(t.trim())) return true;
    return false;
}
var sel = [
    'h1.slds-page-header__title',
    'h1[class*="page-header"]',
    '.forceRecordPageHeader h1',
    '.record-header-container h1',
    'span[class*="title"] h1',
    '.recordName'
];
for(var i=0;i<sel.length;i++){
    var el=document.querySelector(sel[i]);
    if(el){var t=(el.innerText||el.textContent||'').trim();if(!isBadName(t))return t;}
}
function gs(el){return el.shadowRoot||null;}
function walk(root,d){
    if(d>12) return '';
    var all=root.querySelectorAll?root.querySelectorAll('h1'):[];
    for(var i=0;i<all.length;i++){
        var t=(all[i].innerText||all[i].textContent||'').trim();
        if(!isBadName(t)) return t;
    }
    var els=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<els.length;i++){var sr=gs(els[i]);if(sr){var v=walk(sr,d+1);if(v)return v;}}
    return '';
}
return walk(document, 0);
"""

# ─── Full page text extraction (shadow DOM aware) ────────────────────────────
PAGE_TEXT_JS = r"""
try{
  var body=document.body?document.body.innerText:'';
  if(body&&body.length>600) return body;
  function gs(el){return el.shadowRoot||el.$shadowRoot||null;}
  function walk(root,fn){var els=root.querySelectorAll?root.querySelectorAll('*'):[];
    for(var i=0;i<els.length;i++){fn(els[i]);var sh=gs(els[i]);if(sh)walk(sh,fn);}}
  var parts=[],seen={};
  walk(document,function(el){var t=(el.innerText||el.textContent||'').trim();
    if(t&&t.length>0&&t.length<240&&!seen[t]){seen[t]=1;parts.push(t);}});
  return parts.join('\n');
}catch(e){return'';}
"""
