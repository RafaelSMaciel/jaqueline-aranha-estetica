# ShivaZen

Sistema de agendamento online para clinica de estetica, desenvolvido com Django 5.2 e PostgreSQL.

## Visao Geral

O ShivaZen e uma plataforma completa de gestao de agendamentos para clinicas de estetica. O sistema permite que clientes agendem procedimentos diretamente pelo site, enquanto o painel administrativo oferece controle total sobre a operacao da clinica.

### Funcionalidades Principais

**Area do Cliente (Site Publico)**
- Agendamento online com calendario de disponibilidade em tempo real
- Verificacao de identidade via OTP (codigo por WhatsApp)
- Consulta e gerenciamento de agendamentos pessoais
- Confirmacao, reagendamento e cancelamento por link
- Anamnese digital antecipada (preenchimento pre-consulta)
- Pagina de promocoes vigentes

**Painel Administrativo**
- Dashboard com KPIs: atendimentos, taxa de ocupacao, faturamento
- Gestao completa de agenda, profissionais e procedimentos
- Sistema de pacotes com controle de sessoes e validade
- Prontuario digital por cliente com historico por atendimento
- Gestao de promocoes com desconto percentual ou preco fixo
- Bloqueio de agenda (ferias, folgas, reunioes)
- Exportacao de relatorios em Excel
- Log de auditoria completo

**Notificacoes Automaticas (WhatsApp Business API)**
- Lembrete D-1 e 2h antes do atendimento
- Confirmacao de presenca por link com token unico
- Pesquisa de satisfacao NPS 24h apos atendimento
- Alerta imediato ao admin para notas de detratores (NPS 1-2)
- Notificacao de fila de espera com janela de exclusividade
- Aviso de pacote expirando (7 dias e 1 dia antes)
- Fallback automatico para SMS/Email em caso de falha

**Regras de Negocio**
- Sistema 3-strike: bloqueio de agendamento online apos 3 faltas consecutivas
- Prevencao de conflitos de horario com validacao em tempo real
- Debito automatico de sessoes de pacote ao marcar atendimento como realizado
- Fila de espera com priorizacao FIFO e janela de exclusividade de 30 minutos
- Rate limiting por IP contra abuso
- Bloqueio de agendamentos fantasma para clientes sem historico
- Consentimento LGPD obrigatorio no fluxo de agendamento
- Limpeza automatica de status para atendimentos nao atualizados

## Stack Tecnica

| Camada | Tecnologia |
|---|---|
| Backend | Django 5.2, Python 3.12+ |
| Banco de Dados | PostgreSQL 14+ com pg_trgm |
| Tarefas Async | Celery 5.4 + Redis |
| Servidor | Gunicorn + WhiteNoise |
| Frontend | Bootstrap 5, Vanilla JS |
| Notificacoes | WhatsApp Business API |
| Monitoramento | Sentry |
| Deploy | Railway (Nixpacks) |

## Estrutura do Projeto

```
shivazen-app/
├── app_shivazen/
│   ├── models.py              # Modelos do banco de dados
│   ├── signals.py             # Signals (fila de espera, pacotes)
│   ├── tasks.py               # Jobs Celery (lembretes, NPS)
│   ├── decorators.py          # Controle de acesso
│   ├── urls.py                # Rotas da aplicacao
│   ├── views/
│   │   ├── admin.py           # Acoes administrativas
│   │   ├── ajax.py            # Endpoints AJAX (slots, calendario)
│   │   ├── auth.py            # Autenticacao
│   │   ├── booking.py         # Agendamento publico
│   │   ├── dashboard.py       # Painel overview
│   │   ├── notificacoes.py    # Gestao de notificacoes
│   │   ├── public.py          # Paginas publicas
│   │   ├── services.py        # Servicos e procedimentos
│   │   └── whatsapp.py        # Webhook WhatsApp
│   ├── utils/
│   │   ├── audit.py           # Log de auditoria
│   │   └── whatsapp.py        # Integracao WhatsApp API
│   ├── templates/             # Templates Django
│   └── static/                # CSS, JS, imagens
├── shivazen/
│   ├── settings.py            # Configuracoes Django
│   ├── celery.py              # Configuracao Celery
│   ├── urls.py                # Rotas raiz
│   └── wsgi.py                # Entrypoint WSGI
├── requirements.txt           # Dependencias Python
├── Procfile                   # Processo web (Gunicorn)
└── railway.json               # Configuracao de deploy
```

## Seguranca

- Autenticacao customizada com AbstractBaseUser (email como identificador)
- Senhas com hash PBKDF2 (Django password hasher)
- Protecao CSRF em todos os formularios
- Protecao contra SQL Injection via ORM
- Protecao XSS via template escaping
- Rate limiting por IP em endpoints publicos
- Verificacao OTP com codigo de 6 digitos e expiracao de 10 minutos
- Tokens de uso unico para confirmacao de presenca
- Consentimento LGPD registrado com IP e timestamp

## Licenca

Este projeto esta sob a licenca [MIT](LICENSE).

---

Desenvolvido por Rafael Maciel
