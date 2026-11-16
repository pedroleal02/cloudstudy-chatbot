'use strict';

(() => {
  const authRoot = document.getElementById('auth');
  const appRoot = document.getElementById('app');
  const msg = document.getElementById('auth-msg');
  const tabs = document.getElementById('auth-tabs');
  const userEmail = document.getElementById('user-email');
  const signoutBtn = document.getElementById('signout-btn');
  const chatInput = document.getElementById('user-input');
  const forms = {
    signin: document.getElementById('signin-form'),
    signup: document.getElementById('signup-form'),
    confirm: document.getElementById('confirm-form'),
  };

  let pendingEmail = '';
  let busy = false;

  function setMessage(text, kind = 'info') {
    msg.hidden = !text;
    msg.textContent = text || '';
    msg.dataset.kind = kind;
  }

  function showView(name) {
    Object.entries(forms).forEach(([key, form]) => {
      form.hidden = key !== name;
    });
    tabs.hidden = name === 'confirm';
    tabs.querySelectorAll('.auth-tab').forEach((b) => {
      b.setAttribute('aria-selected', String(b.dataset.view === name));
    });
    const campo = Array.from(forms[name].querySelectorAll('input')).find((i) => !i.value) || forms[name].querySelector('input');
    if (campo) campo.focus();
  }

  function setBusy(form, value) {
    form.querySelectorAll('button[type="submit"]').forEach((b) => {
      b.disabled = value;
    });
  }

  async function run(form, action) {
    if (busy) return;
    busy = true;
    setBusy(form, true);
    setMessage('');
    try {
      await action();
    } catch (err) {
      setMessage(err instanceof Auth.AuthError ? err.message : 'Algo deu errado. Tente novamente.', 'error');
    } finally {
      busy = false;
      setBusy(form, false);
    }
  }

  function enterApp(email) {
    userEmail.textContent = email;
    userEmail.title = email;
    authRoot.hidden = true;
    appRoot.hidden = false;
    chatInput.dispatchEvent(new Event('input'));
    if (window.matchMedia('(pointer: fine)').matches) chatInput.focus();
  }

  function goToConfirm(email, texto) {
    pendingEmail = email;
    showView('confirm');
    setMessage(texto, 'info');
  }

  tabs.addEventListener('click', (e) => {
    const botao = e.target.closest('.auth-tab');
    if (!botao || busy) return;
    setMessage('');
    showView(botao.dataset.view);
  });

  forms.signin.addEventListener('submit', (e) => {
    e.preventDefault();
    run(forms.signin, async () => {
      const email = forms.signin.elements.email.value.trim();
      const senha = forms.signin.elements.senha.value;
      if (!email || !senha) throw new Auth.AuthError('Validacao', 'Informe o e-mail e a senha.');

      let sessao;
      try {
        sessao = await Auth.signIn(email, senha);
      } catch (err) {
        if (err.code === 'UserNotConfirmedException') {
          goToConfirm(email, 'Sua conta ainda não foi confirmada. Digite o código enviado ao seu e-mail.');
          return;
        }
        throw err;
      }
      forms.signin.reset();
      enterApp(sessao.email || email);
    });
  });

  forms.signup.addEventListener('submit', (e) => {
    e.preventDefault();
    run(forms.signup, async () => {
      const email = forms.signup.elements.email.value.trim();
      const senha = forms.signup.elements.senha.value;
      const repetir = forms.signup.elements.repetir.value;
      if (!email || !senha) throw new Auth.AuthError('Validacao', 'Informe o e-mail e a senha.');
      if (senha !== repetir) throw new Auth.AuthError('Validacao', 'As senhas não são iguais.');

      const { confirmado } = await Auth.signUp(email, senha);
      forms.signup.reset();
      if (confirmado) {
        showView('signin');
        setMessage('Conta criada. Entre com sua senha.', 'info');
        return;
      }
      goToConfirm(email.toLowerCase(), `Enviamos um código de confirmação para ${email.toLowerCase()}.`);
    });
  });

  forms.confirm.addEventListener('submit', (e) => {
    e.preventDefault();
    run(forms.confirm, async () => {
      const codigo = forms.confirm.elements.codigo.value.trim();
      if (!codigo) throw new Auth.AuthError('Validacao', 'Digite o código recebido por e-mail.');
      await Auth.confirm(pendingEmail, codigo);
      forms.confirm.reset();
      forms.signin.elements.email.value = pendingEmail;
      showView('signin');
      setMessage('E-mail confirmado. Agora é só entrar.', 'info');
    });
  });

  document.getElementById('resend-btn').addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    setMessage('');
    try {
      await Auth.resendCode(pendingEmail);
      setMessage('Enviamos um novo código.', 'info');
    } catch (err) {
      setMessage(err instanceof Auth.AuthError ? err.message : 'Não foi possível reenviar o código.', 'error');
    } finally {
      busy = false;
    }
  });

  document.getElementById('confirm-back').addEventListener('click', () => {
    if (busy) return;
    setMessage('');
    showView('signin');
  });

  signoutBtn.addEventListener('click', () => {
    Auth.signOut();
    window.location.reload();
  });

  (async function init() {
    if (!Auth.configurado) {
      authRoot.hidden = false;
      showView('signin');
      setMessage('O Cognito não está configurado. Preencha COGNITO_USER_POOL_ID e COGNITO_CLIENT_ID no config.js.', 'error');
      Object.values(forms).forEach((f) => setBusy(f, true));
      return;
    }
    const sessao = await Auth.getSession();
    if (sessao) {
      enterApp(sessao.email);
      return;
    }
    authRoot.hidden = false;
    showView('signin');
    try {
      if (sessionStorage.getItem('cloudstudy:sessao-expirada')) {
        sessionStorage.removeItem('cloudstudy:sessao-expirada');
        setMessage('Sua sessão expirou. Entre novamente.', 'info');
      }
    } catch {
    }
  })();
})();
