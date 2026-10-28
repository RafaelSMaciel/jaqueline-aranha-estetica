"""Inventario de dado pessoal (LGPD art. 37) — a fonte que o teste confere contra o codigo.

Cada tabela do app tem uma classe (TITULAR = dado da cliente, EQUIPE = de quem trabalha na
clinica, SISTEMA = catalogo/regra sem pessoa natural) e cada coluna com dado pessoal tem o que
acontece com ela no fim da finalidade:

    ANONIMIZA  sp_anonimizar_cliente limpa ou pseudonimiza (UPDATE) — esquecimento e retencao
    APAGA      a linha sai (DELETE no esquecimento/retencao)
    PURGA      sai por idade (sp_purgar_dados_expirados, job housekeeping)
    RETEM      fica por obrigacao legal ou exercicio de direito (art. 16 I/II, art. 7º VI) — o
               motivo vai junto e o docs/LGPD.md registra o prazo
    EQUIPE     dado da equipe (conta, autoria): sai com a conta (SET NULL / snapshot)

tests/test_inventario_lgpd.py reprova: tabela nova sem classe, coluna com cara de dado pessoal
fora daqui, ANONIMIZA/APAGA/PURGA que a procedure nao faz e tabela TITULAR fora do docs/LGPD.md.
Mudou o tratamento? Mude a procedure (migration nova), este arquivo e o docs/LGPD.md juntos.
"""

TITULAR, EQUIPE, SISTEMA = 'titular', 'equipe', 'sistema'
ANONIMIZA, APAGA, PURGA, RETEM = 'anonimiza', 'apaga', 'purga', 'retem'

TABELAS = {
    # dado da cliente
    'cliente': TITULAR, 'consentimento': TITULAR, 'atendimento': TITULAR, 'notificacao': TITULAR,
    'prontuario': TITULAR, 'prontuario_versao': TITULAR, 'anotacao_sessao': TITULAR,
    'aceite_termo': TITULAR, 'avaliacao_nps': TITULAR, 'compra_pacote': TITULAR,
    'compra_pacote_item': TITULAR, 'consumo_sessao': TITULAR, 'lista_espera': TITULAR,
    'codigo_otp': TITULAR, 'resposta_formulario': TITULAR, 'link_acesso': TITULAR,
    'carteira': TITULAR, 'movimento_carteira': TITULAR,
    # trilhas (dado de quem agiu e do registro alterado)
    'log_auditoria': TITULAR, 'historico_alteracao': TITULAR,
    # equipe
    'usuario': EQUIPE, 'profissional': EQUIPE, 'assinatura_push': EQUIPE, 'movimento_comissao': EQUIPE,
    'agenda_horario': EQUIPE, 'agenda_excecao': EQUIPE, 'habilitacao': EQUIPE, 'regra_comissao': EQUIPE,
    # catalogo e configuracao
    'procedimento': SISTEMA, 'preco': SISTEMA, 'promocao': SISTEMA, 'versao_termo': SISTEMA,
    'pacote': SISTEMA, 'pacote_item': SISTEMA, 'configuracao': SISTEMA, 'formulario': SISTEMA,
    'execucao_job': SISTEMA,
}

_SAUDE = 'registro de saude: 20 anos (cifrado em repouso)'
_PROVA = 'prova do aceite/consentimento (exercicio de direitos, art. 7º VI)'

# (tabela, coluna) -> tratamento ou (tratamento, motivo)
COLUNAS = {
    # cadastro
    ('cliente', 'nome'): ANONIMIZA, ('cliente', 'data_nascimento'): ANONIMIZA,
    ('cliente', 'cpf'): ANONIMIZA, ('cliente', 'email'): ANONIMIZA, ('cliente', 'telefone'): ANONIMIZA,
    ('cliente', 'cep'): ANONIMIZA, ('cliente', 'endereco'): ANONIMIZA,
    # consentimento: o historico fica (prova), IP e user-agent saem
    ('consentimento', 'ip_origem'): ANONIMIZA, ('consentimento', 'user_agent'): ANONIMIZA,
    # mensagens e contatos
    ('notificacao', 'mensagem'): ANONIMIZA,
    ('avaliacao_nps', 'comentario'): ANONIMIZA,
    ('lista_espera', 'email_contato'): APAGA,
    ('codigo_otp', 'identificador'): APAGA, ('codigo_otp', 'telefone'): APAGA,
    ('codigo_otp', 'ip_origem'): APAGA,
    # saude
    ('prontuario', 'alergias'): (RETEM, _SAUDE), ('prontuario', 'contraindicacoes'): (RETEM, _SAUDE),
    ('prontuario', 'historico_saude'): (RETEM, _SAUDE), ('prontuario', 'medicamentos_uso'): (RETEM, _SAUDE),
    ('prontuario', 'observacoes_gerais'): (RETEM, _SAUDE), ('prontuario', 'respostas'): (RETEM, _SAUDE),
    ('prontuario_versao', 'dados'): (RETEM, _SAUDE),
    ('anotacao_sessao', 'texto'): (RETEM, _SAUDE),
    # ficha sem atendimento REALIZADO sai no esquecimento e na retencao (90 dias); a do realizado fica
    ('resposta_formulario', 'respostas'): APAGA,
    # provas
    ('aceite_termo', 'ip_origem'): (RETEM, _PROVA), ('aceite_termo', 'user_agent'): (RETEM, _PROVA),
    # livro-razao financeiro (append-only)
    ('movimento_carteira', 'observacao'): (RETEM, 'livro-razao da carteira (append-only)'),
    # trilhas: nome do titular pseudonimizado no esquecimento; o resto sai por idade
    ('log_auditoria', 'acao'): ANONIMIZA, ('log_auditoria', 'detalhes'): ANONIMIZA,
    ('log_auditoria', 'ip_origem'): PURGA,
    ('historico_alteracao', 'dados_antes'): PURGA, ('historico_alteracao', 'dados_depois'): PURGA,
    ('historico_alteracao', 'ip_origem'): PURGA,
    # equipe
    ('usuario', 'nome'): EQUIPE, ('usuario', 'email'): EQUIPE,
    ('profissional', 'nome'): EQUIPE, ('profissional', 'url_foto'): EQUIPE, ('profissional', 'biografia'): EQUIPE,
    ('assinatura_push', 'user_agent'): EQUIPE, ('assinatura_push', 'endpoint'): EQUIPE,
    ('prontuario_versao', 'usuario_autor_nome'): EQUIPE, ('anotacao_sessao', 'usuario_autor_nome'): EQUIPE,
    ('log_auditoria', 'usuario_autor_nome'): EQUIPE,
    ('movimento_comissao', 'observacao'): EQUIPE,
    ('agenda_excecao', 'motivo'): EQUIPE, ('agenda_excecao', 'observacao'): EQUIPE,
}


def tratamento(tabela, coluna):
    valor = COLUNAS.get((tabela, coluna))
    return valor[0] if isinstance(valor, tuple) else valor
