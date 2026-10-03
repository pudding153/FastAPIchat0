let history = JSON.parse(localStorage.getItem('chat_history')) || [];
let currentPrompt = "";

// プロンプト設定
window.addEventListener('DOMContentLoaded', () => {
    const setPromptBtn = document.getElementById('setprompt');
    const promptInput = document.getElementById('prompt');
    if (setPromptBtn && promptInput) {
        setPromptBtn.addEventListener('click', () => {
            currentPrompt = promptInput.value.trim();
            if (currentPrompt.length > 60) {
                alert("プロンプトは60文字以内で入力してください。");
                return;
            }
            alert("プロンプト適応済み");
        });
    }
});

function parseMarkdown(text) {
    if (!text) return ``;
    let safeText = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");

    safeText = safeText.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    safeText = safeText.replace(/__(.*?)__/g, '<strong>$1</strong>');

    safeText = safeText.replace(/\*(.*?)\*/g, '<em>$1</em>');
    safeText = safeText.replace(/_(.*?)_/g, '<em>$1</em>');
    safeText = safeText.replace(/\n/g, '<br>');
    return safeText;
}

function addBubble(role, text) {
    const isUser = role === 'user';
    const p = document.createElement('p');
    p.className = isUser ? 'chat-bubble user-bubble' : 'chat-bubble ai-bubble';
    p.innerHTML = `${isUser ? '自分' : 'AI'}: ${parseMarkdown(text)}`;
    log.appendChild(p);
    return p;
}

function renderHistory() {
    log.innerHTML = '';
    history.forEach(talk => {
        addBubble(talk.role, talk.parts[0].text);
    });
    log.scrollTop = log.scrollHeight;
}

window.addEventListener('DOMContentLoaded', renderHistory);

async function send() {
    const txt = input.value;
    if (!txt) return;

    addBubble('user', txt);

    input.value = '';
    log.scrollTop = log.scrollHeight;

    const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: txt,
            history: history,
            custom_prompt: currentPrompt
        })
    });

    const aiPara = addBubble('model', '');
    aiPara.innerHTML = 'AI: ';

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentAiText = '';

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
                    aiPara.innerHTML = `AI: ${parseMarkdown(currentAiText)}`;
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
            if (parsed.text) {
                currentAiText += parsed.text;
                aiPara.innerHTML = `AI: ${parseMarkdown(currentAiText)}`;
                log.scrollTop = log.scrollHeight;
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

function undoChat() {
    if (history.length < 2) {
        alert('削除できる会話履歴がありません。');
        return;
    }

    if (confirm('最新の会話履歴を1往復分削除しますか？')) {
        history.pop();
        history.pop();

        localStorage.setItem('chat_history', JSON.stringify(history));
        renderHistory();
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

const DOUBLE_ENTER_INTERVAL = 700;
let lastEnterTime = 0;

input.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') {
        lastEnterTime = 0;
        return;
    }
    if (e.isComposing || e.keyCode === 229) return;

    const now = Date.now();

    if (lastEnterTime !== 0 && now - lastEnterTime <= DOUBLE_ENTER_INTERVAL) {
        e.preventDefault();
        lastEnterTime = 0;
        const pos = input.selectionStart;
        if (input.value[pos - 1] === '\n') {
            input.value = input.value.slice(0, pos - 1) + input.value.slice(pos);
        }
        send();
    } else {
        lastEnterTime = now;
    }
});
