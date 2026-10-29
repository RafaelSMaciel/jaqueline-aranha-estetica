"""Servico de LGPD — DSAR (export), unsubscribe, esquecimento (pseudonimizacao) e retencao.

O "esquecimento" e PSEUDONIMIZACAO (art. 13 §4), nao anonimizacao: o cadastro vira
'[ANONIMIZADO-<id>]' sem contato, documento, indicacao nem IP, mas o registro de saude e os
contratos ficam pelo prazo legal ligados a esse id. Os prazos vem de settings.RETENCAO_* (sem env:
mudar exige versao nova do termo LGPD; check aranha.W016).
"""
import logging
from typing import Any

from django.conf import settings
from django.db import connection, transaction

from aranha_estetica.models import Cliente, Atendimento, AvaliacaoNPS
from aranha_estetica.utils.banco import chamar, exigir_postgres
from aranha_estetica.utils.auditoria import autor_e_ip

logger = logging.getLogger(__name__)


def _iso(valor):
    """date/datetime -> ISO 8601 (None preservado)."""
    return valor.isoformat() if valor else None


def _dec(valor):
    """Decimal -> str (JSON sem perda de precisao)."""
    return str(valor) if valor is not None else None


def prazos_retencao() -> dict[str, int]:
    """Prazos efetivos (dias) que as procedures recebem — os mesmos publicados no termo LGPD."""
    return {
        'cliente_inativo': settings.RETENCAO_CLIENTE_INATIVO_DIAS,
        'carencia_soft_delete': settings.RETENCAO_CARENCIA_SOFT_DELETE_DIAS,
        'saude': settings.RETENCAO_ATENDIMENTO_SAUDE_DIAS,
        'ficha_sem_atendimento': settings.RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS,
        'pacote_apos_expirar': settings.RETENCAO_PACOTE_APOS_EXPIRAR_DIAS,
    }


# ─── DSAR (art. 18 II e V; declaracao completa do art. 19 II) ─────────────────────────────────

MSG_SAUDE_FORA_DO_SITE = (
    'Por segurança, a ficha de saúde (prontuário, versões, anotações e fichas de avaliação) não sai '
    'pelo site: ela é enviada ao e-mail cadastrado ou entregue mediante pedido na clínica.'
)


def _secao_pessoal(cliente) -> dict:
    return {
        'nome': cliente.nome,
        'data_nascimento': _iso(cliente.data_nascimento),
        'cpf': cliente.cpf,
        'email': cliente.email,
        'telefone': cliente.telefone,
        'cep': cliente.cep,
        'endereco': cliente.endereco,
        'codigo_indicacao': cliente.codigo_indicacao,
        'indicada_por_outra_cliente': cliente.indicado_por_id is not None,   # o nome e de terceira
        'criado_em': _iso(cliente.criado_em),
        'atualizado_em': _iso(cliente.atualizado_em),
    }


def _secao_preferencias(cliente) -> dict:
    return {
        'agendamento_online_suspenso': cliente.bloqueado_online,
        'faltas_consecutivas': cliente.faltas_consecutivas,
        'consentimento_saude_revogado_em': _iso(cliente.saude_revogada_em),
    }


def _secao_consentimentos(cliente) -> dict:
    from aranha_estetica.services import consentimentos

    # estado vigente por finalidade/canal + historico completo (data, origem, texto exibido e IP)
    return {
        'vigentes': [
            {'finalidade': fin, 'canal': canal, 'concedido': concedido}
            for (fin, canal), concedido in consentimentos.estado_atual(cliente).items()
        ],
        'historico': consentimentos.historico(cliente),
    }


def _secao_atendimentos(cliente) -> list:
    return [
        {
            'inicio': _iso(a.data_hora_inicio),
            'fim': _iso(a.data_hora_fim),
            'procedimento': a.procedimento.nome if a.procedimento_id else None,
            'profissional': a.profissional.nome if a.profissional_id else None,
            'status': a.status,
            'retorno': a.eh_retorno,
            'valor_cobrado': _dec(a.valor_cobrado),
            'valor_original': _dec(a.valor_original),
            'descricao_preco': a.descricao_preco,
            'criado_em': _iso(a.criado_em),
        }
        for a in (Atendimento.objects.filter(cliente=cliente)
                  .select_related('procedimento', 'profissional').order_by('-data_hora_inicio'))
    ]


