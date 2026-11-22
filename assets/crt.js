/* Tornado Laboratori — monitor CRT, tema HP 9845C. Nessuna dipendenza. */
(function(){
  var root=document.documentElement, scr=document.getElementById('screen'),
      reduce=matchMedia('(prefers-reduced-motion: reduce)').matches, GLY='#@%&*+=?<>/\\|01';
  function rnd(){return GLY[Math.floor(Math.random()*GLY.length)]}

  /* scritta che si decodifica */
  function scramble(el,ms){
    if(reduce||el._s)return; var t=el.dataset.t||(el.dataset.t=el.textContent); el._s=1;
    var n=t.length,st=performance.now(); ms=ms||420;
    (function f(now){var p=Math.min(1,(now-st)/ms),k=Math.floor(p*n),o='';
      for(var i=0;i<n;i++)o+=(i<k||t[i]===' ')?t[i]:rnd();
      el.textContent=o; if(p<1)requestAnimationFrame(f); else{el.textContent=t;el._s=0}})(st);
  }
  document.querySelectorAll('#softkeys a,.filters button,.btn').forEach(function(a){
    a.addEventListener('mouseenter',function(){scramble(a,380)});a.addEventListener('focus',function(){scramble(a,380)});});
  var io='IntersectionObserver' in window&&new IntersectionObserver(function(es){
    es.forEach(function(e){if(e.isIntersecting){scramble(e.target,600);io.unobserve(e.target)}});},{root:scr,threshold:.6});
  document.querySelectorAll('h2,.card h3').forEach(function(h){if(io)io.observe(h);h.addEventListener('mouseenter',function(){scramble(h,450)})});

  /* barra di stato: descrizione del programma sotto il puntatore */
  var st=document.getElementById('stmsg');
  document.querySelectorAll('.row').forEach(function(r){
    function on(){st.textContent='> '+(r.dataset.tip||'').toUpperCase()} function off(){st.textContent=st.dataset.def}
    r.addEventListener('mouseenter',on);r.addEventListener('focus',on);r.addEventListener('mouseleave',off);r.addEventListener('blur',off);});

  /* macchina da scrivere */
  var q=[].slice.call(document.querySelectorAll('.type'));
  q.forEach(function(e){e.dataset.t=e.textContent;if(!reduce)e.textContent=''});
  (function next(i){
    if(i>=q.length||reduce)return; var e=q[i],t=e.dataset.t,j=0,sp=Math.max(12,Math.min(32,1300/t.length));
    e.classList.add('on');
    (function step(){e.textContent=t.slice(0,++j); if(j<t.length)setTimeout(step,sp); else{e.classList.remove('on');next(i+1)}})();
  })(0);

  /* bagliore che segue il puntatore */
  var hero=document.querySelector('.hero');
  if(hero)hero.addEventListener('pointermove',function(e){var r=hero.getBoundingClientRect();
    hero.style.setProperty('--mx',(e.clientX-r.left)+'px');hero.style.setProperty('--my',(e.clientY-r.top)+'px')});

  /* menu: scorrimento dentro lo schermo */
  function goto(id,smooth){
    var el=document.getElementById(id); if(!el||!scr)return false;
    var h=document.querySelector('header.top'),y=el.getBoundingClientRect().top-scr.getBoundingClientRect().top+scr.scrollTop-(h?h.offsetHeight:0);
    scr.scrollTo({top:y,behavior:smooth&&!reduce?'smooth':'auto'});return true;}
  document.querySelectorAll('#softkeys a').forEach(function(a){
    var m=a.getAttribute('href').match(/index\.html#(\w+)$/);
    if(m&&document.getElementById(m[1])){a.addEventListener('click',function(e){e.preventDefault();goto(m[1],true);history.replaceState(null,'','#'+m[1])})}});
  if(location.hash)setTimeout(function(){goto(location.hash.slice(1),false)},60);

  /* manopole: luminosità, contrasto, fuoco */
  var D=[60,60,80],K='tornado-crt',v=D.slice();
  try{var sv=JSON.parse(localStorage.getItem(K)||'null');if(sv&&sv.length===3)v=sv}catch(e){}
  var names=['lum','con','foc'],btn=names.map(function(k){return document.querySelector('.kn button[data-k="'+k+'"]')});
  function apply(){
    var lum=v[0],con=v[1],foc=v[2],blur=(100-foc)/100*1.75;
    root.style.setProperty('--lum',(0.6+lum/100*0.9).toFixed(2));
    root.style.setProperty('--con',(0.7+con/100*0.8).toFixed(2));
    root.style.setProperty('--blur',blur.toFixed(2)+'px');
    root.style.setProperty('--g',(0.2+lum/100*1.2).toFixed(2));
    root.style.setProperty('--scan',(Math.max(0,con-60)/40*0.3).toFixed(2));
    btn.forEach(function(b,i){if(!b)return;b.firstChild.style.transform='rotate('+Math.round(-135+v[i]*2.7)+'deg)';
      b.setAttribute('aria-valuenow',v[i]);b.parentNode.querySelector('b').textContent=v[i]});
  }
  function set(i,x){v[i]=Math.max(0,Math.min(100,Math.round(x)));apply();try{localStorage.setItem(K,JSON.stringify(v))}catch(e){}}
  var drag=null;
  btn.forEach(function(b,i){ if(!b)return;
    b.addEventListener('pointerdown',function(e){e.preventDefault();drag={i:i,y:e.clientY,v:v[i]};b.setPointerCapture(e.pointerId)});
    b.addEventListener('pointermove',function(e){if(drag&&drag.i===i)set(i,drag.v+(drag.y-e.clientY)*0.7)});
    b.addEventListener('pointerup',function(){drag=null});
    b.addEventListener('wheel',function(e){e.preventDefault();set(i,v[i]-Math.sign(e.deltaY)*4)},{passive:false});
    b.addEventListener('keydown',function(e){var d={ArrowUp:5,ArrowRight:5,ArrowDown:-5,ArrowLeft:-5}[e.key];
      if(d){e.preventDefault();set(i,v[i]+d)}else if(e.key==='Home')set(i,0);else if(e.key==='End')set(i,100)});
  });
  var rs=document.getElementById('reset');
  if(rs)rs.addEventListener('click',function(){v=D.slice();apply();try{localStorage.removeItem(K)}catch(e){}});
  apply();

  /* tasti funzione F1-F5 = softkey */
  var sk=[].slice.call(document.querySelectorAll('#softkeys a'));
  document.addEventListener('keydown',function(e){var m=/^F([1-5])$/.exec(e.key);
    if(m&&sk[m[1]-1]&&!e.ctrlKey&&!e.altKey&&!e.metaKey){e.preventDefault();sk[m[1]-1].click()}});
  sk.forEach(function(a){var m=a.getAttribute('href').match(/index\.html#(\w+)$/);
    if(m&&document.getElementById(m[1])){a.addEventListener('click',function(e){e.preventDefault();goto(m[1],true);history.replaceState(null,'','#'+m[1])})}});
})();
