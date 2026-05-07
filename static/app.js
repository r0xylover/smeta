async function postUpdate(url, payload){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  return r.json();
}
document.querySelectorAll('.work-q,.work-p').forEach(el=>el.addEventListener('change',async()=>{
  const id = el.dataset.id;
  const q = document.querySelector(`.work-q[data-id="${id}"]`).value;
  const p = document.querySelector(`.work-p[data-id="${id}"]`).value;
  const data = await postUpdate(`/ajax/work/${id}`, {quantity:q,price:p});
  document.getElementById(`work-total-${id}`).textContent = Number(data.total).toFixed(2);
}));
document.querySelectorAll('.mat-q,.mat-p').forEach(el=>el.addEventListener('change',async()=>{
  const id = el.dataset.id;
  const q = document.querySelector(`.mat-q[data-id="${id}"]`).value;
  const p = document.querySelector(`.mat-p[data-id="${id}"]`).value;
  const data = await postUpdate(`/ajax/material/${id}`, {quantity:q,price:p});
  document.getElementById(`mat-total-${id}`).textContent = Number(data.total).toFixed(2);
}));
