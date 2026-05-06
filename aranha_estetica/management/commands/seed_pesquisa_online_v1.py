"""Seed FormularioAnamnese tipo PESQUISA — V1 atendimento online.

Replica perguntas do Google Form fornecido pela cliente.
Cria/atualiza WorkflowRegra `pesquisa_online_pos_realizado` que dispara
2h apos REALIZADO em atendimentos de procedimento.modalidade=ONLINE.

Idempotente: pode rodar varias vezes sem duplicar.

Uso:
  python manage.py seed_pesquisa_online_v1
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from aranha_estetica.models import FormularioAnamnese
from aranha_estetica.models.workflow import WorkflowRegra


SCHEMA_V1 = [
    {
        'key': 'genero',
        'tipo': 'select',
        'label': 'Qual e o seu genero?',
        'opcoes': ['Mulher', 'Homem', 'Nao binario', 'Prefiro nao dizer'],
        'obrigatorio': True,
    },
    {
        'key': 'idade',
        'tipo': 'select',
        'label': 'Qual e a sua idade?',
        'opcoes': ['< 18 anos', '18-24 anos', '25-34 anos', '35-44 anos', '45-54 anos', '55+ anos'],
        'obrigatorio': True,
    },
    {
        'key': 'frequencia_estetica',
        'tipo': 'select',
        'label': 'Com que frequencia voce utiliza servicos de estetica?',
        'opcoes': ['Nunca utilizei', 'Raramente', 'As vezes', 'Frequentemente'],
        'obrigatorio': True,
    },
    {
        'key': 'avaliacao_clareza',
        'tipo': 'scale',
        'label': 'Como voce avalia a clareza das informacoes recebidas no atendimento online?',
        'opcoes': ['Muito Ruim', 'Ruim', 'Neutro', 'Bom', 'Excelente'],
        'obrigatorio': True,
    },
    {
        'key': 'avaliacao_tempo_resposta',
        'tipo': 'scale',
        'label': 'Como voce avalia o tempo de resposta no atendimento?',
        'opcoes': ['Muito Ruim', 'Ruim', 'Neutro', 'Bom', 'Excelente'],
        'obrigatorio': True,
    },
    {
        'key': 'avaliacao_comunicacao',
        'tipo': 'scale',
        'label': 'Como voce avalia a facilidade de comunicacao com o atendente?',
        'opcoes': ['Muito Ruim', 'Ruim', 'Neutro', 'Bom', 'Excelente'],
        'obrigatorio': True,
    },
    {
        'key': 'avaliacao_personalizacao',
        'tipo': 'scale',
        'label': 'O atendimento foi personalizado de acordo com sua necessidade?',
        'opcoes': ['Muito Ruim', 'Ruim', 'Neutro', 'Bom', 'Excelente'],
        'obrigatorio': True,
    },
    {
        'key': 'avaliacao_explicacoes',
        'tipo': 'scale',
        'label': 'As explicacoes sobre os procedimentos foram suficientes para sua decisao?',
        'opcoes': ['Muito Ruim', 'Ruim', 'Neutro', 'Bom', 'Excelente'],
        'obrigatorio': True,
    },
    {
        'key': 'recursos_desejados',
        'tipo': 'checkboxes',
        'label': 'O que voce gostaria de ter recebido durante o atendimento? (marque todas que se aplicam)',
        'opcoes': [
            'Fotos de antes/depois',
            'Videos explicativos',
            'Depoimentos de clientes',
            'Explicacao detalhada do procedimento',
            'Simulacao de resultados',
        ],
        'obrigatorio': True,
    },
    {
        'key': 'busca_externa',
        'tipo': 'longtext',
        'label': 'Voce precisou buscar informacoes fora (Google/Instagram) sobre o procedimento? Conte um pouco.',
        'obrigatorio': False,
    },
    {
        'key': 'sugestao_melhoria',
        'tipo': 'longtext',
        'label': 'Se voce pudesse mudar uma coisa no atendimento, o que seria?',
        'obrigatorio': False,
    },
]


class Command(BaseCommand):
    help = 'Cria/atualiza FormularioAnamnese tipo PESQUISA V1 + WorkflowRegra de disparo'

    @transaction.atomic
    def handle(self, *args, **options):
        formulario, criado = FormularioAnamnese.objects.update_or_create(
            nome='Pesquisa Atendimento Online — V1',
            defaults={
                'tipo': 'PESQUISA',
                'escopo': 'MODALIDADE',
                'modalidade': 'ONLINE',
                'schema_json': SCHEMA_V1,
                'ativo': True,
                'obrigatorio': False,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f'{"CRIADO" if criado else "ATUALIZADO"} FormularioAnamnese pk={formulario.pk}'
        ))

        regra, regra_criada = WorkflowRegra.objects.update_or_create(
            nome='Pesquisa pos-atendimento online (D+0 +2h)',
            defaults={
                'ativo': True,
                'trigger': 'AFTER_EVENT',
                'offset_minutos': 120,
                'acao': 'SEND_WHATSAPP',
                'template': 'Pesquisa pos-atendimento online enviada via template Meta `pesquisa_online`',
                'config_json': {
                    'tipo_notificacao': 'PESQUISA',
                    'modalidade_filter': 'ONLINE',
                    'formulario_id': formulario.pk,
                },
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f'{"CRIADA" if regra_criada else "ATUALIZADA"} WorkflowRegra pk={regra.pk}'
        ))

        self.stdout.write(self.style.WARNING(
            '\nProximos passos manuais:\n'
            '1. Submeter template WhatsApp `pesquisa_online` ao Meta Business\n'
            '   Body: "Ola {{1}}, agradecemos sua consulta online em {{2}}.\n'
            '          Pode nos ajudar com 2 minutos respondendo essa pesquisa? {{3}}"\n'
            '   Aguardar 24-72h aprovacao.\n'
            '2. Setar Procedimento(s) com modalidade=ONLINE no painel.\n'
            '3. Setar WHATSAPP_TOKEN + WHATSAPP_PHONE_ID + WHATSAPP_TEMPLATE_PESQUISA no Railway.\n'
            '4. Workflow Beat avalia AFTER_EVENT a cada tick (60s).\n'
        ))
