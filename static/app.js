let history = [];
async function send(){
    const txt = input.value;
    if (!txt) return;
    
    log.innerHTML += `<p>自分: ${txt}</p>`;
    input.value = '';
    
    const res = await fetch('/api/chat',{
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: txt, history: history})
    });


    const aiPara = document.createElement('p');
    aiPara.innerHTML = 'AI: ';
    log.appendChild(aiPara);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        
        const lines = buffer.split('\n');
        
        buffer = lines.pop(); 

        for (const line of lines) {
            if (!line.trim()) continue;
            const parsed = JSON.parse(line);
            
            if (parsed.text) {
                aiPara.innerHTML += parsed.text;
            }
            if (parsed.final_history) {
                history = parsed.final_history;
            }
        }
    }
}
