const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');

// Chama o backend (POST /chat) e devolve o objeto { resposta, referencias }
async function sendToApi(message) {
  const controller = new AbortController();
  // um pouco acima do timeout da Lambda (30 s), para a Lambda falhar primeiro
  const timeoutId = setTimeout(() => controller.abort(), 35000);

  try {
    const response = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pergunta: message }), // a Lambda espera "pergunta"
      signal: controller.signal,
    });

    let data = {};
    try {
      data = await response.json();
    } catch {
      // resposta sem JSON válido
    }

    if (!response.ok) {
      // "erro" vem da Lambda; "message" vem do próprio API Gateway
      if (data.erro) throw new Error(data.erro);
      if (response.status === 429) {
        throw new Error("Muitas requisições. Aguarde alguns segundos e tente de novo.");
      }
      throw new Error(data.message || `Erro ${response.status}`);
    }

    if (!data.resposta) {
      throw new Error("A API não devolveu uma resposta.");
    }
    return data; // a Lambda devolve "resposta" e "referencias"
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("A resposta demorou demais. Tente novamente.");
    }
    if (err instanceof TypeError) {
      // falha de rede ou CORS
      throw new Error("Não foi possível conectar ao servidor.");
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// sender: 'user' | 'bot' | 'error' | 'loading'
function addMessage(text, sender, referencias = []) {
  const messageDiv = document.createElement('div');
  messageDiv.classList.add('message');

  if (sender === 'user') {
    messageDiv.classList.add('user-message');
  } else {
    messageDiv.classList.add('bot-message');
    if (sender === 'error') messageDiv.classList.add('error-message');
    if (sender === 'loading') messageDiv.classList.add('loading-message');
  }

  messageDiv.textContent = text;

  if (referencias.length > 0) {
    const fontes = document.createElement('div');
    fontes.classList.add('fontes');
    fontes.textContent = 'Fontes: ' + referencias.map(r => `[${r.id}] ${r.arquivo}`).join(' · ');
    messageDiv.appendChild(fontes);
  }

  chatMessages.appendChild(messageDiv);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return messageDiv; // permite remover o "Digitando..." depois
}

function setLoading(isLoading) {
  userInput.disabled = isLoading;
  sendBtn.disabled = isLoading;
  sendBtn.textContent = isLoading ? 'Enviando...' : 'Enviar';
}

async function handleSendMessage() {
  if (userInput.disabled) return; // evita envio duplo

  const text = userInput.value.trim();
  if (!text) return;

  addMessage(text, 'user');
  userInput.value = '';

  setLoading(true);
  const loadingEl = addMessage('Digitando...', 'loading');

  try {
    const dados = await sendToApi(text);
    loadingEl.remove();
    addMessage(dados.resposta, 'bot', dados.referencias);
  } catch (err) {
    loadingEl.remove();
    addMessage(err.message, 'error');
  } finally {
    setLoading(false);
    userInput.focus();
  }
}

sendBtn.addEventListener('click', handleSendMessage);

userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.isComposing) {
    handleSendMessage();
  }
});