const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');

// Chama o backend (POST /chat) e devolve o texto da resposta
async function sendToApi(message) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 s

  try {
    const response = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal: controller.signal,
    });

    let data = {};
    try {
      data = await response.json();
    } catch {
      // resposta sem JSON válido
    }

    if (!response.ok) {
      if (response.status === 429) {
        throw new Error("Muitas requisições. Aguarde alguns segundos e tente de novo.");
      }
      throw new Error(data.error || data.message || `Erro ${response.status}`);
    }

    return data.reply;
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
function addMessage(text, sender) {
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
  const text = userInput.value.trim();
  if (!text) return;

  addMessage(text, 'user');
  userInput.value = '';

  setLoading(true);
  const loadingEl = addMessage('Digitando...', 'loading');

  try {
    const reply = await sendToApi(text);
    loadingEl.remove();
    addMessage(reply, 'bot');
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
  if (e.key === 'Enter') {
    handleSendMessage();
  }
});