"""Forms de autenticacao."""
from django import forms


class LoginForm(forms.Form):
    email = forms.EmailField(required=True, max_length=254)
    senha = forms.CharField(required=True, widget=forms.PasswordInput, min_length=8)
    next = forms.CharField(required=False, widget=forms.HiddenInput)
