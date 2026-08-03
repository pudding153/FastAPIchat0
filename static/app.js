let history = [];
async function send(){
    const txt = input.value;
    if (!txt) return;
    
    log.innerHTML += `<p>自分: ${txt}</p>`;
    input.value = '';
    
    log.scrollTop = log.scrollHeight;
    
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
            
            try {
                const parsed = JSON.parse(line);
                
                if (parsed.text) {
                    const isAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;

                    aiPara.innerHTML += parsed.text;

                
                    if (isAtBottom) {
                        log.scrollTop = log.scrollHeight;
                    }
                }
                if (parsed.final_history) {
                    history = parsed.final_history;
                }
            } catch (e) {
                console.error("JSONパースエラー:", e, "対象の行:", line);
            }
        }
    }

    if (buffer.trim()) {
        try {
            const parsed = JSON.parse(buffer);
            if (parsed.text) aiPara.innerHTML += parsed.text;
            if (parsed.final_history) history = parsed.final_history;
            
        
            log.scrollTop = log.scrollHeight;
        } catch (e) {
            console.error("最終バッファのパースエラー:", e);
        }
    }
}
