-- CRIAÇÃO DO SCHEMA DEDICADO
CREATE SCHEMA IF NOT EXISTS shivazen_app;

-- CRIAÇÃO DAS TABELAS DENTRO DO SCHEMA
CREATE TABLE shivazen_app.funcionalidade (
    id_funcionalidade SERIAL PRIMARY KEY,
    nome VARCHAR(100) UNIQUE NOT NULL,
    descricao TEXT
);

CREATE TABLE shivazen_app.perfil (
    id_perfil SERIAL PRIMARY KEY,
    nome VARCHAR(50) UNIQUE NOT NULL,
    descricao TEXT
);

CREATE TABLE shivazen_app.profissional (
    id_profissional SERIAL PRIMARY KEY,
    nome VARCHAR(100) NOT NULL,
    especialidade VARCHAR(100),
    ativo BOOLEAN DEFAULT TRUE
);

CREATE TABLE shivazen_app.cliente (
    id_cliente SERIAL PRIMARY KEY,
    nome_completo VARCHAR(150) NOT NULL,
    data_nascimento DATE,
    cpf VARCHAR(14) UNIQUE,
    rg VARCHAR(20),
    profissao VARCHAR(100),
    email VARCHAR(100),
    telefone VARCHAR(20),
    cep VARCHAR(10),
    endereco TEXT,
    ativo BOOLEAN DEFAULT TRUE,
    data_cadastro TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shivazen_app.prontuario_pergunta (
    id_pergunta SERIAL PRIMARY KEY,
    texto TEXT NOT NULL,
    tipo_resposta VARCHAR(50) NOT NULL,
    ativa BOOLEAN DEFAULT TRUE
);

CREATE TABLE shivazen_app.procedimento (
    id_procedimento SERIAL PRIMARY KEY,
    nome VARCHAR(100) NOT NULL,
    descricao TEXT,
    duracao_minutos INTEGER NOT NULL,
    ativo BOOLEAN DEFAULT TRUE
);

CREATE TABLE shivazen_app.perfil_funcionalidade (
    id_perfil INTEGER NOT NULL REFERENCES shivazen_app.perfil(id_perfil) ON DELETE CASCADE,
    id_funcionalidade INTEGER NOT NULL REFERENCES shivazen_app.funcionalidade(id_funcionalidade) ON DELETE CASCADE,
    PRIMARY KEY (id_perfil, id_funcionalidade)
);

CREATE TABLE shivazen_app.usuario (
    id_usuario SERIAL PRIMARY KEY,
    id_perfil INTEGER NOT NULL REFERENCES shivazen_app.perfil(id_perfil) ON DELETE RESTRICT,
    id_profissional INTEGER UNIQUE REFERENCES shivazen_app.profissional(id_profissional) ON DELETE SET NULL,
    nome VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    senha_hash VARCHAR(255) NOT NULL,
    ativo BOOLEAN DEFAULT TRUE
);

CREATE TABLE shivazen_app.prontuario (
    id_prontuario SERIAL PRIMARY KEY,
    id_cliente INTEGER UNIQUE NOT NULL REFERENCES shivazen_app.cliente(id_cliente) ON DELETE CASCADE
);

CREATE TABLE shivazen_app.profissional_procedimento (
    id_profissional INTEGER NOT NULL REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE CASCADE,
    PRIMARY KEY (id_profissional, id_procedimento)
);

CREATE TABLE shivazen_app.preco (
    id_preco SERIAL PRIMARY KEY,
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE CASCADE,
    id_profissional INTEGER REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    valor DECIMAL(10, 2) NOT NULL,
    descricao VARCHAR(255)
);

CREATE TABLE shivazen_app.disponibilidade_profissional (
    id_disponibilidade SERIAL PRIMARY KEY,
    id_profissional INTEGER NOT NULL REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    dia_semana INTEGER NOT NULL,
    hora_inicio TIME NOT NULL,
    hora_fim TIME NOT NULL
);

CREATE TABLE shivazen_app.bloqueio_agenda (
    id_bloqueio SERIAL PRIMARY KEY,
    id_profissional INTEGER REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    data_hora_inicio TIMESTAMP WITH TIME ZONE NOT NULL,
    data_hora_fim TIMESTAMP WITH TIME ZONE NOT NULL,
    motivo TEXT
);

CREATE TABLE shivazen_app.atendimento (
    id_atendimento SERIAL PRIMARY KEY,
    id_cliente INTEGER NOT NULL REFERENCES shivazen_app.cliente(id_cliente) ON DELETE RESTRICT,
    id_profissional INTEGER NOT NULL REFERENCES shivazen_app.profissional(id_profissional) ON DELETE RESTRICT,
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE RESTRICT,
    data_hora_inicio TIMESTAMP WITH TIME ZONE NOT NULL,
    data_hora_fim TIMESTAMP WITH TIME ZONE NOT NULL,
    valor_cobrado DECIMAL(10, 2),
    status_atendimento VARCHAR(20) NOT NULL DEFAULT 'AGENDADO',
    observacoes TEXT
);

CREATE TABLE shivazen_app.prontuario_resposta (
    id_resposta SERIAL PRIMARY KEY,
    id_atendimento INTEGER NOT NULL REFERENCES shivazen_app.atendimento(id_atendimento) ON DELETE CASCADE,
    id_pergunta INTEGER NOT NULL REFERENCES shivazen_app.prontuario_pergunta(id_pergunta) ON DELETE RESTRICT,
    resposta_texto TEXT,
    resposta_boolean BOOLEAN
);

CREATE TABLE shivazen_app.notificacao (
    id_notificacao SERIAL PRIMARY KEY,
    id_atendimento INTEGER NOT NULL REFERENCES shivazen_app.atendimento(id_atendimento) ON DELETE CASCADE,
    canal VARCHAR(20) NOT NULL,
    status_envio VARCHAR(20) NOT NULL,
    data_hora_envio TIMESTAMP WITH TIME ZONE
);

CREATE TABLE shivazen_app.termo_consentimento (
    id_termo SERIAL PRIMARY KEY,
    id_atendimento INTEGER NOT NULL REFERENCES shivazen_app.atendimento(id_atendimento) ON DELETE CASCADE,
    id_usuario_assinatura INTEGER REFERENCES shivazen_app.usuario(id_usuario) ON DELETE SET NULL,
    ip_assinatura VARCHAR(45),
    data_hora_assinatura TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shivazen_app.log_auditoria (
    id_log SERIAL PRIMARY KEY,
    id_usuario INTEGER REFERENCES shivazen_app.usuario(id_usuario) ON DELETE SET NULL,
    acao VARCHAR(255) NOT NULL,
    tabela_afetada VARCHAR(100),
    id_registro_afetado INTEGER,
    detalhes JSONB,
    data_hora TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- CRIAÇÃO DE ÍNDICES
CREATE INDEX idx_atendimento_cliente ON shivazen_app.atendimento(id_cliente);
CREATE INDEX idx_atendimento_profissional ON shivazen_app.atendimento(id_profissional);
CREATE INDEX idx_atendimento_data_inicio ON shivazen_app.atendimento(data_hora_inicio);
CREATE INDEX idx_cliente_nome ON shivazen_app.cliente(nome_completo);
CREATE INDEX idx_usuario_email ON shivazen_app.usuario(email);
CREATE INDEX idx_prontuario_resposta_atendimento ON shivazen_app.prontuario_resposta(id_atendimento);
CREATE INDEX idx_termo_consentimento_atendimento ON shivazen_app.termo_consentimento(id_atendimento);
CREATE INDEX idx_notificacao_atendimento ON shivazen_app.notificacao(id_atendimento);


-- CRIAÇÃO DE CONSTRAINTS ADICIONAIS
ALTER TABLE shivazen_app.atendimento ADD CONSTRAINT chk_datas_atendimento CHECK (data_hora_fim > data_hora_inicio);
ALTER TABLE shivazen_app.bloqueio_agenda ADD CONSTRAINT chk_datas_bloqueio CHECK (data_hora_fim > data_hora_inicio);
ALTER TABLE shivazen_app.disponibilidade_profissional ADD CONSTRAINT chk_horas_disponibilidade CHECK (hora_fim > hora_inicio);
ALTER TABLE shivazen_app.preco ADD CONSTRAINT chk_preco_positivo CHECK (valor >= 0);





INSERT INTO shivazen_app.funcionalidade (nome, descricao) VALUES
('ACESSAR_DASHBOARD', 'Permite visualizar a tela principal do sistema'),
('GERENCIAR_AGENDA', 'Permite criar, editar e cancelar agendamentos de qualquer profissional'),
('VISUALIZAR_AGENDA_PROPRIA', 'Permite visualizar apenas a própria agenda'),
('GERENCIAR_CLIENTES', 'Permite criar, editar e visualizar clientes'),
('GERENCIAR_PRONTUARIOS', 'Permite editar as respostas do prontuário de um cliente'),
('GERENCIAR_USUARIOS', 'Permite criar e editar usuários do sistema e seus perfis'),
('GERENCIAR_SERVICOS', 'Permite cadastrar e editar procedimentos e preços');

INSERT INTO shivazen_app.perfil (nome, descricao) VALUES
('Administrador', 'Acesso total a todas as funcionalidades do sistema.'),
('Profissional', 'Acesso à própria agenda e aos prontuários dos seus clientes.'),
('Recepção', 'Acesso à gestão de clientes e à agenda de todos os profissionais.');

INSERT INTO shivazen_app.perfil_funcionalidade (id_perfil, id_funcionalidade)
SELECT (SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Administrador'), id_funcionalidade FROM shivazen_app.funcionalidade;

INSERT INTO shivazen_app.perfil_funcionalidade (id_perfil, id_funcionalidade) VALUES
((SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Profissional'), (SELECT id_funcionalidade FROM shivazen_app.funcionalidade WHERE nome = 'ACESSAR_DASHBOARD')),
((SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Profissional'), (SELECT id_funcionalidade FROM shivazen_app.funcionalidade WHERE nome = 'VISUALIZAR_AGENDA_PROPRIA')),
((SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Profissional'), (SELECT id_funcionalidade FROM shivazen_app.funcionalidade WHERE nome = 'GERENCIAR_PRONTUARIOS'));

INSERT INTO shivazen_app.perfil_funcionalidade (id_perfil, id_funcionalidade) VALUES
((SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Recepção'), (SELECT id_funcionalidade FROM shivazen_app.funcionalidade WHERE nome = 'ACESSAR_DASHBOARD')),
((SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Recepção'), (SELECT id_funcionalidade FROM shivazen_app.funcionalidade WHERE nome = 'GERENCIAR_AGENDA')),
((SELECT id_perfil FROM shivazen_app.perfil WHERE nome = 'Recepção'), (SELECT id_funcionalidade FROM shivazen_app.funcionalidade WHERE nome = 'GERENCIAR_CLIENTES'));




CREATE OR REPLACE FUNCTION shivazen_app.f_agendar_atendimento(
    p_id_cliente INTEGER,
    p_id_profissional INTEGER,
    p_id_procedimento INTEGER,
    p_data_hora_inicio TIMESTAMP WITH TIME ZONE
)
RETURNS INTEGER 
LANGUAGE plpgsql
AS $$
DECLARE
    v_duracao_minutos INTEGER;
    v_data_hora_fim TIMESTAMP WITH TIME ZONE;
    v_valor_cobrado DECIMAL(10, 2);
    v_conflito_atendimento INTEGER;
    v_conflito_bloqueio INTEGER;
    v_disponivel BOOLEAN;
    v_novo_atendimento_id INTEGER;
BEGIN
    SELECT duracao_minutos INTO v_duracao_minutos FROM shivazen_app.procedimento WHERE id_procedimento = p_id_procedimento;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Procedimento com ID % não encontrado.', p_id_procedimento;
    END IF;
    v_data_hora_fim := p_data_hora_inicio + (v_duracao_minutos * INTERVAL '1 minute');

    SELECT count(*) INTO v_conflito_atendimento
    FROM shivazen_app.atendimento
    WHERE id_profissional = p_id_profissional
      AND (p_data_hora_inicio, v_data_hora_fim) OVERLAPS (data_hora_inicio, data_hora_fim)
      AND status_atendimento NOT IN ('CANCELADO');
    IF v_conflito_atendimento > 0 THEN
        RAISE EXCEPTION 'Horário em conflito com outro agendamento existente.';
    END IF;

    SELECT count(*) INTO v_conflito_bloqueio
    FROM shivazen_app.bloqueio_agenda
    WHERE (id_profissional = p_id_profissional OR id_profissional IS NULL)
      AND (p_data_hora_inicio, v_data_hora_fim) OVERLAPS (data_hora_inicio, data_hora_fim);
    IF v_conflito_bloqueio > 0 THEN
        RAISE EXCEPTION 'Horário indisponível devido a um bloqueio na agenda.';
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM shivazen_app.disponibilidade_profissional
        WHERE id_profissional = p_id_profissional
          AND dia_semana = EXTRACT(ISODOW FROM p_data_hora_inicio) + 1 
          AND p_data_hora_inicio::TIME >= hora_inicio
          AND v_data_hora_fim::TIME <= hora_fim
    ) INTO v_disponivel;
    IF NOT v_disponivel THEN
        RAISE EXCEPTION 'Fora do horário de disponibilidade do profissional.';
    END IF;

    SELECT valor INTO v_valor_cobrado
    FROM shivazen_app.preco
    WHERE id_procedimento = p_id_procedimento
    ORDER BY id_profissional 
    LIMIT 1;

    INSERT INTO shivazen_app.atendimento (
        id_cliente, id_profissional, id_procedimento, data_hora_inicio, data_hora_fim, valor_cobrado, status_atendimento
    ) VALUES (
        p_id_cliente, p_id_profissional, p_id_procedimento, p_data_hora_inicio, v_data_hora_fim, v_valor_cobrado, 'AGENDADO'
    ) RETURNING id_atendimento INTO v_novo_atendimento_id;

    RETURN v_novo_atendimento_id;
END;
$$;


CREATE OR REPLACE FUNCTION shivazen_app.f_registrar_cliente(
    p_nome_completo VARCHAR,
    p_data_nascimento DATE,
    p_cpf VARCHAR,
    p_rg VARCHAR,
    p_profissao VARCHAR,
    p_email VARCHAR,
    p_telefone VARCHAR,
    p_cep VARCHAR,
    p_endereco TEXT
)
RETURNS INTEGER 
LANGUAGE plpgsql
AS $$
DECLARE
    v_novo_cliente_id INTEGER;
BEGIN
    INSERT INTO shivazen_app.cliente (
        nome_completo, data_nascimento, cpf, rg, profissao, email, telefone, cep, endereco
    ) VALUES (
        p_nome_completo, p_data_nascimento, p_cpf, p_rg, p_profissao, p_email, p_telefone, p_cep, p_endereco
    ) RETURNING id_cliente INTO v_novo_cliente_id;

    INSERT INTO shivazen_app.prontuario (id_cliente) VALUES (v_novo_cliente_id);

    RETURN v_novo_cliente_id;
END;
$$;

-- =====================================================================
-- CONTROLE DE ESTOQUE
-- =====================================================================

CREATE TABLE shivazen_app.categoria_produto (
    id_categoria SERIAL PRIMARY KEY,
    nome VARCHAR(100) UNIQUE NOT NULL,
    descricao TEXT,
    ativo BOOLEAN DEFAULT TRUE
);

CREATE TABLE shivazen_app.produto (
    id_produto SERIAL PRIMARY KEY,
    id_categoria INTEGER REFERENCES shivazen_app.categoria_produto(id_categoria) ON DELETE SET NULL,
    nome VARCHAR(150) NOT NULL,
    descricao TEXT,
    marca VARCHAR(100),
    codigo_barras VARCHAR(50) UNIQUE,
    preco_custo DECIMAL(10, 2) NOT NULL DEFAULT 0,
    preco_venda DECIMAL(10, 2) NOT NULL DEFAULT 0,
    quantidade_estoque INTEGER NOT NULL DEFAULT 0,
    estoque_minimo INTEGER NOT NULL DEFAULT 5,
    unidade VARCHAR(20) NOT NULL DEFAULT 'UN',
    ativo BOOLEAN DEFAULT TRUE,
    data_cadastro TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shivazen_app.movimentacao_estoque (
    id_movimentacao SERIAL PRIMARY KEY,
    id_produto INTEGER NOT NULL REFERENCES shivazen_app.produto(id_produto) ON DELETE CASCADE,
    tipo VARCHAR(20) NOT NULL,
    quantidade INTEGER NOT NULL,
    quantidade_anterior INTEGER NOT NULL DEFAULT 0,
    quantidade_posterior INTEGER NOT NULL DEFAULT 0,
    motivo TEXT,
    id_usuario INTEGER REFERENCES shivazen_app.usuario(id_usuario) ON DELETE SET NULL,
    data_movimentacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shivazen_app.configuracao_sistema (
    id_config SERIAL PRIMARY KEY,
    chave VARCHAR(100) UNIQUE NOT NULL,
    valor TEXT,
    descricao VARCHAR(255)
);

-- Indices de estoque
CREATE INDEX idx_produto_categoria ON shivazen_app.produto(id_categoria);
CREATE INDEX idx_produto_codigo_barras ON shivazen_app.produto(codigo_barras);
CREATE INDEX idx_movimentacao_produto ON shivazen_app.movimentacao_estoque(id_produto);
CREATE INDEX idx_movimentacao_data ON shivazen_app.movimentacao_estoque(data_movimentacao);

-- Configurações iniciais
INSERT INTO shivazen_app.configuracao_sistema (chave, valor, descricao) VALUES
('WHATSAPP_NUMERO', '5517000000000', 'Número do WhatsApp da clínica'),
('NOME_CLINICA', 'Shiva Zen', 'Nome da clínica'),
('HORARIO_FUNCIONAMENTO', 'Seg-Sex: 9h-18h | Sáb: 9h-14h', 'Horário de funcionamento'),
('ENDERECO', 'Rua Example, 123 - Centro, Cidade/SP', 'Endereço da clínica'),
('EMAIL_CONTATO', 'contato@shivazen.com', 'Email de contato'),
('INSTAGRAM', '@shivazen', 'Instagram da clínica');

-- Promoção tables
CREATE TABLE IF NOT EXISTS shivazen_app.promocao (
    id_promocao SERIAL PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    descricao TEXT,
    id_procedimento INTEGER REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE CASCADE,
    desconto_percentual DECIMAL(5, 2) DEFAULT 0,
    preco_promocional DECIMAL(10, 2),
    data_inicio DATE NOT NULL,
    data_fim DATE NOT NULL,
    ativa BOOLEAN DEFAULT TRUE,
    imagem_url VARCHAR(500)
);

CREATE TABLE IF NOT EXISTS shivazen_app.codigo_verificacao (
    id SERIAL PRIMARY KEY,
    telefone VARCHAR(20) NOT NULL,
    codigo VARCHAR(6) NOT NULL,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    usado BOOLEAN DEFAULT FALSE
);

-- Expansion tables
CREATE TABLE IF NOT EXISTS shivazen_app.pacote (
    id_pacote SERIAL PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    descricao TEXT,
    preco_total DECIMAL(10, 2) NOT NULL,
    ativo BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS shivazen_app.item_pacote (
    id_item_pacote SERIAL PRIMARY KEY,
    id_pacote INTEGER NOT NULL REFERENCES shivazen_app.pacote(id_pacote) ON DELETE CASCADE,
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE CASCADE,
    quantidade_sessoes INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS shivazen_app.pacote_cliente (
    id_pacote_cliente SERIAL PRIMARY KEY,
    id_cliente INTEGER NOT NULL REFERENCES shivazen_app.cliente(id_cliente) ON DELETE CASCADE,
    id_pacote INTEGER NOT NULL REFERENCES shivazen_app.pacote(id_pacote) ON DELETE RESTRICT,
    data_compra TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    valor_pago DECIMAL(10, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ATIVO'
);

CREATE TABLE IF NOT EXISTS shivazen_app.sessao_pacote (
    id_sessao_pacote SERIAL PRIMARY KEY,
    id_pacote_cliente INTEGER NOT NULL REFERENCES shivazen_app.pacote_cliente(id_pacote_cliente) ON DELETE CASCADE,
    id_atendimento INTEGER NOT NULL UNIQUE REFERENCES shivazen_app.atendimento(id_atendimento) ON DELETE RESTRICT,
    data_debito TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shivazen_app.lista_espera (
    id_lista_espera SERIAL PRIMARY KEY,
    id_cliente INTEGER NOT NULL REFERENCES shivazen_app.cliente(id_cliente) ON DELETE CASCADE,
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE CASCADE,
    id_profissional INTEGER REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    data_desejada DATE NOT NULL,
    turno_desejado VARCHAR(20),
    notificado BOOLEAN DEFAULT FALSE,
    data_registro TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shivazen_app.avaliacao_nps (
    id_avaliacao SERIAL PRIMARY KEY,
    id_atendimento INTEGER NOT NULL UNIQUE REFERENCES shivazen_app.atendimento(id_atendimento) ON DELETE CASCADE,
    nota INTEGER NOT NULL CHECK (nota >= 1 AND nota <= 5),
    comentario TEXT,
    data_avaliacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shivazen_app.meta_profissional (
    id_meta SERIAL PRIMARY KEY,
    id_profissional INTEGER NOT NULL REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    mes INTEGER NOT NULL,
    ano INTEGER NOT NULL,
    valor_meta DECIMAL(10, 2) NOT NULL,
    UNIQUE(id_profissional, mes, ano)
);

CREATE TABLE IF NOT EXISTS shivazen_app.token_google_agenda (
    id_token SERIAL PRIMARY KEY,
    id_profissional INTEGER NOT NULL UNIQUE REFERENCES shivazen_app.profissional(id_profissional) ON DELETE CASCADE,
    access_token VARCHAR(255) NOT NULL,
    refresh_token VARCHAR(255) NOT NULL,
    token_uri VARCHAR(255) NOT NULL,
    client_id VARCHAR(255) NOT NULL,
    client_secret VARCHAR(255) NOT NULL,
    scopes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shivazen_app.venda (
    id_venda SERIAL PRIMARY KEY,
    id_cliente INTEGER NOT NULL REFERENCES shivazen_app.cliente(id_cliente) ON DELETE RESTRICT,
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE RESTRICT,
    id_profissional INTEGER REFERENCES shivazen_app.profissional(id_profissional) ON DELETE SET NULL,
    data DATE NOT NULL,
    sessoes INTEGER NOT NULL DEFAULT 1,
    valor DECIMAL(10, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE',
    observacoes TEXT,
    data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shivazen_app.orcamento (
    id_orcamento SERIAL PRIMARY KEY,
    nome_completo VARCHAR(150) NOT NULL,
    data_nascimento DATE,
    profissao VARCHAR(100),
    endereco_cep VARCHAR(200),
    email VARCHAR(100),
    rg VARCHAR(20),
    cpf VARCHAR(14),
    telefone VARCHAR(20),
    id_procedimento INTEGER NOT NULL REFERENCES shivazen_app.procedimento(id_procedimento) ON DELETE RESTRICT,
    sessoes INTEGER NOT NULL DEFAULT 1,
    valor DECIMAL(10, 2) NOT NULL,
    data DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE',
    observacoes TEXT,
    tratamento_estetico_anterior TEXT,
    doenca_pele TEXT,
    tratamento_cancer TEXT,
    melasma_pintas TEXT,
    uso_acido TEXT,
    medicacao_continua TEXT,
    gravida_amamentando TEXT,
    alergia TEXT,
    implante_marcapasso TEXT,
    data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE ROLE shiva_zen_app_role WITH LOGIN PASSWORD 'shivazen@fam@2026';
GRANT USAGE ON SCHEMA shivazen_app TO shiva_zen_app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA shivazen_app TO shiva_zen_app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shivazen_app TO shiva_zen_app_role;
GRANT EXECUTE ON FUNCTION shivazen_app.f_agendar_atendimento TO shivazen_app_role;
GRANT EXECUTE ON FUNCTION shivazen_app.f_registrar_cliente TO shivazen_app_role;
