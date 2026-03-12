from django.shortcuts import render


def servicos_faciais(request):
    """Página de serviços faciais"""
    return render(request, 'servicos/faciais.html')


def servicos_corporais(request):
    """Página de serviços corporais"""
    return render(request, 'servicos/corporais.html')


def servicos_produtos(request):
    """Página de produtos"""
    return render(request, 'servicos/produtos.html')
