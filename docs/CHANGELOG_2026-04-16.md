# Changelog - 2026-04-16 - Melhorias Gerais

## Testes
- Configurado SQLite como banco de testes local (settings.py detecta `test` em sys.argv)
- Corrigido helper `_criar_staff` em test_security.py (Perfil 'Administrador' em vez de property set)
- Compatibilidade SQLite em `CodigoVerificacao.consumir()` (skip select_for_update em SQLite)
- Adicionados testes unitarios: signals (7), decorators (6), context_processors (3)
- Adicionados testes de integracao: booking flow (3), package lifecycle (3), NPS flow (4)
- Suite total: 72 -> 98 testes, todos passando em ~4s

## Banco de Dados (Migration 0008)
- Indexes adicionados: Profissional.ativo, Procedimento.ativo/categoria, Preco lookup composto, Notificacao tipo+status/-criado_em, LogAuditoria tabela/-criado_em, PacoteCliente cliente+status, Promocao ativa+datas
- CHECK constraints: Preco.valor >= 0, Procedimento.duracao > 0, Procedimento.categoria enum, Pacote.preco >= 0, Pacote.validade > 0, ItemPacote.qtd > 0, BloqueioAgenda.fim > inicio, Promocao.data_fim >= data_inicio
- Queries N+1: auditadas em todos os view files - ja estavam otimizadas

## Seguranca
- Removido `|safe` em painel_overview.html - substituido por `json_script` (Django recommended)
- CSRF AJAX: adicionado `$.ajaxSetup()` com X-CSRFToken em main.js
- Rate limiting adicionado: nps_web (10/m), termo_assinatura (10/m), reagendar_agendamento (10/m)
- Auditoria completa: 0 raw SQL, 0 autoescape off, 26/26 forms com csrf_token

## Refatoracao
- `booking.py` (719 linhas) -> `booking.py` + `booking_api.py` (4 endpoints AJAX extraidos)
- `admin.py` views (416 linhas) -> `admin.py` + `admin_professional.py` + `admin_promotions.py`
- Imports organizados em todos os 17 view files (stdlib -> third-party -> local)
- Dead code removido: `whatsapp_webhook_verify` (exportado mas sem URL)
- Imports inline movidos para top-level em 7+ arquivos
- Django admin: `readonly_fields` adicionado em AvaliacaoNPSAdmin e PacoteClienteAdmin
- Celery tasks: retry policy (bind=True, max_retries=3, delay=60s) em todas as 8 tasks

## Frontend
- CSS: adicionado `.btn-primary` global (pill-shaped, 50px radius, accent color)
- CSS: adicionado `.card` hover generico (translateY -8px)
- CSS: adicionado `.whatsapp-float` styling (position fixed, green, circular)
- Templates: corrigido AOS delay em equipe.html e especialidades.html (100,200,300ms pattern)
- Templates: corrigido loop de estrelas em depoimentos.html (era 11 iteracoes, agora 5)

## Arquitetura
- Decisao: manter Bootstrap Icons (ja integrado) em vez de migrar para Font Awesome
- Views modules: 14 -> 17 arquivos (melhor separacao de responsabilidades)
- Migrations: 7 -> 8 (0008_add_indexes_constraints)
- Test files: 10 -> 16 arquivos
