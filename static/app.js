let history = [];
async function send(){
    const txt = input.value;
    if (!txt)return;
        log.innerHTML += `<p>自分: ${txt}</p>`;
        input.value = '';
}