def _secao_avaliacoes(cliente) -> list:
    return [
        {'nota': a.nota, 'comentario': a.comentario, 'respondida_em': _iso(a.criado_em),
         'autoriza_publicacao': a.autoriza_publicacao}
        for a in AvaliacaoNPS.objects.filter(atendimento__cliente=cliente).order_by('-criado_em')
    ]


def _secao_prontuario(cliente):
    from aranha_estetica.models import Prontuario
    from aranha_estetica.utils.saude import perguntas_prontuario, rotulo_da_chave

    prontuario = Prontuario.objects.filter(cliente=cliente).first()
    if prontuario is None:
        return None
    extras = prontuario.respostas_extras if isinstance(prontuario.respostas_extras, dict) else {}
    rotulos = {p['key']: p.get('label') for p in perguntas_prontuario()} if extras else {}
    return {
        'alergias': prontuario.alergias,
        'contraindicacoes': prontuario.contraindicacoes,
        'historico_saude': prontuario.historico_saude,
        'medicamentos_uso': prontuario.medicamentos_uso,
        'observacoes_gerais': prontuario.observacoes_gerais,
        'respostas_extras': prontuario.respostas_extras,
        # mesmas respostas com a pergunta legivel (questionario do prontuario)
        'questionario': [
            {'pergunta': rotulos.get(key) or rotulo_da_chave(key), 'resposta': valor}
            for key, valor in extras.items()
        ],
        'atualizado_em': _iso(prontuario.atualizado_em),
    }


def _secao_prontuario_versoes(cliente) -> list:
    from aranha_estetica.models import ProntuarioVersao

    # estado ANTERIOR a cada edicao da equipe
    return [
        {'registrado_em': _iso(v.criado_em), 'dados': v.dados}
        for v in ProntuarioVersao.objects.filter(prontuario__cliente=cliente).order_by('-criado_em', '-pk')
    ]


def _secao_anotacoes(cliente) -> list:
    from aranha_estetica.models import AnotacaoSessao

    return [
        {'atendimento_data': _iso(n.atendimento.data_hora_inicio), 'texto': n.texto, 'criado_em': _iso(n.criado_em)}
        for n in (AnotacaoSessao.objects.filter(atendimento__cliente=cliente)
                  .select_related('atendimento').order_by('-criado_em'))
    ]


def _secao_fichas(cliente) -> list:
    from aranha_estetica.models import RespostaAnamnese

    return [
        {'formulario': r.formulario.nome, 'tipo': r.formulario.tipo, 'respostas': r.respostas_json,
         'criado_em': _iso(r.criado_em), 'respondida_em': _iso(r.respondida_em)}
        for r in (RespostaAnamnese.objects.filter(cliente=cliente)
                  .select_related('formulario').order_by('-criado_em'))
    ]


def _secao_aceites(cliente) -> list:
    from aranha_estetica.models import AceiteTermo

    return [
        {'termo': a.versao_termo.titulo, 'tipo': a.versao_termo.tipo, 'versao': a.versao_termo.versao,
         'ip': a.ip, 'user_agent': a.user_agent, 'aceito_em': _iso(a.criado_em),
         'conteudo_sha256': a.conteudo_sha256}
        for a in (AceiteTermo.objects.filter(cliente=cliente)
                  .select_related('versao_termo').order_by('-criado_em'))
    ]


def _secao_pacotes(cliente) -> list:
    pacotes = []
    for compra in (cliente.pacotes_comprados.prefetch_related('itens__procedimento', 'sessoes_realizadas')
                   .order_by('-criado_em')):
        pacotes.append({
            # o que foi vendido (congelado na venda), nao o catalogo atual
            'pacote': compra.pacote_nome,
            'valor_pago': _dec(compra.valor_pago),
            'valor_reembolsado': _dec(compra.valor_reembolsado),
            'status': compra.status,
            'comprado_em': _iso(compra.criado_em),
            'data_expiracao': _iso(compra.data_expiracao),
            'cancelado_em': _iso(compra.cancelado_em),
            'sessoes_realizadas': len(compra.sessoes_realizadas.all()),
            'itens': [
                {
                    'procedimento': linha['procedimento_nome'],
                    'sessoes': linha['total'],
                    'sessoes_usadas': linha['usadas'],
                    'sessoes_restantes': linha['restantes'],
                    'valor_por_sessao': _dec(linha['item'].valor_unitario),
                }
                for linha in compra.saldo_por_procedimento()
            ],
        })
    return pacotes


