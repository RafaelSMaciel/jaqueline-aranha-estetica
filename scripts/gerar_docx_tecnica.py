"""Gera documentacao tecnica em .docx a partir do SISTEMA.txt + complementos."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Cm


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'Shiva_Zen_Documentacao_Tecnica.docx'


def add_page_number(paragraph):
    run = paragraph.add_run()
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    instrText = OxmlElement('w:instrText')
    instrText.set(qn('xml:space'), 'preserve')
    instrText.text = 'PAGE'
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'end')
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)


def build():
    doc = Document()

    # Margens
    for section in doc.sections:
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    # Estilo base
    normal = doc.styles['Normal']
    normal.font.name = 'Calibri'
    normal.font.size = Pt(11)

    # Footer com numero de pagina
    footer = doc.sections[0].footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.text = 'Shiva Zen — Documentacao Tecnica — pagina '
    add_page_number(p)

    # ── Capa ──
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run('SHIVA ZEN')
    run.bold = True
    run.font.size = Pt(32)
    run.font.color.rgb = RGBColor(0x2C, 0x3E, 0x50)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = subtitle.add_run('Sistema de Gestao de Clinica de Estetica')
    r.font.size = Pt(16)
    r.font.color.rgb = RGBColor(0x7F, 0x8C, 0x8D)

    doc.add_paragraph()
    doc.add_paragraph()

    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = info.add_run('Documentacao Tecnica de Arquitetura e Infraestrutura')
    r.font.size = Pt(13)
    r.italic = True

    doc.add_paragraph()
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run('Versao 1.0 | Abril 2026\n').font.size = Pt(11)
    meta.add_run('Django 5.2.1 + Python 3.14 + PostgreSQL + Railway').font.size = Pt(11)

    doc.add_page_break()

    # ── Indice ──
    h = doc.add_heading('Indice', level=1)
    itens = [
        '1. Visao Geral',
        '2. Arquitetura da Aplicacao',
        '3. Modelo de Dados',
        '4. Autenticacao e Autorizacao',
        '5. Fluxo de Agendamento Publico',
        '6. Comunicacao Multi-Canal',
        '7. Seguranca',
        '8. LGPD e Compliance',
        '9. Integracoes Externas',
        '10. Observabilidade',
        '11. Infraestrutura e Deploy',
        '12. API REST',
        '13. Testes',
        '14. Variaveis de Ambiente',
        '15. Limites de Escala',
        '16. E-mail Sincrono (decisao corrente)',
        '17. Roadmap Infraestrutura',
    ]
    for i in itens:
        doc.add_paragraph(i, style='List Number')
    doc.add_page_break()

    # ── 1. Visao Geral ──
    doc.add_heading('1. Visao Geral', level=1)
    doc.add_paragraph(
        'O Shiva Zen e uma plataforma web monolitica (Django 5.2.1 + Python 3.14) '
        'para gerenciamento de uma clinica de estetica de pequeno porte '
        '(aproximadamente 40 clientes/mes). A plataforma cobre o ciclo completo: '
        'agendamento publico sem login, portal do cliente, portal do profissional, '
        'painel administrativo, prontuario eletronico, pacotes de sessoes, '
        'pesquisa NPS pos-atendimento, campanhas de e-mail marketing com consent '
        'LGPD granular e auditoria de operacoes sensiveis.'
    )
    doc.add_paragraph(
        'A arquitetura e Django MVT classico, sem SPA. Templates renderizados no '
        'servidor com progressive enhancement via fetch. Um service worker PWA '
        'e habilitado apenas no painel administrativo. Uma API REST v1 '
        '(DRF + drf-spectacular / OpenAPI) esta disponivel em /api/ para futuras '
        'integracoes, mas o fluxo principal opera com HTTP tradicional e forms.'
    )
    doc.add_paragraph(
        'Deploy atual: Railway free tier (web + Postgres). Celery e Redis foram '
        'removidos em favor de envio de e-mail sincrono e cron externo HTTP '
        '(cron-job.org) para jobs agendados, reduzindo custo para zero.'
    )

    # ── 2. Arquitetura ──
    doc.add_heading('2. Arquitetura da Aplicacao', level=1)
    doc.add_paragraph(
        'O codigo esta organizado em app Django unica (`app_shivazen`) com '
        'separacao por responsabilidade:'
    )
    for linha in [
        'models/: 39 modelos distribuidos em 11 arquivos por dominio '
        '(acesso, agendamentos, clientes, pacotes, procedimentos, '
        'profissionais, prontuario, sistema, nps, termos, mixins).',
        'views/: 15+ modulos separados por contexto (publico, booking, dashboard, '
        'admin, prontuario, profissional, whatsapp, lgpd, cron, health).',
        'services/: camada de logica de negocio (agendamento, lgpd, otp, '
        'notificacao, auditoria, pacote).',
        'utils/: helpers transversais (email, whatsapp, sms, captcha, security).',
        'forms/: Django Forms com validacao por recurso.',
        'api/: DRF serializers, views e urls em /api/.',
        'tasks.py: funcoes decoradas @shared_task (rodadas sincronas via endpoint '
        'cron).',
        'middleware.py: CSP com nonce + SecurityHeadersMiddleware.',
        'signals.py: hooks do Django (auditoria, derivacoes de status).',
        'validators.py: validadores reutilizaveis (CPF, telefone BR, etc).',
    ]:
        doc.add_paragraph(linha, style='List Bullet')

    # ── 3. Modelo de Dados ──
    doc.add_heading('3. Modelo de Dados', level=1)
    doc.add_paragraph(
        'O banco e PostgreSQL em producao (Railway managed). 39 modelos agrupados '
        'em dominios logicos. Principais:'
    )
    table = doc.add_table(rows=1, cols=3)
    table.style = 'Light Grid Accent 1'
    hdr = table.rows[0].cells
    hdr[0].text = 'Dominio'
    hdr[1].text = 'Modelos'
    hdr[2].text = 'Responsabilidade'
    rows = [
        ('Acesso', 'Usuario, Perfil, Funcionalidade, PerfilFuncionalidade, OTPCode',
         'Auth customizada + ACL por perfil + OTP para clientes.'),
        ('Clientes', 'Cliente, PreferenciaComunicacao',
         'Dados pessoais + consent granular LGPD por canal.'),
        ('Agendamentos', 'Atendimento, Bloqueio, ListaEspera, Feriado',
         'Core: reservas, locks por horario, bloqueios e feriados BR.'),
        ('Procedimentos', 'Procedimento, CategoriaProcedimento, Profissional'
         'Procedimento', 'Catalogo de servicos e relacao com profissionais.'),
        ('Pacotes', 'Pacote, PacoteCliente, SessaoPacote',
         'Venda de pacotes de sessoes e consumo por sessao.'),
        ('Profissionais', 'Profissional, JornadaTrabalho, AprovacaoProfissional',
         'Cadastro, disponibilidade e aprovacao pelo admin.'),
        ('Prontuario', 'Prontuario, AnotacaoSessao, Consentimento',
         'Registro clinico e termos assinados.'),
        ('NPS', 'RespostaNPS, TokenNPS',
         'Pesquisa de satisfacao pos-atendimento com token 7 dias.'),
        ('Sistema', 'Notificacao, LogAuditoria, TermoUso, Parametro',
         'Auditoria, notificacoes enviadas, termos versionados.'),
    ]
    for dom, mod, resp in rows:
        row = table.add_row().cells
        row[0].text = dom
        row[1].text = mod
        row[2].text = resp

    # ── 4. Auth ──
    doc.add_heading('4. Autenticacao e Autorizacao', level=1)
    doc.add_paragraph('Tres perfis distintos:')
    for p in [
        'Cliente anonimo: acessa agendamento publico sem login. Autenticacao via '
        'OTP por e-mail (codigo de 6 digitos, validade 10min, max 5 tentativas).',
        'Profissional: login com senha hash. Acessa apenas sua agenda, marca '
        'atendimentos como realizados, adiciona anotacoes.',
        'Admin: login com senha hash + django-axes (5 tentativas, lockout 1h). '
        'Acesso total ao painel.',
    ]:
        doc.add_paragraph(p, style='List Bullet')
    doc.add_paragraph(
        'django-axes esta integrado com AxesStandaloneBackend e captura IP + '
        'username. Lockout por combinacao ip_address+username.'
    )

    # ── 5. Fluxo Agendamento ──
    doc.add_heading('5. Fluxo de Agendamento Publico', level=1)
    doc.add_paragraph('Fluxo de 6 etapas, cada uma renderizada na mesma pagina via JS:')
    for i, etapa in enumerate([
        'Selecao de procedimento (filtro por categoria).',
        'Selecao de profissional disponivel (opcional, default: qualquer).',
        'Selecao de data + horario (API verifica slots + bloqueios + feriados).',
        'Dados do cliente (nome, e-mail, telefone, data nascimento) + consents LGPD.',
        'OTP: codigo enviado por e-mail, validado antes de confirmar.',
        'Confirmacao: atomic transaction com SELECT FOR UPDATE no slot. '
        'E-mail de confirmacao enviado sincrono (2-3s, com spinner).',
    ], start=1):
        doc.add_paragraph(f'{i}. {etapa}')
    doc.add_paragraph(
        'Turnstile (Cloudflare CAPTCHA) ativado no submit final. Rate limiting '
        'por IP em solicitar_otp, verificar_otp e confirmar_agendamento.'
    )

    # ── 6. Comunicacao ──
    doc.add_heading('6. Comunicacao Multi-Canal', level=1)
    doc.add_paragraph(
        'Canais suportados, com envio condicionado a consent:'
    )
    canais = doc.add_table(rows=1, cols=3)
    canais.style = 'Light Grid Accent 1'
    h = canais.rows[0].cells
    h[0].text = 'Canal'
    h[1].text = 'Uso'
    h[2].text = 'Provedor'
    for canal, uso, prov in [
        ('E-mail', 'OTP, confirmacao, lembrete, NPS, aniversario, promocoes, LGPD',
         'SMTP (Gmail app password ou SendGrid)'),
        ('WhatsApp', 'Templates Meta Business (confirmacao D-1, NPS)',
         'Meta WhatsApp Business API via webhook'),
        ('SMS', 'OTP alternativo quando e-mail nao disponivel',
         'Zenvia (autenticacao API + webhook HMAC IP-allowlist)'),
    ]:
        r = canais.add_row().cells
        r[0].text = canal
        r[1].text = uso
        r[2].text = prov
    doc.add_paragraph(
        'O cliente possui campos de consent granulares: aceita_cadastro, '
        'consent_email_marketing, consent_email_nps, consent_whatsapp_confirmacao, '
        'consent_whatsapp_nps, consent_sms_otp. Capturados no agendamento publico.'
    )

    # ── 7. Seguranca ──
    doc.add_heading('7. Seguranca', level=1)
    for item in [
        'CSP com nonce por request (middleware proprio). Bloqueia XSS.',
        'SecurityHeadersMiddleware: HSTS, X-Frame-Options DENY, '
        'Referrer-Policy, X-Content-Type-Options, Permissions-Policy.',
        'CSRF: tokens em todos os forms. SAMESITE=Lax.',
        'HTTPS forcado em producao com SECURE_SSL_REDIRECT + HSTS 1 ano + preload.',
        'Healthchecks excluidos do redirect para probe do Railway funcionar.',
        'django-axes: brute-force login lockout.',
        'Rate limiting: OTP (IP + cliente), agendamento (IP), webhooks (token + '
        'HMAC + IP-allowlist).',
        'Senhas: hash padrao Django (pbkdf2_sha256).',
        'Zenvia webhook: IP-allowlist + HMAC do payload.',
        'Cron endpoint: header X-Cron-Token validado com hmac.compare_digest.',
        'PII masking em logs (LogAuditoria grava hash de e-mail/telefone).',
        'Secrets via env vars, nunca em commit. Baseline de secrets removida '
        'junto com dev tooling.',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    # ── 8. LGPD ──
    doc.add_heading('8. LGPD e Compliance', level=1)
    for item in [
        'Consent granular por canal no cliente, capturado no agendamento.',
        'DSAR: /lgpd/meus-dados/ expoe todos os dados do cliente em JSON + '
        'HTML, autenticado via OTP.',
        'Opt-out one-click: List-Unsubscribe header RFC 8058 em todos os e-mails '
        'marketing.',
        'Retencao: job lgpd_purgar_inativos apaga clientes sem atendimento ha '
        'mais de 5 anos (semanalmente, domingo 03:00).',
        'Auditoria: LogAuditoria grava toda alteracao sensivel (acesso prontuario, '
        'exportacao relatorio, alteracao consent).',
        'Termos: TermoUso versionado, assinatura digital do cliente armazenada.',
        'DPIA documentado em docs/DPIA.md.',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    # ── 9. Integracoes ──
    doc.add_heading('9. Integracoes Externas', level=1)
    for item in [
        'SMTP (Gmail ou SendGrid): envio transacional.',
        'Meta WhatsApp Business API: webhook + templates.',
        'Zenvia: SMS OTP com webhook de delivery.',
        'Cloudflare Turnstile: CAPTCHA invisivel no agendamento.',
        'Sentry (opcional): tracing e error reporting quando SENTRY_DSN '
        'setado.',
        'cron-job.org (externo, gratuito): substitui Celery Beat. Chama '
        '/cron/run/<job>/ com X-Cron-Token.',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    # ── 10. Observabilidade ──
    doc.add_heading('10. Observabilidade', level=1)
    for item in [
        '/healthz/: liveness, retorna 200 se processo vivo.',
        '/health/: readiness, checa DB + cache. Retorna 503 se degradado. '
        '?celery=1 forca check adicional de broker.',
        'Logging estruturado JSON em producao (python-json-logger).',
        'Nivel: WARNING root, INFO django. app_shivazen em INFO.',
        'Sentry: opt-in via SENTRY_DSN. SendDefaultPII desativado.',
        'LogAuditoria (DB): trilha operacoes sensiveis, imutavel apos escrita.',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    # ── 11. Infra ──
    doc.add_heading('11. Infraestrutura e Deploy', level=1)
    doc.add_paragraph('Configuracao corrente no Railway free tier:')
    infra = doc.add_table(rows=1, cols=3)
    infra.style = 'Light Grid Accent 1'
    h = infra.rows[0].cells
    h[0].text = 'Servico'
    h[1].text = 'Configuracao'
    h[2].text = 'Custo'
    for srv, cfg, custo in [
        ('web', 'Django + gunicorn (1 worker / 4 threads). Nixpacks build. '
         'Sleep apos 5min sem trafego.', 'Free'),
        ('Postgres', 'Managed Railway. 1GB volume. DATABASE_URL injetada.', 'Free'),
        ('Cron externo', 'cron-job.org chama endpoints HTTP a cada horario', 'Free'),
    ]:
        r = infra.add_row().cells
        r[0].text = srv
        r[1].text = cfg
        r[2].text = custo
    doc.add_paragraph('Ambientes:')
    for item in [
        'production: deploy automatico do branch main. Dominio '
        'web-production-465af.up.railway.app.',
        'dev: deploy automatico do branch dev via deployment trigger. '
        'Ambiente de testes.',
    ]:
        doc.add_paragraph(item, style='List Bullet')
    doc.add_paragraph('Configuracao de build:')
    doc.add_paragraph(
        'railway.json define NIXPACKS build + startCommand que roda migrate, '
        'collectstatic e gunicorn. Healthcheck em /healthz/.', style='List Bullet')

    # ── 12. API ──
    doc.add_heading('12. API REST', level=1)
    doc.add_paragraph(
        'API REST v1 disponivel em /api/ com drf-spectacular (OpenAPI 3.0). '
        'Autenticacao por Session + DRF Throttling (anon: 60/hour, user: '
        '1000/hour). Paginacao PageNumber com 20 items.'
    )
    doc.add_paragraph(
        'Schema auto-gerado em /api/schema/ (JSON/YAML). Swagger UI em '
        '/api/docs/. Endpoints principais expoem agendamentos, horarios '
        'disponiveis e procedimentos para leitura.'
    )

    # ── 13. Testes ──
    doc.add_heading('13. Testes', level=1)
    doc.add_paragraph(
        '135 testes unitarios + integracao em app_shivazen/tests/. SQLite em '
        'memoria via conftest (DATABASES override quando sys.argv contem test '
        'ou PYTEST_CURRENT_TEST setado). Cobre:'
    )
    for item in [
        'Booking: fluxo completo com OTP.',
        'NPS: envio de token, resposta, idempotencia.',
        'Pacotes: compra, consumo, expiracao.',
        'OTP: geracao, expiracao, rate limit.',
        'WhatsApp webhook: HMAC, templates.',
        'Security utils: CSRF, IP extraction, LGPD services.',
        'Validators: CPF, telefone BR.',
        'Context processors + decorators.',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    # ── 14. Env vars ──
    doc.add_heading('14. Variaveis de Ambiente', level=1)
    envs = doc.add_table(rows=1, cols=3)
    envs.style = 'Light Grid Accent 1'
    h = envs.rows[0].cells
    h[0].text = 'Variavel'
    h[1].text = 'Obrigatoria?'
    h[2].text = 'Descricao'
    for var, req, desc in [
        ('DJANGO_SECRET_KEY', 'Sim', 'Chave de assinatura. Min 50 chars aleatorios.'),
        ('DJANGO_ENV', 'Sim', 'prod ou dev (seleciona settings).'),
        ('DATABASE_URL', 'Sim', 'URL postgres:// injetada pelo Railway.'),
        ('ALLOWED_HOSTS', 'Sim', 'Dominios separados por virgula.'),
        ('CSRF_TRUSTED_ORIGINS', 'Sim', 'Origens com esquema https://.'),
        ('USE_HTTPS', 'Nao', 'Default True. False desativa SSL redirect.'),
        ('CRON_TOKEN', 'Sim', 'Token para endpoint /cron/run/.'),
        ('EMAIL_HOST', 'Sim', 'SMTP host.'),
        ('EMAIL_HOST_USER', 'Sim', 'SMTP user.'),
        ('EMAIL_HOST_PASSWORD', 'Sim', 'SMTP password/app-password.'),
        ('EMAIL_PORT', 'Nao', 'Default 587.'),
        ('DEFAULT_FROM_EMAIL', 'Sim', 'E-mail de remetente.'),
        ('TURNSTILE_SITE_KEY', 'Nao', 'CAPTCHA publico.'),
        ('TURNSTILE_SECRET_KEY', 'Nao', 'CAPTCHA validacao server.'),
        ('ZENVIA_API_TOKEN', 'Nao', 'SMS Zenvia.'),
        ('ZENVIA_WEBHOOK_SECRET', 'Nao', 'HMAC webhook.'),
        ('SENTRY_DSN', 'Nao', 'Observabilidade.'),
        ('REDIS_URL', 'Nao', 'Se presente, ativa cache/sessions em Redis e Celery.'),
    ]:
        r = envs.add_row().cells
        r[0].text = var
        r[1].text = req
        r[2].text = desc

    # ── 15. Limites ──
    doc.add_heading('15. Limites de Escala', level=1)
    for item in [
        'Volume alvo: ate 40 clientes/mes, ~50 agendamentos/mes, ~200 e-mails/mes.',
        'Servico web dorme apos 5min sem trafego (cold start ~10-30s primeiro '
        'request).',
        'SMTP sincrono limita request a ~3s.',
        'Sem fila de retry para e-mails: se SMTP falhar, cliente vê erro.',
        'Postgres Railway free: 1GB. Suficiente para ~10k clientes.',
        'Cache local em cada worker (LocMemCache) — nao compartilha entre '
        'workers multiplos. Irrelevante com 1 worker gunicorn.',
        'Jobs agendados executam via cron-job.org — depende de disponibilidade '
        'do servico externo (99.9% historico).',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    # ── 16. E-mail sync ──
    doc.add_heading('16. E-mail Sincrono (decisao corrente)', level=1)
    doc.add_paragraph(
        'O sistema envia e-mails dentro do request HTTP do Django. Latencia '
        'tipica 2-3 segundos. Mitigacao UX: spinner global em forms com atributo '
        'data-loading-msg, mostrando "Enviando confirmacao por e-mail..." durante '
        'o submit. Forms marcados:'
    )
    for item in [
        'agendamento_publico.html (confirmacao)',
        'reagendar.html (reagendamento)',
        'confirmar_presenca.html (confirmar/cancelar)',
        'contato.html (contato publico)',
        'lista_espera.html (lista de espera)',
    ]:
        doc.add_paragraph(item, style='List Bullet')
    doc.add_paragraph(
        'Criterio para migrar a envio assincrono (Celery + Redis + worker): '
        'volume >200 clientes/mes, SMTP com latencia >3s, ou campanhas em '
        'batch. Documento completo em docs/EMAIL_ASYNC_VS_SYNC.md.'
    )

    # ── 17. Roadmap ──
    doc.add_heading('17. Roadmap Infraestrutura', level=1)
    doc.add_paragraph('Proximos passos recomendados, em ordem de prioridade:')
    for item in [
        '1. Backup automatizado Postgres (dump diario + copia off-Railway — LGPD).',
        '2. Sentry: free tier cobre volume atual.',
        '3. Cloudflare na frente (free tier): WAF + DDoS + cache static.',
        '4. Storage S3/R2 para MEDIA_ROOT (fotos prontuario / profissionais).',
        '5. Uptime monitor externo (BetterStack ou UptimeRobot).',
        '6. Log aggregation externa (BetterStack Logs): Railway logs sao '
        'volateis; LGPD requer retencao de auditoria.',
        '7. Staging dev isolado com Postgres proprio (custo ~$5/mes adicional).',
        '8. Redis + Celery quando volume >200 clientes/mes (custo +$10-15/mes).',
        '9. Custom domain com HTTPS (Railway emite certificado).',
        '10. Split app_shivazen em apps menores quando crescer para >50 models.',
    ]:
        doc.add_paragraph(item, style='List Bullet')

    doc.add_paragraph()
    doc.add_paragraph(
        '— Fim do documento —', style='Intense Quote'
    ).alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.save(OUT)
    print(f'Gerado: {OUT}')


if __name__ == '__main__':
    build()
