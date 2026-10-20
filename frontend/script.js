const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');

// Respostas fictícias para simular o retorno da AWS/Backend
const mockResponses = [
  "Resposta simulada: layout validado com sucesso!",
  "Esta é uma mensagem temporária do bot local.",
  "Entendi seu pedido. Quando conectar à AWS, isso virá da API.",
  "Tudo certo por aqui no frontend!"
];

function addMessage(text, sender) {
  const messageDiv = document.createElement('div');
  messageDiv.classList.add('message', sender === 'user' ? 'user-message' : 'bot-message');
  messageDiv.textContent = text;
  
  chatMessages.appendChild(messageDiv);
  
  // Rola a área de chat para a mensagem mais recente
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function handleSendMessage() {
  const text = userInput.value.trim();
  if (!text) return;

  // 1. Renderiza mensagem do usuário
  addMessage(text, 'user');
  userInput.value = '';

  // 2. Simula o tempo de resposta do servidor (800ms) com resposta fictícia
  setTimeout(() => {
    const randomIndex = Math.floor(Math.random() * mockResponses.length);
    addMessage(mockResponses[randomIndex], 'bot');
  }, 800);
}

// Eventos de clique no botão e tecla Enter no campo
sendBtn.addEventListener('click', handleSendMessage);

userInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') {
    handleSendMessage();
  }
});