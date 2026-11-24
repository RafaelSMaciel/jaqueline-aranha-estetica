/* wizard.js — Booking wizard (CSP-safe, sem tags Django)
 * Configuração injetada pelo template em window.WIZARD_CFG:
 *   urlOtpSolicitar / urlOtpVerificar — endpoints do OTP (SMS)
 *   urlDias / urlHorarios             — disponibilidade (SlotService)
 *   procPreselect   — id do procedimento pré-selecionado (ou vazio)
 *   profPreselect   — id do profissional pré-selecionado (link "Agendar com X")
 *   otpTelefone     — celular já verificado nesta sessão (dígitos) ou vazio
 *   otpTitular      — esse celular já tem cadastro (nome só leitura — D16)
 *   rehidratar      — true quando o servidor devolveu erro do "Confirmar"
 *   whatsappNumero  — dígitos p/ CTA de WhatsApp (vazio = sem CTA)
 *   urlListaEspera  — inscrição na lista de espera (dia sem horário: "Avise-me se vagar")
 *   maxTratamentos  — quantos tratamentos cabem num pedido (MAX_TRATAMENTOS_POR_PEDIDO)
 *
 * Identidade do agendamento = CELULAR verificado por SMS. O e-mail é só um
 * dado de contato — nunca é enviado no OTP.
 *
 * Um pedido leva 1 ou mais tratamentos (`itens`): a etapa 1 é uma seleção (toca, marca; toca de
 * novo, tira), a etapa 2 abre um calendário POR tratamento e a etapa 3 confirma todos de uma vez.
 * As regras (horário livre, conflito entre os tratamentos, formato dos dados, ficha) são do
 * servidor; aqui elas só são antecipadas p/ a pessoa ver o erro ao lado do campo.
 */

/* ─── bloco 1: filtro de categorias ─── */
(function(){
    var tabs = document.querySelectorAll('.cat-tab');
    var cards = document.querySelectorAll('.proc-card');
    if (!tabs.length) return;
    tabs.forEach(function(tab){
        tab.addEventListener('click', function(){
            var cat = tab.dataset.cat;
            // Botoes liga/desliga (nao abas): o estado e o aria-pressed; o visual vem do site.css
            tabs.forEach(function(t){ t.setAttribute('aria-pressed', t === tab ? 'true' : 'false'); });
            cards.forEach(function(c){
                c.hidden = !(!cat || c.dataset.categoria === cat);
            });
        });
    });
})();

