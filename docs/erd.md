# ShivaZen — MER (Modelo Entidade-Relacionamento)

> Fonte da verdade: `app_shivazen/models.py` (Django) + `signals.py`.
> Documentacao narrativa complementar: `docs/mer_base_documentacao.txt`.
> Renderiza nativo no GitHub/GitLab/VS Code com suporte a Mermaid.

O modelo foi dividido em 3 paginas para caber em A4 landscape sem
perder legibilidade. A divisao segue os dominios funcionais do sistema:

1. **Pagina 1 — Acesso, Profissionais e Catalogo**
2. **Pagina 2 — Clientes, Agendamentos, Pacotes, Fila e Promocoes**
3. **Pagina 3 — Prontuario, Termos, Notificacoes, NPS e Infra**

---

## Pagina 1 — Acesso, Profissionais e Catalogo

Cobre controle de acesso (RBAC), profissionais e o catalogo de
procedimentos, precos e disponibilidade.

```mermaid
erDiagram
    FUNCIONALIDADE {
        int id PK
        string nome UK
        text descricao
    }
    PERFIL {
        int id PK
        string nome UK
        text descricao
    }
    PERFIL_FUNCIONALIDADE {
        int id PK
        int perfil_id FK
        int funcionalidade_id FK
    }
    USUARIO {
        int id PK
        int perfil_id FK "RESTRICT"
        int profissional_id FK "SET NULL, UNIQUE"
        string nome
        string email UK
        string senha_hash
        bool ativo
    }
    PROFISSIONAL {
        int id PK
        string nome
        text especialidade
        bool ativo
    }
    PROCEDIMENTO {
        int id PK
        string nome
        text descricao
        smallint duracao_minutos
        string categoria "FACIAL|CORPORAL|CAPILAR|OUTRO"
        bool ativo
    }
    PROFISSIONAL_PROCEDIMENTO {
        int id PK
        int profissional_id FK
        int procedimento_id FK
    }
    PRECO {
        int id PK
        int procedimento_id FK
        int profissional_id FK "NULL = generico"
        decimal valor
        text descricao
        date vigente_desde
    }
    DISPONIBILIDADE_PROFISSIONAL {
        int id PK
        int profissional_id FK
        smallint dia_semana "1..7"
        time hora_inicio
        time hora_fim
    }
    BLOQUEIO_AGENDA {
        int id PK
        int profissional_id FK "NULL = global"
        datetime data_hora_inicio
        datetime data_hora_fim
        text motivo
    }

    PERFIL ||--o{ PERFIL_FUNCIONALIDADE : "possui"
    FUNCIONALIDADE ||--o{ PERFIL_FUNCIONALIDADE : "pertence a"
    PERFIL ||--o{ USUARIO : "autoriza"
    PROFISSIONAL ||--o| USUARIO : "login opcional"
    PROFISSIONAL ||--o{ PROFISSIONAL_PROCEDIMENTO : "executa"
    PROCEDIMENTO ||--o{ PROFISSIONAL_PROCEDIMENTO : "executado por"
    PROCEDIMENTO ||--o{ PRECO : "precificado por"
    PROFISSIONAL ||--o{ PRECO : "especifico de"
    PROFISSIONAL ||--o{ DISPONIBILIDADE_PROFISSIONAL : "tem janela"
    PROFISSIONAL ||--o{ BLOQUEIO_AGENDA : "bloqueio pessoal"
```

**Notas de leitura:**

- `USUARIO.profissional_id` eh OneToOne com SET NULL: um profissional
  pode ter (ou nao) uma conta de login, e apagar o profissional nao
  apaga a conta.
- `PRECO.profissional_id` NULL indica preco generico do procedimento
  (fallback); quando preenchido, eh preco especifico daquele
  profissional e tem precedencia na consulta.
- `BLOQUEIO_AGENDA.profissional_id` NULL representa bloqueio global
  da clinica (feriado, manutencao).
- `DISPONIBILIDADE_PROFISSIONAL` deliberadamente NAO tem UNIQUE
  composto, pois precisa suportar multiplos turnos no mesmo dia.

---

## Pagina 2 — Clientes, Agendamentos, Pacotes, Fila e Promocoes

Cobre o nucleo operacional do sistema: o ciclo de vida do atendimento
e seus satelites (cliente, promocoes, pacotes e fila de espera).

