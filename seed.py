"""Seed de dados para dev. Uso: python manage.py seed"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shivazen.settings')
django.setup()
from datetime import time, timedelta
from decimal import Decimal
from django.utils import timezone
from app_shivazen.models import (
    Perfil, Profissional, DisponibilidadeProfissional,
    Procedimento, ProfissionalProcedimento, Preco,
    Cliente, Prontuario, Atendimento, Promocao,
    Pacote, ItemPacote, PacoteCliente, Usuario,
)

def seed():
    pa, _ = Perfil.objects.get_or_create(nome='Administrador', defaults={'descricao': 'Admin'})
    pp, _ = Perfil.objects.get_or_create(nome='Profissional', defaults={'descricao': 'Prof'})
    profs = []
    for n, e in [('Dra. Ana Paula','Facial'),('Dr. Carlos','Peeling'),('Larissa','Capilar')]:
        p, c = Profissional.objects.get_or_create(nome=n, defaults={'especialidade':e,'ativo':True})
        profs.append(p)
        if c:
            for d in range(2,8):
                DisponibilidadeProfissional.objects.get_or_create(profissional=p, dia_semana=d, defaults={'hora_inicio':time(9,0),'hora_fim':time(18,0)})
    procs = []
    for n,cat,dur,v,desc in [('Limpeza de Pele','FACIAL',60,150,'Limpeza profunda.'),('Peeling Quimico','FACIAL',45,250,'Renovacao celular.'),('Drenagem Linfatica','CORPORAL',60,180,'Massagem.'),('Criolipólise','CORPORAL',50,800,'Gordura localizada.'),('Terapia Capilar','CAPILAR',40,120,'Revitalizacao.'),('Cauterizacao','CAPILAR',90,200,'Reconstrucao.'),('Aromaterapia','OUTRO',30,90,'Oleos essenciais.'),('Depilacao Laser','OUTRO',30,350,'Laser diodo.')]:
        proc, c = Procedimento.objects.get_or_create(nome=n, defaults={'categoria':cat,'duracao_minutos':dur,'descricao':desc,'ativo':True})
        procs.append(proc)
        if c:
            Preco.objects.get_or_create(procedimento=proc, profissional=None, defaults={'valor':Decimal(v)})
            for pr in profs:
                ProfissionalProcedimento.objects.get_or_create(profissional=pr, procedimento=proc)
    clis = []
    for n,t,em in [('Maria Oliveira','17999990001','maria@e.com'),('Joao Santos','17999990002','joao@e.com'),('Ana Costa','17999990003','ana@e.com'),('Pedro Lima','17999990004','pedro@e.com'),('Fernanda Almeida','17999990005','fernanda@e.com')]:
        cl, _ = Cliente.objects.get_or_create(telefone=t, defaults={'nome_completo':n,'email':em,'ativo':True})
        clis.append(cl)
    for i,cl in enumerate(clis):
        pr, _ = Prontuario.objects.get_or_create(cliente=cl, defaults={'observacoes_gerais':f'Prontuario {cl.nome_completo}'})
        if i==0 and not pr.alergias:
            pr.alergias='Alergia a parabenos'; pr.contraindicacoes='Pele sensivel'; pr.save()
    agora = timezone.now()
    if Atendimento.objects.count() < 5:
        for i,st in enumerate(['AGENDADO','AGENDADO','CONFIRMADO','CONFIRMADO','REALIZADO','REALIZADO','REALIZADO','CANCELADO','FALTOU','AGENDADO']):
            cl,pr,proc = clis[i%5], profs[i%3], procs[i%8]
            d = timedelta(days=-(i+1)) if st in ['REALIZADO','CANCELADO','FALTOU'] else timedelta(days=i+1)
            dt = (agora+d).replace(hour=10+(i%7),minute=0,second=0,microsecond=0)
            pobj = Preco.objects.filter(procedimento=proc,profissional=None).first()
            Atendimento.objects.create(cliente=cl,profissional=pr,procedimento=proc,data_hora_inicio=dt,data_hora_fim=dt+timedelta(minutes=proc.duracao_minutos),valor_cobrado=pobj.valor if pobj else Decimal('100'),status=st)
    hoje = timezone.now().date()
    Promocao.objects.get_or_create(nome='Semana da Beleza', defaults={'procedimento':procs[0],'desconto_percentual':Decimal('20'),'data_inicio':hoje-timedelta(days=2),'data_fim':hoje+timedelta(days=12),'ativa':True})
    pac,_ = Pacote.objects.get_or_create(nome='Pacote Glow', defaults={'preco_total':Decimal('500'),'ativo':True,'validade_meses':6})
    ItemPacote.objects.get_or_create(pacote=pac,procedimento=procs[0],defaults={'quantidade_sessoes':4})
    PacoteCliente.objects.get_or_create(cliente=clis[0],pacote=pac,defaults={'valor_pago':Decimal('500'),'status':'ATIVO'})
    if not Usuario.objects.filter(email='admin@shivazen.com').exists():
        u=Usuario.objects.create_user(email='admin@shivazen.com',password='admin123',nome='Admin'); u.is_staff=True; u.perfil=pa; u.save()
    if not Usuario.objects.filter(email='ana@shivazen.com').exists():
        u=Usuario.objects.create_user(email='ana@shivazen.com',password='prof123',nome='Dra. Ana Paula'); u.profissional=profs[0]; u.perfil=pp; u.save()
    print('Seed OK!')

if __name__=='__main__':
    seed()
