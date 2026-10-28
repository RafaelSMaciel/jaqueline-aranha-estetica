"""Fumaca de TODAS as rotas: nenhuma pagina quebra e cada papel so ve o que pode.

Cada rota nomeada (aranha:* e as do projeto) esta em ROTAS com os argumentos de exemplo e o
perfil de acesso; rota nova sem entrada reprova em test_toda_rota_esta_no_mapa. Para cada papel
(anonimo, ADMIN, PROFISSIONAL) a resposta nunca e 500 (o client do teste propaga a excecao), as
paginas do papel certo abrem com 200 e as privadas nunca abrem (200) para quem nao pode.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from aranha_estetica.models import (
    AgendaExcecao, AvaliacaoNPS, Configuracao, FormularioAnamnese, LinkAcesso, ListaEspera, MovimentoComissao,
    Promocao, RegraComissao, RespostaAnamnese, Usuario,
)

from .factories import (
    criar_atendimento, criar_cliente, criar_comissao, criar_compra_pacote, criar_link,
    criar_notificacao_com_link, criar_pacote, criar_procedimento, criar_profissional, limpar_feriados,
)

# Perfis de acesso (o que cada papel recebe num GET):
PUBLICA = 'publica'            # qualquer um: 200
STAFF = 'staff'                # so ADMIN/equipe: 200; anonimo vai ao login; profissional nunca 200
PROFISSIONAL = 'profissional'  # profissional e equipe: 200; anonimo vai ao login
SO_POST = 'so_post'            # acao (POST): GET nunca da 200 (405 ou redirect)
OUTRA = 'outra'                # redirect/JSON/infra: so nao pode quebrar (e privada nao abre p/ anonimo)

PRIVADOS = ('/painel/', '/profissional/', '/django-admin-sv/')
PUBLICOS_EM_PREFIXO_PRIVADO = {'aranha:manifest_admin'}   # manifesto do PWA: JSON estatico


def _d(**kw):
    return lambda dados: {k: (v(dados) if callable(v) else v) for k, v in kw.items()}


SEM = _d()

# nome -> (kwargs(dados), query string, perfil)
ROTAS = {
    # ─── site publico ───
    'aranha:inicio': (SEM, '', PUBLICA),
    'aranha:quem_somos': (SEM, '', PUBLICA),
    'aranha:termos_uso': (SEM, '', PUBLICA),
    'aranha:politica_privacidade': (SEM, '', PUBLICA),
    'aranha:agenda_contato': (SEM, '', PUBLICA),
    'aranha:promocoes': (SEM, '', PUBLICA),
    'aranha:favicon': (SEM, '', OUTRA),
    'aranha:servicos_faciais': (SEM, '', PUBLICA),
    'aranha:servicos_corporais': (SEM, '', PUBLICA),
    'aranha:equipe': (SEM, '', PUBLICA),
    'aranha:especialidades': (SEM, '', PUBLICA),
    'aranha:depoimentos': (SEM, '', PUBLICA),
    'aranha:galeria': (SEM, '', PUBLICA),
    'aranha:servico_detalhe': (_d(slug=lambda d: d['proc'].slug), '', PUBLICA),
    'aranha:lista_espera_publica': (SEM, '', PUBLICA),
    'aranha:lista_espera_sucesso': (SEM, '', OUTRA),
    'aranha:manifest': (SEM, '', OUTRA),
    'aranha:manifest_admin': (SEM, '', OUTRA),
    'aranha:sw': (SEM, '', OUTRA),
    'django.contrib.sitemaps.views.sitemap': (SEM, '', OUTRA),
    # ─── agendamento e portal da cliente ───
    'aranha:agendamento_publico': (SEM, lambda d: f"procedimento={d['proc'].pk}", PUBLICA),
    'aranha:agendar_por_profissional': (_d(slug=lambda d: d['prof'].slug), '', OUTRA),
    'aranha:ics_feed_profissional': (_d(slug=lambda d: d['prof'].slug), lambda d: f"token={d['tok_ics']}", OUTRA),
    'aranha:embed_agendar': (SEM, '', PUBLICA),
    'aranha:confirmar_agendamento': (SEM, '', SO_POST),
    'aranha:agendamento_sucesso': (SEM, '', OUTRA),
    'aranha:solicitar_otp_agendamento': (SEM, '', SO_POST),
    'aranha:verificar_otp_agendamento': (SEM, '', SO_POST),
    'aranha:confirmar_presenca': (_d(token=lambda d: d['tok_confirmar']), '', PUBLICA),
    'aranha:reagendar_agendamento': (_d(token=lambda d: d['tok_reagendar']), '', PUBLICA),
    'aranha:reagendar_horarios': (_d(token=lambda d: d['tok_reagendar']), lambda d: f"data={d['amanha']}", OUTRA),
    'aranha:meus_agendamentos': (SEM, '', PUBLICA),
    'aranha:meus_agendamentos_enviar_otp': (SEM, '', SO_POST),
    'aranha:meus_agendamentos_verificar_otp': (SEM, '', SO_POST),
    'aranha:meus_agendamentos_logout': (SEM, '', OUTRA),
    'aranha:reagendar_agendamento_portal': (_d(pk=lambda d: d['at'].pk), '', OUTRA),
    'aranha:reagendar_horarios_portal': (_d(pk=lambda d: d['at'].pk), lambda d: f"data={d['amanha']}", OUTRA),
    'aranha:api_dias_disponiveis': (SEM, lambda d: f"mes={d['mes']}&procedimento_id={d['proc'].pk}", OUTRA),
    'aranha:api_horarios_disponiveis': (
        SEM, lambda d: f"data={d['amanha']}&procedimento_id={d['proc'].pk}", OUTRA),
    'aranha:cancelar_agendamento': (SEM, '', SO_POST),
    'aranha:nps_web': (_d(token=lambda d: d['tok_nps']), '', PUBLICA),
    'aranha:nps_obrigado': (_d(token=lambda d: d['tok_nps']), '', PUBLICA),
    'aranha:anamnese_publica': (_d(token=lambda d: d['tok_anamnese']), '', PUBLICA),
    'aranha:anamnese_obrigado': (SEM, '', PUBLICA),
    'aranha:pesquisa_publica': (_d(token=lambda d: d['tok_pesquisa']), '', PUBLICA),
    'aranha:pesquisa_obrigado': (SEM, '', PUBLICA),
    'aranha:termo_assinatura': (_d(token=lambda d: d['tok_termo']), '', PUBLICA),
    'aranha:lgpd_meus_dados': (SEM, '', OUTRA),
    'aranha:lgpd_unsubscribe': (_d(token=lambda d: d['tok_descadastro']), '', PUBLICA),
    'aranha:lgpd_aceitar_cookies': (SEM, '', SO_POST),
    # ─── conta da equipe ───
    'aranha:usuario_login': (SEM, '', OUTRA),
    'aranha:usuario_logout': (SEM, '', OUTRA),
    'aranha:password_reset': (SEM, '', PUBLICA),
    'aranha:password_reset_done': (SEM, '', PUBLICA),
    'aranha:password_reset_confirm': (_d(uidb64=lambda d: d['uidb64'], token=lambda d: d['reset_token']), '', OUTRA),
    'aranha:password_reset_complete': (SEM, '', PUBLICA),
    'aranha:admin_2fa_setup': (SEM, '', OUTRA),
    'aranha:admin_2fa_verify': (SEM, '', OUTRA),
    'aranha:admin_2fa_challenge': (SEM, '', OUTRA),
    # ─── painel ───
    'aranha:painel': (SEM, '', OUTRA),
    'aranha:painel_overview': (SEM, '', STAFF),
    'aranha:painel_agendamentos': (SEM, '', STAFF),
    'aranha:painel_clientes': (SEM, '', STAFF),
    'aranha:painel_profissionais': (SEM, '', STAFF),
    'aranha:prontuario_consentimento': (SEM, '', STAFF),
    'aranha:profissional_cadastro': (SEM, '', STAFF),
    'aranha:profissional_editar': (_d(pk=lambda d: d['prof'].pk), '', STAFF),
    'aranha:profissional_rotacionar_ics': (_d(pk=lambda d: d['prof'].pk), '', SO_POST),
    'aranha:exportar_relatorio_excel': (SEM, '', OUTRA),
    'aranha:admin_promocoes': (SEM, '', STAFF),
    'aranha:admin_criar_promocao': (SEM, '', SO_POST),   # formulario em modal na lista
    'aranha:admin_editar_promocao': (_d(pk=lambda d: d['promo'].pk), '', SO_POST),
    'aranha:admin_excluir_promocao': (_d(pk=lambda d: d['promo'].pk), '', SO_POST),
    'aranha:admin_disparar_promocao': (_d(pk=lambda d: d['promo'].pk), '', SO_POST),
    'aranha:dashboard_financeiro': (SEM, '', STAFF),
    'aranha:painel_nps': (SEM, '', STAFF),
    'aranha:admin_nps_publicacao': (_d(pk=lambda d: d['nps'].pk), '', SO_POST),
    'aranha:painel_comissoes': (SEM, '', STAFF),
    'aranha:admin_comissao_pagar': (_d(pk=lambda d: d['comissao'].pk), '', SO_POST),
    'aranha:admin_comissao_repasse': (SEM, '', SO_POST),
    'aranha:painel_comissao_repasse': (_d(pk=lambda d: d['pagamento'].pk), '', STAFF),
    'aranha:painel_comissao_regras': (SEM, '', STAFF),
    'aranha:admin_comissao_regra_criar': (SEM, '', SO_POST),
    'aranha:admin_comissao_regra_ativo': (_d(pk=lambda d: d['regra'].pk), '', SO_POST),
    'aranha:admin_comissao_regra_excluir': (_d(pk=lambda d: d['regra'].pk), '', SO_POST),
    'aranha:painel_notificacoes': (SEM, '', STAFF),
    'aranha:prontuario_detalhe': (_d(cliente_id=lambda d: d['cliente'].pk), '', PROFISSIONAL),
    'aranha:prontuario_salvar': (_d(cliente_id=lambda d: d['cliente'].pk), '', SO_POST),
    'aranha:anotacao_sessao_salvar': (_d(atendimento_id=lambda d: d['realizado'].pk), '', SO_POST),
    'aranha:admin_pacotes': (SEM, '', STAFF),
    'aranha:admin_criar_pacote': (SEM, '', SO_POST),   # formulario em modal na lista
    'aranha:admin_editar_pacote': (_d(pk=lambda d: d['pacote'].pk), '', SO_POST),
    'aranha:admin_vender_pacote': (SEM, '', SO_POST),
    'aranha:admin_cancelar_compra_pacote': (_d(pk=lambda d: d['compra'].pk), '', SO_POST),
    'aranha:admin_prorrogar_compra_pacote': (_d(pk=lambda d: d['compra'].pk), '', SO_POST),
    'aranha:admin_bloqueios': (SEM, '', STAFF),
    'aranha:admin_criar_bloqueio': (SEM, '', OUTRA),
    'aranha:admin_excluir_bloqueio': (_d(bloqueio_id=lambda d: d['bloqueio'].pk), '', SO_POST),
    'aranha:admin_procedimentos': (SEM, '', STAFF),
    'aranha:admin_criar_procedimento': (SEM, '', OUTRA),
    'aranha:admin_editar_procedimento': (_d(pk=lambda d: d['proc'].pk), '', OUTRA),
    'aranha:admin_cliente_detalhe': (_d(pk=lambda d: d['cliente'].pk), '', STAFF),
    'aranha:admin_lista_espera': (SEM, '', STAFF),
    'aranha:admin_notificar_espera': (_d(pk=lambda d: d['espera'].pk), '', SO_POST),
    'aranha:admin_termos': (SEM, '', STAFF),
    'aranha:admin_criar_termo': (SEM, '', SO_POST),   # formulario em modal na lista
    'aranha:admin_termos_compliance': (SEM, '', STAFF),
    'aranha:admin_gerar_link_termo': (_d(pk=lambda d: d['at'].pk), '', OUTRA),
    'aranha:admin_auditoria': (SEM, '', STAFF),
    'aranha:admin_atualizar_status': (SEM, '', SO_POST),
    'aranha:admin_aprovar_agendamento': (_d(pk=lambda d: d['pendente'].pk), '', SO_POST),
    'aranha:admin_rejeitar_agendamento': (_d(pk=lambda d: d['pendente'].pk), '', SO_POST),
    'aranha:admin_bulk_agendamentos': (SEM, '', SO_POST),
    'aranha:admin_agendamento_novo': (SEM, '', STAFF),
    'aranha:admin_atendimento_valor': (_d(pk=lambda d: d['realizado'].pk), '', SO_POST),
    'aranha:admin_desfazer_realizacao': (_d(pk=lambda d: d['realizado'].pk), '', SO_POST),
    'aranha:admin_link_ficha': (_d(pk=lambda d: d['at'].pk), '', SO_POST),
    'aranha:painel_retornos': (SEM, '', STAFF),
    'aranha:admin_agendar_retorno': (_d(pk=lambda d: d['realizado'].pk), '', OUTRA),
    'aranha:admin_usuarios': (SEM, '', STAFF),
    'aranha:admin_criar_usuario': (SEM, '', STAFF),
    'aranha:admin_editar_usuario': (_d(pk=lambda d: d['usuario_prof'].pk), '', STAFF),
    'aranha:admin_resetar_senha_usuario': (_d(pk=lambda d: d['usuario_prof'].pk), '', SO_POST),
    'aranha:admin_desativar_usuario': (_d(pk=lambda d: d['usuario_prof'].pk), '', SO_POST),
    'aranha:admin_configuracoes': (SEM, '', STAFF),
    'aranha:admin_criar_configuracao': (SEM, '', SO_POST),   # formulario em modal na lista
    'aranha:admin_editar_configuracao': (_d(pk=lambda d: d['config'].pk), '', SO_POST),
    'aranha:admin_excluir_configuracao': (_d(pk=lambda d: d['config'].pk), '', SO_POST),
    'aranha:admin_branding': (SEM, '', STAFF),
    'aranha:admin_calendar': (SEM, '', STAFF),
    'aranha:admin_calendar_events': (SEM, lambda d: f"start={d['hoje']}&end={d['semana']}", OUTRA),
    'aranha:admin_calendar_mover': (SEM, '', SO_POST),
    'aranha:admin_email_preview': (SEM, '', OUTRA),
    'aranha:admin_email_preview_nome': (_d(nome='otp'), '', OUTRA),
    'aranha:admin_anamneses': (SEM, '', STAFF),
    'aranha:admin_anamnese_criar': (SEM, '', STAFF),
    'aranha:admin_anamnese_editar': (_d(pk=lambda d: d['form'].pk), '', STAFF),
    'aranha:admin_anamnese_excluir': (_d(pk=lambda d: d['form'].pk), '', SO_POST),
    'aranha:admin_anamnese_respostas': (_d(pk=lambda d: d['form'].pk), '', STAFF),
    'aranha:admin_excecoes': (_d(prof_id=lambda d: d['prof'].pk), '', STAFF),
    'aranha:admin_excecao_criar': (_d(prof_id=lambda d: d['prof'].pk), '', SO_POST),
    'aranha:admin_excecao_excluir': (_d(pk=lambda d: d['folga'].pk), '', OUTRA),
    'aranha:gcal_connect': (_d(prof_id=lambda d: d['prof'].pk), '', OUTRA),
    'aranha:gcal_callback': (SEM, '', OUTRA),
    'aranha:gcal_pull': (_d(prof_id=lambda d: d['prof'].pk), '', SO_POST),
    # ─── portal do profissional ───
    'aranha:profissional_agenda': (SEM, '', PROFISSIONAL),
    'aranha:profissional_marcar_realizado': (_d(pk=lambda d: d['at'].pk), '', SO_POST),
    'aranha:profissional_anotar': (_d(pk=lambda d: d['realizado'].pk), '', OUTRA),
    'aranha:profissional_aprovar': (_d(pk=lambda d: d['pendente'].pk), '', SO_POST),
    'aranha:profissional_rejeitar': (_d(pk=lambda d: d['pendente'].pk), '', SO_POST),
    'aranha:profissional_link_calendario': (SEM, '', SO_POST),
    'aranha:profissional_comissoes': (SEM, '', PROFISSIONAL),
    # ─── infra ───
    'aranha:healthcheck': (SEM, '', OUTRA),
    'aranha:liveness': (SEM, '', PUBLICA),
    'aranha:cron_run_job': (_d(job_name='emails'), '', OUTRA),
    'aranha:whatsapp_webhook': (SEM, '', OUTRA),
    'aranha:zenvia_sms_webhook': (SEM, '', OUTRA),
    'aranha:webpush_public_key': (SEM, '', OUTRA),
    'aranha:webpush_subscribe': (SEM, '', SO_POST),
    'aranha:webpush_unsubscribe': (SEM, '', SO_POST),
}


def _nomes_das_rotas():
    """Todas as rotas nomeadas do projeto (namespace:nome), exceto o Django admin."""
    nomes = set()

    def visitar(padroes, prefixo):
        for p in padroes:
            if isinstance(p, URLResolver):
                if p.app_name == 'admin':
                    continue
                visitar(p.url_patterns, f'{p.namespace}:' if p.namespace else prefixo)
            elif isinstance(p, URLPattern) and p.name:
                nomes.add(f'{prefixo}{p.name}')

    visitar(get_resolver().url_patterns, '')
    return nomes


def montar_dados():
    """Um exemplo de cada objeto que as rotas pedem (tambem usado pela validacao visual)."""
    limpar_feriados()
    agora = timezone.now()
    hoje = timezone.localdate()
    prof = criar_profissional('Jaqueline Aranha')
    proc = criar_procedimento(profissional=prof)
    cliente = criar_cliente(email='maria@exemplo.com')
    # longe o bastante p/ o link de reagendar valer (antecedencia minima) a qualquer hora do dia
    at = criar_atendimento(cliente, prof, proc, data_hora=timezone.make_aware(
        datetime.combine(hoje + timedelta(days=4), time(10, 0))))
    pendente = criar_atendimento(cliente, prof, proc, data_hora=agora + timedelta(days=3), status='PENDENTE')
    realizado = criar_atendimento(cliente, prof, proc, data_hora=agora - timedelta(days=2), status='REALIZADO')
    pacote = criar_pacote(procedimento=proc)
    form = FormularioAnamnese.objects.create(
        nome='Anamnese', tipo='ANAMNESE', escopo='GLOBAL',
        schema_json=[{'key': 'alergia', 'tipo': 'text', 'label': 'Alergia?'}])
    pesquisa = FormularioAnamnese.objects.create(
        nome='Pesquisa', tipo='PESQUISA', escopo='GLOBAL',
        schema_json=[{'key': 'gostou', 'tipo': 'text', 'label': 'O que achou?'}])
    resposta_anamnese = RespostaAnamnese.objects.create(formulario=form, cliente=cliente, atendimento=at)
    resposta_pesquisa = RespostaAnamnese.objects.create(formulario=pesquisa, cliente=cliente, atendimento=realizado)
    dados = {
        'prof': prof, 'proc': proc, 'cliente': cliente, 'at': at, 'pendente': pendente, 'realizado': realizado,
        'pacote': pacote, 'compra': criar_compra_pacote(cliente, pacote), 'form': form,
        'promo': Promocao.objects.create(
            nome='Semana da pele', procedimento=proc, desconto_percentual=Decimal('10'),
            data_inicio=hoje, data_fim=hoje + timedelta(days=10)),
        'bloqueio': AgendaExcecao.objects.create(
            tipo=AgendaExcecao.TIPO_BLOQUEIO, profissional=prof,
            data_hora_inicio=agora + timedelta(days=5), data_hora_fim=agora + timedelta(days=5, hours=1)),
        'folga': AgendaExcecao.objects.create(
            tipo=AgendaExcecao.TIPO_FOLGA, profissional=prof,
            data_inicio=hoje + timedelta(days=6), data_fim=hoje + timedelta(days=6)),
        'config': Configuracao.objects.create(chave='teste_rotas', valor='1'),
        'nps': AvaliacaoNPS.objects.create(atendimento=realizado, nota=10),
        'comissao': criar_comissao(realizado),
        'pagamento': MovimentoComissao.objects.get(tipo='PAGAMENTO', movimento_origem=criar_comissao(
            criar_atendimento(cliente, prof, proc, data_hora=agora - timedelta(days=3), status='REALIZADO'),
            status=MovimentoComissao.STATUS_PAGA)),
        'regra': RegraComissao.objects.create(percentual=Decimal('30'), ativo=True),
        'espera': ListaEspera.objects.create(
            cliente=cliente, procedimento=proc, data_desejada=hoje + timedelta(days=3), turno_desejado='MANHA'),
        'tok_confirmar': criar_notificacao_com_link(at, 'LEMBRETE')[1],
        'tok_reagendar': criar_link(LinkAcesso.GERENCIAR_AGENDAMENTO, at),
        'tok_nps': criar_notificacao_com_link(realizado, 'NPS')[1],
        'tok_termo': criar_notificacao_com_link(at, 'TERMO')[1],
        'tok_anamnese': criar_link(LinkAcesso.FORMULARIO, resposta_anamnese),
        'tok_pesquisa': criar_link(LinkAcesso.FORMULARIO, resposta_pesquisa),
        'tok_descadastro': criar_link(LinkAcesso.DESCADASTRO, cliente),
        'tok_ics': criar_link(LinkAcesso.FEED_ICS, prof),
        'hoje': hoje.isoformat(), 'amanha': (hoje + timedelta(days=1)).isoformat(),
        'semana': (hoje + timedelta(days=7)).isoformat(), 'mes': hoje.strftime('%Y-%m'),
    }
    admin = Usuario.objects.create_user(email='admin@rotas.test', password='x', papel=Usuario.PAPEL_ADMIN)
    dados['usuario_prof'] = usuario_prof = Usuario.objects.create_user(
        email='prof@rotas.test', password='x', papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof)
    dados['uidb64'] = urlsafe_base64_encode(force_bytes(admin.pk))
    dados['reset_token'] = default_token_generator.make_token(admin)
    return dados, admin, usuario_prof


@override_settings(RATELIMIT_ENABLE=False)
class RotasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dados, cls.admin, cls.usuario_prof = montar_dados()

    def _url(self, nome):
        kwargs, query, _ = ROTAS[nome]
        url = reverse(nome, kwargs=kwargs(self.dados))
        query = query(self.dados) if callable(query) else query
        return f'{url}?{query}' if query else url

    def _get_como(self, papel, nome):
        self.client.logout()
        if papel == 'admin':
            self.client.force_login(self.admin)
        elif papel == 'profissional':
            self.client.force_login(self.usuario_prof)
        return self.client.get(self._url(nome))

    def test_toda_rota_esta_no_mapa(self):
        faltando = sorted(_nomes_das_rotas() - set(ROTAS))
        self.assertEqual(faltando, [], 'rota nova: inclua em tests/test_rotas.py ROTAS')
        sobrando = sorted(set(ROTAS) - _nomes_das_rotas())
        self.assertEqual(sobrando, [], 'rota removida/renomeada: atualize ROTAS')

    def test_cada_papel_ve_so_o_que_pode(self):
        for nome, (_, _, perfil) in sorted(ROTAS.items()):
            for papel in ('anonimo', 'admin', 'profissional'):
                with self.subTest(rota=nome, papel=papel):
                    resp = self._get_como(papel, nome)
                    self.assertLess(resp.status_code, 500)
                    caminho = self._url(nome)
                    if perfil == PUBLICA or (perfil == STAFF and papel == 'admin') or (
                            perfil == PROFISSIONAL and papel == 'profissional'):
                        self.assertEqual(resp.status_code, 200, f'{caminho} como {papel}')
                    elif perfil == SO_POST:
                        self.assertNotEqual(resp.status_code, 200, f'{caminho} (acao) abriu com GET')
                    elif perfil == STAFF or (perfil == PROFISSIONAL and papel == 'anonimo') or (
                            papel == 'anonimo' and caminho.startswith(PRIVADOS)
                            and nome not in PUBLICOS_EM_PREFIXO_PRIVADO):
                        self.assertNotEqual(resp.status_code, 200, f'{caminho} abriu para {papel}')
                        if papel == 'anonimo':
                            self.assertIn(resp.status_code, (302, 403, 404), caminho)

    def test_paginas_html_abrem_completas(self):
        """200 de pagina HTML vem com o documento inteiro (sem template cortado no meio)."""
        for nome, (_, _, perfil) in sorted(ROTAS.items()):
            if perfil not in (PUBLICA, STAFF, PROFISSIONAL):
                continue
            papel = {STAFF: 'admin', PROFISSIONAL: 'profissional'}.get(perfil, 'anonimo')
            with self.subTest(rota=nome):
                resp = self._get_como(papel, nome)
                if 'text/html' not in resp.get('Content-Type', ''):
                    continue
                html = resp.content.decode()
                self.assertIn('<html', html)
                self.assertIn('</html>', html)
