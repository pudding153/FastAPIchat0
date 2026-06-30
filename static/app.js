let history = [];
async function send(){
    const txt = input.value;
    if (!txt)return;
        log.innerHTML += `<p>自分: ${txt}</p>`;
        input.value = '';
        const res = await fetch('/api/chat',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            boby:JSON.stringify({massage:txt,history:history})
        })
        const data = await res.json();
    }