def _secao_carteira(cliente):
    from aranha_estetica.models import Carteira

    carteira = Carteira.objects.filter(cliente=cliente).first()
    if carteira is None:
        return None
    return {
        'saldo': _dec(carteira.saldo),
        'movimentos': [
            {'tipo': m.tipo, 'origem': m.origem, 'valor': _dec(m.valor),
             'saldo_resultante': _dec(m.saldo_resultante), 'criado_em': _iso(m.criado_em)}
            for m in carteira.movimentos.order_by('-criado_em')
        ],
    }


def _secao_lista_espera(cliente) -> list:
    from django.db.models import Q

    from aranha_estetica.models import ListaEspera

    # do cadastro e as inscricoes anonimas feitas com o mesmo celular
    filtro = Q(cliente=cliente) | (Q(telefone_contato=cliente.telefone) if cliente.telefone else Q(pk__in=[]))
    return [
        {'procedimento': e.procedimento.nome, 'data_desejada': _iso(e.data_desejada),
         'turno': e.turno_desejado, 'ultimo_aviso_em': _iso(e.ultimo_aviso_em),
         'profissional_desejado': e.profissional_desejado.nome if e.profissional_desejado else None,
         'nome_contato': e.nome_contato, 'email_contato': e.email_contato, 'criado_em': _iso(e.criado_em)}
        for e in (ListaEspera.objects.filter(filtro)
                  .select_related('procedimento', 'profissional_desejado').order_by('-criado_em'))
    ]


def _secao_notificacoes(cliente) -> list:
    from aranha_estetica.models import Notificacao

    return [
        {'tipo': n.tipo, 'canal': n.canal, 'status': n.status,
         'enviado_em': _iso(n.enviado_em), 'criado_em': _iso(n.criado_em)}
        for n in Notificacao.objects.filter(atendimento__cliente=cliente).order_by('-criado_em')
    ]


def sobre_o_tratamento() -> dict:
    """Declaracao do art. 19 II: finalidades e bases, operadores e prazos efetivos (mesma fonte da
    Politica de Privacidade: conteudo/lgpd.py; prazos do settings)."""
    from aranha_estetica.conteudo.lgpd import BASES_LEGAIS, PRAZOS_RETENCAO, linha_prazo

    prazos = [
        linha_prazo(rotulo, getattr(settings, chave) if chave else None, unidade, complemento)[2:]
        for rotulo, chave, _padrao, unidade, complemento in PRAZOS_RETENCAO
    ]
    return {
        'controladora': 'a clínica identificada na Política de Privacidade do site (/politica-de-privacidade/)',
        'origem_dos_dados': 'informados por você no site, no atendimento ou pela equipe da clínica',
        'tratamentos': BASES_LEGAIS['tratamentos'],
        'compartilhamento': BASES_LEGAIS['operadores'],
        'prazos_de_guarda': prazos,
        'decisao_automatizada': (
            'Faltas seguidas (o cancelamento com menos de 24 h conta como falta) suspendem o agendamento '
            'online; a recepção revisa a pedido (art. 20).'
        ),
        'seus_direitos': 'arts. 18 e 20 da LGPD — peça pela área "Meus agendamentos" ou pelos canais da clínica.',
    }


