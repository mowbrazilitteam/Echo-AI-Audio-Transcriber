/* Echo-AI — a tela. JS puro; todo texto que vem de dado entra por textContent (nunca innerHTML). Os textos da tela
   estão em i18n.js (inglês, português e espanhol). */
(function () {
  'use strict';

  /* ─────────── utilidades ─────────── */
  const $ = (id) => document.getElementById(id);
  function el(tag, classe, texto) {
    const e = document.createElement(tag);
    if (classe) e.className = classe;
    if (texto !== undefined && texto !== null) e.textContent = texto;
    return e;
  }
  function limpar(no) { while (no.firstChild) no.removeChild(no.firstChild); return no; }
  function icone(caminho) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    caminho.split('|').forEach((d) => { const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', d); svg.appendChild(p); });
    return svg;
  }
  const ICONES = {
    lapis: 'M4 20h4L19 9l-4-4L4 16v4|M14 6l4 4',
    lixo: 'M4 7h16|M9 7V4h6v3|M6 7l1 13h10l1-13',
    copiar: 'M8 8h11v12H8z|M5 16V4h11',
    falar: 'M4 10v4h4l5 4V6L8 10H4|M16 9a4 4 0 0 1 0 6|M18.5 6.5a8 8 0 0 1 0 11',
    play: 'M8 5v14l11-7z', pausa: 'M7 5h4v14H7z|M13 5h4v14h-4z',
    seta: 'M6 9l6 6 6-6', check: 'M5 12l5 5L20 7',
    mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z|M5 11a7 7 0 0 0 14 0|M12 18v3',
    arquivo: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5',
    globo: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z|M3 12h18|M12 3a14 14 0 0 1 0 18|M12 3a14 14 0 0 0 0 18',
    livro: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z|M4 19V5',
    conversa: 'M21 12a8 8 0 0 1-11.7 7.1L4 20l1-4.6A8 8 0 1 1 21 12z',
    ondas: 'M4 12h2|M8 8v8|M12 5v14|M16 8v8|M20 11v2',
    ia: 'M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z|M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z',
  };
  function botaoIcone(nome, rotulo, aoClicar, classe) {
    const b = el('button', 'icone-bt miudo' + (classe ? ' ' + classe : ''));
    b.type = 'button'; b.title = rotulo; b.setAttribute('aria-label', rotulo);
    b.appendChild(icone(ICONES[nome])); b.addEventListener('click', aoClicar);
    return b;
  }
  function botao(texto, classe, aoClicar) {
    const b = el('button', 'bt' + (classe ? ' ' + classe : ''), texto);
    b.type = 'button'; b.addEventListener('click', aoClicar);
    return b;
  }
  async function api(url, opcoes) {
    const o = Object.assign({ headers: {} }, opcoes || {});
    if (o.method && o.method !== 'GET') o.headers['X-Echo'] = '1';
    if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; delete o.json; }
    const r = await fetch(url, o);
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try { const d = await r.json(); msg = d.erro || (d.detail && (d.detail[0] && d.detail[0].msg || d.detail)) || msg; } catch (_) { /* resposta sem JSON: fica o código */ }
      const erro = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)); erro.status = r.status; throw erro;
    }
    return r.headers.get('content-type') && r.headers.get('content-type').includes('json') ? r.json() : r;
  }

  /* ─────────── idioma da tela ─────────── */
  const TEXTOS = window.ECHO_TEXTOS;
  let lingua = 'en';
  function t(chave, valores) {
    let s = (TEXTOS[lingua] && TEXTOS[lingua][chave]) || TEXTOS.en[chave] || chave;
    if (valores) Object.keys(valores).forEach((k) => { s = s.split('{' + k + '}').join(valores[k]); });
    return s;
  }
  function linguaDoSistema() {
    const n = (navigator.language || 'en').toLowerCase();
    return n.startsWith('pt') ? 'pt' : n.startsWith('es') ? 'es' : 'en';
  }
  function aplicarTextos() {
    document.documentElement.lang = lingua === 'pt' ? 'pt-BR' : lingua;
    document.querySelectorAll('[data-t]').forEach((e) => { e.textContent = t(e.dataset.t); });
    document.querySelectorAll('[data-t-placeholder]').forEach((e) => { e.placeholder = t(e.dataset.tPlaceholder); });
    document.querySelectorAll('[data-t-aria]').forEach((e) => { e.setAttribute('aria-label', t(e.dataset.tAria)); });
    document.querySelectorAll('[data-t-title]').forEach((e) => { e.title = t(e.dataset.tTitle); });
    $('tema-rotulo').textContent = document.documentElement.classList.contains('escuro') ? t('tema.claro') : t('tema.escuro');
  }
  function nomeDoIdioma(codigo) {
    if (codigo === 'auto') return t('idioma.auto');
    try { const nome = new Intl.DisplayNames([lingua], { type: 'language' }).of(codigo); return nome.charAt(0).toUpperCase() + nome.slice(1); }
    catch (_) { return codigo; }
  }
  function numero(valor, casas) { return new Intl.NumberFormat(lingua, { maximumFractionDigits: casas === undefined ? 1 : casas }).format(valor); }
  function gb(valor) { return valor < 1 ? numero(valor * 1024, 0) + ' MB' : numero(valor) + ' GB'; }
  function pct(fracao) { return fracao === null || fracao === undefined ? '' : Math.round(100 * fracao) + '%'; }

  /* ─────────── avisos e confirmação ─────────── */
  let temporizadorDoAviso = null;
  function avisar(texto) {
    const a = $('aviso'); a.textContent = texto; a.classList.remove('oculto');
    clearTimeout(temporizadorDoAviso); temporizadorDoAviso = setTimeout(() => a.classList.add('oculto'), 4600);
  }
  function confirmar(texto, rotuloSim) {
    return new Promise((resolver) => {
      const d = $('janela-confirmar'); $('texto-confirmar').textContent = texto; $('confirmar-sim').textContent = rotuloSim || t('confirmar');
      let respondido = false;
      const fim = (v) => { if (respondido) return; respondido = true; d.close(); resolver(v); };
      $('confirmar-sim').onclick = () => fim(true); $('confirmar-nao').onclick = () => fim(false);
      d.onclose = () => fim(false);
      d.showModal();
    });
  }
  function relogio(s) {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, '0');
    return h ? h + ':' + String(m).padStart(2, '0') + ':' + ss : m + ':' + ss;
  }
  function duracao(s) {
    s = Math.round(s || 0);
    if (s < 60) return s + ' s';
    const m = Math.floor(s / 60), r = s % 60;
    return m < 60 ? m + ' min' + (r ? ' ' + r + ' s' : '') : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  }
  function cor(nome) { return getComputedStyle(document.documentElement).getPropertyValue(nome).trim(); }

  /* ─────────── estado ─────────── */
  let estado = null, atual = null, busca = '';
  const escutas = new Map();          // audio_id -> EventSource
  const tocadores = new Map();        // audio_id -> {audio, desenhar, realcar}
  let perguntaEsperandoIA = null;     // a pergunta que pediu IA e espera a instalação terminar

  async function carregarEstado() {
    try { estado = await api('/api/estado'); } catch (erro) { avisar(t('app.fora') + ' ' + erro.message); return; }
    const guardada = ajuste('idioma_da_tela');
    const nova = guardada && TEXTOS[guardada] ? guardada : linguaDoSistema();
    if (nova !== lingua) { lingua = nova; aplicarTextos(); if (atual) desenhar(); else if (!$('coluna')) desenhar(); carregarLista(); }
    montarEscolhas(); montarDispositivo();
    if (estado.fila_viva === false) avisar(t('fila.parada') + (estado.fila_erro ? ' (' + estado.fila_erro + ')' : ''));
    else if (estado.fila_erro) avisar(t('fila.erro') + ' ' + estado.fila_erro);
    if ($('janela-ajustes').open) montarAjustes();
    if (!$('painel-modelos').classList.contains('oculto')) montarPainel();
    atualizarCartoesDeIA();
    acompanharDownloads();
  }
  function ajuste(chave) { return (estado && estado.ajustes && estado.ajustes[chave]) || ''; }
  async function gravarAjuste(chave, valor) {
    try { await api('/api/ajustes', { method: 'POST', json: { [chave]: String(valor) } }); estado.ajustes[chave] = String(valor); }
    catch (erro) { avisar(erro.message); }
  }
  function whisperEscolhido() {
    const baixados = estado.modelos_whisper.filter((m) => m.baixado);
    const pedido = ajuste('whisper_modelo');
    return baixados.find((m) => m.nome === pedido) || baixados.find((m) => m.recomendado) || baixados[0] || null;
  }
  function iaEscolhida() {
    const pedida = ajuste('ollama_modelo');
    const instaladas = estado.modelos_ollama || [];
    return instaladas.find((m) => m.nome === pedida) || instaladas.find((m) => m.nome === (estado.catalogo_de_ia.find((c) => c.recomendado) || {}).nome) || instaladas[0] || null;
  }
  function download(tipo, nome) { return (estado.downloads || []).find((d) => d.tipo === tipo && d.nome === nome) || null; }

  /* ─────────── o topo: onde está transcrevendo ─────────── */
  function montarDispositivo() {
    const d = limpar($('dispositivo')), m = estado.maquina;
    const ocupado = atual && Object.values(atual.audios || {}).some((a) => a.estado === 'transcrevendo');
    d.classList.toggle('ocupado', !!ocupado);
    d.appendChild(el('i'));
    const onde = m.dispositivo === 'cuda' ? t('placa') + (m.placa ? ' · ' + m.placa.replace(/^NVIDIA (GeForce )?/, '') : '') : t('cpu');
    d.appendChild(document.createTextNode((ocupado ? t('placa.trabalhando') : t('placa.livre')) + ' · ' + onde));
    if (estado.aceleracao && estado.aceleracao.pendente) { const b = botao(t('acel.curto'), 'pedido', () => abrirAjustes('maquina')); d.appendChild(b); }
    d.title = m.motivo_da_cpu ? t('maquina.caiu', { motivo: m.motivo_da_cpu }) : '';
  }

  /* ─────────── as escolhas da caixa de pergunta ─────────── */
  function rotuloDaEscolha(botaoEscolha, nomeIcone, texto, falta) {
    limpar(botaoEscolha).append(icone(ICONES[nomeIcone]), el('span', null, texto), icone(ICONES.seta));
    botaoEscolha.lastChild.classList.add('seta');
    botaoEscolha.classList.toggle('falta', !!falta);
  }
  function montarEscolhas() {
    const w = whisperEscolhido();
    rotuloDaEscolha($('escolher-whisper'), 'ondas', w ? w.rotulo : t('modelo.nenhum'), !w);
    $('escolher-whisper').title = t('modelo.whisper');
    rotuloDaEscolha($('escolher-idioma'), 'globo', nomeDoIdioma(ajuste('idioma') || 'auto'));
    $('escolher-idioma').title = t('idioma.audio');
    const ia = iaEscolhida();
    const rotuloIA = ia ? ((estado.catalogo_de_ia.find((c) => c.nome === ia.nome) || {}).rotulo || ia.nome) : t('modelo.nenhum');
    rotuloDaEscolha($('escolher-ia'), 'ia', rotuloIA, !ia);
    $('escolher-ia').title = t('modelo.ia');
    const historico = ajuste('modo_da_pergunta') === 'historico';
    rotuloDaEscolha($('escolher-escopo'), historico ? 'livro' : 'conversa', historico ? t('escopo.historico') : t('escopo.conversa'));
    $('escolher-escopo').lastChild.remove();
    $('pergunta').placeholder = atual ? t('pergunta.dica') : t('pergunta.dica.vazia');
  }
  $('escolher-escopo').addEventListener('click', () => {
    gravarAjuste('modo_da_pergunta', ajuste('modo_da_pergunta') === 'historico' ? 'conversa' : 'historico').then(montarEscolhas);
  });
  $('escolher-idioma').addEventListener('click', (e) => abrirMenuIdioma(e.currentTarget));
  $('escolher-whisper').addEventListener('click', (e) => abrirPainel('whisper', e.currentTarget));
  $('escolher-ia').addEventListener('click', (e) => abrirPainel('ia', e.currentTarget));

  function posicionarAcima(caixa, ancora) {
    const r = ancora.getBoundingClientRect();
    caixa.style.left = Math.max(12, Math.min(r.left, window.innerWidth - caixa.offsetWidth - 12)) + 'px';
    caixa.style.bottom = (window.innerHeight - r.top + 8) + 'px';
  }
  function abrirMenuIdioma(ancora) {
    const menu = limpar($('menu-idioma'));
    Object.keys(estado.idiomas).forEach((codigo) => {
      const b = el('button', null, nomeDoIdioma(codigo)); b.type = 'button'; b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', String((ajuste('idioma') || 'auto') === codigo));
      b.addEventListener('click', () => { fecharFlutuantes(); gravarAjuste('idioma', codigo).then(montarEscolhas); });
      menu.appendChild(b);
    });
    menu.classList.remove('oculto'); posicionarAcima(menu, ancora); ancora.setAttribute('aria-expanded', 'true');
  }

  /* ─────────── o painel de modelos: lado a lado, com a força de cada um ─────────── */
  let painelTipo = null, painelAncora = null;
  function pontos(n) { const p = el('span', 'pontos'); for (let i = 1; i <= 5; i++) p.appendChild(el('i', i <= n ? 'cheio' : '')); return p; }
  function forca(rotulo, n) { const s = el('span', null, rotulo); s.appendChild(pontos(n)); return s; }
  function cartaoDeModelo(tipo, m, escolhido) {
    const baixado = tipo === 'whisper' ? m.baixado : m.instalado;
    const dl = download(tipo, m.nome);
    const linha = el('div', 'modelo' + (escolhido ? ' escolhido' : '')); linha.setAttribute('role', 'option'); linha.tabIndex = 0;
    const nome = el('div', 'modelo-nome', m.rotulo);
    if (m.recomendado) nome.appendChild(el('span', 'selo', t('recomendado')));
    const acao = el('div', 'modelo-acao');
    if (baixado) { const ok = el('span', 'selo ok'); ok.append(icone(ICONES.check)); ok.appendChild(document.createTextNode(' ' + t('instalado'))); acao.appendChild(ok); }
    else if (!dl || dl.estado !== 'baixando') acao.appendChild(el('span', 'apagado', t(tipo === 'whisper' ? 'baixar' : 'instalar', { tamanho: gb(m.download_gb) })));
    linha.append(nome, acao, el('div', 'modelo-texto', t((tipo === 'whisper' ? 'w.' : 'i.') + m.nome)));
    const f = el('div', 'modelo-forca');
    if (tipo === 'whisper') f.append(forca(t('precisao'), m.precisao), forca(t('velocidade'), m.velocidade), el('span', null, gb(m.download_gb)));
    else f.append(forca(t('qualidade'), m.qualidade), forca(t('velocidade'), m.velocidade), el('span', null, numero(m.bilhoes) + ' B · ' + gb(m.download_gb)));
    linha.appendChild(f);
    if (dl && dl.estado !== 'pronto' && !baixado) {
      const and = el('div', 'modelo-andamento');
      if (dl.estado === 'baixando') {
        const chave = dl.fase ? 'fase.' + dl.fase : 'baixando';
        and.appendChild(el('span', null, dl.fracao === null ? t('preparando') : t(chave, { pct: pct(dl.fracao) })));
        const barra = el('div', 'barra'), i = el('i'); i.style.width = Math.round(100 * (dl.fracao || 0)) + '%'; barra.appendChild(i); and.appendChild(barra);
      } else and.appendChild(el('span', 'erro-texto', t('falhou', { erro: dl.erro || '' }) + ' · ' + t('tentar')));
      linha.appendChild(and);
    }
    const agir = () => {
      if (baixado) {
        gravarAjuste(tipo === 'whisper' ? 'whisper_modelo' : 'ollama_modelo', m.nome).then(() => { montarEscolhas(); if (painelTipo) montarPainel(); if ($('janela-ajustes').open) montarAjustes(); });
        if (painelTipo) fecharFlutuantes();
      } else if (!dl || dl.estado !== 'baixando') baixarModelo(tipo, m);
    };
    linha.addEventListener('click', agir);
    linha.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); agir(); } });
    return linha;
  }
  function montarPainel() {
    const painel = limpar($('painel-modelos'));
    if (painelTipo === 'whisper') {
      painel.appendChild(el('div', 'painel-titulo', t('modelo.whisper')));
      const acel = avisoDeAceleracao(); if (acel) painel.appendChild(acel);
      const w = whisperEscolhido();
      estado.modelos_whisper.forEach((m) => painel.appendChild(cartaoDeModelo('whisper', m, w && w.nome === m.nome)));
    } else {
      painel.appendChild(el('div', 'painel-titulo', t('modelo.ia')));
      const ia = iaEscolhida();
      estado.catalogo_de_ia.forEach((m) => painel.appendChild(cartaoDeModelo('ia', m, ia && ia.nome === m.nome)));
    }
    if (painelAncora) posicionarAcima(painel, painelAncora);
  }
  function abrirPainel(tipo, ancora) {
    if (painelTipo === tipo) { fecharFlutuantes(); return; }
    fecharFlutuantes();
    painelTipo = tipo; painelAncora = ancora; ancora.setAttribute('aria-expanded', 'true');
    $('painel-modelos').classList.remove('oculto'); montarPainel();
  }
  function fecharFlutuantes() {
    painelTipo = null; painelAncora = null;
    $('painel-modelos').classList.add('oculto'); $('menu-idioma').classList.add('oculto');
    document.querySelectorAll('.escolha[aria-expanded="true"]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  }
  document.addEventListener('mousedown', (e) => {
    if (!e.target.closest('#painel-modelos, #menu-idioma, .escolha')) fecharFlutuantes();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fecharFlutuantes(); });

  /* a aceleração da placa NVIDIA: aparece só quando há placa com driver e sem o pacote */
  function avisoDeAceleracao() {
    if (!estado.aceleracao || !estado.aceleracao.pendente) return null;
    const tamanho = gb(estado.aceleracao.tamanho_gb), dl = download('aceleracao', 'nvidia');
    const c = el('div', 'instalar-ia');
    c.append(el('h3', null, t('acel.titulo')), el('p', null, t('acel.texto', { tamanho: tamanho })));
    if (dl && dl.estado === 'baixando') {
      const and = el('div', 'modelo-andamento');
      and.appendChild(el('span', null, dl.fracao === null ? t('preparando') : t('baixando', { pct: pct(dl.fracao) })));
      const barra = el('div', 'barra'), i = el('i'); i.style.width = Math.round(100 * (dl.fracao || 0)) + '%'; barra.appendChild(i); and.appendChild(barra);
      c.appendChild(and);
    } else {
      if (dl && dl.estado === 'erro') c.appendChild(el('p', 'erro-texto', t('falhou', { erro: dl.erro || '' })));
      const botoes = el('div', 'botoes');
      botoes.appendChild(botao(t('acel.botao', { tamanho: tamanho }), 'marca', async () => {
        if (!(await confirmar(t('acel.confirma', { tamanho: tamanho }), t('acel.botao', { tamanho: tamanho })))) return;
        try { await api('/api/aceleracao/instalar', { method: 'POST' }); await carregarEstado(); } catch (erro) { avisar(erro.message); }
      }));
      c.appendChild(botoes);
    }
    return c;
  }

  async function baixarModelo(tipo, m) {
    const nomeTexto = m.rotulo, tamanho = gb(m.download_gb);
    let texto = t(tipo === 'whisper' ? 'baixar.confirma' : 'instalar.confirma', { nome: nomeTexto, tamanho: tamanho });
    if (tipo === 'ia' && estado.motor_ia === 'ausente') texto += t('instalar.motor');
    if (!(await confirmar(texto, t(tipo === 'whisper' ? 'baixar' : 'instalar', { tamanho: tamanho })))) return;
    try {
      await api(tipo === 'whisper' ? '/api/modelos/' + m.nome + '/baixar' : '/api/ia/' + encodeURIComponent(m.nome) + '/instalar', { method: 'POST' });
      await carregarEstado();
    } catch (erro) { avisar(erro.message); }
  }

  /* os downloads andando: a tela pergunta a cada segundo e avisa quando termina */
  let temporizadorDosDownloads = null;
  const jaAvisados = new Set();
  function acompanharDownloads() {
    const andando = (estado.downloads || []).some((d) => d.estado === 'baixando');
    (estado.downloads || []).forEach((d) => {
      const chave = d.tipo + ':' + d.nome;
      if (d.estado === 'baixando') jaAvisados.delete(chave);  // tentou de novo: o fim desta tentativa avisa outra vez
      if (d.estado === 'erro' && !jaAvisados.has(chave)) {
        jaAvisados.add(chave);
        if (d.tipo === 'ia') perguntaEsperandoIA = null;  // a pergunta não vai sair sozinha para outra conversa depois
        avisar(t('falhou', { erro: d.erro || '' }));
      }
      if (d.estado === 'pronto' && !jaAvisados.has(chave)) {
        jaAvisados.add(chave);
        if (d.tipo === 'aceleracao') { avisar(t('acel.pronta')); return; }
        const m = (d.tipo === 'whisper' ? estado.modelos_whisper : estado.catalogo_de_ia).find((x) => x.nome === d.nome);
        avisar(t('download.pronto', { nome: m ? m.rotulo : d.nome }));
        if (d.tipo === 'ia' && perguntaEsperandoIA) { const p = perguntaEsperandoIA; perguntaEsperandoIA = null; gravarAjuste('ollama_modelo', d.nome).then(() => perguntar(p.texto, p.modo)); }
      }
    });
    clearTimeout(temporizadorDosDownloads);
    if (andando) temporizadorDosDownloads = setTimeout(atualizarDownloads, 1000);
  }
  async function atualizarDownloads() {
    try { estado.downloads = await api('/api/downloads'); } catch (_) { return; }
    const terminou = estado.downloads.some((d) => d.estado !== 'baixando' && !jaAvisados.has(d.tipo + ':' + d.nome) && d.estado === 'pronto');
    if (terminou) { await carregarEstado(); return; }
    if (painelTipo) montarPainel();
    if ($('janela-ajustes').open) montarAjustes();
    atualizarCartoesDeIA();
    acompanharDownloads();
  }

  /* ─────────── lateral: o histórico por data ─────────── */
  function grupoDaData(iso) {
    const d = new Date(iso), hoje = new Date(); hoje.setHours(0, 0, 0, 0);
    const dias = Math.floor((hoje - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
    return dias <= 0 ? t('grupo.hoje') : dias === 1 ? t('grupo.ontem') : dias < 7 ? t('grupo.7') : dias < 30 ? t('grupo.30') : t('grupo.antes');
  }
  async function carregarLista() {
    let conversas;
    try { conversas = await api('/api/conversas?busca=' + encodeURIComponent(busca)); } catch (erro) { avisar(erro.message); return; }
    const lista = limpar($('lista'));
    if (!conversas.length) { lista.appendChild(el('p', 'lista-vazia', busca ? t('lista.nada', { busca: busca }) : t('lista.vazia'))); return; }
    let grupo = null;
    conversas.forEach((c) => {
      const g = grupoDaData(c.atualizada_em);
      if (g !== grupo) { grupo = g; lista.appendChild(el('div', 'grupo-lista', g)); }
      const item = el('div', 'item' + (atual && atual.conversa.id === c.id ? ' ativo' : ''));
      const abrir = el('button', 'abrir', c.titulo); abrir.type = 'button'; abrir.title = c.titulo;
      abrir.addEventListener('click', () => { abrirConversa(c.id); fecharLateral(); });
      const acoes = el('div', 'acoes-item');
      acoes.append(botaoIcone('lapis', t('renomear'), () => { abrirConversa(c.id).then(() => editarTitulo()); }),
        botaoIcone('lixo', t('apagar'), async () => {
          if (!(await confirmar(t('apagar.pergunta', { titulo: c.titulo }), t('apagar')))) return;
          try { await api('/api/conversas/' + c.id, { method: 'DELETE' }); if (atual && atual.conversa.id === c.id) novaConversa(); carregarLista(); avisar(t('apagada')); }
          catch (erro) { avisar(erro.message); }
        }));
      item.append(abrir, acoes); lista.appendChild(item);
    });
  }
  let temporizadorDaBusca = null;
  $('busca').addEventListener('input', (e) => { clearTimeout(temporizadorDaBusca); temporizadorDaBusca = setTimeout(() => { busca = e.target.value.trim(); carregarLista(); }, 250); });

  /* ─────────── abrir, nova, título ─────────── */
  async function abrirConversa(id) {
    let dados;
    try { dados = await api('/api/conversas/' + id); } catch (erro) { avisar(erro.message); return; }
    fecharEscutas();
    atual = dados;
    $('titulo').textContent = dados.conversa.titulo; document.title = dados.conversa.titulo + ' · Echo-AI';
    desenhar();
    /* um fluxo de eventos por conversa: o áudio que está transcrevendo, ou o próximo da fila. Um fluxo por áudio passava
       do limite de 6 conexões do navegador com 6 ou mais na fila e travava a tela. */
    const vivos = Object.values(dados.audios);
    const proximo = vivos.find((a) => a.estado === 'transcrevendo') || vivos.find((a) => a.estado === 'na_fila');
    if (proximo) escutar(proximo.id);
    carregarLista(); montarDispositivo(); montarEscolhas();
    rolarParaOFim();
  }
  function novaConversa() {
    fecharEscutas(); atual = null;
    $('titulo').textContent = ''; document.title = 'Echo-AI';
    desenhar(); carregarLista(); montarEscolhas();
  }
  async function garantirConversa() {
    if (atual) return atual.conversa.id;
    const c = await api('/api/conversas', { method: 'POST' });
    atual = { conversa: c, mensagens: [], audios: {} };
    return c.id;
  }
  function editarTitulo() {
    if (!atual) return;
    const h = $('titulo'), campo = el('input', 'titulo-edicao'); campo.value = atual.conversa.titulo; campo.maxLength = 120;
    campo.setAttribute('aria-label', t('renomear'));
    h.replaceWith(campo); campo.focus(); campo.select();
    let feito = false;
    const fim = async (gravar) => {
      if (feito) return; feito = true;
      const novo = campo.value.trim();
      campo.replaceWith(h);
      if (gravar && novo && novo !== atual.conversa.titulo) {
        try { atual.conversa = await api('/api/conversas/' + atual.conversa.id, { method: 'PATCH', json: { titulo: novo } }); h.textContent = atual.conversa.titulo; carregarLista(); }
        catch (erro) { avisar(erro.message); }
      }
    };
    campo.addEventListener('keydown', (e) => { if (e.key === 'Enter') fim(true); if (e.key === 'Escape') fim(false); });
    campo.addEventListener('blur', () => fim(true));
  }
  $('titulo').addEventListener('click', editarTitulo);
  $('titulo').addEventListener('keydown', (e) => { if (e.key === 'Enter') editarTitulo(); });
  $('nova').addEventListener('click', () => { novaConversa(); fecharLateral(); $('pergunta').focus(); });

  /* ─────────── o centro ─────────── */
  function desenhar() {
    tocadores.forEach((x) => x.audio.pause()); tocadores.clear();
    const area = limpar($('mensagens'));
    if (!atual || !atual.mensagens.length) { area.appendChild(comeco()); return; }
    const coluna = el('div', 'coluna'); coluna.id = 'coluna';
    atual.mensagens.forEach((m) => coluna.appendChild(mensagem(m)));
    area.appendChild(coluna);
  }
  function comeco() {
    const c = el('div', 'comeco');
    c.appendChild(el('h2', null, t('inicio.titulo')));
    c.appendChild(el('p', null, t('inicio.texto')));
    const palco = el('div', 'palco');
    const rec = el('button', 'eco'); rec.type = 'button'; rec.setAttribute('aria-label', t('microfone')); rec.title = t('gravar');
    rec.appendChild(icone(ICONES.mic)); rec.addEventListener('click', comecarGravacao);
    const abrir = botao(t('abrir'), 'contorno', () => $('arquivo').click()); abrir.prepend(icone(ICONES.arquivo));
    palco.append(rec, abrir); c.appendChild(palco);
    const dicas = el('div', 'dicas'); dicas.append(el('span', null, t('inicio.dica1')), el('span', null, t('inicio.dica2')));
    c.appendChild(dicas);
    return c;
  }
  function mensagem(m) {
    if (m.papel === 'audio') return cartaoDoAudio(atual.audios[m.audio_id]);
    if (m.papel === 'usuario') return el('div', 'pergunta-feita', m.texto);
    return respostaPronta(m);
  }

  /* ── o cartão do áudio ── */
  function cartaoDoAudio(a) {
    const f = el('article', 'audio'); f.dataset.audio = a.id;
    const cab = el('div', 'audio-cab');
    cab.appendChild(el('div', 'audio-nome', a.nome_original));
    const meta = el('div', 'audio-meta'); meta.dataset.meta = '1'; cab.appendChild(meta);
    const est = el('span', 'estado'); est.dataset.estado = '1'; cab.appendChild(est);
    f.appendChild(cab);
    f.appendChild(tocador(a));
    const and = el('div', 'andamento'); and.appendChild(el('i')); f.appendChild(and);
    const texto = el('div', 'texto'); texto.dataset.texto = '1'; f.appendChild(texto);
    f.appendChild(el('div', 'erro-audio oculto'));
    f.appendChild(el('div', 'audio-base'));
    f.appendChild(el('div', 'pedidos'));
    (a.trechos || []).forEach((x) => texto.appendChild(linhaDoTrecho(a.id, x)));
    atualizarCartao(f, a);
    return f;
  }
  function linhaDoTrecho(audioId, x) {
    const l = el('div', 'trecho'); l.dataset.inicio = x.inicio; l.dataset.fim = x.fim;
    const tempo = el('button', 'tempo', relogio(x.inicio)); tempo.type = 'button'; tempo.title = t('ouvir.daqui');
    tempo.addEventListener('click', () => tocarDe(audioId, x.inicio));
    l.append(tempo, el('span', 'fala', x.texto));
    return l;
  }
  function rotuloDoWhisper(nome) { const m = estado && estado.modelos_whisper.find((x) => x.nome === nome); return m ? m.rotulo : nome; }
  function atualizarCartao(f, a) {
    const est = f.querySelector('[data-estado]');
    est.className = 'estado ' + a.estado; est.textContent = t('estado.' + a.estado);
    const meta = limpar(f.querySelector('[data-meta]'));
    if (a.duracao) meta.appendChild(el('span', null, duracao(a.duracao)));
    meta.appendChild(el('span', null, rotuloDoWhisper(a.modelo)));
    if (a.estado === 'pronto' && a.segundos_de_trabalho) meta.appendChild(el('span', null, t('transcrito.em', { tempo: duracao(a.segundos_de_trabalho) })));
    const and = f.querySelector('.andamento'); and.classList.toggle('oculto', a.estado !== 'transcrevendo');
    and.firstChild.style.width = Math.round(100 * (a.progresso || 0)) + '%';
    const texto = f.querySelector('[data-texto]');
    texto.querySelectorAll('.cursor-vivo, .aguardando').forEach((x) => x.remove());
    if (!texto.querySelector('.trecho')) {
      const aviso = a.estado === 'na_fila' ? t('aguardando.fila') : a.estado === 'transcrevendo' ? t('aguardando.ouvindo') : a.estado === 'pronto' ? t('aguardando.vazio') : '';
      if (aviso) texto.appendChild(el('p', 'aguardando', aviso));
    }
    if (a.estado === 'transcrevendo') { const ult = texto.querySelector('.trecho:last-of-type .fala'); (ult || texto).appendChild(el('span', 'cursor-vivo')); }
    const erro = f.querySelector('.erro-audio'); erro.classList.toggle('oculto', a.estado !== 'erro');
    erro.textContent = a.estado === 'erro' ? t('falhou.transcricao', { erro: a.erro || '' }) : '';
    montarBase(f, a);
    const x = tocadores.get(a.id); if (x) { x.onda = a.onda ? JSON.parse(typeof a.onda === 'string' ? a.onda : JSON.stringify(a.onda)) : null; x.desenhar(); }
  }
  function montarBase(f, a) {
    const base = limpar(f.querySelector('.audio-base')), ped = limpar(f.querySelector('.pedidos'));
    const pronto = a.estado === 'pronto';
    const copiar = botao(t('copiar'), '', async () => {
      const texto = Array.from(f.querySelectorAll('.fala')).map((x) => x.textContent).join('\n');
      try { await navigator.clipboard.writeText(texto); avisar(t('copiado')); } catch (_) { avisar(t('nao.copiou')); }
    });
    copiar.prepend(icone(ICONES.copiar)); base.appendChild(copiar);
    if (pronto) {
      const txt = el('a', 'bt', t('baixar.txt')); txt.href = '/api/audios/' + a.id + '/texto.txt'; base.appendChild(txt);
      const srt = el('a', 'bt', t('baixar.srt')); srt.href = '/api/audios/' + a.id + '/texto.srt'; base.appendChild(srt);
    }
    const texto = f.querySelector('[data-texto]');
    base.appendChild(botao(texto.classList.contains('corrido') ? t('mostrar.tempos') : t('texto.corrido'), '', (e) => {
      texto.classList.toggle('corrido'); e.currentTarget.textContent = texto.classList.contains('corrido') ? t('mostrar.tempos') : t('texto.corrido');
    }));
    base.appendChild(el('span', 'espaco'));
    if (pronto || a.estado === 'erro') {
      const sel = el('select', 'bt'); sel.setAttribute('aria-label', t('modelo.whisper'));
      estado.modelos_whisper.filter((m) => m.baixado).forEach((m) => { const o = el('option', null, m.rotulo); o.value = m.nome; sel.appendChild(o); });
      sel.value = a.modelo; base.appendChild(sel);
      base.appendChild(botao(t('refazer'), 'contorno', async () => {
        if (!(await confirmar(t('refazer.confirma', { nome: a.nome_original, modelo: rotuloDoWhisper(sel.value) }), t('refazer')))) return;
        try { await api('/api/audios/' + a.id + '/refazer', { method: 'POST', json: { modelo: sel.value, idioma: ajuste('idioma') || 'auto' } }); await abrirConversa(atual.conversa.id); }
        catch (erro) { avisar(erro.message); }
      }));
    }
    if (pronto) {
      ['resumo', 'topicos', 'acoes', 'atencao'].forEach((k) => ped.appendChild(botao(t('pedido.' + k), 'pedido', () => perguntar(t('prompt.' + k), 'conversa'))));
    }
  }

  /* ── o tocador: a onda do áudio, com o trecho que toca realçado ── */
  function tocador(a) {
    const caixa = el('div', 'tocador');
    const audio = new Audio(); audio.preload = 'metadata'; audio.src = '/api/audios/' + a.id + '/arquivo';
    const play = el('button', 'play'); play.type = 'button'; play.setAttribute('aria-label', t('tocar')); play.appendChild(icone(ICONES.play));
    const tela = el('canvas', 'onda'); tela.setAttribute('role', 'slider'); tela.setAttribute('aria-label', t('posicao')); tela.tabIndex = 0;
    const rel = el('span', 'relogio', '0:00');
    caixa.append(play, tela, rel);
    const x = { audio: audio, onda: a.onda ? JSON.parse(a.onda) : null, duracao: a.duracao || 0 };
    x.desenhar = () => {
      const dpr = window.devicePixelRatio || 1, w = tela.clientWidth || 600, h = tela.clientHeight || 40;
      if (tela.width !== Math.round(w * dpr)) { tela.width = Math.round(w * dpr); tela.height = Math.round(h * dpr); }
      const g = tela.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      const dur = audio.duration && isFinite(audio.duration) ? audio.duration : x.duracao;
      const feito = dur ? audio.currentTime / dur : 0;
      const picos = x.onda && x.onda.length ? x.onda : null;
      const barras = Math.max(20, Math.floor(w / 4)), largura = Math.max(1.5, w / barras - 1.5);
      for (let i = 0; i < barras; i++) {
        const p = picos ? picos[Math.floor(i / barras * picos.length)] : 0.08;
        const alt = Math.max(2, p * (h - 4));
        g.fillStyle = i / barras < feito ? cor('--tocada') : cor('--onda');
        g.beginPath(); if (g.roundRect) g.roundRect(i * (w / barras), (h - alt) / 2, largura, alt, 1); else g.rect(i * (w / barras), (h - alt) / 2, largura, alt); g.fill();
      }
      rel.textContent = relogio(audio.currentTime) + ' / ' + relogio(dur);
      tela.setAttribute('aria-valuetext', relogio(audio.currentTime) + ' ' + t('de') + ' ' + relogio(dur));
    };
    x.realcar = () => {
      const f = document.querySelector('.audio[data-audio="' + a.id + '"]'); if (!f) return;
      const agora = audio.currentTime;
      f.querySelectorAll('.trecho').forEach((l) => l.classList.toggle('tocando', !audio.paused && agora >= +l.dataset.inicio && agora < +l.dataset.fim));
      const vivo = f.querySelector('.trecho.tocando');
      if (vivo) { const caixaTexto = f.querySelector('.texto'); const topo = vivo.offsetTop - caixaTexto.offsetTop;
        if (topo < caixaTexto.scrollTop || topo > caixaTexto.scrollTop + caixaTexto.clientHeight - 40) caixaTexto.scrollTop = topo - 60; }
    };
    const trocarIcone = () => { limpar(play).appendChild(icone(audio.paused ? ICONES.play : ICONES.pausa)); play.setAttribute('aria-label', audio.paused ? t('tocar') : t('pausar')); };
    play.addEventListener('click', () => { if (audio.paused) { tocadores.forEach((o) => { if (o.audio !== audio) o.audio.pause(); }); audio.play().catch(() => avisar(t('nao.tocou'))); } else audio.pause(); });
    ['play', 'pause', 'ended'].forEach((ev) => audio.addEventListener(ev, () => { trocarIcone(); x.desenhar(); x.realcar(); }));
    audio.addEventListener('timeupdate', () => { x.desenhar(); x.realcar(); });
    audio.addEventListener('loadedmetadata', x.desenhar);
    const buscarPosicao = (fracao) => { const dur = audio.duration && isFinite(audio.duration) ? audio.duration : x.duracao; if (dur) audio.currentTime = Math.max(0, Math.min(dur, fracao * dur)); };
    tela.addEventListener('click', (e) => { const r = tela.getBoundingClientRect(); buscarPosicao((e.clientX - r.left) / r.width); });
    tela.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight') audio.currentTime += 5; if (e.key === 'ArrowLeft') audio.currentTime -= 5; if (e.key === ' ') { e.preventDefault(); play.click(); } });
    tocadores.set(a.id, x);
    requestAnimationFrame(x.desenhar);
    return caixa;
  }
  function tocarDe(audioId, segundos) {
    const x = tocadores.get(audioId); if (!x) return;
    tocadores.forEach((o) => { if (o !== x) o.audio.pause(); });
    x.audio.currentTime = segundos; x.audio.play().catch(() => avisar(t('nao.tocou')));
  }
  window.addEventListener('resize', () => { tocadores.forEach((x) => x.desenhar()); fecharFlutuantes(); });

  /* ── o texto ao vivo (Server-Sent Events) ── */
  function escutar(audioId) {
    if (escutas.has(audioId)) return;
    const fonte = new EventSource('/api/audios/' + audioId + '/eventos');
    escutas.set(audioId, fonte);
    fonte.onmessage = (ev) => {
      const e = JSON.parse(ev.data), a = atual && atual.audios[audioId]; if (!a) return;
      const f = document.querySelector('.audio[data-audio="' + audioId + '"]');
      if (e.tipo === 'onda') { a.onda = JSON.stringify(e.onda); a.duracao = e.duracao; const x = tocadores.get(audioId); if (x) { x.onda = e.onda; x.duracao = e.duracao; x.desenhar(); } }
      if (e.tipo === 'trecho') { a.trechos = a.trechos || []; a.trechos.push(e); a.progresso = e.progresso; if (f) f.querySelector('[data-texto]').appendChild(linhaDoTrecho(audioId, e)); }
      if (e.tipo === 'estado') {
        a.estado = e.estado; if (e.progresso !== undefined && e.progresso !== null) a.progresso = e.progresso;
        if (e.estado === 'pronto' || e.estado === 'erro') { fonte.close(); escutas.delete(audioId); abrirConversa(atual.conversa.id); carregarEstado(); return; }
      }
      if (f) atualizarCartao(f, a);
      montarDispositivo(); rolarSePerto();
    };
    fonte.onerror = () => { /* o navegador reconecta sozinho; se o app saiu do ar, o topo mostra */ };
  }
  function fecharEscutas() { escutas.forEach((f) => f.close()); escutas.clear(); }

  /* ─────────── enviar áudio: qualquer arquivo; quem decide se tem áudio é o app, pelo conteúdo ─────────── */
  async function enviarAudio(arquivo) {
    const w = whisperEscolhido();
    if (!w) { avisar(t('sem.whisper')); abrirPainel('whisper', $('escolher-whisper')); return; }
    let conversaId;
    try { conversaId = await garantirConversa(); } catch (erro) { avisar(erro.message); return; }
    const dados = new FormData(); dados.append('arquivo', arquivo, arquivo.name); dados.append('modelo', w.nome); dados.append('idioma', ajuste('idioma') || 'auto');
    const barra = $('envio'); barra.classList.remove('oculto'); $('texto-envio').textContent = t('enviando', { nome: arquivo.name });
    await new Promise((resolver) => {
      const x = new XMLHttpRequest(); x.open('POST', '/api/conversas/' + conversaId + '/audios'); x.setRequestHeader('X-Echo', '1');
      x.upload.onprogress = (e) => { if (e.lengthComputable) $('barra-envio').style.width = Math.round(100 * e.loaded / e.total) + '%'; };
      x.onload = () => {
        if (x.status >= 400) { let m = 'HTTP ' + x.status; try { m = JSON.parse(x.responseText).erro || m; } catch (_) { /* sem JSON */ } avisar(t('nao.enviou', { erro: m })); }
        resolver();
      };
      x.onerror = () => { avisar(t('nao.enviou', { erro: t('app.fora') })); resolver(); };
      x.send(dados);
    });
    barra.classList.add('oculto'); $('barra-envio').style.width = '0';
    await abrirConversa(conversaId);
  }
  function enviarArquivos(lista) { Array.from(lista).reduce((p, f) => p.then(() => enviarAudio(f)), Promise.resolve()); }
  $('anexar').addEventListener('click', () => $('arquivo').click());
  $('arquivo').addEventListener('change', (e) => { enviarArquivos(e.target.files); e.target.value = ''; });
  let arrastando = 0;
  window.addEventListener('dragenter', (e) => { if (e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')) { arrastando++; $('soltar').classList.remove('oculto'); } });
  window.addEventListener('dragleave', () => { arrastando = Math.max(0, arrastando - 1); if (!arrastando) $('soltar').classList.add('oculto'); });
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => { e.preventDefault(); arrastando = 0; $('soltar').classList.add('oculto'); if (e.dataTransfer.files.length) enviarArquivos(e.dataTransfer.files); });

  /* ─────────── gravar pelo microfone ─────────── */
  let gravador = null;
  async function comecarGravacao() {
    if (gravador) return;
    let fluxo;
    try { fluxo = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (erro) { avisar(erro.name === 'NotAllowedError' ? t('microfone.negado') : t('microfone.erro', { erro: erro.message })); return; }
    const tipo = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm', 'audio/mp4'].find((x) => window.MediaRecorder && MediaRecorder.isTypeSupported(x)) || '';
    const rec = new MediaRecorder(fluxo, tipo ? { mimeType: tipo } : undefined), pedacos = [];
    const contexto = new (window.AudioContext || window.webkitAudioContext)(), analisador = contexto.createAnalyser();
    analisador.fftSize = 1024; contexto.createMediaStreamSource(fluxo).connect(analisador);
    const amostras = new Uint8Array(analisador.fftSize), inicio = Date.now(), historico = [];
    gravador = { rec: rec, descartar: false, quadro: 0 };
    rec.ondataavailable = (e) => { if (e.data.size) pedacos.push(e.data); };
    rec.onstop = () => {
      fluxo.getTracks().forEach((x) => x.stop()); contexto.close(); cancelAnimationFrame(gravador.quadro);
      const descartar = gravador.descartar; gravador = null;
      $('gravando').classList.add('oculto'); $('gravar').classList.remove('gravando-agora');
      if (descartar || !pedacos.length) return;
      const agora = new Date(), extensao = (rec.mimeType || '').includes('ogg') ? '.ogg' : (rec.mimeType || '').includes('mp4') ? '.m4a' : '.webm';
      const data = new Intl.DateTimeFormat(lingua, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(agora).replace(/[/:]/g, '-');
      enviarAudio(new File(pedacos, t('gravacao.nome', { data: data }) + extensao, { type: rec.mimeType || 'audio/webm' }));
    };
    rec.start(1000);
    $('gravando').classList.remove('oculto'); $('gravar').classList.add('gravando-agora');
    const tela = $('onda-viva'), g = tela.getContext('2d');
    (function quadro() {
      analisador.getByteTimeDomainData(amostras);
      let pico = 0; for (let i = 0; i < amostras.length; i++) pico = Math.max(pico, Math.abs(amostras[i] - 128) / 128);
      historico.push(pico); if (historico.length > 150) historico.shift();
      g.clearRect(0, 0, tela.width, tela.height); g.fillStyle = cor('--vivo');
      historico.forEach((p, i) => { const alt = Math.max(3, p * tela.height * 1.6); g.fillRect(i * 4, (tela.height - alt) / 2, 2.5, Math.min(tela.height, alt)); });
      $('tempo-gravando').textContent = relogio((Date.now() - inicio) / 1000);
      if (gravador) gravador.quadro = requestAnimationFrame(quadro);
    })();
  }
  $('gravar').addEventListener('click', () => { if (gravador) gravador.rec.stop(); else comecarGravacao(); });
  $('parar-gravacao').addEventListener('click', () => { if (gravador) gravador.rec.stop(); });
  $('descartar-gravacao').addEventListener('click', () => { if (gravador) { gravador.descartar = true; gravador.rec.stop(); } });

  /* ─────────── perguntar ─────────── */
  const campo = $('pergunta');
  function ajustarCampo() { campo.style.height = 'auto'; campo.style.height = Math.min(220, campo.scrollHeight) + 'px'; $('enviar').disabled = !campo.value.trim(); }
  campo.addEventListener('input', ajustarCampo);
  campo.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); enviarPergunta(); } });
  $('enviar').addEventListener('click', enviarPergunta);
  function enviarPergunta() { const x = campo.value.trim(); if (!x) return; campo.value = ''; ajustarCampo(); perguntar(x, ajuste('modo_da_pergunta') || 'conversa'); }

  function garantirColuna() {
    if (!$('coluna')) { const area = limpar($('mensagens')), c = el('div', 'coluna'); c.id = 'coluna'; area.appendChild(c); }
    return $('coluna');
  }
  /* o cartão de instalar a IA, quando a pergunta chega sem IA instalada */
  function cartaoDeInstalarIA(texto, modo) {
    const recomendada = estado.catalogo_de_ia.find((m) => m.recomendado) || estado.catalogo_de_ia[0];
    const c = el('div', 'instalar-ia'); c.dataset.cartaoIa = '1';
    c.append(el('h3', null, t('ia.falta.titulo')), el('p', null, t('ia.falta.texto')), el('p', 'apagado', t('ia.falta.ollama')));
    c.appendChild(cartaoDeModelo('ia', recomendada, false));
    const botoes = el('div', 'botoes');
    botoes.appendChild(botao(t('instalar', { tamanho: gb(recomendada.download_gb) }), 'marca', () => { perguntaEsperandoIA = { texto: texto, modo: modo }; baixarModelo('ia', recomendada); }));
    botoes.appendChild(botao(t('ver.todas'), 'contorno', () => { perguntaEsperandoIA = { texto: texto, modo: modo }; abrirAjustes('ia'); }));
    c.appendChild(botoes);
    return c;
  }
  function atualizarCartoesDeIA() {
    document.querySelectorAll('[data-cartao-ia]').forEach((c) => {
      if ((estado.modelos_ollama || []).length && !perguntaEsperandoIA) { c.remove(); return; }
      const recomendada = estado.catalogo_de_ia.find((m) => m.recomendado) || estado.catalogo_de_ia[0];
      const velho = c.querySelector('.modelo'); if (velho) velho.replaceWith(cartaoDeModelo('ia', recomendada, false));
    });
  }

  async function perguntar(texto, modo) {
    const semAudio = !atual || !Object.values(atual.audios || {}).some((a) => a.estado === 'pronto');
    if (modo === 'conversa' && semAudio) { modo = 'historico'; avisar(t('sem.audio.pronto')); }
    let conversaId;
    try { conversaId = await garantirConversa(); } catch (erro) { avisar(erro.message); return; }
    const coluna = garantirColuna();
    coluna.appendChild(el('div', 'pergunta-feita', texto));
    const resposta = el('div', 'resposta'); const corpo = el('div'); corpo.appendChild(el('p', 'escrevendo', modo === 'historico' ? t('procurando') : t('lendo')));
    resposta.appendChild(corpo); coluna.appendChild(resposta); rolarParaOFim();
    let acumulado = '', fontes = [], ultimoDesenho = 0;
    try {
      const ia = iaEscolhida();
      const r = await fetch('/api/conversas/' + conversaId + '/perguntar', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Echo': '1' },
        body: JSON.stringify({ pergunta: texto, modo: modo, modelo: ia ? ia.nome : null, idioma: lingua }) });
      if (r.status === 409) { resposta.replaceWith(cartaoDeInstalarIA(texto, modo)); coluna.lastElementChild.previousElementSibling.remove(); rolarParaOFim(); return; }
      if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).erro || m; } catch (_) { /* sem JSON */ } throw new Error(m); }
      const leitor = r.body.getReader(), decodificador = new TextDecoder(); let sobra = '';
      for (;;) {
        const { value, done } = await leitor.read(); if (done) break;
        sobra += decodificador.decode(value, { stream: true });
        const linhas = sobra.split('\n'); sobra = linhas.pop();
        for (const linha of linhas) {
          if (!linha.trim()) continue;
          const e = JSON.parse(linha);
          if (e.tipo === 'fontes') fontes = e.fontes;
          if (e.tipo === 'texto') { acumulado += e.texto; if (Date.now() - ultimoDesenho > 60) { ultimoDesenho = Date.now(); escreverResposta(corpo, acumulado, fontes, true); rolarSePerto(); } }
          if (e.tipo === 'erro') throw new Error(e.erro);
          if (e.tipo === 'fim') { escreverResposta(corpo, acumulado, fontes, false); resposta.appendChild(acoesDaResposta(acumulado)); if (ajuste('responder_em_voz') === '1') falarTexto(acumulado); }
        }
      }
      carregarLista();
    } catch (erro) {
      limpar(corpo).appendChild(el('p', 'erro-texto', t('ia.nao.respondeu', { erro: erro.message })));
    }
    rolarSePerto();
  }
  function respostaPronta(m) {
    const r = el('div', 'resposta'), corpo = el('div'); r.appendChild(corpo);
    escreverResposta(corpo, m.texto, m.fontes || [], false);
    r.appendChild(acoesDaResposta(m.texto));
    return r;
  }
  function acoesDaResposta(texto) {
    const a = el('div', 'acoes-resposta');
    a.appendChild(botaoIcone('copiar', t('copiar.resposta'), async () => { try { await navigator.clipboard.writeText(texto); avisar(t('copiado')); } catch (_) { avisar(t('nao.copiou')); } }));
    if (!(estado && estado.voz_erro)) a.appendChild(botaoIcone('falar', t('ouvir.resposta'), () => falarTexto(texto)));
    return a;
  }
  /* markdown curto e seguro: parágrafos, listas, **negrito** e citações [n] viram elementos, nunca HTML */
  function escreverResposta(corpo, texto, fontes, escrevendo) {
    limpar(corpo);
    texto.replace(/\r/g, '').split(/\n{2,}/).forEach((bloco) => {
      const linhas = bloco.split('\n').filter((l) => l.trim());
      if (!linhas.length) return;
      if (linhas.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l))) {
        const ul = el(/^\s*\d/.test(linhas[0]) ? 'ol' : 'ul');
        linhas.forEach((l) => { const li = el('li'); inline(li, l.replace(/^\s*([-*•]|\d+[.)])\s+/, ''), fontes); ul.appendChild(li); });
        corpo.appendChild(ul);
      } else {
        const p = el('p'); linhas.forEach((l, i) => { if (i) p.appendChild(el('br')); inline(p, l.replace(/^#+\s*/, ''), fontes); }); corpo.appendChild(p);
      }
    });
    if (escrevendo) (corpo.lastElementChild || corpo).appendChild(el('span', 'cursor-vivo'));
    if (!escrevendo && fontes && fontes.length) {
      const lista = el('div', 'fontes');
      fontes.forEach((f) => {
        const b = el('button', 'fonte'); b.type = 'button';
        b.append(el('b', null, '[' + f.n + '] ' + f.titulo), document.createTextNode(' · ' + relogio(f.inicio) + ' · ' + f.texto.slice(0, 140) + (f.texto.length > 140 ? '…' : '')));
        b.addEventListener('click', () => irParaFonte(f)); lista.appendChild(b);
      });
      corpo.appendChild(lista);
    }
  }
  function inline(pai, texto, fontes) {
    texto.split(/(\*\*[^*]+\*\*|\[\d+\])/g).forEach((p) => {
      if (!p) return;
      const negrito = /^\*\*([^*]+)\*\*$/.exec(p), cita = /^\[(\d+)\]$/.exec(p);
      if (negrito) pai.appendChild(el('strong', null, negrito[1]));
      else if (cita && fontes && fontes[+cita[1] - 1]) { const b = el('button', 'cita', cita[1]); b.type = 'button'; b.title = t('ouvir.trecho'); b.addEventListener('click', () => irParaFonte(fontes[+cita[1] - 1])); pai.appendChild(b); }
      else if (!cita) pai.appendChild(document.createTextNode(p.replace(/\*\*/g, '')));  // [n] sem fonte some
    });
  }
  async function irParaFonte(f) {
    if (!atual || atual.conversa.id !== f.conversa_id) await abrirConversa(f.conversa_id);
    const alvo = document.querySelector('.audio[data-audio="' + f.audio_id + '"]');
    if (alvo) alvo.scrollIntoView({ behavior: 'smooth', block: 'start' });
    tocarDe(f.audio_id, f.inicio);
  }

  /* ─────────── voz ─────────── */
  let vozTocando = null;
  async function falarTexto(texto) {
    try {
      const r = await api('/api/voz', { method: 'POST', json: { texto: texto } });
      if (vozTocando) vozTocando.pause();
      vozTocando = new Audio(r.url); await vozTocando.play();
    } catch (erro) { avisar(erro.message); }
  }

  /* ─────────── ajustes ─────────── */
  function secao(titulo, texto) {
    const s = el('section', 'secao'); s.appendChild(el('h3', null, titulo)); if (texto) s.appendChild(el('p', null, texto)); return s;
  }
  function opcoes(lista, atualValor, aoEscolher) {
    const l = el('div', 'opcoes-linha');
    lista.forEach(([valor, rotulo]) => { const b = botao(rotulo, 'contorno', () => aoEscolher(valor)); b.setAttribute('aria-pressed', String(valor === atualValor)); l.appendChild(b); });
    return l;
  }
  function montarAjustes() {
    const corpo = limpar($('corpo-ajustes'));
    const ling = secao(t('ajustes.lingua'));
    ling.appendChild(opcoes(Object.keys(TEXTOS).map((k) => [k, TEXTOS[k].lingua]), lingua, (v) => gravarAjuste('idioma_da_tela', v).then(() => { lingua = v; aplicarTextos(); desenhar(); carregarLista(); montarEscolhas(); montarDispositivo(); montarAjustes(); })));
    corpo.appendChild(ling);
    const tema = secao(t('ajustes.tema'));
    const escuro = document.documentElement.classList.contains('escuro');
    tema.appendChild(opcoes([['escuro', t('tema.escuro')], ['claro', t('tema.claro')]], escuro ? 'escuro' : 'claro', (v) => { aplicarTema(v === 'escuro', true); montarAjustes(); }));
    corpo.appendChild(tema);
    const w = secao(t('ajustes.whisper'), t('ajustes.whisper.texto')), gw = el('div', 'grade-modelos');
    const wEscolhido = whisperEscolhido();
    estado.modelos_whisper.forEach((m) => gw.appendChild(cartaoDeModelo('whisper', m, wEscolhido && wEscolhido.nome === m.nome)));
    w.appendChild(gw); w.id = 'ajustes-whisper'; corpo.appendChild(w);
    const ia = secao(t('ajustes.ia'), t('ajustes.ia.texto')), gi = el('div', 'grade-modelos');
    const iaAtual = iaEscolhida();
    estado.catalogo_de_ia.forEach((m) => gi.appendChild(cartaoDeModelo('ia', m, iaAtual && iaAtual.nome === m.nome)));
    ia.appendChild(gi);
    ia.appendChild(el('p', 'apagado', t('motor.titulo') + ': ' + t('motor.' + estado.motor_ia)));
    if (estado.ollama_erro && estado.motor_ia !== 'ausente') ia.appendChild(el('p', 'alerta', estado.ollama_erro));
    ia.id = 'ajustes-ia'; corpo.appendChild(ia);
    const maq = secao(t('maquina.titulo')), m = estado.maquina, ficha = el('dl', 'ficha');
    const linha = (rotulo, valor) => { ficha.append(el('dt', null, rotulo), el('dd', null, valor)); };
    linha(t('maquina.sistema'), ({ windows: 'Windows', mac: 'macOS', linux: 'Linux' }[m.sistema] || m.sistema) + ' · ' + m.arquitetura);
    linha(t('maquina.ram'), numero(m.ram_gb) + ' GB');
    linha(t('maquina.placa'), m.placa ? m.placa + (m.vram_gb ? ' · ' + numero(m.vram_gb) + ' GB' : '') : t('maquina.sem.placa'));
    linha(t('maquina.usando'), m.dispositivo === 'cuda' ? t('placa') : t('cpu'));
    maq.appendChild(ficha);
    if (m.placa_sem_driver) maq.appendChild(el('p', 'alerta', t('maquina.sem.driver')));
    const acel = avisoDeAceleracao(); if (acel) maq.appendChild(acel);
    maq.id = 'ajustes-maquina';
    if (m.motivo_da_cpu) maq.appendChild(el('p', 'alerta', t('maquina.caiu', { motivo: m.motivo_da_cpu })));
    corpo.appendChild(maq);
    const dados = secao(t('ajustes.dados'), t('ajustes.dados.texto'));
    dados.appendChild(el('div', 'caminho', estado.pasta_dados)); corpo.appendChild(dados);
    if (!estado.voz_erro) {
      const voz = secao(t('ajustes.voz'));
      const l = opcoes([['0', '—'], ['1', t('voz')]], ajuste('responder_em_voz') || '0', (v) => gravarAjuste('responder_em_voz', v).then(montarAjustes));
      l.appendChild(botao(t('voz.teste'), '', () => falarTexto(t('voz.amostra'))));
      voz.appendChild(l); corpo.appendChild(voz);
    }
  }
  function abrirAjustes(foco) {
    fecharFlutuantes(); montarAjustes(); $('janela-ajustes').showModal();
    if (foco) { const alvo = $('ajustes-' + foco); if (alvo) alvo.scrollIntoView({ block: 'start' }); }
  }
  $('abrir-ajustes').addEventListener('click', () => { abrirAjustes(); fecharLateral(); });
  $('fechar-ajustes').addEventListener('click', () => $('janela-ajustes').close());
  $('abrir-sobre').addEventListener('click', () => { $('sobre-versao').textContent = t('sobre.versao', { versao: estado.app.versao }); $('janela-sobre').showModal(); fecharLateral(); });
  $('fechar-sobre').addEventListener('click', () => $('janela-sobre').close());

  /* ─────────── tema, lateral no celular, rolagem ─────────── */
  const raiz = document.documentElement;
  function aplicarTema(escuro, guardar) {
    raiz.classList.toggle('escuro', escuro); aplicarTextos(); tocadores.forEach((x) => x.desenhar());
    if (guardar) { try { localStorage.setItem('echo-tema', escuro ? 'escuro' : 'claro'); } catch (_) { /* sem armazenamento: vale nesta janela */ } }
  }
  let temaSalvo = null;
  try { temaSalvo = localStorage.getItem('echo-tema'); } catch (_) { /* sem armazenamento: escuro, o padrão */ }
  raiz.classList.toggle('escuro', temaSalvo !== 'claro');
  $('tema').addEventListener('click', () => aplicarTema(!raiz.classList.contains('escuro'), true));
  function fecharLateral() { $('lateral').classList.remove('aberta'); }
  $('abrir-lateral').addEventListener('click', () => $('lateral').classList.add('aberta'));
  $('fechar-lateral').addEventListener('click', fecharLateral);
  function rolarParaOFim() { const m = $('mensagens'); requestAnimationFrame(() => { m.scrollTop = m.scrollHeight; }); }
  function rolarSePerto() { const m = $('mensagens'); if (m.scrollHeight - m.scrollTop - m.clientHeight < 240) m.scrollTop = m.scrollHeight; }
  document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('busca').focus(); } });

  /* ─────────── começo ─────────── */
  lingua = linguaDoSistema(); aplicarTextos();
  carregarEstado().then(() => { carregarLista(); desenhar(); });
  setInterval(() => { if (!document.hidden) carregarEstado(); }, 15000);
})();
