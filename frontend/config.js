// URL da API (Invoke URL do API Gateway, sem /chat no final)
const API_URL = "https://w5rd9nyes0.execute-api.us-east-1.amazonaws.com";

const COGNITO_CONFIG = {
  region: "us-east-1",
  userPoolId: "us-east-1_nh10bzBWJ",   // ex.: us-east-1_AbCdEfGhI
  clientId: "6fnd0k4ll3kn5td28a09ld9jmp",     // ID do app client (sem segredo)
  domain: "https://us-east-1nh10bzbwj.auth.us-east-1.amazoncognito.com",       // ex.: https://cloudstudy-abc123.auth.us-east-1.amazoncognito.com
};
