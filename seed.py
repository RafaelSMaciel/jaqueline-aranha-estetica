import os
import django
from datetime import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shivazen.settings')
django.setup()

from app_shivazen.models import Profissional, Procedimento, ProfissionalProcedimento, DisponibilidadeProfissional

def seed():
    # 1. Criar Profissional
    prof, created = Profissional.objects.get_or_create(
        nome='Dra. Stefany',
        defaults={'especialidade': 'Estética Avançada', 'ativo': True}
    )
    
    # 2. Criar Procedimentos
    proc1, _ = Procedimento.objects.get_or_create(
        nome='Limpeza de Pele Profunda',
        defaults={'descricao': 'Limpeza facial profunda para remoção de cravos e impurezas.', 'duracao_minutos': 60, 'ativo': True}
    )
    proc2, _ = Procedimento.objects.get_or_create(
        nome='Preenchimento Facial',
        defaults={'descricao': 'Aplicação de ácido hialurônico para harmonização.', 'duracao_minutos': 45, 'ativo': True}
    )
    proc3, _ = Procedimento.objects.get_or_create(
        nome='Massagem Modeladora',
        defaults={'descricao': 'Técnica corporal avançada para redução de medidas.', 'duracao_minutos': 60, 'ativo': True}
    )

    # 3. Vincular Profissional aos Procedimentos
    ProfissionalProcedimento.objects.get_or_create(profissional=prof, procedimento=proc1)
    ProfissionalProcedimento.objects.get_or_create(profissional=prof, procedimento=proc2)
    ProfissionalProcedimento.objects.get_or_create(profissional=prof, procedimento=proc3)

    # 4. Criar Disponibilidade (Segunda a Sexta)
    # A lógica no models.py usa: dia_semana = isoweekday() % 7 + 1 
    # (Ex: Seg = 1%7+1 = 2, Ter=3, Qua=4, Qui=5, Sex=6, Sab=7, Dom=1)
    dias_semana = [2, 3, 4, 5, 6] 
    for dia in dias_semana:
        DisponibilidadeProfissional.objects.get_or_create(
            profissional=prof,
            dia_semana=dia,
            defaults={'hora_inicio': time(9, 0), 'hora_fim': time(18, 0)}
        )
    
    # Adicionar Sábado (7)
    DisponibilidadeProfissional.objects.get_or_create(
        profissional=prof,
        dia_semana=7,
        defaults={'hora_inicio': time(9, 0), 'hora_fim': time(14, 0)}
    )

    print("Procedimentos e disponibilidades criados com sucesso!")

if __name__ == '__main__':
    seed()
