"""Servico de LGPD — DSAR (export), unsubscribe, esquecimento e retencao."""
import logging
import os
from typing import Any

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


class LgpdService:
    # Cliente sem atendimento ha 5 anos e anonimizado (PROJECT.md §Retencao,
    # politica de privacidade). Ajustavel por env sem deploy de codigo.
    RETENCAO_CLIENTE_INATIVO_DIAS = int(os.environ.get('LGPD_RETENCAO_CLIENTE_DIAS', 365 * 5))
    # Soft-delete vira anonimizacao apos 30 dias (PROJECT.md §Direitos).
    CARENCIA_SOFT_DELETE_DIAS = 30
    # Atendimento REALIZADO (registro de saude) e retido por 20 anos.
    RETENCAO_ATENDIMENTO_SAUDE_DIAS = 365 * 20
    # Ficha de anamnese de pedido que nao virou atendimento (rejeitado,
    # cancelado, pendente vencido, convite nunca respondido): fim da
    # finalidade (LGPD art. 15/16) -> exclusao apos N dias.
    RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS = 90

    @staticmethod
    def exportar_dados_cliente(cliente: Cliente) -> dict[str, Any]:
        """DSAR (art. 18 II/V) — exporta todos os dados do titular em dict JSON-friendly."""
        from aranha_estetica.models import (
            AceiteTermo, AnotacaoSessao, Carteira, ListaEspera, Notificacao,
            Prontuario, ProntuarioVersao, RespostaAnamnese,
        )
        from aranha_estetica.services import consentimentos

        atendimentos = (
            Atendimento.objects
            .filter(cliente=cliente)
            .select_related('procedimento', 'profissional')
            .order_by('-data_hora_inicio')
        )
        avaliacoes = AvaliacaoNPS.objects.filter(atendimento__cliente=cliente).order_by('-criado_em')

        prontuario = Prontuario.objects.filter(cliente=cliente).first()
        prontuario_dict = None
        if prontuario:
            from aranha_estetica.utils.saude import perguntas_prontuario, rotulo_da_chave

            extras = prontuario.respostas_extras if isinstance(prontuario.respostas_extras, dict) else {}
            rotulos = {p['key']: p.get('label') for p in perguntas_prontuario()} if extras else {}
            prontuario_dict = {
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
        # Historico do prontuario: estado ANTERIOR a cada edicao da equipe.
        prontuario_versoes = [
            {'registrado_em': _iso(v.criado_em), 'dados': v.dados}
            for v in ProntuarioVersao.objects.filter(prontuario__cliente=cliente).order_by('-criado_em', '-pk')
        ]

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

        carteira = None
        carteira_obj = Carteira.objects.filter(cliente=cliente).first()
        if carteira_obj is not None:
            carteira = {
                'saldo': _dec(carteira_obj.saldo),
                'movimentos': [
                    {
                        'tipo': m.tipo,
                        'origem': m.origem,
                        'valor': _dec(m.valor),
                        'saldo_resultante': _dec(m.saldo_resultante),
                        'criado_em': _iso(m.criado_em),
                    }
                    for m in carteira_obj.movimentos.order_by('-criado_em')
                ],
            }

        return {
            'pessoal': {
                'nome': cliente.nome,
                'data_nascimento': _iso(cliente.data_nascimento),
                'cpf': cliente.cpf,
                'email': cliente.email,
                'telefone': cliente.telefone,
                'cep': cliente.cep,
                'endereco': cliente.endereco,
                'criado_em': _iso(cliente.criado_em),
            },
            'preferencias': {
                'bloqueado_online': cliente.bloqueado_online,
            },
            # Estado vigente por finalidade/canal + historico completo (cada
            # concessao/revogacao com data, origem, texto exibido e IP).
            'consentimentos': {
                'vigentes': [
                    {'finalidade': fin, 'canal': canal, 'concedido': concedido}
                    for (fin, canal), concedido in consentimentos.estado_atual(cliente).items()
                ],
                'historico': consentimentos.historico(cliente),
            },
            'atendimentos': [
                {
                    'data': _iso(a.data_hora_inicio),
                    'procedimento': a.procedimento.nome if a.procedimento_id else None,
                    'profissional': a.profissional.nome if a.profissional_id else None,
                    'status': a.status,
                    'valor_cobrado': _dec(a.valor_cobrado),
                } for a in atendimentos
            ],
            'avaliacoes_nps': [
                {
                    'nota': a.nota,
                    'comentario': a.comentario,
                    'respondida_em': _iso(a.criado_em),
                    'autoriza_publicacao': a.autoriza_publicacao,
                }
                for a in avaliacoes
            ],
            'prontuario': prontuario_dict,
            'prontuario_versoes': prontuario_versoes,
            'anotacoes_sessao': [
                {'atendimento_data': _iso(n.atendimento.data_hora_inicio), 'texto': n.texto,
                 'criado_em': _iso(n.criado_em)}
                for n in (AnotacaoSessao.objects.filter(atendimento__cliente=cliente)
                          .select_related('atendimento').order_by('-criado_em'))
            ],
            'anamneses': [
                {'formulario': r.formulario.nome, 'respostas': r.respostas_json,
                 'criado_em': _iso(r.criado_em), 'respondida_em': _iso(r.respondida_em)}
                for r in (RespostaAnamnese.objects.filter(cliente=cliente)
                          .select_related('formulario').order_by('-criado_em'))
            ],
            'aceites_termos': [
                {'termo': a.versao_termo.titulo, 'tipo': a.versao_termo.tipo,
                 'versao': a.versao_termo.versao, 'ip': a.ip, 'aceito_em': _iso(a.criado_em),
                 'conteudo_sha256': a.conteudo_sha256}
                for a in (AceiteTermo.objects.filter(cliente=cliente)
                          .select_related('versao_termo').order_by('-criado_em'))
            ],
            'pacotes': pacotes,
            'carteira': carteira,
            'lista_espera': [
                {'procedimento': e.procedimento.nome, 'data_desejada': _iso(e.data_desejada),
                 'turno': e.turno_desejado, 'notificado': e.notificado,
                 'profissional_desejado': (
                     e.profissional_desejado.nome if e.profissional_desejado else None
                 ),
                 'email_contato': e.email_contato,
                 'criado_em': _iso(e.criado_em)}
                for e in (ListaEspera.objects.filter(cliente=cliente)
                          .select_related('procedimento', 'profissional_desejado').order_by('-criado_em'))
            ],
            'notificacoes': [
                {'tipo': n.tipo, 'canal': n.canal, 'status': n.status,
                 'enviado_em': _iso(n.enviado_em), 'criado_em': _iso(n.criado_em)}
                for n in Notificacao.objects.filter(atendimento__cliente=cliente).order_by('-criado_em')
            ],
        }

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
        """Direito ao esquecimento: anonimiza PII do titular e faz soft-delete.

        ``CALL sp_anonimizar_cliente`` — a procedure e a fonte UNICA (spec padrao-sql §5.6):
        cadastro, consentimentos (revoga e tira IP/user-agent), links, OTP, lista de espera,
        texto das notificacoes, fichas de pedido nao realizado, depoimento, nome nas trilhas e no
        django_admin_log, e o registro LGPD com autor e IP. Exige Postgres (utils/banco.chamar).
        """
        autor, ip = autor_e_ip(usuario, request)
        chamar('sp_anonimizar_cliente', cliente.pk, getattr(autor, 'pk', None), ip)
        cliente.refresh_from_db()
        logger.info('Cliente %s anonimizado (direito ao esquecimento).', cliente.pk)

    @classmethod
    def candidatos_retencao(cls) -> list[int]:
        """Ids que a proxima retencao anonimiza (``fn_clientes_para_anonimizar``, a mesma funcao
        que o ``sp_aplicar_retencao_lgpd`` usa).

        Regra: ativo criado ha mais de N anos sem atendimento nesse periodo, ou soft-deletado ha
        mais de 30 dias; nunca quem ja foi anonimizado, nem quem tem prontuario com conteudo ou com
        historico (versoes), pacote comprado ou atendimento REALIZADO nos ultimos 20 anos. Aceite
        de termo NAO retem (todo booking online grava o aceite LGPD).
        """
        exigir_postgres('fn_clientes_para_anonimizar')
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM fn_clientes_para_anonimizar(%s, %s, %s)',
                [cls.RETENCAO_CLIENTE_INATIVO_DIAS, cls.CARENCIA_SOFT_DELETE_DIAS,
                 cls.RETENCAO_ATENDIMENTO_SAUDE_DIAS],
            )
            return [pk for (pk,) in cursor.fetchall()]

    @classmethod
    def aplicar_retencao(cls) -> dict:
        """Retencao LGPD do job lgpd_purgar: ``CALL sp_aplicar_retencao_lgpd`` (fonte unica) —
        anonimiza os candidatos (candidatos_retencao), apaga as fichas de pedido nao realizado ha
        mais de 90 dias (CANCELADO, PENDENTE vencido, convite nunca respondido ou vazio de horario
        passado; ficha RESPONDIDA de FALTOU/REAGENDADO fica) e as inscricoes de espera vencidas.
        {'clientes', 'fichas', 'esperas'}.

        Em autocommit (cron) a procedure confirma cliente a cliente (``COMMIT`` por
        anonimizacao: um aborto no meio preserva os ja feitos); dentro de uma transacao (testes)
        roda inteira na transacao do chamador.
        """
        confirmar_por_cliente = not connection.in_atomic_block
        clientes, fichas, esperas = chamar(
            'sp_aplicar_retencao_lgpd',
            cls.RETENCAO_CLIENTE_INATIVO_DIAS, cls.CARENCIA_SOFT_DELETE_DIAS,
            cls.RETENCAO_ATENDIMENTO_SAUDE_DIAS, cls.RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS,
            confirmar_por_cliente, None, None, None,
        )
        logger.info('sp_aplicar_retencao_lgpd: %s cliente(s), %s ficha(s), %s inscricao(oes).',
                    clientes, fichas, esperas)
        return {'clientes': clientes or 0, 'fichas': fichas or 0, 'esperas': esperas or 0}