/* ─── bloco 2: IIFE principal do wizard ─── */
(function() {
    // ═══ CONFIG (injetada pelo template) ═══
    var cfg = window.WIZARD_CFG || {};
    var URL_DIAS = cfg.urlDias || '/ajax/dias-disponiveis/';
    var URL_HORARIOS = cfg.urlHorarios || '/ajax/horarios-disponiveis/';
    var TZ = 'America/Sao_Paulo';
    var MAX_TRATAMENTOS = parseInt(cfg.maxTratamentos, 10) || 3;

    // ═══ STATE ═══
    // Tratamentos escolhidos, na ordem do clique. Cada item:
    //   proc {id, nome, preco, duracao, buffer, categoria} · date 'YYYY-MM-DD' · slot {horario, iso,
    //   profissionais} · prof {id, nome, valor...} — e o que e so da tela (mes aberto, respostas do
    //   servidor, o bloco do calendario).
    var itens = [];

    var meses = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                 'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];

    var preselect = cfg.procPreselect || '';
    var profPreselect = cfg.profPreselect || '';

    // ═══ HELPERS ═══
    function soDigitos(v) {
        var d = String(v || '').replace(/\D/g, '');
        // Autofill "+55 17 9..." -> remove DDI
        if (d.length > 11 && d.indexOf('55') === 0) d = d.slice(2);
        return d;
    }

    function formatarTelefone(d) {
        d = soDigitos(d).slice(0, 11);
        if (d.length === 10) return '(' + d.slice(0,2) + ') ' + d.slice(2,6) + '-' + d.slice(6);
        if (d.length > 6) return '(' + d.slice(0,2) + ') ' + d.slice(2,7) + '-' + d.slice(7);
        if (d.length > 2) return '(' + d.slice(0,2) + ') ' + d.slice(2);
        if (d.length > 0) return '(' + d;
        return '';
    }

    function brl(p) {
        try {
            return p.toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});
        } catch (e) {
            return 'R$ ' + p.toFixed(2).replace('.', ',');
        }
    }

    function formatarPreco(p) {
        if (!(p > 0)) return 'A consultar';
        return brl(p);
    }

    // 30 -> '30 min' · 60 -> '1h' · 90 -> '1h30'
    function duracaoTexto(min) {
        min = parseInt(min, 10) || 0;
        if (min < 60) return min + ' min';
        var h = Math.floor(min / 60), m = min % 60;
        return h + 'h' + (m ? String(m).padStart(2, '0') : '');
    }

    // Mês do calendário sempre no dia 1: setMonth(+1) em 31/10 pularia novembro
    function primeiroDoMes(d) {
        d = d || new Date();
        return new Date(d.getFullYear(), d.getMonth(), 1);
    }

    function parseData(str) {
        // 'YYYY-MM-DD' -> Date local (new Date(str) seria UTC e voltaria 1 dia no Brasil)
        var p = String(str || '').split('-');
        if (p.length !== 3) return null;
        var d = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
        if (parseInt(p[0], 10) < 100) d.setFullYear(parseInt(p[0], 10));   // ano 0002 nao vira 1902
        return isNaN(d.getTime()) ? null : d;
    }

    function query(params) {
        return Object.keys(params).filter(function(k){ return params[k] !== '' && params[k] != null; })
            .map(function(k){ return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
            .join('&');
    }

    function quandoTexto(iso, curto) {
        var d = new Date(iso);
        try {
            if (!curto) {
                return d.toLocaleString('pt-BR', {weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: TZ});
            }
            // 'qui., 26/11 às 10:30'
            var dia = d.toLocaleDateString('pt-BR', {weekday: 'short', day: '2-digit', month: '2-digit', timeZone: TZ});
            var hora = d.toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit', timeZone: TZ});
            return dia + ' às ' + hora;
        } catch (e) { return d.toLocaleString('pt-BR'); }
    }

    function pronto(item) { return !!(item && item.slot && item.prof); }
    function todosProntos() { return itens.length > 0 && itens.every(pronto); }
    function indiceDoProc(id) {
        for (var i = 0; i < itens.length; i++) if (itens[i].proc.id === String(id)) return i;
        return -1;
    }
    // Ordem do pedido = ordem dos horarios (o servidor e a pagina de sucesso mostram assim)
    function emOrdem() {
        return itens.slice().sort(function(a, b) {
            return new Date(a.slot.iso).getTime() - new Date(b.slot.iso).getTime();
        });
    }

    // ═══ SESSION STORAGE — salvar/re-hidratar estado (W4) ═══
    var SESSION_KEY = 'wizard_estado';

    function valorDe(id) { return (document.getElementById(id) || {}).value || ''; }

    function salvarEstado() {
        try {
            sessionStorage.setItem(SESSION_KEY, JSON.stringify({
                itens: itens.map(function(it) { return {proc: it.proc, date: it.date, slot: it.slot, prof: it.prof}; }),
                form: {
                    nome:       valorDe('form-nome'),
                    telefone:   valorDe('form-telefone'),
                    nascimento: valorDe('form-nascimento'),
                    email:      valorDe('form-email')
                }
            }));
        } catch(e) {}
    }

    function rehidratar() {
        // Só reidrata quando o servidor recusou o "Confirmar" (flag da sessão)
        if (!cfg.rehidratar) return false;
        var raw = null;
        try { raw = sessionStorage.getItem(SESSION_KEY); } catch(e) { return false; }
        if (!raw) return false;
        var st;
        try { st = JSON.parse(raw); } catch(e) { return false; }
        if (!st || !Array.isArray(st.itens) || !st.itens.length) return false;

        itens = [];
        st.itens.slice(0, MAX_TRATAMENTOS).forEach(function(salvo) {
            if (!salvo || !salvo.proc) return;
            var card = document.querySelector('.proc-card[data-proc-id="' + salvo.proc.id + '"]');
            if (!card) return;   // tratamento saiu do catalogo no meio do caminho
            var item = novoItem(card);
            item.date = salvo.date || null;
            item.slot = salvo.slot || null;
            item.prof = salvo.prof || null;
            if (item.date) item.mes = primeiroDoMes(parseData(item.date));
            itens.push(item);
            marcarCard(card, true);
        });
        if (!itens.length) return false;
        atualizarCarrinho();

        // Repor campos do form
        if (st.form) {
            ['nome', 'telefone', 'nascimento', 'email'].forEach(function(k) {
                var f = document.getElementById('form-' + k);
                if (f && st.form[k] != null) f.value = st.form[k];
            });
        }

        // Calendários da etapa 2 prontos p/ o "Voltar"; se tudo estava escolhido, abre a etapa 3
        montarAgenda();
        if (todosProntos()) goToStep3({semFoco: true});
        else goToStep(2, {semFoco: true});
        return true;
    }

    // ═══ STEP NAVIGATION ═══
    // Etapa 1 = seleção (não avança sozinha: dá p/ marcar mais de um tratamento; o "Continuar"
    // fica na barra presa ao pé da tela). Etapa 2: escolher o horário do último tratamento (com a
    // profissional definida) abre a confirmação. A etapa vai p/ o histórico do navegador (o
    // "Voltar" do celular volta uma etapa, não sai do agendamento) e o foco vai p/ o título da
    // etapa nova (leitor de tela anuncia onde a pessoa está).
    var etapaAtual = 1;

    function avisar(texto) {
        var el = document.getElementById('wizard-aviso');
        if (el) { el.textContent = ''; setTimeout(function() { el.textContent = texto; }, 30); }
    }

    function etapaPossivel(n) {
        if (n >= 3 && !todosProntos()) n = 2;
        if (n >= 2 && !itens.length) n = 1;
        return n;
    }

    window.goToStep = function(n, opcoes) {
        opcoes = opcoes || {};
        n = etapaPossivel(n);
        if (n === 2) montarAgenda();
        var panels = document.querySelectorAll('.step-panel');
        for (var i = 0; i < panels.length; i++) {
            panels[i].classList.remove('active');
        }
        document.getElementById('step-' + n).classList.add('active');

        var items = document.querySelectorAll('.step-item');
        for (var k = 0; k < items.length; k++) {
            items[k].classList.remove('active', 'done');
            items[k].removeAttribute('aria-current');
            if (k + 1 < n) items[k].classList.add('done');
            if (k + 1 === n) { items[k].classList.add('active'); items[k].setAttribute('aria-current', 'step'); }
        }

        if (!opcoes.semHistorico && n !== etapaAtual) {
            try { history.pushState({passo: n}, '', ''); } catch (e) {}
        }
        var mudou = n !== etapaAtual;
        etapaAtual = n;
        if (!opcoes.semFoco) {
            var titulo = document.getElementById('step-' + n + '-titulo');
            if (titulo) titulo.focus({preventScroll: true});
            // etapa nova começa do topo do cartão (no celular a lista da etapa 1 é longa)
            var barra = document.querySelector('.steps-bar');
            if (mudou && barra && barra.scrollIntoView) barra.scrollIntoView({block: 'start'});
        }
    };

    try { history.replaceState({passo: 1}, '', ''); } catch (e) {}
    window.addEventListener('popstate', function(e) {
        var passo = (e.state && e.state.passo) || 1;
        if (passo === 3) { goToStep3({semHistorico: true}); return; }
        goToStep(passo, {semHistorico: true});
    });

    function liberarContinuar(id, ok) {
        var b = document.getElementById(id);
        if (b) b.disabled = !ok;
    }
    var btnCont1 = document.getElementById('btn-continuar-1');
    if (btnCont1) btnCont1.addEventListener('click', function() { if (itens.length) goToStep(2); });
    var btnCont2 = document.getElementById('btn-continuar-2');
    if (btnCont2) btnCont2.addEventListener('click', function() { goToStep3(); });

    // ═══ DELEGATED [data-goto] LISTENER ═══
    document.addEventListener('click', function(e) {
        var el = e.target.closest('[data-goto]');
        if (!el) return;
        goToStep(parseInt(el.dataset.goto, 10));
    });

    // ═══ STEP 1: ESCOLHER TRATAMENTOS (um ou mais) ═══
    function novoItem(card) {
        return {
            proc: {
                id: String(card.dataset.procId),
                nome: card.dataset.procNome,
                preco: parseFloat(card.dataset.procPreco) || 0,
                duracao: parseInt(card.dataset.procDuracao, 10) || 30,
                buffer: parseInt(card.dataset.procBuffer, 10) || 0,
                categoria: card.dataset.procCategoria || card.dataset.categoria || ''
            },
            date: null, slot: null, prof: null,
            mes: primeiroDoMes(), dias: [], horarios: [], seqMes: 0, seqHorarios: 0,
            sugestao: null, aviso: '', bloco: null, refs: null
        };
    }

    function marcarCard(card, sim) {
        card.classList.toggle('selected', !!sim);
        card.setAttribute('aria-pressed', sim ? 'true' : 'false');
    }

    function mostrarLimite(sim) {
        var el = document.getElementById('carrinho-limite');
        if (el) el.hidden = !sim;
    }

    function atualizarCarrinho() {
        var barra = document.getElementById('carrinho-barra');
        var n = itens.length;
        if (barra) barra.hidden = n === 0;
        liberarContinuar('btn-continuar-1', n > 0);
        var titulo = document.getElementById('carrinho-titulo');
        var detalhe = document.getElementById('carrinho-detalhe');
        if (!titulo || !detalhe) return;
        titulo.textContent = n === 1 ? '1 tratamento escolhido' : n + ' tratamentos escolhidos';
        var minutos = itens.reduce(function(t, it) { return t + it.proc.duracao; }, 0);
        detalhe.textContent = itens.map(function(it) { return it.proc.nome; }).join(' + ') + ' · ' + duracaoTexto(minutos);
    }

    function alternarProc(card) {
        var idx = indiceDoProc(card.dataset.procId);
        if (idx !== -1) {
            itens.splice(idx, 1);
            marcarCard(card, false);
            mostrarLimite(false);
            avisar(card.dataset.procNome + ' removido.');
        } else {
            if (itens.length >= MAX_TRATAMENTOS) { mostrarLimite(true); avisar('Limite de ' + MAX_TRATAMENTOS + ' tratamentos por pedido.'); return; }
            itens.push(novoItem(card));
            marcarCard(card, true);
            avisar(card.dataset.procNome + ' escolhido. ' + itens.length + (itens.length === 1 ? ' tratamento.' : ' tratamentos.'));
        }
        atualizarCarrinho();
        salvarEstado();
    }

    var procCards = document.querySelectorAll('.proc-card');
    for (var c = 0; c < procCards.length; c++) {
        procCards[c].addEventListener('click', function() { alternarProc(this); });
    }

    // ═══ STEP 2: UM CALENDÁRIO POR TRATAMENTO ═══
    var chaveAgenda = null;   // ids dos tratamentos p/ os quais os blocos estao montados
    var itemAberto = null;

    function montarAgenda() {
        var cont = document.getElementById('agenda-itens');
        var tpl = document.getElementById('tpl-agenda-item');
        if (!cont || !tpl) return;
        var chave = itens.map(function(it) { return it.proc.id; }).join(',');
        if (chave !== chaveAgenda) {
            chaveAgenda = chave;
            cont.textContent = '';
            cont.classList.toggle('agenda-itens--unico', itens.length === 1);
            itens.forEach(function(item, i) {
                criarBloco(item, i, tpl);
                cont.appendChild(item.bloco);
                carregarMes(item);
                if (item.date) carregarHorarios(item);
            });
            itemAberto = null;
        }
        var sub = document.getElementById('step-2-sub');
        if (sub) {
            sub.textContent = itens.length > 1
                ? 'Cada tratamento tem o seu horário. Escolha um de cada vez — os horários que batem entre si não aparecem.'
                : 'Escolha o dia no calendário e depois o horário.';
        }
        if (!itemAberto || itens.indexOf(itemAberto) === -1) itemAberto = primeiroPendente() || itens[0];
        atualizarBlocos();
    }

    function primeiroPendente() {
        for (var i = 0; i < itens.length; i++) if (!pronto(itens[i])) return itens[i];
        return null;
    }

    // O 1o bloco fica com os ids de sempre (#cal-days-grid, #slots-container...); os demais ganham sufixo
    function criarBloco(item, i, tpl) {
        var bloco = tpl.content.firstElementChild.cloneNode(true);
        var suf = i === 0 ? '' : '-' + (i + 1);
        var refs = {};
        var ids = {mes: 'cal-month-label', anterior: 'cal-prev', proximo: 'cal-next', dias: 'cal-days-grid',
                   horarios: 'slots-container', 'prof-secao': 'prof-section', profs: 'prof-options'};
        Object.keys(ids).forEach(function(nome) {
            var el = bloco.querySelector('[data-ref="' + nome + '"]');
            el.id = ids[nome] + suf;
            refs[nome] = el;
        });
        refs.dias.setAttribute('aria-labelledby', refs.mes.id);
        refs.num = bloco.querySelector('.agenda-item-num');
        refs.nome = bloco.querySelector('.agenda-item-nome');
        refs.meta = bloco.querySelector('.agenda-item-meta');
        refs.alterar = bloco.querySelector('.agenda-item-alterar');
        refs.corpo = bloco.querySelector('.agenda-item-corpo');
        refs.num.textContent = String(i + 1);
        refs.nome.textContent = item.proc.nome;
        refs.nome.id = 'agenda-item-nome' + suf;
        bloco.setAttribute('aria-labelledby', refs.nome.id);
        refs.anterior.addEventListener('click', function() {
            item.mes = new Date(item.mes.getFullYear(), item.mes.getMonth() - 1, 1);
            carregarMes(item);
        });
        refs.proximo.addEventListener('click', function() {
            item.mes = new Date(item.mes.getFullYear(), item.mes.getMonth() + 1, 1);
            carregarMes(item);
        });
        refs.alterar.addEventListener('click', function() { abrirItem(item, true); });
        item.bloco = bloco;
        item.refs = refs;
    }

    function abrirItem(item, rolar) {
        itemAberto = item;
        atualizarBlocos();
        if (rolar && item.bloco && item.bloco.scrollIntoView) item.bloco.scrollIntoView({block: 'start', behavior: 'smooth'});
    }

    // Estado de cada bloco: aberto (calendario a vista) · pronto (resumo + Alterar) · pendente
    function atualizarBlocos() {
        var unico = itens.length === 1;
        itens.forEach(function(item) {
            if (!item.bloco) return;
            var aberto = unico || item === itemAberto;
            var estado = aberto ? 'aberto' : (pronto(item) ? 'pronto' : 'pendente');
            item.bloco.dataset.estado = estado;
            var meta = duracaoTexto(item.proc.duracao);
            if (pronto(item)) meta = quandoTexto(item.slot.iso, true) + ' · ' + item.prof.nome;
            else if (!aberto) meta += ' · falta escolher o horário';
            item.refs.meta.textContent = meta;
            item.refs.alterar.hidden = aberto;
            item.refs.alterar.textContent = pronto(item) ? 'Alterar' : 'Escolher horário';
            item.refs.alterar.setAttribute('aria-label', (pronto(item) ? 'Alterar o horário de ' : 'Escolher o horário de ') + item.proc.nome);
            mostrarAvisoDoItem(item);
        });
        liberarContinuar('btn-continuar-2', todosProntos());
    }

    function mostrarAvisoDoItem(item) {
        var atual = item.refs.corpo.querySelector('.agenda-item-aviso');
        if (atual) atual.remove();
        if (!item.aviso) return;
        var p = document.createElement('p');
        p.className = 'agenda-item-aviso';
        p.setAttribute('role', 'status');
        p.textContent = item.aviso;
        item.refs.corpo.insertBefore(p, item.refs.corpo.firstChild);
    }

    function carregarMes(item) {
        var year = item.mes.getFullYear();
        var month = item.mes.getMonth();
        item.refs.mes.textContent = meses[month] + ' ' + year;

        var mesStr = year + '-' + String(month + 1).padStart(2, '0');
        var qs = query({mes: mesStr, procedimento_id: item.proc.id, profissional_id: profPreselect});
        var meu = ++item.seqMes;

        function pintar(dias) {
            if (meu !== item.seqMes) return;  // outro mês já foi pedido
            item.dias = dias;
            renderCalendar(item, year, month);
            // Dia sugerido (o mesmo do tratamento anterior): ja abre os horarios dele
            if (item.sugestao && !item.date && dias.indexOf(item.sugestao) !== -1) {
                var dia = item.sugestao;
                item.sugestao = null;
                selectDate(item, dia);
            }
        }
        fetch(URL_DIAS + '?' + qs)
            .then(function(r) { return r.json(); })
            .then(function(data) { pintar(data.dias_disponiveis || []); })
            .catch(function() { pintar([]); });
    }

    function renderCalendar(item, year, month) {
        var grid = item.refs.dias;
        grid.textContent = '';

        var firstDay = new Date(year, month, 1).getDay();
        var daysInMonth = new Date(year, month + 1, 0).getDate();
        var today = new Date();
        today.setHours(0, 0, 0, 0);

        for (var i = 0; i < firstDay; i++) {
            var empty = document.createElement('button');
            empty.type = 'button';
            empty.className = 'cal-day empty';
            empty.setAttribute('aria-hidden', 'true');
            empty.disabled = true;
            grid.appendChild(empty);
        }

        for (var d = 1; d <= daysInMonth; d++) {
            var dateObj = new Date(year, month, d);
            var dateStr = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
            var isToday = dateObj.getTime() === today.getTime();
            var isAvailable = item.dias.indexOf(dateStr) !== -1;

            var div = document.createElement('button');
            div.type = 'button';
            div.textContent = d;
            div.className = 'cal-day';
            try {
                div.setAttribute('aria-label', dateObj.toLocaleDateString('pt-BR', {weekday: 'long', day: 'numeric', month: 'long'})
                    + (isAvailable ? '' : ', sem horário'));
            } catch (e) {}
            if (isToday) div.classList.add('today');
            if (isAvailable) {
                div.classList.add('available');
                div.setAttribute('data-date', dateStr);
                if (dateStr === item.date) { div.classList.add('selected'); div.setAttribute('aria-pressed', 'true'); }
                div.addEventListener('click', function() {
                    selectDate(item, this.getAttribute('data-date'));
                });
            } else {
                div.disabled = true;
            }
            grid.appendChild(div);
        }
    }

    // icones estaticos (sem dado do usuario); cor e margem pelas classes do site.css
    var SVG_LOADING = '<svg class="slots-icone slots-icone--carregando" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></path></svg>';
    var SVG_INFO = '<svg class="slots-icone" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

    function mensagemSlots(container, svg, texto) {
        var box = document.createElement('div');
        box.className = 'slots-loading';
        box.innerHTML = svg; // SVG estático (sem dados do usuário)
        box.appendChild(document.createTextNode(texto));
        container.textContent = '';
        container.appendChild(box);
    }

    // Dia sem horário: inscrição na lista de espera já com o tratamento, a profissional e o dia
    function oferecerListaEspera(item, container, dateStr) {
        if (!cfg.urlListaEspera) return;
        var a = document.createElement('a');
        a.className = 'lista-espera-link';
        if (item === itens[0]) a.id = 'lista-espera-link';
        a.href = cfg.urlListaEspera + '?' + query({procedimento: item.proc.id, profissional: profPreselect, data: dateStr});
        a.textContent = 'Avise-me se vagar';
        var box = container.querySelector('.slots-loading') || container;
        box.appendChild(document.createElement('br'));
        box.appendChild(a);
    }

    function selectDate(item, dateStr) {
        item.date = dateStr;
        item.slot = null;
        item.prof = null;
        item.aviso = '';
        salvarEstado();

        var allDays = item.refs.dias.querySelectorAll('.cal-day');
        for (var i = 0; i < allDays.length; i++) { allDays[i].classList.remove('selected'); allDays[i].removeAttribute('aria-pressed'); }
        var sel = item.refs.dias.querySelector('.cal-day[data-date="' + dateStr + '"]');
        if (sel) { sel.classList.add('selected'); sel.setAttribute('aria-pressed', 'true'); }
        atualizarBlocos();
        carregarHorarios(item);
    }

    // Os outros tratamentos do pedido que ja tem horario: o servidor tira da lista o que bate com eles
    function carrinhoDe(item) {
        var outros = itens.filter(function(o) { return o !== item && pronto(o); })
            .map(function(o) { return {p: o.proc.id, f: o.prof.id, i: o.slot.iso}; });
        return outros.length ? JSON.stringify(outros) : '';
    }

    // Fim (ms) do ultimo tratamento ja escolhido no mesmo dia: o 1o horario depois dele ganha destaque
    function fimDosOutrosNoDia(item, dateStr) {
        var fim = 0, nome = '';
        itens.forEach(function(o) {
            if (o === item || !pronto(o) || o.date !== dateStr) return;
            var f = new Date(o.slot.iso).getTime() + o.proc.duracao * 60000;
            if (f > fim) { fim = f; nome = o.proc.nome; }
        });
        return {fim: fim, nome: nome};
    }

    function carregarHorarios(item) {
        var dateStr = item.date;
        item.refs['prof-secao'].hidden = true;
        var container = item.refs.horarios;
        mensagemSlots(container, SVG_LOADING, 'Carregando horários...');

        var qs = query({data: dateStr, procedimento_id: item.proc.id, profissional_id: profPreselect, carrinho: carrinhoDe(item)});
        var meu = ++item.seqHorarios;
        // Resposta antiga (outro dia clicado depois) não troca os horários
        function obsoleta() { return meu !== item.seqHorarios || dateStr !== item.date; }
        fetch(URL_HORARIOS + '?' + qs)
            .then(function(r) { return r.json().then(function(data) { return {ok: r.ok, data: data}; }); })
            .then(function(res) {
                if (obsoleta()) return;
                var data = res.data;
                if (!res.ok) {
                    // 4xx/5xx: mostra o motivo do servidor (antes virava "Nenhum horario disponivel")
                    mensagemSlots(container, SVG_INFO, data.mensagem || 'Não foi possível carregar os horários. Tente de novo.');
                    return;
                }
                item.horarios = data.horarios || [];
                if (item.horarios.length === 0) {
                    mensagemSlots(container, SVG_INFO, 'Nenhum horário disponível nesta data.');
                    oferecerListaEspera(item, container, dateStr);
                    return;
                }

                var parts = dateStr.split('-');
                var title = document.createElement('div');
                title.className = 'slots-title';
                title.textContent = 'Horários para ' + parts[2] + '/' + parts[1] + '/' + parts[0];
                var grid = document.createElement('div');
                grid.className = 'slots-grid';
                var antes = fimDosOutrosNoDia(item, dateStr);
                var destacado = false;
                item.horarios.forEach(function(slot, i) {
                    var b = document.createElement('button');
                    b.type = 'button';
                    b.className = 'slot-btn';
                    b.setAttribute('data-idx', String(i));
                    b.setAttribute('aria-pressed', 'false');
                    b.textContent = slot.horario;
                    if (item.slot && item.slot.iso === slot.datetime_iso) { b.classList.add('selected'); b.setAttribute('aria-pressed', 'true'); }
                    if (antes.fim && !destacado && new Date(slot.datetime_iso).getTime() >= antes.fim) {
                        destacado = true;
                        b.classList.add('slot-btn--seguida');
                        b.setAttribute('aria-label', slot.horario + ', logo depois de ' + antes.nome);
                    }
                    b.addEventListener('click', function() { selectSlot(item, this); });
                    grid.appendChild(b);
                });
                container.textContent = '';
                container.appendChild(title);
                container.appendChild(grid);
                if (destacado) {
                    var dica = document.createElement('p');
                    dica.className = 'slots-dica';
                    dica.textContent = 'Com contorno: o primeiro horário logo depois de ' + antes.nome + '.';
                    container.appendChild(dica);
                }
                if (pronto(item)) mostrarProfissionais(item);
                avisar(item.horarios.length + ' horários disponíveis em ' + parts[2] + '/' + parts[1] + ' para ' + item.proc.nome + '.');
            })
            .catch(function() {
                if (obsoleta()) return;
                mensagemSlots(container, SVG_INFO, 'Erro ao carregar horários.');
            });
    }

    function selectSlot(item, btn) {
        var allBtns = item.refs.horarios.querySelectorAll('.slot-btn');
        for (var i = 0; i < allBtns.length; i++) { allBtns[i].classList.remove('selected'); allBtns[i].setAttribute('aria-pressed', 'false'); }
        btn.classList.add('selected');
        btn.setAttribute('aria-pressed', 'true');

        var slot = item.horarios[parseInt(btn.getAttribute('data-idx'), 10)];
        item.slot = {horario: slot.horario, iso: slot.datetime_iso, profissionais: slot.profissionais};
        item.prof = null;
        item.aviso = '';
        if (item.slot.profissionais.length === 1) item.prof = item.slot.profissionais[0];
        salvarEstado();
        mostrarProfissionais(item);
        if (item.prof) aoEscolher(item);
        else atualizarBlocos();
    }

    function mostrarProfissionais(item) {
        var secao = item.refs['prof-secao'];
        var opcoes = item.refs.profs;
        secao.hidden = false;
        opcoes.textContent = '';
        item.slot.profissionais.forEach(function(prof) {
            var b = document.createElement('button');
            b.type = 'button';
            var escolhida = !!item.prof && String(item.prof.id) === String(prof.id);
            b.className = 'prof-btn' + (escolhida ? ' selected' : '');
            b.setAttribute('aria-pressed', escolhida ? 'true' : 'false');
            b.textContent = prof.nome;
            if (item.slot.profissionais.length > 1) {
                b.addEventListener('click', function() {
                    var all = opcoes.querySelectorAll('.prof-btn');
                    for (var k = 0; k < all.length; k++) { all[k].classList.remove('selected'); all[k].setAttribute('aria-pressed', 'false'); }
                    this.classList.add('selected');
                    this.setAttribute('aria-pressed', 'true');
                    item.prof = prof;
                    salvarEstado();
                    aoEscolher(item);
                });
            }
            opcoes.appendChild(b);
        });
    }

    // Dois tratamentos do pedido batem? A cliente não faz dois ao mesmo tempo; com a MESMA
    // profissional conta também o intervalo entre sessões (mesma conta do servidor).
    function conflita(a, b) {
        var ai = new Date(a.slot.iso).getTime(), af = ai + a.proc.duracao * 60000;
        var bi = new Date(b.slot.iso).getTime(), bf = bi + b.proc.duracao * 60000;
        var mesma = String(a.prof.id) === String(b.prof.id);
        var fa = mesma ? a.proc.buffer * 60000 : 0, fb = mesma ? b.proc.buffer * 60000 : 0;
        return ai < bf + fb && bi < af + fa;
    }

    // Tratamento com horário e profissional definidos: quem batia com ele volta a escolher, o
    // próximo sem horário abre (já no mesmo dia) e, com todos prontos, vai p/ a confirmação.
    function aoEscolher(item) {
        itens.forEach(function(outro) {
            if (outro === item) return;
            if (pronto(outro) && conflita(item, outro)) {
                outro.slot = null;
                outro.prof = null;
                outro.aviso = 'O horário que você tinha escolhido batia com o de ' + item.proc.nome + '. Escolha outro.';
            }
            // a lista de horarios dos outros muda com esta escolha
            if (outro.date && outro.bloco) carregarHorarios(outro);
        });
        salvarEstado();

        var proximo = primeiroPendente();
        if (proximo) {
            if (!proximo.date && item.date) {
                proximo.sugestao = item.date;
                var base = parseData(item.date);
                if (base && (proximo.mes.getFullYear() !== base.getFullYear() || proximo.mes.getMonth() !== base.getMonth())) {
                    proximo.mes = primeiroDoMes(base);
                }
                carregarMes(proximo);
            }
            avisar(item.proc.nome + ' marcado. Agora escolha o horário de ' + proximo.proc.nome + '.');
            abrirItem(proximo, true);
            return;
        }
        atualizarBlocos();
        goToStep3();
    }

    // ═══ STEP 3: RESUMO + DADOS ═══
    function goToStep3(opcoes) {
        if (!todosProntos()) { goToStep(2, opcoes); return; }
        var ordem = emOrdem();

        document.getElementById('form-procedimento').value = ordem[0].proc.id;
        document.getElementById('form-profissional').value = ordem[0].prof.id;
        document.getElementById('form-datetime').value = ordem[0].slot.iso;
        document.getElementById('form-itens-extras').value = ordem.length > 1
            ? JSON.stringify(ordem.slice(1).map(function(it) {
                return {procedimento: it.proc.id, profissional: it.prof.id, datetime: it.slot.iso};
            }))
            : '';

        renderResumo(ordem);
        renderAnamnese();
        renderTermos();

        goToStep(3, opcoes);
    }

    // Valor da DATA escolhida p/ o profissional (API de horários, promoção inclusa) — o mesmo que
    // o servidor grava. Cheio riscado + nome da promo. Devolve o número (NaN = a consultar).
    function pintarPreco(el, item) {
        el.textContent = '';
        var prof = item.prof || {};
        if (!('valor' in prof)) {  // estado antigo salvo na aba: preço base do card
            el.textContent = formatarPreco(item.proc.preco);
            return item.proc.preco > 0 ? item.proc.preco : NaN;
        }
        var valor = prof.valor === null ? NaN : parseFloat(prof.valor);
        if (isNaN(valor)) {
            el.textContent = 'A consultar';
            return NaN;
        }
        if (prof.promocao && prof.valor_cheio) {
            var cheio = document.createElement('span');
            cheio.className = 'recap-cheio';
            cheio.textContent = brl(parseFloat(prof.valor_cheio));
            el.appendChild(cheio);
        }
        el.appendChild(document.createTextNode(brl(valor)));
        if (prof.promocao) {
            var promo = document.createElement('span');
            promo.className = 'recap-promo';
            promo.textContent = prof.promocao;
            el.appendChild(promo);
        }
        return valor;
    }

    function renderResumo(ordem) {
        var unico = document.getElementById('recap-unico');
        var varios = document.getElementById('recap-varios');
        var titulo = document.getElementById('step-3-titulo');
        if (titulo) titulo.textContent = ordem.length > 1 ? 'Confirme seus ' + ordem.length + ' agendamentos' : 'Confirme seu agendamento';
        if (ordem.length === 1) {
            unico.hidden = false;
            varios.hidden = true;
            document.getElementById('sum-proc').textContent = ordem[0].proc.nome;
            document.getElementById('sum-datetime').textContent = quandoTexto(ordem[0].slot.iso);
            document.getElementById('sum-prof').textContent = ordem[0].prof.nome;
            pintarPreco(document.getElementById('sum-price'), ordem[0]);
            return;
        }
        unico.hidden = true;
        varios.hidden = false;
        varios.textContent = '';
        var total = 0, aConsultar = false;
        ordem.forEach(function(item) {
            var linha = document.createElement('div');
            linha.className = 'recap-item';
            var texto = document.createElement('div');
            var nome = document.createElement('span');
            nome.className = 'recap-item-nome';
            nome.textContent = item.proc.nome;
            var quando = document.createElement('span');
            quando.className = 'recap-item-quando';
            quando.textContent = quandoTexto(item.slot.iso, true) + ' · ' + item.prof.nome;
            texto.appendChild(nome);
            texto.appendChild(quando);
            var valor = document.createElement('span');
            valor.className = 'recap-item-valor';
            var v = pintarPreco(valor, item);
            if (isNaN(v)) aConsultar = true; else total += v;
            linha.appendChild(texto);
            linha.appendChild(valor);
            varios.appendChild(linha);
        });
        var rodape = document.createElement('div');
        rodape.className = 'recap-total';
        var rotulo = document.createElement('span');
        rotulo.textContent = 'Investimento total';
        var soma = document.createElement('span');
        soma.id = 'sum-total';
        soma.textContent = (total > 0 ? brl(total) : '') + (aConsultar ? (total > 0 ? ' + ' : '') + 'a consultar' : '');
        rodape.appendChild(rotulo);
        rodape.appendChild(soma);
        varios.appendChild(rodape);
    }

    // Ficha/termo -> procedimentos onde vale: lista calculada no servidor (FormularioAnamnese.aplica_a,
    // services/termos.vale_para); o JS nunca repete a regra. Vale se vale p/ ALGUM tratamento do pedido.
    function valeParaPedido(item) {
        var ids = (item.procedimentos || []).map(String);
        return itens.some(function(it) { return ids.indexOf(String(it.proc.id)) !== -1; });
    }

    // ═══ ERRO JUNTO DO CAMPO ═══
    function marcarErro(campo, alvoErro, msg) {
        if (alvoErro) { alvoErro.textContent = msg || ''; alvoErro.hidden = !msg; }
        if (!campo) return;
        if (msg) campo.setAttribute('aria-invalid', 'true');
        else campo.removeAttribute('aria-invalid');
    }

    function erroGeral(msg) {
        var el = document.getElementById('form-erro-geral');
        if (!el) return;
        el.textContent = msg || '';
        el.hidden = !msg;
    }

    // ═══ REGRAS DE QUALIDADE DOS DADOS (as mesmas do servidor: forms/agendamento.py) ═══
    var NOME_RE = /^\p{L}+(?:[ '’.\-]+\p{L}+)*\.?$/u;

    function validarNome(v) {
        v = String(v || '').replace(/\s+/g, ' ').trim();
        if (!v) return 'Informe seu nome completo.';
        // 'III' (João Paulo III) é nome; fora isso, 3 letras iguais seguidas não existem em nome
        var repetido = v.split(' ').some(function(p) { return p.toUpperCase() !== 'III' && /(.)\1\1/i.test(p); });
        if (!NOME_RE.test(v) || repetido) return 'Use só letras no nome, sem números ou símbolos.';
        var palavras = v.split(/[ '’.\-]+/).filter(function(p) { return p.length >= 2; });
        if (palavras.length < 2) return 'Informe nome e sobrenome.';
        return '';
    }

    function validarNascimento(v) {
        if (!v) return 'Informe sua data de nascimento.';
        var d = parseData(v);
        if (!d) return 'Data de nascimento inválida.';
        var hoje = new Date();
        if (d.getTime() > hoje.getTime()) return 'A data de nascimento não pode ser no futuro.';
        var idade = hoje.getFullYear() - d.getFullYear()
            - ((hoje.getMonth() < d.getMonth() || (hoje.getMonth() === d.getMonth() && hoje.getDate() < d.getDate())) ? 1 : 0);
        if (idade < 18) return 'É necessário ter pelo menos 18 anos para agendar.';
        if (idade > 120) return 'Confira o ano de nascimento.';
        return '';
    }

    function validarEmail(v) {
        v = String(v || '').trim();
        if (!v) return '';   // opcional
        return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) ? '' : 'E-mail inválido. Confira ou deixe em branco.';
    }

    // Resposta escrita que dá p/ ler: 2+ letras e não a mesma letra repetida (services/anamnese.texto_plausivel)
    function textoPlausivel(v) {
        var letras = String(v || '').match(/\p{L}/gu) || [];
        if (letras.length < 2) return false;
        if (letras.length < 4) return true;
        var distintas = {};
        letras.forEach(function(l) { distintas[l.toLowerCase()] = 1; });
        return Object.keys(distintas).length > 1;
    }

    var CAMPOS_PESSOAIS = [
        {id: 'form-nome', erro: 'erro-nome', regra: validarNome},
        {id: 'form-nascimento', erro: 'erro-nascimento', regra: validarNascimento},
        {id: 'form-email', erro: 'erro-email', regra: validarEmail}
    ];

    function validarCampoPessoal(def) {
        var campo = document.getElementById(def.id);
        if (!campo) return '';
        // cadastro existente: o nome fica so leitura e o servidor usa o do cadastro
        var msg = campo.readOnly ? '' : def.regra(campo.value);
        marcarErro(campo, document.getElementById(def.erro), msg);
        return msg;
    }

    CAMPOS_PESSOAIS.forEach(function(def) {
        var campo = document.getElementById(def.id);
        if (!campo) return;
        // valida ao sair do campo (nao a cada tecla); depois de um erro, corrige em tempo real
        campo.addEventListener('blur', function() { if (campo.value) validarCampoPessoal(def); });
        campo.addEventListener('input', function() {
            if (campo.getAttribute('aria-invalid') === 'true') validarCampoPessoal(def);
            salvarEstado();
        });
        campo.addEventListener('change', salvarEstado);
    });

    // ═══ TERMO(S) DO PROCEDIMENTO (JSON via json_script p/ XSS-safe) ═══
    var TERMOS_PROCEDIMENTO = [];
    try {
        TERMOS_PROCEDIMENTO = JSON.parse(
            document.getElementById('termos-procedimento-data').textContent
        ) || [];
    } catch (e) { TERMOS_PROCEDIMENTO = []; }

    function renderTermos() {
        var container = document.getElementById('termosContainer');
        var fields = document.getElementById('termosFields');
        if (!container || !fields || !itens.length) return;
        var marcados = {};
        fields.querySelectorAll('input[type="checkbox"]').forEach(function(cb) { if (cb.checked) marcados[cb.name] = 1; });
        fields.textContent = '';
        // O servidor decide onde cada termo vale (services/termos.vale_para) — aqui só consulta
        var aplicaveis = TERMOS_PROCEDIMENTO.filter(valeParaPedido);
        container.hidden = !aplicaveis.length;
        aplicaveis.forEach(function(t) {
            var box = document.createElement('div');
            box.className = 'aceite-box';
            var ttl = document.createElement('p');
            ttl.className = 'aceite-titulo';
            ttl.textContent = t.titulo + ' ';
            var ver = document.createElement('small');
            ver.textContent = '(versão ' + t.versao + ')';
            ttl.appendChild(ver);
            var txt = document.createElement('div');
            txt.className = 'aceite-texto';
            txt.tabIndex = 0;  // rolável por teclado
            txt.setAttribute('role', 'region');
            txt.setAttribute('aria-label', 'Texto do termo ' + t.titulo);
            txt.textContent = t.conteudo;
            var lbl = document.createElement('label');
            lbl.className = 'aceite-check';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.name = 'aceite_termo_' + t.id;
            cb.required = true;
            cb.dataset.termo = t.titulo;
            cb.checked = !!marcados[cb.name];
            var sp = document.createElement('span');
            sp.textContent = 'Li e aceito este termo.';
            var erro = document.createElement('small');
            erro.className = 'form-erro';
            erro.setAttribute('role', 'alert');
            erro.hidden = true;
            cb.addEventListener('change', function() { if (cb.checked) { marcarErro(cb, erro, ''); box.classList.remove('com-erro'); } });
            lbl.appendChild(cb);
            lbl.appendChild(sp);
            box.appendChild(ttl);
            box.appendChild(txt);
            box.appendChild(lbl);
            box.appendChild(erro);
            fields.appendChild(box);
        });
    }

    // ═══ ANAMNESE DINAMICA (JSON via json_script p/ XSS-safe) ═══
    var FORMULARIOS_ANAMNESE = [];
    try {
        FORMULARIOS_ANAMNESE = JSON.parse(
            document.getElementById('formularios-anamnese-data').textContent
        ) || [];
    } catch (e) { FORMULARIOS_ANAMNESE = []; }


    function criarSelect(opcoes) {
        var sel = document.createElement('select');
        var vazio = document.createElement('option');
        vazio.value = ''; vazio.textContent = '— selecione —';
        sel.appendChild(vazio);
        opcoes.forEach(function(o){
            var opt = document.createElement('option');
            opt.value = o.valor; opt.textContent = o.rotulo;
            sel.appendChild(opt);
        });
        return sel;
    }

    function camposDaFicha() { return document.querySelectorAll('#anamneseFields [data-form-id]'); }

    // Pergunta fora da tela nao conta: ficha em dia recolhida ou detalhe ("se") de um Nao
    function campoVisivel(el) {
        var wrap = el.closest('.ficha-campo');
        var ficha = el.closest('[data-ficha-id]');
        return !(wrap && wrap.hidden) && !(ficha && ficha.classList.contains('ficha--em-dia'));
    }

    // {formId: {key: valor}} do que esta na tela (radio/checkbox: os marcados)
    function respostasDaTela(soVisiveis) {
        var dados = {};
        camposDaFicha().forEach(function(el) {
            if (soVisiveis && !campoVisivel(el)) return;
            var fid = el.dataset.formId, key = el.dataset.fieldKey;
            if (!dados[fid]) dados[fid] = {};
            if (el.type === 'checkbox') {
                if (!Array.isArray(dados[fid][key])) dados[fid][key] = [];
                if (el.checked) dados[fid][key].push(el.value);
            } else if (el.type === 'radio') {
                if (el.checked) dados[fid][key] = el.value;
                else if (!(key in dados[fid])) dados[fid][key] = '';
            } else {
                dados[fid][key] = el.value;
            }
        });
        return dados;
    }

    function aplicarRespostas(dados) {
        camposDaFicha().forEach(function(el) {
            var valor = (dados[el.dataset.formId] || {})[el.dataset.fieldKey];
            if (valor === undefined) return;
            if (el.type === 'checkbox') el.checked = Array.isArray(valor) && valor.indexOf(el.value) !== -1;
            else if (el.type === 'radio') el.checked = valor === el.value;
            else el.value = valor;
        });
    }

    function renderAnamnese() {
        var container = document.getElementById('anamneseContainer');
        var fields = document.getElementById('anamneseFields');
        if (!container || !fields || !itens.length) return;
        var guardadas = respostasDaTela(false);   // voltar e avancar nao apaga o que ja foi respondido
        fields.textContent = '';

        var aplicaveis = FORMULARIOS_ANAMNESE.filter(valeParaPedido);
        setAnamneseMsg('');
        if (aplicaveis.length === 0) {
            container.hidden = true;
            atualizarConsentSaude();
            return;
        }
        container.hidden = false;

        aplicaveis.forEach(function(form){
            var formWrap = document.createElement('div');
            // Ficha opcional: perguntas "obrigatórias" só valem se a pessoa
            // começar a responder (mesma regra do servidor, fichas_respondidas).
            formWrap.dataset.fichaId = form.id;
            formWrap.dataset.fichaObrigatoria = form.obrigatorio ? '1' : '';
            // Cores pelas classes do template (tokens): o cssText fixo ficava ilegivel no tema escuro
            formWrap.className = 'ficha-anamnese';
            var ttl = document.createElement('div');
            ttl.className = 'ficha-titulo';
            ttl.textContent = form.nome;
            formWrap.appendChild(ttl);
            var legenda = document.createElement('p');
            legenda.className = 'ficha-legenda';
            legenda.textContent = form.obrigatorio
                ? 'Leva menos de um minuto. As perguntas com * são obrigatórias.'
                : 'Opcional. Se começar a responder, as perguntas com * passam a ser obrigatórias.';
            formWrap.appendChild(legenda);

            (form.schema || []).forEach(function(field){
                var base = 'an-' + form.id + '-' + String(field.key).replace(/[^\w-]/g, '_');
                var fwrap = document.createElement('div');
                fwrap.className = 'ficha-campo';
                fwrap.dataset.key = field.key;
                fwrap.dataset.label = field.label;
                fwrap.dataset.obrigatorio = field.obrigatorio ? '1' : '';
                fwrap.dataset.tipo = field.tipo || 'text';
                if (field.se) {   // detalhe de um Sim: so aparece (e so e exigido) quando a pergunta de controle = Sim
                    fwrap.dataset.se = field.se;
                    fwrap.classList.add('ficha-campo--detalhe');
                    fwrap.hidden = true;
                }
                var lbl = document.createElement('label');
                lbl.className = 'ficha-rotulo';
                lbl.id = 'lbl-' + base;
                lbl.textContent = field.label + (field.obrigatorio ? ' *' : '');
                fwrap.appendChild(lbl);
                var erro = document.createElement('small');
                erro.className = 'form-erro';
                erro.id = 'erro-' + base;
                erro.setAttribute('role', 'alert');
                erro.hidden = true;

                var opcoes = (field.opcoes || []).map(function(o){ return {valor: String(o), rotulo: String(o)}; });

                function marcavel(tipo, o, grupo) {
                    var l = document.createElement('label');
                    l.className = tipo === 'radio' ? 'simnao-opcao' : 'ficha-opcao';
                    var inp = document.createElement('input');
                    inp.type = tipo;
                    inp.value = o.valor;
                    if (tipo === 'radio') inp.name = base;
                    inp.dataset.formId = form.id;
                    inp.dataset.fieldKey = field.key;
                    l.appendChild(inp);
                    if (tipo === 'radio') { var sp = document.createElement('span'); sp.textContent = o.rotulo; l.appendChild(sp); }
                    else l.appendChild(document.createTextNode(o.rotulo));
                    grupo.appendChild(l);
                }

                if (field.tipo === 'checkboxes' || field.tipo === 'bool') {
                    var grupo = document.createElement('div');
                    // Nome acessível do grupo = a pergunta visível
                    grupo.setAttribute('role', field.tipo === 'bool' ? 'radiogroup' : 'group');
                    grupo.setAttribute('aria-labelledby', lbl.id);
                    grupo.setAttribute('aria-describedby', erro.id);
                    grupo.dataset.grupo = '1';
                    if (field.tipo === 'bool') {
                        grupo.className = 'ficha-simnao';
                        [{valor: 'sim', rotulo: 'Sim'}, {valor: 'nao', rotulo: 'Não'}].forEach(function(o) { marcavel('radio', o, grupo); });
                    } else {
                        opcoes.forEach(function(o) { marcavel('checkbox', o, grupo); });
                    }
                    fwrap.appendChild(grupo);
                    fwrap.appendChild(erro);
                    formWrap.appendChild(fwrap);
                    return;
                }

                var input;
                if (field.tipo === 'select' || field.tipo === 'scale') {
                    input = criarSelect(opcoes);
                } else if (field.tipo === 'longtext') {
                    input = document.createElement('textarea');
                    input.rows = 2;
                    input.maxLength = 4000;
                } else if (field.tipo === 'number') {
                    input = document.createElement('input'); input.type = 'number'; input.inputMode = 'decimal';
                } else if (field.tipo === 'date') {
                    input = document.createElement('input'); input.type = 'date';
                } else if (field.tipo === 'email') {
                    input = document.createElement('input'); input.type = 'email';
                } else {
                    input = document.createElement('input'); input.type = 'text';
                    input.maxLength = parseInt(field.max, 10) || 1000;
                }
                input.className = 'ficha-entrada';
                input.id = base;
                lbl.htmlFor = base;
                input.dataset.formId = form.id;
                input.dataset.fieldKey = field.key;
                input.setAttribute('aria-describedby', erro.id);
                fwrap.appendChild(input);
                fwrap.appendChild(erro);
                formWrap.appendChild(fwrap);
            });
            fields.appendChild(formWrap);
        });
        aplicarRespostas(guardadas);
        atualizarCondicionais();
        aplicarFichasEmDia();
    }

    // Detalhe ("se"): aparece quando a pergunta sim/nao de controle esta em Sim; escondido, perde o valor
    function atualizarCondicionais() {
        document.querySelectorAll('#anamneseFields .ficha-campo[data-se]').forEach(function(fwrap) {
            var ficha = fwrap.closest('[data-ficha-id]');
            var sim = ficha.querySelector('input[type="radio"][data-field-key="' + fwrap.dataset.se + '"][value="sim"]');
            var mostrar = !!(sim && sim.checked);
            if (fwrap.hidden === !mostrar) return;
            fwrap.hidden = !mostrar;
            if (!mostrar) {
                fwrap.querySelectorAll('[data-form-id]').forEach(function(el) {
                    if (el.type === 'checkbox' || el.type === 'radio') el.checked = false; else el.value = '';
                });
                limparErroDoCampo(fwrap);
            }
        });
    }

    // ═══ FICHA EM DIA (D14: vale 12 meses) ═══
    // {id da ficha: 'dd/mm/aaaa'} — decidido no servidor (OTP ou celular já verificado na sessão).
    // Ficha obrigatória em dia não é pedida de novo; "Atualizar" abre as perguntas (e, se a pessoa
    // responder, ela é validada e gravada como a opcional).
    var FICHAS_EM_DIA = {};
    try {
        FICHAS_EM_DIA = JSON.parse(document.getElementById('fichas-em-dia-data').textContent) || {};
    } catch (e) { FICHAS_EM_DIA = {}; }

    function aplicarFichasEmDia() {
        var wraps = document.querySelectorAll('#anamneseFields [data-ficha-id]');
        for (var i = 0; i < wraps.length; i++) {
            var wrap = wraps[i];
            var data = FICHAS_EM_DIA[wrap.dataset.fichaId];
            if (!data || wrap.dataset.emDia || !wrap.dataset.fichaObrigatoria) continue;
            if (fichaPreenchida(wrap.dataset.fichaId)) continue;  // já começou a responder
            marcarEmDia(wrap, data);
        }
        atualizarConsentSaude();
    }

    function marcarEmDia(wrap, data) {
        wrap.dataset.emDia = '1';
        wrap.dataset.fichaObrigatoria = '';
        wrap.classList.add('ficha--em-dia');   // recolhe as perguntas (site.css)
        // erro pintado antes do SMS (ficha ainda obrigatoria) nao fica guardado na ficha recolhida
        wrap.querySelectorAll('.ficha-campo').forEach(limparErroDoCampo);
        if (!document.querySelector('#anamneseFields [aria-invalid="true"]')) setAnamneseMsg('');
        var nota = document.createElement('p');
        nota.className = 'ficha-em-dia';
        nota.textContent = 'Sua ficha de ' + data + ' está em dia — algo mudou? ';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ficha-atualizar';
        btn.textContent = 'Atualizar';
        btn.addEventListener('click', function() {
            wrap.classList.remove('ficha--em-dia');
            nota.remove();
            atualizarCondicionais();
            var primeiro = wrap.querySelector('[data-form-id]');
            if (primeiro) primeiro.focus();
        });
        nota.appendChild(btn);
        var titulo = wrap.querySelector('.ficha-titulo');
        wrap.insertBefore(nota, titulo ? titulo.nextSibling : wrap.firstChild);
    }

    function temResposta(campos) {
        for (var i = 0; i < campos.length; i++) {
            var el = campos[i];
            if (!campoVisivel(el)) continue;
            if ((el.type === 'checkbox' || el.type === 'radio') ? el.checked : String(el.value || '').trim()) return true;
        }
        return false;
    }

    function anamneseTemResposta() { return temResposta(camposDaFicha()); }

    // Alguma pergunta DESTA ficha respondida (a ficha opcional passa a valer)
    function fichaPreenchida(formId) {
        return temResposta(document.querySelectorAll(
            '#anamneseFields [data-form-id="' + String(formId) + '"]'
        ));
    }

    function fichaExigida(wrap) {
        return !!wrap.dataset.fichaObrigatoria || fichaPreenchida(wrap.dataset.fichaId);
    }

    // Consentimento de dado de saúde (LGPD art. 11): só é exigido quando a
    // pessoa respondeu alguma pergunta — igual ao servidor, que só pede o
    // consentimento quando há resposta para gravar. Sem resposta, nada de
    // saúde é enviado e o agendamento segue sem esse consentimento.
    function atualizarConsentSaude() {
        var cb = document.getElementById('form-consent-saude');
        if (cb) cb.required = anamneseTemResposta();
    }

    function setAnamneseMsg(texto) {
        var el = document.getElementById('anamnese-msg');
        if (el) el.textContent = texto;
    }

    function limparErroDoCampo(fwrap) {
        var alvo = fwrap.querySelector('[data-grupo]') || fwrap.querySelector('[data-form-id]');
        marcarErro(alvo, fwrap.querySelector('.form-erro'), '');
    }

    // Erro de UMA pergunta da ficha ('' = ok): obrigatoria sem resposta ou texto que nao e resposta
    function erroDaPergunta(fwrap, exigida) {
        var campos = fwrap.querySelectorAll('[data-form-id]');
        if (!campos.length) return '';
        var respondida = temResposta(campos);
        if (!respondida) {
            if (!(exigida && fwrap.dataset.obrigatorio)) return '';
            return fwrap.dataset.tipo === 'bool' ? 'Escolha Sim ou Não.' : 'Responda esta pergunta.';
        }
        var tipo = fwrap.dataset.tipo;
        if ((tipo === 'text' || tipo === 'longtext') && !textoPlausivel(campos[0].value)) {
            return 'Escreva uma resposta com palavras (ex.: o nome do produto).';
        }
        if (tipo === 'email' && validarEmail(campos[0].value)) return 'E-mail inválido.';
        return '';
    }

    // Valida as fichas na tela; devolve o 1o campo com erro (ou null) e pinta o erro em cada um
    function validarAnamnese() {
        var primeiro = null, quantos = 0, rotulo = '';
        document.querySelectorAll('#anamneseFields [data-ficha-id]').forEach(function(wrap) {
            if (wrap.classList.contains('ficha--em-dia')) return;
            var exigida = fichaExigida(wrap);
            wrap.querySelectorAll('.ficha-campo').forEach(function(fwrap) {
                if (fwrap.hidden) return;
                var msg = erroDaPergunta(fwrap, exigida);
                var alvo = fwrap.querySelector('[data-grupo]') || fwrap.querySelector('[data-form-id]');
                marcarErro(alvo, fwrap.querySelector('.form-erro'), msg);
                if (msg) {
                    quantos++;
                    if (!primeiro) { primeiro = fwrap.querySelector('[data-form-id]'); rotulo = fwrap.dataset.label; }
                }
            });
        });
        setAnamneseMsg(quantos ? 'Responda: ' + rotulo + (quantos > 1 ? ' (e mais ' + (quantos - 1) + ').' : '.') : '');
        return {primeiro: primeiro, quantos: quantos};
    }

    function aoResponderAnamnese(e) {
        atualizarCondicionais();
        atualizarConsentSaude();
        var fwrap = e && e.target && e.target.closest ? e.target.closest('.ficha-campo') : null;
        if (fwrap && fwrap.querySelector('[aria-invalid="true"]')) {
            var ficha = fwrap.closest('[data-ficha-id]');
            var msg = erroDaPergunta(fwrap, fichaExigida(ficha));
            var alvo = fwrap.querySelector('[data-grupo]') || fwrap.querySelector('[data-form-id]');
            marcarErro(alvo, fwrap.querySelector('.form-erro'), msg);
        }
        if (!document.querySelector('#anamneseFields [aria-invalid="true"]')) setAnamneseMsg('');
    }

    var anamneseFieldsEl = document.getElementById('anamneseFields');
    if (anamneseFieldsEl) {
        anamneseFieldsEl.addEventListener('input', aoResponderAnamnese);
        anamneseFieldsEl.addEventListener('change', aoResponderAnamnese);
    }

    function coletarAnamnese() {
        document.getElementById('anamneseRespostasJson').value = JSON.stringify(respostasDaTela(true));
    }

    // ═══ VALIDAÇÃO DO FORMULÁRIO INTEIRO (antes de enviar) ═══
    // Pinta o erro ao lado de cada campo e devolve {primeiro, quantos}. O servidor valida tudo de novo.
    function validarFormulario() {
        var primeiro = null, quantos = 0;
        function anotar(campo, msg) {
            if (!msg) return;
            quantos++;
            if (!primeiro) primeiro = campo;
        }
        // dados pessoais so entram depois do celular verificado (antes disso nem estao na tela)
        var dadosNaTela = !(document.getElementById('dados-fields') || {}).hidden;
        if (dadosNaTela) {
            CAMPOS_PESSOAIS.forEach(function(def) { anotar(document.getElementById(def.id), validarCampoPessoal(def)); });
        }

        var ficha = validarAnamnese();
        if (ficha.quantos) { quantos += ficha.quantos; if (!primeiro) primeiro = ficha.primeiro; }

        var saude = document.getElementById('form-consent-saude');
        if (saude) {
            var msgSaude = (saude.required && !saude.checked)
                ? 'Marque esta autorização para enviarmos suas respostas à profissional.' : '';
            marcarErro(saude, document.getElementById('erro-consent-saude'), msgSaude);
            document.getElementById('consent-saude-box').classList.toggle('com-erro', !!msgSaude);
            anotar(saude, msgSaude);
        }

        document.querySelectorAll('#termosFields input[type="checkbox"]').forEach(function(cb) {
            var msg = cb.checked ? '' : 'Leia e aceite o termo "' + (cb.dataset.termo || '') + '" para continuar.';
            var box = cb.closest('.aceite-box');
            marcarErro(cb, box.querySelector('.form-erro'), msg);
            box.classList.toggle('com-erro', !!msg);
            anotar(cb, msg);
        });

        var politica = document.getElementById('form-aceite-politica');
        if (politica) {
            var msgPolitica = politica.checked ? '' : 'Para agendar, marque que leu e aceita a Política de Privacidade.';
            marcarErro(politica, document.getElementById('erro-politica'), msgPolitica);
            document.getElementById('aceite-politica-box').classList.toggle('com-erro', !!msgPolitica);
            anotar(politica, msgPolitica);
        }
        return {primeiro: primeiro, quantos: quantos};
    }

    ['form-consent-saude', 'form-aceite-politica'].forEach(function(id) {
        var cb = document.getElementById(id);
        if (!cb) return;
        cb.addEventListener('change', function() {
            if (!cb.checked) return;
            var erro = document.getElementById(id === 'form-consent-saude' ? 'erro-consent-saude' : 'erro-politica');
            marcarErro(cb, erro, '');
            cb.closest('.aceite-box').classList.remove('com-erro');
        });
    });

    // ═══ SUBMIT (guarda contra duplo clique) ═══
    var bookingForm = document.getElementById('booking-form');
    var btnConfirmar = document.getElementById('btn-confirmar');
    var textoConfirmar = btnConfirmar ? btnConfirmar.innerHTML : '';
    if (bookingForm) {
        bookingForm.addEventListener('submit', function(e) {
            if (bookingForm.dataset.enviando === '1') { e.preventDefault(); return; }
            var resultado = validarFormulario();
            if (resultado.quantos) {
                e.preventDefault();
                erroGeral(resultado.quantos === 1
                    ? 'Falta um item para concluir. Veja o campo destacado acima.'
                    : 'Faltam ' + resultado.quantos + ' itens para concluir. Veja os campos destacados acima.');
                var alvo = resultado.primeiro;
                if (alvo) {
                    var bloco = alvo.closest('.ficha-campo, .form-field, .aceite-box') || alvo;
                    if (bloco.scrollIntoView) bloco.scrollIntoView({block: 'center'});
                    if (alvo.focus) alvo.focus({preventScroll: true});
                }
                return;
            }
            erroGeral('');
            coletarAnamnese();
            salvarEstado();
            bookingForm.dataset.enviando = '1';
            if (btnConfirmar) {
                btnConfirmar.disabled = true;
                btnConfirmar.textContent = 'Confirmando...';
            }
        });
        // Volta pelo histórico (bfcache): reabilita o botão
        window.addEventListener('pageshow', function(ev) {
            if (ev.persisted && bookingForm.dataset.enviando === '1') {
                bookingForm.dataset.enviando = '';
                if (btnConfirmar) { btnConfirmar.disabled = !otpVerificado; btnConfirmar.innerHTML = textoConfirmar; }
            }
        });
    }

    // ═══ PHONE MASK ═══
    var telInput = document.getElementById('form-telefone');
    if (telInput) {
        telInput.addEventListener('input', function(e) {
            if (e.target.readOnly) return;
            e.target.value = formatarTelefone(e.target.value);
            salvarEstado();
        });
    }

    // ═══ OTP FLOW ═══
    var csrfEl = document.querySelector('#booking-form input[name="csrfmiddlewaretoken"]');
    var OTP_CSRF = csrfEl ? csrfEl.value : '';
    var otpVerificado = false;

    function linkWhatsApp() {
        if (!cfg.whatsappNumero) return null;
        var a = document.createElement('a');
        a.href = 'https://wa.me/' + encodeURIComponent(cfg.whatsappNumero) + '?text=' +
            encodeURIComponent('Olá! Gostaria de agendar um horário.');
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = 'Agende pelo WhatsApp';
        // estilo (sublinhado, cor da mensagem) vem de .msg-ok/.msg-erro no template
        return a;
    }

    function escreverMsg(elId, texto, ok, comWhatsApp) {
        var el = document.getElementById(elId);
        if (!el) return;
        el.textContent = texto;
        // tokens do tema: as cores fixas davam ~3:1 e ~2,5:1 no tema escuro
        el.classList.toggle('msg-ok', !!ok);
        el.classList.toggle('msg-erro', !ok);
        if (comWhatsApp) {
            var a = linkWhatsApp();
            if (a) { el.appendChild(document.createTextNode(' ')); el.appendChild(a); }
        }
    }

    // Mensagens do envio (campo celular) e da verificação (campo código)
    function setStatusMsg(texto, ok, comWhatsApp) {
        escreverMsg('otp-status', texto, ok, comWhatsApp);
        var tel = document.getElementById('form-telefone');
        if (tel) { if (ok) tel.removeAttribute('aria-invalid'); else tel.setAttribute('aria-invalid', 'true'); }
    }
    function setOtpMsg(texto, ok, comWhatsApp) {
        var field = document.getElementById('otp-field');
        if (field && field.hidden) { setStatusMsg(texto, ok, comWhatsApp); return; }
        escreverMsg('otp-msg', texto, ok, comWhatsApp);
        var cod = document.getElementById('otp-codigo');
        if (cod) { if (ok) cod.removeAttribute('aria-invalid'); else cod.setAttribute('aria-invalid', 'true'); }
    }

    // Contrato do servidor (utils/respostas): {ok, codigo, mensagem}. Resposta que não é JSON
    // (bloqueio do rate limit, queda de rede) vira um código local.
    function lerJson(r) {
        return r.text().then(function(t) {
            var data;
            try { data = JSON.parse(t); }
            catch (e) { data = {ok: false, codigo: (r.status === 403 || r.status === 429) ? 'limite' : 'rede'}; }
            return {status: r.status, data: data};
        });
    }

    function resetCaptcha() {
        if (window.turnstile && typeof window.turnstile.reset === 'function') {
            try { window.turnstile.reset(); } catch (e) {}
        }
    }

    function mostrarDados(prefill, clienteExistente) {
        var dados = document.getElementById('dados-fields');
        var badge = document.getElementById('cliente-badge');
        dados.hidden = false;
        var nome = document.getElementById('form-nome');
        var nasc = document.getElementById('form-nascimento');
        var email = document.getElementById('form-email');
        if (prefill) {
            nome.value = prefill.nome || '';
            nasc.value = prefill.data_nascimento || '';
            if (email && prefill.email && !email.value) email.value = prefill.email;
            salvarEstado();
        }
        nome.required = true;
        nasc.required = true;
        titularDoCelular(clienteExistente);
        if (clienteExistente) {
            badge.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
            badge.appendChild(document.createTextNode('Bem-vindo(a) de volta — seus dados foram carregados.'));
            badge.setAttribute('data-state', 'existing');
        } else {
            badge.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.9 5.5L19.5 10l-5.6 1.5L12 17l-1.9-5.5L4.5 10l5.6-1.5z"/></svg>';
            badge.appendChild(document.createTextNode('Novo por aqui — preencha seus dados abaixo.'));
            badge.setAttribute('data-state', 'new');
        }
        badge.hidden = false;
        if (btnConfirmar) {
            btnConfirmar.disabled = false;
            btnConfirmar.title = '';
        }
    }

    // D16: o agendamento é da titular do celular verificado — com cadastro, o nome não muda por
    // aqui (o servidor ignora nome diferente); agendar p/ outra pessoa = usar o celular dela.
    function titularDoCelular(existente) {
        var nome = document.getElementById('form-nome');
        var aviso = document.getElementById('nome-titular-aviso');
        if (!nome) return;
        nome.readOnly = !!existente;
        if (existente) { nome.setAttribute('aria-readonly', 'true'); marcarErro(nome, document.getElementById('erro-nome'), ''); }
        else nome.removeAttribute('aria-readonly');
        if (aviso) aviso.hidden = !existente;
    }

    function marcarVerificado() {
        otpVerificado = true;
        var tel = document.getElementById('form-telefone');
        tel.readOnly = true;  // o agendamento vale só p/ o celular verificado
        tel.setAttribute('aria-readonly', 'true');
        var bEnv = document.getElementById('btn-enviar-otp');
        var bVer = document.getElementById('btn-verificar-otp');
        if (bEnv) bEnv.hidden = true;
        if (bVer) bVer.hidden = true;
        var otpField = document.getElementById('otp-field');
        if (otpField) otpField.hidden = true;
        var cap = document.getElementById('captcha-field');
        if (cap) cap.hidden = true;
        setStatusMsg('Celular verificado.', true);
    }

    // SMS só chega em celular: DDD que existe + 9 + 8 dígitos, e não um número de enfeite
    // (mesma regra do servidor: utils/telefone.eh_celular)
    var CELULAR_RE = /^[1-9]{2}9\d{8}$/;
    var DDDS = '11 12 13 14 15 16 17 18 19 21 22 24 27 28 31 32 33 34 35 37 38 41 42 43 44 45 46 47 48 49 51 53 54 55 '
        + '61 62 63 64 65 66 67 68 69 71 73 74 75 77 79 81 82 83 84 85 86 87 88 89 91 92 93 94 95 96 97 98 99';
    var DDDS_VALIDOS = DDDS.split(' ');

    function erroDoCelular(digitos) {
        if (!digitos) return 'Informe seu celular com DDD.';
        if (digitos.length < 11) return 'Número incompleto. Informe o DDD e os 9 dígitos do celular.';
        if (DDDS_VALIDOS.indexOf(digitos.slice(0, 2)) === -1) return 'O DDD ' + digitos.slice(0, 2) + ' não existe. Confira o número.';
        if (!CELULAR_RE.test(digitos)) return 'Informe um celular com DDD (9 dígitos, começando com 9).';
        if (/^(\d)\1+$/.test(digitos.slice(3))) return 'Esse número não parece um celular de verdade. Confira os dígitos.';
        return '';
    }

    var btnEnviar = document.getElementById('btn-enviar-otp');
    if (btnEnviar) {
        btnEnviar.addEventListener('click', function() {
            var digitos = soDigitos(document.getElementById('form-telefone').value);
            var erroTel = erroDoCelular(digitos);
            if (erroTel) {  // fixo não recebe SMS; DDD inexistente e número de enfeite também não
                setStatusMsg(erroTel, false);
                document.getElementById('form-telefone').focus();
                return;
            }
            btnEnviar.disabled = true;
            setStatusMsg('Enviando SMS...', true);
            var fd = new FormData();
            fd.append('telefone', digitos);
            fd.append('csrfmiddlewaretoken', OTP_CSRF);
            var cap = document.querySelector('#captcha-field input[name="cf-turnstile-response"]');
            if (cap) fd.append('cf-turnstile-response', cap.value);
            fetch(cfg.urlOtpSolicitar, {method:'POST', body: fd, headers:{'X-CSRFToken': OTP_CSRF}})
                .then(lerJson)
                .then(function(res) {
                    btnEnviar.disabled = false;
                    resetCaptcha();  // token Turnstile é de uso único
                    var codigo = res.data.codigo;
                    if (res.data.ok) {
                        document.getElementById('otp-field').hidden = false;
                        setStatusMsg('Código enviado por SMS para seu celular. Válido por 10 minutos.', true);
                        var txt = btnEnviar.querySelector('.otp-btn-text');
                        if (txt) txt.textContent = 'Reenviar';
                        var campoCodigo = document.getElementById('otp-codigo');
                        if (campoCodigo) campoCodigo.focus();
                        // Ambiente de testes / dev: SMS simulado, o servidor devolve o codigo
                        if (res.data.codigo_teste && campoCodigo) {
                            campoCodigo.value = res.data.codigo_teste;
                            setStatusMsg('Ambiente de testes (SMS simulado): código ' + res.data.codigo_teste + ' já preenchido. Clique em Verificar.', true);
                        }
                    } else if (codigo === 'limite') {
                        setStatusMsg('Aguarde um minuto antes de pedir outro código.', false);
                    } else {
                        // Nenhum código novo no limite da hora: o último recebido ainda vale
                        if (codigo === 'limite_sms') document.getElementById('otp-field').hidden = false;
                        // Erro que a pessoa resolve sozinha não oferece o WhatsApp
                        var resolveSozinha = ['aguarde', 'captcha', 'telefone_invalido'].indexOf(codigo) !== -1;
                        setStatusMsg(res.data.mensagem || 'Não foi possível enviar o código. Tente novamente.', false, !resolveSozinha);
                    }
                })
                .catch(function() {
                    btnEnviar.disabled = false;
                    setStatusMsg('Erro de conexão. Tente novamente.', false);
                });
        });
    }

    var campoOtp = document.getElementById('otp-codigo');
    if (campoOtp) {
        campoOtp.addEventListener('input', function() { campoOtp.value = campoOtp.value.replace(/\D/g, '').slice(0, 6); });
    }

    var btnVerif = document.getElementById('btn-verificar-otp');
    if (btnVerif) {
        btnVerif.addEventListener('click', function() {
            var digitos = soDigitos(document.getElementById('form-telefone').value);
            var codigo = (document.getElementById('otp-codigo').value || '').trim();
            if (!/^\d{6}$/.test(codigo)) {
                setOtpMsg('O código tem 6 números. Confira o SMS.', false);
                return;
            }
            btnVerif.disabled = true;
            var fd = new FormData();
            fd.append('telefone', digitos);
            fd.append('codigo', codigo);
            // ultimo horário do pedido: o servidor diz quais fichas ainda estarão em dia nele (D14)
            if (todosProntos()) { var ordem = emOrdem(); fd.append('inicio', ordem[ordem.length - 1].slot.iso); }
            fd.append('csrfmiddlewaretoken', OTP_CSRF);
            fetch(cfg.urlOtpVerificar, {method:'POST', body: fd, headers:{'X-CSRFToken': OTP_CSRF}})
                .then(lerJson)
                .then(function(res) {
                    btnVerif.disabled = false;
                    if (res.data.ok) {
                        marcarVerificado();
                        if (res.data.aviso === 'bloqueado_online') {
                            setStatusMsg(res.data.aviso_mensagem, false, true);
                            return;
                        }
                        mostrarDados(res.data.prefill, !!res.data.prefill);
                        FICHAS_EM_DIA = res.data.fichas_em_dia || {};
                        aplicarFichasEmDia();
                    } else if (res.data.codigo === 'limite') {
                        setOtpMsg('Muitas tentativas. Aguarde um minuto.', false);
                    } else {
                        setOtpMsg(res.data.mensagem || 'Código expirado. Solicite um novo.', false);
                    }
                })
                .catch(function() {
                    btnVerif.disabled = false;
                    setOtpMsg('Erro de conexão. Tente novamente.', false);
                });
        });
    }

    // ═══ RE-HIDRATAR no load se houve erro do servidor (W4) ═══
    var rehidratado = rehidratar();

    // Celular já verificado nesta sessão (ex.: erro no "Confirmar"): não exige novo SMS
    if (cfg.otpTelefone && telInput) {
        var atual = soDigitos(telInput.value);
        if (!atual) {
            telInput.value = formatarTelefone(cfg.otpTelefone);
            atual = cfg.otpTelefone;
        }
        if (atual === cfg.otpTelefone) {
            marcarVerificado();
            mostrarDados(null, false);
            titularDoCelular(!!cfg.otpTitular);
            var badge = document.getElementById('cliente-badge');
            if (badge) badge.hidden = true;
        }
    }

    // Recusa do servidor: o motivo fica no topo do cartão — leva a pessoa até ele
    var erroServidor = document.getElementById('wizard-erro-servidor');
    if (erroServidor) {
        setTimeout(function() {
            if (erroServidor.scrollIntoView) erroServidor.scrollIntoView({block: 'start'});
            erroServidor.focus({preventScroll: true});
        }, 60);
    }

    // Pre-select: veio de "Agendar <servico>" — a escolha ja foi feita, abre na data (o Voltar
    // do navegador leva a etapa 1 p/ trocar ou somar outro tratamento)
    if (!rehidratado && preselect) {
        var card = document.querySelector('.proc-card[data-proc-id="' + preselect + '"]');
        if (card) {
            alternarProc(card);
            goToStep(2, {semFoco: true});
        }
    }

})();
