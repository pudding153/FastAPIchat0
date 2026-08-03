let history = JSON.parse(localStorage.getItem('chat_history')) || [];

window.addEventListener('DOMContentLoaded', () => {
    history.forEach(talk => {
        const role = talk.role === 'user' ? '自分' : 'AI';
        const text = talk.parts[0].text;
        
        const p = document.createElement('p');
        p.className = 'chat-bubble';
        p.innerText = `${role}: ${text}`;
        log.appendChild(p);
    });
    log.scrollTop = log.scrollHeight;
});

async function send(){
    const txt = input.value;
    if (!txt) return;
    
    const myPara = document.createElement('p');
    myPara.className = 'chat-bubble';
    myPara.innerText = `自分: ${txt}`;
    log.appendChild(myPara);
    
    input.value = '';
    log.scrollTop = log.scrollHeight;
    
    const res = await fetch('/api/chat',{
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: txt, history: history})
    });

    const aiPara = document.createElement('p');
    aiPara.className = 'chat-bubble';
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
                    aiPara.innerHTML += parsed.text;
                    log.scrollTop = log.scrollHeight;
                }
                
                if (parsed.final_history) {
                    history = parsed.final_history;
                    localStorage.setItem('chat_history', JSON.stringify(history));
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
            if (parsed.final_history) {
                history = parsed.final_history;
                localStorage.setItem('chat_history', JSON.stringify(history));
            }
            log.scrollTop = log.scrollHeight;
        } catch (e) {
            console.error("最終バッファのパースエラー:", e);
        }
    }
}

function clearChat() {
    if (confirm('これまでの会話履歴をすべて削除しますか？')) {
        localStorage.removeItem('chat_history'); 
        history = [];                           
        log.innerHTML = '';                      
    }
}