```mermaid
erDiagram
    CLIENTE {
        int id PK
        string nome_completo
        date data_nascimento
        string cpf UK
        string rg
        string email
        string telefone "IDX"
        text endereco
        bool ativo
        smallint faltas_consecutivas
        bool bloqueado_online
        datetime criado_em
    }
    PROMOCAO {
        int id PK
        int procedimento_id FK "NULL = geral"
        string nome
        decimal desconto_percentual
        decimal preco_promocional
        date data_inicio
        date data_fim
        bool ativa
    }
    ATENDIMENTO {
        int id PK
        int cliente_id FK "RESTRICT"
        int profissional_id FK "RESTRICT"
        int procedimento_id FK "RESTRICT"
        int promocao_id FK "SET NULL"
        int reagendado_de_id FK "self, SET NULL"
        datetime data_hora_inicio "IDX"
        datetime data_hora_fim
        decimal valor_cobrado
        decimal valor_original
        string status "AGENDADO|CONFIRMADO|REALIZADO|CANCELADO|FALTOU"
        string token_cancelamento UK
        datetime criado_em
    }
    PACOTE {
        int id PK
        string nome
        text descricao
        decimal preco_total
        bool ativo
        smallint validade_meses
    }
    ITEM_PACOTE {
        int id PK
        int pacote_id FK
        int procedimento_id FK
        smallint quantidade_sessoes
    }
    PACOTE_CLIENTE {
        int id PK
        int cliente_id FK
        int pacote_id FK "RESTRICT"
        datetime criado_em
        decimal valor_pago
        string status "ATIVO|FINALIZADO|CANCELADO|EXPIRADO"
        date data_expiracao
    }
    SESSAO_PACOTE {
        int id PK
        int pacote_cliente_id FK
        int atendimento_id FK "RESTRICT, UNIQUE"
        datetime criado_em
    }
    LISTA_ESPERA {
        int id PK
        int cliente_id FK
        int procedimento_id FK
        int profissional_desejado_id FK "SET NULL"
        date data_desejada
        string turno_desejado "MANHA|TARDE|NOITE"
        bool notificado
        string token_reserva
        datetime expira_em
        datetime criado_em
    }

    CLIENTE ||--o{ ATENDIMENTO : "agenda"
    CLIENTE ||--o{ PACOTE_CLIENTE : "compra"
    CLIENTE ||--o{ LISTA_ESPERA : "aguarda vaga"
    PROMOCAO ||--o{ ATENDIMENTO : "aplicada em"
    ATENDIMENTO ||--o| ATENDIMENTO : "reagendado de"
    PACOTE ||--o{ ITEM_PACOTE : "contem"
    PACOTE ||--o{ PACOTE_CLIENTE : "vendido como"
    PACOTE_CLIENTE ||--o{ SESSAO_PACOTE : "consome via"
    ATENDIMENTO ||--o| SESSAO_PACOTE : "debita sessao"
```

**Notas de leitura:**

- As tres FKs principais do ATENDIMENTO (cliente, profissional e
  procedimento) usam RESTRICT: dados historicos sao imutaveis e
  nao podem ser apagados indiretamente por remocao de catalogo.
- SESSAO_PACOTE tem relacao 1:1 com ATENDIMENTO e eh criada
  automaticamente pelo signal quando `status=REALIZADO` e o
  cliente tem pacote ativo cobrindo o procedimento.
- PACOTE_CLIENTE.status transita para FINALIZADO automaticamente
  via `verificar_finalizacao()` quando todas as sessoes do pacote
  foram consumidas, e para EXPIRADO quando a data_expiracao eh
  ultrapassada durante uma tentativa de debito.
- LISTA_ESPERA eh consultada pelo signal post_save de ATENDIMENTO
  quando o status muda para CANCELADO/FALTOU, disparando
  notificacao assincrona via Celery.
- `PROFISSIONAL` aparece implicitamente neste diagrama como FK
  em ATENDIMENTO e LISTA_ESPERA — esta detalhado na Pagina 1.

---

## Pagina 3 — Prontuario, Termos, Notificacoes, NPS e Infra

Cobre prontuario medico (hibrido), termos de consentimento (LGPD +
por procedimento), log de notificacoes, avaliacao NPS e infraestrutura
de auditoria/configuracao.