# (secao, funcao, tabelas que cobre, e de saude) — a ordem e a do arquivo exportado
SECOES_DSAR = (
    ('pessoal', _secao_pessoal, ('cliente',), False),
    ('preferencias', _secao_preferencias, ('cliente',), False),
    ('consentimentos', _secao_consentimentos, ('consentimento',), False),
    ('atendimentos', _secao_atendimentos, ('atendimento',), False),
    ('avaliacoes_nps', _secao_avaliacoes, ('avaliacao_nps',), False),
    ('prontuario', _secao_prontuario, ('prontuario',), True),
    ('prontuario_versoes', _secao_prontuario_versoes, ('prontuario_versao',), True),
    ('anotacoes_sessao', _secao_anotacoes, ('anotacao_sessao',), True),
    ('anamneses', _secao_fichas, ('resposta_formulario',), True),
    ('aceites_termos', _secao_aceites, ('aceite_termo',), False),
    ('pacotes', _secao_pacotes, ('compra_pacote', 'compra_pacote_item', 'consumo_sessao'), False),
    ('carteira', _secao_carteira, ('carteira', 'movimento_carteira'), False),
    ('lista_espera', _secao_lista_espera, ('lista_espera',), False),
    ('notificacoes', _secao_notificacoes, ('notificacao',), False),
)
SECOES_SAUDE = tuple(nome for nome, _f, _t, saude in SECOES_DSAR if saude)
# tabelas do titular que nao viram secao: efemeras/so hash (nao identificam ninguem sozinhas) e trilhas
FORA_DO_DSAR = {
    'codigo_otp': 'codigo de 24 h, so o HMAC',
    'link_acesso': 'so o hash do token dos links enviados',
    'log_auditoria': 'trilha de seguranca da clinica (descrita em sobre_o_tratamento)',
    'historico_alteracao': 'trilha de seguranca da clinica (descrita em sobre_o_tratamento)',
}


def exportar_dados(cliente: Cliente, *, incluir_saude: bool = False) -> dict[str, Any]:
    """DSAR em dict JSON-friendly. O self-service (site/portal: 1 fator, o chip pode ter mudado de dono)
    NAO leva as secoes de saude (decisao 38) e diz como pedir; a equipe exporta completo
    (incluir_saude=True, LgpdService.exportar_dados_cliente)."""
    from django.utils import timezone

    dados: dict[str, Any] = {'gerado_em': _iso(timezone.now())}
    for nome, funcao, _tabelas, saude in SECOES_DSAR:
        if saude and not incluir_saude:
            continue
        dados[nome] = funcao(cliente)
    if not incluir_saude:
        dados['ficha_de_saude'] = MSG_SAUDE_FORA_DO_SITE
    dados['sobre_o_tratamento'] = sobre_o_tratamento()
    return dados


