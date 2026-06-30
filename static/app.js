let history = [];
async function send(){
    const txt = input.value;
    if (!txt)return;
        log.innerHTML += `<p>自分: ${txt}</p>`;
        input.value = '';
        const res = await fetch('/api/chat',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({message:txt,history:history})
        })
        const data = await res.json();
        if(data.success){
            log.innerHTML += `<p>AI: ${data.reply}</p>`;
            history = data.history;
    }}