```mermaid
erDiagram
    PRONTUARIO {
        int id PK
        int cliente_id FK "UNIQUE 1:1"
        text alergias
        text contraindicacoes
        text historico_saude
        text medicamentos_uso
        text observacoes_gerais
        datetime atualizado_em
    }
    PRONTUARIO_PERGUNTA {
        int id PK
        text texto
        string tipo_resposta "TEXTO|BOOLEAN|SELECAO"
        bool ativa
    }
    PRONTUARIO_RESPOSTA {
        int id PK
        int prontuario_id FK
        int pergunta_id FK "RESTRICT"
        text resposta_texto
        bool resposta_boolean
        datetime atualizado_em
    }
    ANOTACAO_SESSAO {
        int id PK
        int atendimento_id FK
        int usuario_id FK "SET NULL"
        text texto
        datetime criado_em
    }
    VERSAO_TERMO {
        int id PK
        string tipo "LGPD|PROCEDIMENTO"
        int procedimento_id FK "NULL quando LGPD"
        text titulo
        text conteudo
        string versao
        date vigente_desde
        bool ativa
    }
    ACEITE_PRIVACIDADE {
        int id PK
        int cliente_id FK
        int versao_termo_id FK "RESTRICT"
        string ip
        datetime criado_em
    }
    ASSINATURA_TERMO_PROCEDIMENTO {
        int id PK
        int cliente_id FK
        int versao_termo_id FK "RESTRICT"
        int atendimento_id FK "SET NULL"
        string ip
        datetime criado_em
    }
    NOTIFICACAO {
        int id PK
        int atendimento_id FK
        string tipo "LEMBRETE|CONFIRMACAO|CANCELAMENTO|NPS"
        string canal "WHATSAPP|SMS|EMAIL"
        string status_envio "PENDENTE|ENVIADO|FALHOU"
        string resposta_cliente "CONFIRMOU|CANCELOU"
        string token UK
        text mensagem
        datetime enviado_em
        datetime respondido_em
        datetime criado_em
    }
    AVALIACAO_NPS {
        int id PK
        int atendimento_id FK "UNIQUE 1:1"
        smallint nota "0..10"
        text comentario
        bool alerta_enviado
        datetime criado_em
    }
    LOG_AUDITORIA {
        int id PK
        int usuario_id FK "SET NULL"
        text acao
        string tabela_afetada
        int id_registro_afetado
        json detalhes
        datetime criado_em
    }
    CONFIGURACAO_SISTEMA {
        int id PK
        string chave UK
        text valor
        text descricao
    }
    CODIGO_VERIFICACAO {
        int id PK
        string telefone
        string codigo
        bool usado
        datetime criado_em
    }

    PRONTUARIO ||--o{ PRONTUARIO_RESPOSTA : "contem"
    PRONTUARIO_PERGUNTA ||--o{ PRONTUARIO_RESPOSTA : "respondida em"
    ATENDIMENTO ||--o{ ANOTACAO_SESSAO : "tem observacao"
    ATENDIMENTO ||--o{ NOTIFICACAO : "gera"
    ATENDIMENTO ||--o| AVALIACAO_NPS : "avaliada por"
    CLIENTE ||--o| PRONTUARIO : "possui"
    CLIENTE ||--o{ ACEITE_PRIVACIDADE : "aceita LGPD"
    CLIENTE ||--o{ ASSINATURA_TERMO_PROCEDIMENTO : "assina termo"
    USUARIO ||--o{ LOG_AUDITORIA : "registra"
    USUARIO ||--o{ ANOTACAO_SESSAO : "registra"
    VERSAO_TERMO ||--o{ ACEITE_PRIVACIDADE : "assinada em"
    VERSAO_TERMO ||--o{ ASSINATURA_TERMO_PROCEDIMENTO : "assinada em"
```

**Notas de leitura:**

- O prontuario eh hibrido: `PRONTUARIO` guarda anamnese base
  permanente (1:1 com cliente), `PRONTUARIO_RESPOSTA` armazena
  respostas estruturadas por pergunta, e `ANOTACAO_SESSAO` guarda
  observacoes evolutivas ligadas a cada atendimento individual.
- `VERSAO_TERMO` eh uma tabela unica para dois tipos: LGPD (sem
  FK para procedimento) e PROCEDIMENTO (com FK). As tabelas de
  aceite sao distintas porque as regras de quando se re-assina
  diferem entre os dois tipos.
- `ASSINATURA_TERMO_PROCEDIMENTO` referencia o atendimento que
  originou a assinatura, mas a FK usa SET NULL: se o atendimento
  for removido, o registro legal da assinatura sobrevive.
- `NOTIFICACAO`, `AVALIACAO_NPS` e `ANOTACAO_SESSAO` sao
  dependentes de ATENDIMENTO (detalhado na Pagina 2) — as setas
  estao implicitas neste diagrama para manter o foco no dominio
  clinico/legal.
- `LOG_AUDITORIA.usuario_id` eh SET NULL para preservar a trilha
  de auditoria mesmo apos remocao do autor.

---

## Como exportar para PDF

### Opcao 1 — VS Code (mais simples no Windows)

1. Instale a extensao **Markdown PDF** (yzane.markdown-pdf).
2. Instale a extensao **Markdown Preview Mermaid Support**
   (bierner.markdown-mermaid).
3. Abra este arquivo (`docs/erd.md`), clique com o botao direito
   no editor e escolha `Markdown PDF: Export (pdf)`.
4. O PDF sai em `docs/erd.pdf`.

### Opcao 2 — Pandoc + mermaid-filter (linha de comando)

```bash
npm install -g mermaid-filter
pandoc docs/erd.md \
  --filter mermaid-filter \
  -o docs/erd.pdf \
  --pdf-engine=xelatex \
  -V geometry:landscape \
  -V geometry:margin=1.5cm
```

### Opcao 3 — mermaid-cli + HTML intermediario

O script `docs/erd.html` (gerado junto com este arquivo) ja
renderiza os tres diagramas com a biblioteca mermaid.js via CDN
e pode ser exportado para PDF direto pelo navegador
(`Ctrl+P` -> `Salvar como PDF` em modo paisagem).