class LgpdService:

    @staticmethod
    def exportar_dados_cliente(cliente: Cliente) -> dict[str, Any]:
        """Export COMPLETO (com saude) para a equipe — painel/Django admin, a pedido da titular."""
        return exportar_dados(cliente, incluir_saude=True)

    @staticmethod
    def link_descadastro(token: str):
        """LinkAcesso DESCADASTRO vigente de cliente nao excluido (None se invalido)."""
        from aranha_estetica.services import links

        link = links.resolver(links.DESCADASTRO, token, select_related=('cliente',))
        if link is None or link.cliente is None or link.cliente.deletado_em is not None:
            return None
        return link

    @staticmethod
    def cliente_por_token_descadastro(token: str) -> Cliente | None:
        """Cliente ativo dono do link de descadastro (None se invalido)."""
        link = LgpdService.link_descadastro(token)
        return link.cliente if link else None

    @staticmethod
    @transaction.atomic
    def unsubscribe_por_token(token: str, request=None) -> Cliente | None:
        """Opt-out de marketing e pesquisas via token publico (link do e-mail).

        Revoga (origem DESCADASTRO, com IP/UA de quem confirmou) MARKETING por
        e-mail e PESQUISA_SATISFACAO por WhatsApp. O lembrete do agendamento
        (LEMBRETE_AGENDAMENTO) e aviso transacional e continua — a pagina de
        descadastro informa isso ao cliente. Se a cliente marcar o opt-in de
        novo no agendamento, o registro mais recente volta a valer.
        """
        from aranha_estetica.models import Consentimento
        from aranha_estetica.services import consentimentos, links

        link = LgpdService.link_descadastro(token)
        if link is None:
            return None
        cliente = link.cliente
        consentimentos.revogar_todos(
            cliente, origem=Consentimento.ORIGEM_DESCADASTRO, texto=consentimentos.TEXTO_DESCADASTRO,
            pares=consentimentos.PARES_DESCADASTRO, request=request,
        )
        links.marcar_usado(link)
        logger.info('Cliente %s opt-out via token.', cliente.pk)
        return cliente

    @classmethod
    @transaction.atomic
    def esquecer_cliente(cls, cliente: Cliente, usuario=None, request=None) -> None:
        """Direito ao esquecimento: PSEUDONIMIZA o titular (art. 13 §4) e faz soft-delete.

        ``CALL sp_anonimizar_cliente`` — a procedure e a fonte UNICA (spec padrao-sql §5.6):
        cadastro (com codigo de indicacao e quem indicou), consentimentos (revoga e tira
        IP/user-agent), IP/user-agent do aceite LGPD, links, OTP, lista de espera (inclusive a
        inscricao anonima pelo celular), texto das notificacoes, fichas de pedido nao realizado,
        depoimento, nome e detalhes LGPD nas trilhas e no django_admin_log, e o registro com autor
        e IP. O registro de saude fica pelo prazo (expurgo aos 20 anos). Exige Postgres.
        """
        autor, ip = autor_e_ip(usuario, request)
        chamar('sp_anonimizar_cliente', cliente.pk, getattr(autor, 'pk', None), ip)
        cliente.refresh_from_db()
        logger.info('Cliente %s pseudonimizado (direito ao esquecimento).', cliente.pk)

    @classmethod
    def candidatos_retencao(cls) -> list[int]:
        """Ids que a proxima retencao pseudonimiza (``fn_clientes_para_anonimizar``, a mesma funcao
        que o ``sp_aplicar_retencao_lgpd`` usa).

        Regra: ativo criado ha mais de RETENCAO_CLIENTE_INATIVO_DIAS sem atendimento nesse periodo,
        ou soft-deletado ha mais da carencia; nunca quem ja foi pseudonimizado. Retem enquanto o
        registro de saude esta na janela (REALIZADO, versao do prontuario ou prontuario editado nos
        ultimos RETENCAO_ATENDIMENTO_SAUDE_DIAS) e enquanto o pacote nao passou
        RETENCAO_PACOTE_APOS_EXPIRAR_DIAS do fim (cancelamento, validade ou compra). Aceite de termo
        NAO retem (todo booking online grava o aceite LGPD).
        """
        exigir_postgres('fn_clientes_para_anonimizar')
        prazos = prazos_retencao()
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM fn_clientes_para_anonimizar(%s, %s, %s, %s)',
                [prazos['cliente_inativo'], prazos['carencia_soft_delete'], prazos['saude'],
                 prazos['pacote_apos_expirar']],
            )
            return [pk for (pk,) in cursor.fetchall()]

    @classmethod
    def aplicar_retencao(cls) -> dict:
        """Retencao LGPD do job lgpd_purgar: ``CALL sp_aplicar_retencao_lgpd`` (fonte unica) —
        pseudonimiza os candidatos (candidatos_retencao), apaga as fichas de pedido nao realizado
        ha mais de RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS (CANCELADO, PENDENTE vencido, convite nunca
        respondido ou vazio de horario passado; ficha RESPONDIDA de FALTOU/REAGENDADO fica) e as
        inscricoes de espera vencidas. {'clientes', 'fichas', 'esperas'}. O expurgo do registro de
        saude aos 20 anos e a etapa 'saude' do job_housekeeping (sp_purgar_dados_expirados).

        Em autocommit (cron) a procedure confirma cliente a cliente (``COMMIT`` por
        pseudonimizacao: um aborto no meio preserva os ja feitos); dentro de uma transacao (testes)
        roda inteira na transacao do chamador.
        """
        confirmar_por_cliente = not connection.in_atomic_block
        prazos = prazos_retencao()
        clientes, fichas, esperas = chamar(
            'sp_aplicar_retencao_lgpd',
            prazos['cliente_inativo'], prazos['carencia_soft_delete'], prazos['saude'],
            prazos['ficha_sem_atendimento'], prazos['pacote_apos_expirar'],
            confirmar_por_cliente, None, None, None,
        )
        logger.info('sp_aplicar_retencao_lgpd: %s cliente(s), %s ficha(s), %s inscricao(oes).',
                    clientes, fichas, esperas)
        return {'clientes': clientes or 0, 'fichas': fichas or 0, 'esperas': esperas or 0}
