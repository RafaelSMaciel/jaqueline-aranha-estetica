/* wizard.js — Booking wizard (CSP-safe, sem tags Django)
 * Configuração injetada pelo template em window.WIZARD_CFG:
 *   urlOtpSolicitar  — {% url 'aranha:solicitar_otp_agendamento' %}
 *   urlOtpVerificar  — {% url 'aranha:verificar_otp_agendamento' %}
 *   procPreselect    — {{ proc_preselect }} (string ou vazio)
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

    // ═══ STATE ═══
    var selectedProc = null;
    var selectedDate = null;
    var selectedSlot = null;
    var selectedProf = null;
    var currentMonth = new Date();
    var diasDisponiveis = [];
    var horariosData = [];

    var meses = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                 'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];

    var preselect = cfg.procPreselect || '';

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
                }
            }));
        } catch(e) {}
    }

    function rehidratar() {
        var el = document.getElementById('booking-error');
        if (!el) return;
        var raw = sessionStorage.getItem(SESSION_KEY);
        if (!raw) return;
        var st;
        try { st = JSON.parse(raw); } catch(e) { return; }
        if (!st || !st.proc) return;

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

        // Consome o estado salvo — não re-hidrata novamente numa visita futura limpa
        sessionStorage.removeItem(SESSION_KEY);

        // Popula hiddens + resumo + navega pro step 3 (reutiliza função existente)
        goToStep3();
    }

    // ═══ STEP NAVIGATION ═══
    window.goToStep = function(n) {
        // Hide all panels
        var panels = document.querySelectorAll('.step-panel');
        for (var i = 0; i < panels.length; i++) {
            panels[i].classList.remove('active');
        }
        // Show target panel
        document.getElementById('step-' + n).classList.add('active');

        // Update step indicators
        var items = document.querySelectorAll('.step-item');
        for (var i = 0; i < items.length; i++) {
            items[i].classList.remove('active', 'done');
            // Remove ::after pseudo-element by clearing class
            if (i + 1 < n) items[i].classList.add('done');
            if (i + 1 === n) items[i].classList.add('active');
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
                id: this.dataset.procId,
                nome: this.dataset.procNome,
                preco: parseFloat(this.dataset.procPreco),
                duracao: parseInt(this.dataset.procDuracao)
            };

            selectedDate = null;
            selectedSlot = null;
            selectedProf = null;

            // Limpa estado salvo quando o usuário inicia novo fluxo sem erro ativo
            if (!document.getElementById('booking-error')) {
                try { sessionStorage.removeItem(SESSION_KEY); } catch(e) {}
            }

            salvarEstado();
            goToStep(2);
            currentMonth = new Date();
            loadMonth();
        });
    }

    // ═══ STEP 2: CALENDAR ═══
    function loadMonth() {
        var year = currentMonth.getFullYear();
        var month = currentMonth.getMonth();
        document.getElementById('cal-month-label').textContent = meses[month] + ' ' + year;

        var mesStr = year + '-' + String(month + 1).padStart(2, '0');

        fetch('/ajax/dias-disponiveis/?mes=' + mesStr + '&procedimento_id=' + selectedProc.id)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                diasDisponiveis = data.dias_disponiveis || [];
                renderCalendar(year, month);
            })
            .catch(function() { renderCalendar(year, month); });
    }

    function renderCalendar(year, month) {
        var grid = document.getElementById('cal-days-grid');
        grid.innerHTML = '';

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
        currentMonth.setMonth(currentMonth.getMonth() - 1);
        loadMonth();
    });
    document.getElementById('cal-next').addEventListener('click', function() {
        currentMonth.setMonth(currentMonth.getMonth() + 1);
        loadMonth();
    });

    function selectDate(dateStr) {
        selectedDate = dateStr;
        selectedSlot = null;
        selectedProf = null;
        salvarEstado();
        document.getElementById('prof-section').style.display = 'none';

        var allDays = document.querySelectorAll('.cal-day');
        for (var i = 0; i < allDays.length; i++) allDays[i].classList.remove('selected');
        var sel = document.querySelector('.cal-day[data-date="' + dateStr + '"]');
        if (sel) sel.classList.add('selected');

        var container = document.getElementById('slots-container');
        container.innerHTML = '<div class="slots-loading"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" stroke-width="2" stroke-linecap="round" style="display:block;margin:0 auto 0.75rem;" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></path></svg>Carregando horários...</div>';

        fetch('/ajax/horarios-disponiveis/?data=' + dateStr + '&procedimento_id=' + selectedProc.id)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                horariosData = data.horarios || [];
                if (horariosData.length === 0) {
                    container.innerHTML = '<div class="slots-loading"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#CBB994" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:0 auto 0.75rem;" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>Nenhum horário disponível nesta data.</div>';
                    return;
                }

                var parts = dateStr.split('-');
                var dateDisplay = parts[2] + '/' + parts[1] + '/' + parts[0];

                var html = '<div class="slots-title"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Horários para ' + dateDisplay + '</div>';
                html += '<div class="slots-grid">';
                for (var i = 0; i < horariosData.length; i++) {
                    var slot = horariosData[i];
                    html += '<button type="button" class="slot-btn" data-idx="' + i + '">' + slot.horario + '</button>';
                }
                html += '</div>';
                container.innerHTML = html;

                var btns = container.querySelectorAll('.slot-btn');
                for (var j = 0; j < btns.length; j++) {
                    btns[j].addEventListener('click', function() { selectSlot(this); });
                }
            })
            .catch(function() {
                container.innerHTML = '<div class="slots-loading">Erro ao carregar horários.</div>';
            });
    }

    function selectSlot(btn) {
        var allBtns = document.querySelectorAll('.slot-btn');
        for (var i = 0; i < allBtns.length; i++) allBtns[i].classList.remove('selected');
        btn.classList.add('selected');

        var idx = parseInt(btn.getAttribute('data-idx'));
        var slot = horariosData[idx];
        selectedSlot = {
            horario: slot.horario,
            iso: slot.datetime_iso,
            profissionais: slot.profissionais
        };
        salvarEstado();

        var profSection = document.getElementById('prof-section');
        var profOptions = document.getElementById('prof-options');
        profSection.style.display = 'block';
        profSection.style.setProperty('display', 'block', 'important');
        profOptions.innerHTML = '';

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
            for (var p = 0; p < selectedSlot.profissionais.length; p++) {
                (function(prof) {
                    var pbtn = document.createElement('button');
                    pbtn.type = 'button';
                    pbtn.className = 'prof-btn';
                    pbtn.textContent = prof.nome;
                    pbtn.addEventListener('click', function() {
                        var all = document.querySelectorAll('.prof-btn');
                        for (var k = 0; k < all.length; k++) all[k].classList.remove('selected');
                        this.classList.add('selected');
                        selectedProf = prof;
                        salvarEstado();
                        setTimeout(function() { goToStep3(); }, 500);
                    });
                    profOptions.appendChild(pbtn);
                })(selectedSlot.profissionais[p]);
            }
        }
    }

    function goToStep3() {
        if (!selectedProc || !selectedSlot || !selectedProf) return;

        document.getElementById('sum-proc').textContent = selectedProc.nome;

        var d = new Date(selectedSlot.iso);
        var opts = {weekday:'long', day:'numeric', month:'long', hour:'2-digit', minute:'2-digit'};
        document.getElementById('sum-datetime').textContent = d.toLocaleDateString('pt-BR', opts);
        document.getElementById('sum-prof').textContent = selectedProf.nome;
        document.getElementById('sum-price').textContent = 'R$ ' + selectedProc.preco.toFixed(2).replace('.', ',');

        document.getElementById('form-procedimento').value = selectedProc.id;
        document.getElementById('form-profissional').value = selectedProf.id;
        document.getElementById('form-datetime').value = selectedSlot.iso;

        renderAnamnese();

        goToStep(3);
    }

    // ═══ ANAMNESE DINAMICA (JSON via json_script p/ XSS-safe) ═══
    var FORMULARIOS_ANAMNESE = JSON.parse(
      document.getElementById('formularios-anamnese-data').textContent
    );

    function formularioAplicaAoProc(form, proc) {
        if (form.escopo === 'GLOBAL') return true;
        if (form.escopo === 'CATEGORIA') return form.categoria === proc.categoria;
        if (form.escopo === 'PROCEDIMENTO') return form.procedimento_id === proc.id;
        return false;
    }

    function renderAnamnese() {
        var container = document.getElementById('anamneseContainer');
        var fields = document.getElementById('anamneseFields');
        if (!container || !fields || !selectedProc) return;
        fields.innerHTML = '';

        var aplicaveis = FORMULARIOS_ANAMNESE.filter(function(f){
            return formularioAplicaAoProc(f, selectedProc);
        });
        if (aplicaveis.length === 0) {
            container.style.display = 'none';
            return;
        }
        container.style.display = '';

        aplicaveis.forEach(function(form){
            var formWrap = document.createElement('div');
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

                var input;
                if (field.tipo === 'bool') {
                    input = document.createElement('select');
                    input.innerHTML = '<option value="">—</option><option value="sim">Sim</option><option value="nao">Nao</option>';
                } else if (field.tipo === 'select') {
                    input = document.createElement('select');
                    input.innerHTML = '<option value="">—</option>';
                    (field.opcoes || []).forEach(function(o){
                        var opt = document.createElement('option');
                        opt.value = o; opt.textContent = o;
                        input.appendChild(opt);
                    });
                } else if (field.tipo === 'longtext') {
                    input = document.createElement('textarea');
                    input.rows = 2;
                } else if (field.tipo === 'number') {
                    input = document.createElement('input'); input.type = 'number';
                } else if (field.tipo === 'date') {
                    input = document.createElement('input'); input.type = 'date';
                } else {
                    input = document.createElement('input'); input.type = 'text';
                }
                input.style.cssText = 'width:100%;padding:0.45rem;border:1px solid #ddd;border-radius:6px;font-size:0.85rem;';
                input.dataset.formId = form.id;
                input.dataset.fieldKey = field.key;
                input.dataset.obrigatorio = field.obrigatorio ? '1' : '';
                if (field.obrigatorio) input.required = true;
                fwrap.appendChild(input);
                formWrap.appendChild(fwrap);
            });
            fields.appendChild(formWrap);
        });
    }

    function coletarAnamnese() {
        var dados = {};
        document.querySelectorAll('#anamneseFields [data-form-id]').forEach(function(el){
            var fid = el.dataset.formId;
            var key = el.dataset.fieldKey;
            if (!dados[fid]) dados[fid] = {};
            dados[fid][key] = el.value;
        });
        document.getElementById('anamneseRespostasJson').value = JSON.stringify(dados);
    }

    var bookingForm = document.getElementById('booking-form');
    if (bookingForm) {
        bookingForm.addEventListener('submit', coletarAnamnese);
    }

    // ═══ PHONE MASK ═══
    var telInput = document.getElementById('form-telefone');
    if (telInput) {
        telInput.addEventListener('input', function(e) {
            var v = e.target.value.replace(/\D/g, '');
            if (v.length > 11) v = v.slice(0, 11);
            if (v.length > 6) v = '(' + v.slice(0,2) + ') ' + v.slice(2,7) + '-' + v.slice(7);
            else if (v.length > 2) v = '(' + v.slice(0,2) + ') ' + v.slice(2);
            else if (v.length > 0) v = '(' + v;
            e.target.value = v;
            salvarEstado();
        });
    }

    // ═══ PERSISTÊNCIA DOS CAMPOS DO FORM (W4) ═══
    ['form-nome', 'form-nascimento', 'form-email'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) {
            el.addEventListener('input',  salvarEstado);
            el.addEventListener('change', salvarEstado);
        }
    });

    // ═══ RE-HIDRATAR no load se houve erro do servidor (W4) ═══
    rehidratar();

    // Pre-select
    if (preselect) {
        var card = document.querySelector('.proc-card[data-proc-id="' + preselect + '"]');
        if (card) card.click();
    }

    // ═══ OTP FLOW ═══
    var OTP_CSRF = document.querySelector('#booking-form input[name="csrfmiddlewaretoken"]').value;
    var otpVerificado = false;

    function setOtpMsg(texto, ok) {
        var el = document.getElementById('otp-msg');
        if (!el) return;
        el.textContent = texto;
        el.style.color = ok ? '#1a7f37' : '#b42318';
    }

    function mostrarDados(prefill, clienteExistente) {
        var dados = document.getElementById('dados-fields');
        var captcha = document.getElementById('captcha-field');
        var checkWrap = document.getElementById('cadastro-check-wrap');
        var badge = document.getElementById('cliente-badge');
        dados.style.display = '';
        if (captcha) captcha.style.display = '';
        var nome = document.getElementById('form-nome');
        var nasc = document.getElementById('form-nascimento');
        var tel = document.getElementById('form-telefone');
        if (prefill) {
            nome.value = prefill.nome || '';
            nasc.value = prefill.data_nascimento || '';
            tel.value = prefill.telefone || '';
            tel.dispatchEvent(new Event('input'));
        }
        nome.required = true;
        nasc.required = true;
        tel.required = true;
        if (clienteExistente) {
            badge.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:6px;" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>Bem-vindo de volta — seus dados foram carregados.';
            badge.setAttribute('data-state', 'existing');
        } else {
            badge.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:6px;" aria-hidden="true"><path d="M12 3l1.9 5.5L19.5 10l-5.6 1.5L12 17l-1.9-5.5L4.5 10l5.6-1.5z"/></svg>Novo por aqui — preencha seus dados abaixo.';
            badge.setAttribute('data-state', 'new');
            checkWrap.style.display = '';
        }
        badge.style.display = '';
        document.getElementById('btn-confirmar').disabled = false;
        document.getElementById('btn-confirmar').title = '';
    }

    var btnEnviar = document.getElementById('btn-enviar-otp');
    if (btnEnviar) {
        btnEnviar.addEventListener('click', function() {
            var telefone = (document.getElementById('form-telefone').value || '').trim();
            var emailEl = document.getElementById('form-email');
            var email = emailEl ? (emailEl.value || '').trim() : '';
            var soDigitos = telefone.replace(/\D/g, '');
            if (soDigitos.length < 10) {
                setOtpMsg('Informe um celular valido (DDD + numero).', false);
                return;
            }
            btnEnviar.disabled = true;
            setOtpMsg('Enviando SMS...', true);
            var fd = new FormData();
            fd.append('telefone', telefone);
            // email opcional aqui — backend deriva pseudo-email se vazio
            if (email) fd.append('email', email);
            fd.append('csrfmiddlewaretoken', OTP_CSRF);
            var cap = document.querySelector('#captcha-field .cf-turnstile input[name="cf-turnstile-response"]');
            if (cap) fd.append('cf-turnstile-response', cap.value);
            fetch(cfg.urlOtpSolicitar, {method:'POST', body: fd, headers:{'X-CSRFToken': OTP_CSRF}})
                .then(function(r) { return r.json().then(function(d){ return {status:r.status, data:d}; }); })
                .then(function(res) {
                    btnEnviar.disabled = false;
                    if (res.data.ok) {
                        document.getElementById('otp-field').style.display = '';
                        setOtpMsg('Código enviado por SMS para seu telefone. Válido por 10min.', true);
                        btnEnviar.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg><span class="otp-btn-text">Reenviar</span>';
                    } else if (res.data.erro === 'aguarde') {
                        setOtpMsg('Aguarde alguns segundos antes de reenviar.', false);
                    } else {
                        setOtpMsg('Falha ao enviar. Tente novamente.', false);
                    }
                })
                .catch(function() {
                    btnEnviar.disabled = false;
                    setOtpMsg('Erro de conexao.', false);
                });
        });
    }

    var btnVerif = document.getElementById('btn-verificar-otp');
    if (btnVerif) {
        btnVerif.addEventListener('click', function() {
            var emailEl = document.getElementById('form-email');
            var email = emailEl ? (emailEl.value || '').trim() : '';
            var telefone = (document.getElementById('form-telefone').value || '').trim();
            var soDigitosV = telefone.replace(/\D/g, '');
            // Se nao informou email, deriva pseudo-email do telefone (mesmo padrao backend)
            if (!email) email = 'sms+' + soDigitosV + '@shivazen.local';
            var codigo = (document.getElementById('otp-codigo').value || '').trim();
            if (!/^\d{6}$/.test(codigo)) {
                setOtpMsg('Codigo deve ter 6 digitos.', false);
                return;
            }
            btnVerif.disabled = true;
            var fd = new FormData();
            fd.append('email', email);
            fd.append('telefone', telefone);
            fd.append('codigo', codigo);
            fd.append('csrfmiddlewaretoken', OTP_CSRF);
            fetch(cfg.urlOtpVerificar, {method:'POST', body: fd, headers:{'X-CSRFToken': OTP_CSRF}})
                .then(function(r) { return r.json().then(function(d){ return {status:r.status, data:d}; }); })
                .then(function(res) {
                    btnVerif.disabled = false;
                    if (res.data.ok) {
                        otpVerificado = true;
                        setOtpMsg('Telefone verificado!', true);
                        var existe = !!res.data.prefill;
                        mostrarDados(res.data.prefill, existe);
                        // esconde botoes OTP apos sucesso
                        btnEnviar.style.display = 'none';
                        btnVerif.style.display = 'none';
                        document.getElementById('otp-codigo').disabled = true;
                    } else if (res.data.erro && res.data.erro.indexOf('incorreto') === 0) {
                        var restante = res.data.erro.split(':')[1] || '';
                        setOtpMsg('Codigo incorreto. Tentativas restantes: ' + restante, false);
                    } else if (res.data.erro === 'bloqueado') {
                        setOtpMsg('Muitas tentativas. Solicite um novo codigo.', false);
                    } else {
                        setOtpMsg('Codigo expirado. Solicite um novo.', false);
                    }
                })
                .catch(function() {
                    btnVerif.disabled = false;
                    setOtpMsg('Erro de conexao.', false);
                });
        });
    }

})();
