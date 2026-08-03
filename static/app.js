let history = JSON.parse(localStorage.getItem('chat_history')) || [];

function parseMarkdown(text){
    if(!text)return ``;
       let safeText = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "'");

        safeText = safeText.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        safeText = safeText.replace(/__(.*?)__/g, '<strong>$1</strong>');

        safeText = safeText.replace(/\*(.*?)\*/g, '<em>$1</em>');
        safeText = safeText.replace(/_(.*?)_/g, '<em>$1</em>');
        safeText = safeText.replace(/\n/g, '<br>');
        return safeText;
}

window.addEventListener('DOMContentLoaded', () => {
    history.forEach(talk => {
        const role = talk.role === 'user' ? '自分' : 'AI';
        const text = talk.parts[0].text; 
        
        const p = document.createElement('p');
        p.className = 'chat-bubble';
        p.innerHTML = `${role}: ${parseMarkdown(text)}`;
        log.appendChild(p);
    });
    log.scrollTop = log.scrollHeight;
});

async function send(){
    const txt = input.value;
    if (!txt) return;
    
    const myPara = document.createElement('p');
    myPara.className = 'chat-bubble';
    myPara.innerHTML = `自分: ${parseMarkdown(txt)}`;
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
    
    let currentAiText = '';       
    let displayedAiText = '';     
    let charQueue = [];           
    let isTyping = false;         

    function typeNextChar() {
        if (charQueue.length === 0) {
            isTyping = false;
            return;
        }
        isTyping = true;
        displayedAiText += charQueue.shift();
        aiPara.innerHTML = `AI: ${parseMarkdown(displayedAiText)}`;
        log.scrollTop = log.scrollHeight;
        setTimeout(typeNextChar, 30);
    }

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
                    currentAiText += parsed.text;
                    charQueue.push(...parsed.text);
                    if (!isTyping) typeNextChar();
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
            if (parsed.text) {
                currentAiText += parsed.text;
                charQueue.push(...parsed.text);
                if (!isTyping) typeNextChar();
            }
            if (parsed.final_history) {
                history = parsed.final_history;
                localStorage.setItem('chat_history', JSON.stringify(history));
            }
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

// ★【追加機能】最新の会話を1往復分だけ完全に消去する関数
function undoChat() {
    if (history.length < 2) {
        alert('削除できる会話履歴がありません。');
        return;
    }

    if (confirm('最新の会話履歴を1往復分削除しますか？')) {
        history.pop(); 
        history.pop(); 

        localStorage.setItem('chat_history', JSON.stringify(history));
        log.innerHTML = ''; 

        // あなたが修正してくれた「parts[0].text」の構造を使って画面に再描画
        history.forEach(talk => {
            const role = talk.role === 'user' ? '自分' : 'AI';
            const text = talk.parts[0].text; 
            
            const p = document.createElement('p');
            p.className = 'chat-bubble';
            p.innerHTML = `${role}: ${parseMarkdown(text)}`;
            log.appendChild(p);
        });
        log.scrollTop = log.scrollHeight;
    }
}

let isServerWoken = false;

async function wakeUpServer() {
    if (isServerWoken) return;
    isServerWoken = true;

    console.log("起動");
    
    try {
        await fetch('/api/ping');
        console.log("サーバーが正常に起動しました。");
    } catch (e) {
        isServerWoken = false;
        console.error("サーバー起動リクエストに失敗しました:", e);
    }
}
input.addEventListener('focus', wakeUpServer);
input.addEventListener('click', wakeUpServer);
