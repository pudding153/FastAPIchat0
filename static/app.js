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

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function parseMarkdown(text) {
    if (!text) return ``;
    let safeText = escapeHtml(text);

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
    if (!isUser && text) {
        p.classList.add('reveal');
    }
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


function statusHtml(icon, label) {
    return `<span class="status-text"><span class="status-icon">${icon}</span>${escapeHtml(label)}<span class="dots"></span></span>`;
}

async function send() {
    const txt = input.value;
    if (!txt) return;

    addBubble('user', txt);

    input.value = '';
    log.scrollTop = log.scrollHeight;

    const aiPara = addBubble('model', '');
    let currentAiText = '';
    let searchQueries = null; 
    let currentStatus = statusHtml('⏳', '送信中');

    function render() {
        if (currentAiText) {
            aiPara.classList.add('reveal');

            let html = `AI: ${parseMarkdown(currentAiText)}`;
            if (searchQueries !== null) {
                const q = searchQueries.length
                    ? `検索: ${searchQueries.map(escapeHtml).join(' / ')}`
                    : '検索を使用';
                html += `<div class="search-note">🔍 ${q}</div>`;
            }
            aiPara.innerHTML = html;
        } else {
            aiPara.innerHTML = `AI: ${currentStatus}`;
        }
        log.scrollTop = log.scrollHeight;
    }

    function handleLine(line) {
        if (!line.trim()) return;
        try {
            const parsed = JSON.parse(line);

            if (parsed.status) {
                if (parsed.status === 'thinking') {
                    currentStatus = statusHtml('🤔', '推論中');
                } else if (parsed.status === 'searching') {
                    searchQueries = parsed.queries || [];
                    const detail = searchQueries.length ? `「${searchQueries.join('」「')}」` : '';
                    currentStatus = statusHtml('🔍', `検索中 ${detail}`.trim());
                }
                render();
            }

            if (parsed.text) {
                currentAiText += parsed.text;
                render();
            }

            if (parsed.error) {
                currentStatus = statusHtml('⚠️', 'エラーが発生しました');
                if (!currentAiText) render();
            }

            if (parsed.final_history) {
                history = parsed.final_history;
                localStorage.setItem('chat_history', JSON.stringify(history));
            }
        } catch (e) {
            console.error("JSONパースエラー:", e, "対象の行:", line);
        }
    }

    render();

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: txt,
                history: history,
                custom_prompt: currentPrompt
            })
        });

        if (!res.ok) {
            aiPara.innerHTML = `AI: ⚠️ エラーが発生しました (${res.status})`;
            return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) handleLine(line);
        }

        if (buffer.trim()) handleLine(buffer);

        if (!currentAiText) {
            aiPara.innerHTML = 'AI: (応答がありませんでした)';
        }
    } catch (e) {
        console.error("通信エラー:", e);
        aiPara.innerHTML = 'AI: ⚠️ 通信に失敗しました';
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

const DOUBLE_ENTER_INTERVAL = 500;
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
