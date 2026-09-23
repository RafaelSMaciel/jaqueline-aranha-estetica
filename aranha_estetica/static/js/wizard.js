/* wizard.js — Booking wizard (CSP-safe, sem tags Django)
 * Configuração injetada pelo template em window.WIZARD_CFG:
 *   urlOtpSolicitar / urlOtpVerificar — endpoints do OTP (SMS)
 *   urlDias / urlHorarios             — disponibilidade (SlotService)
 *   procPreselect   — id do procedimento pré-selecionado (ou vazio)
 *   profPreselect   — id do profissional pré-selecionado (link "Agendar com X")
 *   otpTelefone     — celular já verificado nesta sessão (dígitos) ou vazio
 *   rehidratar      — true quando o servidor devolveu erro do "Confirmar"
 *   whatsappNumero  — dígitos p/ CTA de WhatsApp (vazio = sem CTA)
 *
 * Identidade do agendamento = CELULAR verificado por SMS. O e-mail é só um
 * dado de contato — nunca é enviado no OTP.
 */

/* ─── bloco 1: filtro de categorias ─── */
(function(){
    var tabs = document.querySelectorAll('.cat-tab');
    var cards = document.querySelectorAll('.proc-card');
    if (!tabs.length) return;
    tabs.forEach(function(tab){
        tab.addEventListener('click', function(){
            var cat = tab.dataset.cat;
            tabs.forEach(function(t){
                t.classList.remove('active');
                t.style.background = '#fff';
                t.style.color = '#7a5f1f';
            });
            tab.classList.add('active');
            tab.style.background = 'var(--gold,#C9A84C)';
            tab.style.color = 'white';
            cards.forEach(function(c){
                c.style.display = (!cat || c.dataset.categoria === cat) ? '' : 'none';
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

    // ═══ STATE ═══
    var selectedProc = null;
    var selectedDate = null;
    var selectedSlot = null;
    var selectedProf = null;
    var currentMonth = primeiroDoMes();
    var diasDisponiveis = [];
    var horariosData = [];
    // Respostas AJAX fora de ordem: so a do pedido mais recente pinta a tela
    var seqMes = 0;
    var seqHorarios = 0;

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

    // ═══ CONSENTS DE COMUNICAÇÃO (espelham o cadastro após o OTP) ═══
    var CAMPOS_CONSENT = {
        consent_email_marketing: 'form-consent-email',
        consent_whatsapp_confirmacao: 'form-consent-wa-d1',
        consent_whatsapp_nps: 'form-consent-nps'
    };

    function lerConsents() {
        var out = {};
        Object.keys(CAMPOS_CONSENT).forEach(function(k) {
            var el = document.getElementById(CAMPOS_CONSENT[k]);
            if (el) out[k] = !!el.checked;
        });
        return out;
    }

    function aplicarConsents(consents) {
        if (!consents) return;
        Object.keys(CAMPOS_CONSENT).forEach(function(k) {
            var el = document.getElementById(CAMPOS_CONSENT[k]);
            if (el && typeof consents[k] === 'boolean') el.checked = consents[k];
        });
    }

    // '1' = checkboxes mostram o estado do cadastro: desmarcar revoga no servidor
    function consentsSincronizados(valor) {
        var el = document.getElementById('form-consents-sincronizados');
        if (!el) return '';
        if (valor !== undefined) el.value = valor ? '1' : '';
        return el.value;
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
        return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
    }

    function query(params) {
        return Object.keys(params).filter(function(k){ return params[k] !== '' && params[k] != null; })
            .map(function(k){ return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
            .join('&');
    }

    // ═══ SESSION STORAGE — salvar/re-hidratar estado (W4) ═══
    var SESSION_KEY = 'wizard_estado';

    function salvarEstado() {
        try {
            sessionStorage.setItem(SESSION_KEY, JSON.stringify({
                proc: selectedProc,
                date: selectedDate,
                slot: selectedSlot,
                prof: selectedProf,
                form: {
                    nome:       (document.getElementById('form-nome')       || {}).value || '',
                    telefone:   (document.getElementById('form-telefone')   || {}).value || '',
                    nascimento: (document.getElementById('form-nascimento') || {}).value || '',
                    email:      (document.getElementById('form-email')      || {}).value || ''
                },
                consents: lerConsents(),
                consentsSincronizados: consentsSincronizados()
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
        if (!st || !st.proc) return false;

        selectedProc = st.proc;
        selectedDate = st.date;
        selectedSlot = st.slot;
        selectedProf = st.prof;

        // Repor campos do form
        if (st.form) {
            ['nome', 'telefone', 'nascimento', 'email'].forEach(function(k) {
                var f = document.getElementById('form-' + k);
                if (f && st.form[k] != null) f.value = st.form[k];
            });
        }
        // Opt-ins como a pessoa deixou (sem isso, reenviar revogaria/zeraria)
        aplicarConsents(st.consents);
        consentsSincronizados(st.consentsSincronizados === '1');

        // Calendário e horários do step 2 prontos p/ o "Voltar"
        var base = parseData(selectedDate);
        currentMonth = primeiroDoMes(base);
        if (selectedProc) loadMonth();
        if (selectedDate && selectedProc) carregarHorarios(selectedDate);

        // Popula hiddens + resumo + navega pro step 3 (reutiliza função existente)
        goToStep3();
        return true;
    }

    // ═══ STEP NAVIGATION ═══
    window.goToStep = function(n) {
        var panels = document.querySelectorAll('.step-panel');
        for (var i = 0; i < panels.length; i++) {
            panels[i].classList.remove('active');
        }
        document.getElementById('step-' + n).classList.add('active');

        var items = document.querySelectorAll('.step-item');
        for (var k = 0; k < items.length; k++) {
            items[k].classList.remove('active', 'done');
            if (k + 1 < n) items[k].classList.add('done');
            if (k + 1 === n) items[k].classList.add('active');
        }
    };

    // ═══ DELEGATED [data-goto] LISTENER ═══
    document.addEventListener('click', function(e) {
        var el = e.target.closest('[data-goto]');
        if (!el) return;
        goToStep(parseInt(el.dataset.goto, 10));
    });

    // ═══ STEP 1: SELECT PROCEDURE ═══
    var procCards = document.querySelectorAll('.proc-card');
    for (var c = 0; c < procCards.length; c++) {
        procCards[c].addEventListener('click', function() {
            var allCards = document.querySelectorAll('.proc-card');
            for (var j = 0; j < allCards.length; j++) allCards[j].classList.remove('selected');
            this.classList.add('selected');

            selectedProc = {
                id: String(this.dataset.procId),
                nome: this.dataset.procNome,
                preco: parseFloat(this.dataset.procPreco) || 0,
                duracao: parseInt(this.dataset.procDuracao, 10),
                categoria: this.dataset.procCategoria || this.dataset.categoria || ''
            };

            selectedDate = null;
            selectedSlot = null;
            selectedProf = null;

            salvarEstado();
            goToStep(2);
            currentMonth = primeiroDoMes();
            loadMonth();
        });
    }

    // ═══ STEP 2: CALENDAR ═══
    function loadMonth() {
        var year = currentMonth.getFullYear();
        var month = currentMonth.getMonth();
        document.getElementById('cal-month-label').textContent = meses[month] + ' ' + year;

        var mesStr = year + '-' + String(month + 1).padStart(2, '0');
        var qs = query({mes: mesStr, procedimento_id: selectedProc.id, profissional_id: profPreselect});
        var meu = ++seqMes;

        fetch(URL_DIAS + '?' + qs)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (meu !== seqMes) return;  // outro mês já foi pedido
                diasDisponiveis = data.dias_disponiveis || [];
                renderCalendar(year, month);
            })
            .catch(function() {
                if (meu !== seqMes) return;
                diasDisponiveis = [];
                renderCalendar(year, month);
            });
    }

    function renderCalendar(year, month) {
        var grid = document.getElementById('cal-days-grid');
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
            var isAvailable = diasDisponiveis.indexOf(dateStr) !== -1;

            var div = document.createElement('button');
            div.type = 'button';
            div.textContent = d;
            div.className = 'cal-day';
            if (isToday) div.classList.add('today');
            if (isAvailable) {
                div.classList.add('available');
                div.setAttribute('data-date', dateStr);
                if (dateStr === selectedDate) div.classList.add('selected');
                div.addEventListener('click', function() {
                    selectDate(this.getAttribute('data-date'));
                });
            } else {
                div.disabled = true;
            }
            grid.appendChild(div);
        }
    }

    document.getElementById('cal-prev').addEventListener('click', function() {
        currentMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1);
        loadMonth();
    });
    document.getElementById('cal-next').addEventListener('click', function() {
        currentMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1);
        loadMonth();
    });

    var SVG_LOADING = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" stroke-width="2" stroke-linecap="round" style="display:block;margin:0 auto 0.75rem;" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></path></svg>';
    var SVG_INFO = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#CBB994" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:0 auto 0.75rem;" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

    function mensagemSlots(container, svg, texto) {
        var box = document.createElement('div');
        box.className = 'slots-loading';
        box.innerHTML = svg; // SVG estático (sem dados do usuário)
        box.appendChild(document.createTextNode(texto));
        container.textContent = '';
        container.appendChild(box);
    }

    function selectDate(dateStr) {
        selectedDate = dateStr;
        selectedSlot = null;
        selectedProf = null;
        salvarEstado();

        var allDays = document.querySelectorAll('.cal-day');
        for (var i = 0; i < allDays.length; i++) allDays[i].classList.remove('selected');
        var sel = document.querySelector('.cal-day[data-date="' + dateStr + '"]');
        if (sel) sel.classList.add('selected');

        carregarHorarios(dateStr);
    }

    function carregarHorarios(dateStr) {
        document.getElementById('prof-section').style.setProperty('display', 'none', 'important');
        var container = document.getElementById('slots-container');
        mensagemSlots(container, SVG_LOADING, 'Carregando horários...');

        var qs = query({data: dateStr, procedimento_id: selectedProc.id, profissional_id: profPreselect});
        var meu = ++seqHorarios;
        // Resposta antiga (outro dia clicado depois) não troca os horários
        function obsoleta() { return meu !== seqHorarios || dateStr !== selectedDate; }
        fetch(URL_HORARIOS + '?' + qs)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (obsoleta()) return;
                horariosData = data.horarios || [];
                if (horariosData.length === 0) {
                    mensagemSlots(container, SVG_INFO, 'Nenhum horário disponível nesta data.');
                    return;
                }

                var parts = dateStr.split('-');
                var title = document.createElement('div');
                title.className = 'slots-title';
                title.textContent = 'Horários para ' + parts[2] + '/' + parts[1] + '/' + parts[0];
                var grid = document.createElement('div');
                grid.className = 'slots-grid';
                horariosData.forEach(function(slot, i) {
                    var b = document.createElement('button');
                    b.type = 'button';
                    b.className = 'slot-btn';
                    b.setAttribute('data-idx', String(i));
                    b.textContent = slot.horario;
                    if (selectedSlot && selectedSlot.iso === slot.datetime_iso) b.classList.add('selected');
                    b.addEventListener('click', function() { selectSlot(this); });
                    grid.appendChild(b);
                });
                container.textContent = '';
                container.appendChild(title);
                container.appendChild(grid);
            })
            .catch(function() {
                if (obsoleta()) return;
                mensagemSlots(container, SVG_INFO, 'Erro ao carregar horários.');
            });
    }

    function selectSlot(btn) {
        var allBtns = document.querySelectorAll('.slot-btn');
        for (var i = 0; i < allBtns.length; i++) allBtns[i].classList.remove('selected');
        btn.classList.add('selected');

        var idx = parseInt(btn.getAttribute('data-idx'), 10);
        var slot = horariosData[idx];
        selectedSlot = {
            horario: slot.horario,
            iso: slot.datetime_iso,
            profissionais: slot.profissionais
        };
        salvarEstado();

        var profSection = document.getElementById('prof-section');
        var profOptions = document.getElementById('prof-options');
        profSection.style.setProperty('display', 'block', 'important');
        profOptions.textContent = '';

        if (selectedSlot.profissionais.length === 1) {
            selectedProf = selectedSlot.profissionais[0];
            salvarEstado();
            var pbtn = document.createElement('button');
            pbtn.type = 'button';
            pbtn.className = 'prof-btn selected';
            pbtn.textContent = selectedProf.nome;
            profOptions.appendChild(pbtn);
            setTimeout(function() { goToStep3(); }, 500);
        } else {
            selectedSlot.profissionais.forEach(function(prof) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'prof-btn';
                b.textContent = prof.nome;
                b.addEventListener('click', function() {
                    var all = document.querySelectorAll('.prof-btn');
                    for (var k = 0; k < all.length; k++) all[k].classList.remove('selected');
                    this.classList.add('selected');
                    selectedProf = prof;
                    salvarEstado();
                    setTimeout(function() { goToStep3(); }, 500);
                });
                profOptions.appendChild(b);
            });
        }
    }

    function goToStep3() {
        if (!selectedProc || !selectedSlot || !selectedProf) return;

        document.getElementById('sum-proc').textContent = selectedProc.nome;

        var d = new Date(selectedSlot.iso);
        var opts = {weekday:'long', day:'numeric', month:'long', hour:'2-digit', minute:'2-digit', timeZone: TZ};
        var textoData;
        try { textoData = d.toLocaleString('pt-BR', opts); }
        catch (e) { textoData = d.toLocaleString('pt-BR'); }
        document.getElementById('sum-datetime').textContent = textoData;
        document.getElementById('sum-prof').textContent = selectedProf.nome;
        renderPreco();

        document.getElementById('form-procedimento').value = selectedProc.id;
        document.getElementById('form-profissional').value = selectedProf.id;
        document.getElementById('form-datetime').value = selectedSlot.iso;

        renderAnamnese();
        renderTermos();

        goToStep(3);
    }

    // Valor da DATA escolhida p/ o profissional (API de horários, promoção
    // inclusa) — o mesmo que o servidor grava. Cheio riscado + nome da promo.
    function renderPreco() {
        var el = document.getElementById('sum-price');
        el.textContent = '';
        var prof = selectedProf || {};
        if (!('valor' in prof)) {  // estado antigo salvo na aba: preço base do card
            el.textContent = formatarPreco(selectedProc.preco);
            return;
        }
        var valor = prof.valor === null ? NaN : parseFloat(prof.valor);
        if (isNaN(valor)) {
            el.textContent = 'A consultar';
            return;
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
    }

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
        if (!container || !fields || !selectedProc) return;
        fields.textContent = '';
        var aplicaveis = TERMOS_PROCEDIMENTO.filter(function(t) {
            return t.procedimento_id == null || String(t.procedimento_id) === String(selectedProc.id);
        });
        container.style.display = aplicaveis.length ? '' : 'none';
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
            var sp = document.createElement('span');
            sp.textContent = 'Li e aceito este termo.';
            lbl.appendChild(cb);
            lbl.appendChild(sp);
            box.appendChild(ttl);
            box.appendChild(txt);
            box.appendChild(lbl);
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

    function formularioAplicaAoProc(form, proc) {
        if (form.escopo === 'GLOBAL') return true;
        if (form.escopo === 'CATEGORIA') return !!proc.categoria && form.categoria === proc.categoria;
        if (form.escopo === 'PROCEDIMENTO') return String(form.procedimento_id) === String(proc.id);
        return false;
    }

    function criarSelect(opcoes) {
        var sel = document.createElement('select');
        var vazio = document.createElement('option');
        vazio.value = ''; vazio.textContent = '—';
        sel.appendChild(vazio);
        opcoes.forEach(function(o){
            var opt = document.createElement('option');
            opt.value = o.valor; opt.textContent = o.rotulo;
            sel.appendChild(opt);
        });
        return sel;
    }

    function renderAnamnese() {
        var container = document.getElementById('anamneseContainer');
        var fields = document.getElementById('anamneseFields');
        if (!container || !fields || !selectedProc) return;
        fields.textContent = '';

        var aplicaveis = FORMULARIOS_ANAMNESE.filter(function(f){
            return formularioAplicaAoProc(f, selectedProc);
        });
        setAnamneseMsg('');
        atualizarConsentSaude();
        if (aplicaveis.length === 0) {
            container.style.display = 'none';
            return;
        }
        container.style.display = '';

        aplicaveis.forEach(function(form){
            var formWrap = document.createElement('div');
            // Ficha opcional: perguntas "obrigatórias" só valem se a pessoa
            // começar a responder (mesma regra do servidor, _validar_anamnese).
            formWrap.dataset.fichaId = form.id;
            formWrap.dataset.fichaObrigatoria = form.obrigatorio ? '1' : '';
            formWrap.style.cssText = 'border:1px solid #eee;border-radius:10px;padding:0.75rem;margin-bottom:0.6rem;background:#fafafa;';
            var ttl = document.createElement('div');
            ttl.style.cssText = 'font-weight:700;color:#4A3425;font-size:0.85rem;margin-bottom:0.5rem;';
            ttl.textContent = form.nome;
            formWrap.appendChild(ttl);

            (form.schema || []).forEach(function(field){
                var fwrap = document.createElement('div');
                fwrap.style.marginBottom = '0.5rem';
                var lbl = document.createElement('label');
                lbl.style.cssText = 'display:block;font-size:0.78rem;color:#666;font-weight:600;margin-bottom:0.2rem;';
                lbl.textContent = field.label + (field.obrigatorio ? ' *' : '');
                fwrap.appendChild(lbl);

                var opcoes = (field.opcoes || []).map(function(o){ return {valor: String(o), rotulo: String(o)}; });

                if (field.tipo === 'checkboxes') {
                    var grupo = document.createElement('div');
                    grupo.setAttribute('role', 'group');
                    // Nome acessível do grupo = a pergunta visível
                    lbl.id = 'lbl-' + form.id + '-' + String(field.key).replace(/[^\w-]/g, '_');
                    grupo.setAttribute('aria-labelledby', lbl.id);
                    grupo.dataset.grupoFormId = form.id;
                    grupo.dataset.grupoFieldKey = field.key;
                    grupo.dataset.obrigatorio = field.obrigatorio ? '1' : '';
                    grupo.dataset.label = field.label;
                    opcoes.forEach(function(o){
                        var l = document.createElement('label');
                        l.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:0.82rem;color:#4A3425;';
                        var cb = document.createElement('input');
                        cb.type = 'checkbox';
                        cb.value = o.valor;
                        cb.dataset.formId = form.id;
                        cb.dataset.fieldKey = field.key;
                        l.appendChild(cb);
                        l.appendChild(document.createTextNode(o.rotulo));
                        grupo.appendChild(l);
                    });
                    fwrap.appendChild(grupo);
                    formWrap.appendChild(fwrap);
                    return;
                }

                var input;
                if (field.tipo === 'bool') {
                    input = criarSelect([{valor: 'sim', rotulo: 'Sim'}, {valor: 'nao', rotulo: 'Não'}]);
                } else if (field.tipo === 'select' || field.tipo === 'scale') {
                    input = criarSelect(opcoes);
                } else if (field.tipo === 'longtext') {
                    input = document.createElement('textarea');
                    input.rows = 2;
                } else if (field.tipo === 'number') {
                    input = document.createElement('input'); input.type = 'number';
                } else if (field.tipo === 'date') {
                    input = document.createElement('input'); input.type = 'date';
                } else if (field.tipo === 'email') {
                    input = document.createElement('input'); input.type = 'email';
                } else {
                    input = document.createElement('input'); input.type = 'text';
                }
                input.style.cssText = 'width:100%;padding:0.45rem;border:1px solid #ddd;border-radius:6px;font-size:0.85rem;';
                input.dataset.formId = form.id;
                input.dataset.fieldKey = field.key;
                input.setAttribute('aria-label', field.label);
                input.dataset.obrigatorio = field.obrigatorio ? '1' : '';
                input.required = !!(field.obrigatorio && form.obrigatorio);
                fwrap.appendChild(input);
                formWrap.appendChild(fwrap);
            });
            fields.appendChild(formWrap);
        });
    }

    function temResposta(campos) {
        for (var i = 0; i < campos.length; i++) {
            var el = campos[i];
            if (el.type === 'checkbox' ? el.checked : String(el.value || '').trim()) return true;
        }
        return false;
    }

    function anamneseTemResposta() {
        return temResposta(document.querySelectorAll('#anamneseFields [data-form-id]'));
    }

    // Alguma pergunta DESTA ficha respondida (a ficha opcional passa a valer)
    function fichaPreenchida(formId) {
        return temResposta(document.querySelectorAll(
            '#anamneseFields [data-form-id="' + String(formId) + '"]'
        ));
    }

    function fichaExigida(formId) {
        var wrap = document.querySelector('#anamneseFields [data-ficha-id="' + String(formId) + '"]');
        return !!(wrap && wrap.dataset.fichaObrigatoria) || fichaPreenchida(formId);
    }

    // Ficha opcional em branco não trava o agendamento; ao começar a responder,
    // as perguntas obrigatórias dela passam a ser exigidas.
    function sincronizarObrigatorios() {
        var wraps = document.querySelectorAll('#anamneseFields [data-ficha-id]');
        for (var i = 0; i < wraps.length; i++) {
            if (wraps[i].dataset.fichaObrigatoria) continue;
            var exigir = fichaPreenchida(wraps[i].dataset.fichaId);
            var campos = wraps[i].querySelectorAll('[data-form-id][data-obrigatorio="1"]');
            for (var k = 0; k < campos.length; k++) campos[k].required = exigir;
        }
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

    function aoResponderAnamnese() {
        sincronizarObrigatorios();
        atualizarConsentSaude();
        setAnamneseMsg('');
    }

    var anamneseFieldsEl = document.getElementById('anamneseFields');
    if (anamneseFieldsEl) {
        anamneseFieldsEl.addEventListener('input', aoResponderAnamnese);
        anamneseFieldsEl.addEventListener('change', aoResponderAnamnese);
    }

    function coletarAnamnese() {
        var dados = {};
        document.querySelectorAll('#anamneseFields [data-form-id]').forEach(function(el){
            var fid = el.dataset.formId;
            var key = el.dataset.fieldKey;
            if (!dados[fid]) dados[fid] = {};
            if (el.type === 'checkbox') {
                if (!Array.isArray(dados[fid][key])) dados[fid][key] = [];
                if (el.checked) dados[fid][key].push(el.value);
            } else {
                dados[fid][key] = el.value;
            }
        });
        document.getElementById('anamneseRespostasJson').value = JSON.stringify(dados);
    }

    // Grupo de múltipla escolha obrigatório sem nenhuma opção marcada (só conta
    // em ficha obrigatória ou que a pessoa começou a responder). null = ok.
    function checkboxObrigatorioVazio() {
        var grupos = document.querySelectorAll('#anamneseFields [data-grupo-form-id]');
        for (var i = 0; i < grupos.length; i++) {
            var g = grupos[i];
            if (g.dataset.obrigatorio && fichaExigida(g.dataset.grupoFormId)
                    && !g.querySelector('input[type="checkbox"]:checked')) {
                return g;
            }
        }
        return null;
    }

    // ═══ SUBMIT (guarda contra duplo clique) ═══
    var bookingForm = document.getElementById('booking-form');
    var btnConfirmar = document.getElementById('btn-confirmar');
    var textoConfirmar = btnConfirmar ? btnConfirmar.innerHTML : '';
    if (bookingForm) {
        bookingForm.addEventListener('submit', function(e) {
            if (bookingForm.dataset.enviando === '1') { e.preventDefault(); return; }
            var faltando = checkboxObrigatorioVazio();
            if (faltando) {
                e.preventDefault();
                // Erro junto da ficha (não no status do celular, lá no topo) + foco
                setAnamneseMsg('Responda: ' + (faltando.dataset.label || 'questionário') + '.');
                if (faltando.scrollIntoView) faltando.scrollIntoView({block: 'center'});
                var primeiro = faltando.querySelector('input');
                if (primeiro) primeiro.focus();
                return;
            }
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

    // ═══ PERSISTÊNCIA DOS CAMPOS DO FORM (W4) ═══
    ['form-nome', 'form-nascimento', 'form-email',
     'form-consent-email', 'form-consent-wa-d1', 'form-consent-nps'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) {
            el.addEventListener('input',  salvarEstado);
            el.addEventListener('change', salvarEstado);
        }
    });

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
        a.style.cssText = 'font-weight:700;text-decoration:underline;margin-left:4px;color:#1a7f37;';
        return a;
    }

    function escreverMsg(elId, texto, ok, comWhatsApp) {
        var el = document.getElementById(elId);
        if (!el) return;
        el.textContent = texto;
        el.style.color = ok ? '#1a7f37' : '#b42318';
        if (comWhatsApp) {
            var a = linkWhatsApp();
            if (a) { el.appendChild(document.createTextNode(' ')); el.appendChild(a); }
        }
    }

    // Mensagens do envio (campo celular) e da verificação (campo código)
    function setStatusMsg(texto, ok, comWhatsApp) { escreverMsg('otp-status', texto, ok, comWhatsApp); }
    function setOtpMsg(texto, ok, comWhatsApp) {
        var field = document.getElementById('otp-field');
        if (field && field.style.display === 'none') { setStatusMsg(texto, ok, comWhatsApp); return; }
        escreverMsg('otp-msg', texto, ok, comWhatsApp);
    }

    function lerJson(r) {
        return r.text().then(function(t) {
            var data;
            try { data = JSON.parse(t); }
            catch (e) { data = {ok: false, erro: (r.status === 403 || r.status === 429) ? 'limite' : 'rede'}; }
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
        dados.style.display = '';
        var nome = document.getElementById('form-nome');
        var nasc = document.getElementById('form-nascimento');
        var email = document.getElementById('form-email');
        if (prefill) {
            nome.value = prefill.nome || '';
            nasc.value = prefill.data_nascimento || '';
            if (email && prefill.email && !email.value) email.value = prefill.email;
            aplicarConsents(prefill.consents);
            salvarEstado();
        }
        nome.required = true;
        nasc.required = true;
        if (clienteExistente) {
            badge.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:6px;" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
            badge.appendChild(document.createTextNode('Bem-vindo(a) de volta — seus dados foram carregados.'));
            badge.setAttribute('data-state', 'existing');
        } else {
            badge.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:6px;" aria-hidden="true"><path d="M12 3l1.9 5.5L19.5 10l-5.6 1.5L12 17l-1.9-5.5L4.5 10l5.6-1.5z"/></svg>';
            badge.appendChild(document.createTextNode('Novo por aqui — preencha seus dados abaixo.'));
            badge.setAttribute('data-state', 'new');
        }
        badge.style.display = '';
        if (btnConfirmar) {
            btnConfirmar.disabled = false;
            btnConfirmar.title = '';
        }
    }

    function marcarVerificado() {
        otpVerificado = true;
        var tel = document.getElementById('form-telefone');
        tel.readOnly = true;  // o agendamento vale só p/ o celular verificado
        tel.setAttribute('aria-readonly', 'true');
        var bEnv = document.getElementById('btn-enviar-otp');
        var bVer = document.getElementById('btn-verificar-otp');
        if (bEnv) bEnv.style.display = 'none';
        if (bVer) bVer.style.display = 'none';
        var otpField = document.getElementById('otp-field');
        if (otpField) otpField.style.display = 'none';
        var cap = document.getElementById('captcha-field');
        if (cap) cap.style.display = 'none';
        setStatusMsg('Celular verificado.', true);
    }

    // SMS só chega em celular: DDD + 9 + 8 dígitos (mesma regra do servidor)
    var CELULAR_RE = /^[1-9]{2}9\d{8}$/;
    var MSG_CELULAR = 'Informe um celular com DDD (9 dígitos, começando com 9).';

    var btnEnviar = document.getElementById('btn-enviar-otp');
    if (btnEnviar) {
        btnEnviar.addEventListener('click', function() {
            var digitos = soDigitos(document.getElementById('form-telefone').value);
            if (!CELULAR_RE.test(digitos)) {  // fixo não recebe SMS
                setStatusMsg(MSG_CELULAR, false);
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
                    var erro = res.data.erro;
                    if (res.data.ok) {
                        document.getElementById('otp-field').style.display = '';
                        setStatusMsg('Código enviado por SMS para seu celular. Válido por 10 minutos.', true);
                        var txt = btnEnviar.querySelector('.otp-btn-text');
                        if (txt) txt.textContent = 'Reenviar';
                        var campoCodigo = document.getElementById('otp-codigo');
                        if (campoCodigo) campoCodigo.focus();
                    } else if (erro === 'aguarde' || erro === 'limite') {
                        setStatusMsg('Aguarde um minuto antes de pedir outro código.', false);
                    } else if (erro === 'limite_sms') {
                        // Nenhum código novo foi gerado: o último recebido ainda vale
                        document.getElementById('otp-field').style.display = '';
                        setStatusMsg('Você atingiu o limite de códigos nesta hora. Use o último código recebido.', false, true);
                    } else if (erro === 'captcha') {
                        setStatusMsg('Confirme a verificação de segurança e tente novamente.', false);
                    } else if (erro === 'telefone_invalido') {
                        setStatusMsg(MSG_CELULAR, false);
                    } else if (erro === 'sms_falha') {
                        setStatusMsg('Não conseguimos enviar o código agora.', false, true);
                    } else {
                        setStatusMsg('Não foi possível enviar o código. Tente novamente.', false, true);
                    }
                })
                .catch(function() {
                    btnEnviar.disabled = false;
                    setStatusMsg('Erro de conexão. Tente novamente.', false);
                });
        });
    }

    var btnVerif = document.getElementById('btn-verificar-otp');
    if (btnVerif) {
        btnVerif.addEventListener('click', function() {
            var digitos = soDigitos(document.getElementById('form-telefone').value);
            var codigo = (document.getElementById('otp-codigo').value || '').trim();
            if (!/^\d{6}$/.test(codigo)) {
                setOtpMsg('O código deve ter 6 dígitos.', false);
                return;
            }
            btnVerif.disabled = true;
            var fd = new FormData();
            fd.append('telefone', digitos);
            fd.append('codigo', codigo);
            fd.append('csrfmiddlewaretoken', OTP_CSRF);
            fetch(cfg.urlOtpVerificar, {method:'POST', body: fd, headers:{'X-CSRFToken': OTP_CSRF}})
                .then(lerJson)
                .then(function(res) {
                    btnVerif.disabled = false;
                    var erro = res.data.erro || '';
                    if (res.data.ok) {
                        marcarVerificado();
                        if (res.data.aviso === 'bloqueado_online') {
                            setStatusMsg('Seu cadastro está com agendamento online suspenso. Fale conosco para marcar seu horário.', false, true);
                            return;
                        }
                        // checkboxes agora refletem o cadastro (ou cliente novo)
                        consentsSincronizados(true);
                        mostrarDados(res.data.prefill, !!res.data.prefill);
                    } else if (erro.indexOf('incorreto') === 0) {
                        var restante = erro.split(':')[1] || '';
                        setOtpMsg('Código incorreto. Tentativas restantes: ' + restante, false);
                    } else if (erro === 'bloqueado') {
                        setOtpMsg('Muitas tentativas. Solicite um novo código.', false);
                    } else if (erro === 'limite') {
                        setOtpMsg('Muitas tentativas. Aguarde um minuto.', false);
                    } else {
                        setOtpMsg('Código expirado. Solicite um novo.', false);
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
            var badge = document.getElementById('cliente-badge');
            if (badge) badge.style.display = 'none';
        }
    }

    // Pre-select
    if (!rehidratado && preselect) {
        var card = document.querySelector('.proc-card[data-proc-id="' + preselect + '"]');
        if (card) card.click();
    }

})();
