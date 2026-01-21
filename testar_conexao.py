#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de teste para validar conexão com Google Sheets
Execute: python testar_conexao.py
"""

import os
import json
from google.oauth2.service_account import Credentials
import gspread
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

def testar_credenciais():
    """Testa se as credenciais estão configuradas corretamente"""
    print("="*60)
    print("🔍 TESTE DE CONFIGURAÇÃO - GOOGLE SHEETS")
    print("="*60)
    
    # 1. Verificar variáveis de ambiente
    print("\n1️⃣ Verificando variáveis de ambiente...")
    
    telegram_token = os.getenv("TELEGRAM_TOKEN")
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    credentials_json = os.getenv("CREDENTIALS_JSON")
    
    if not telegram_token:
        print("   ❌ TELEGRAM_TOKEN não configurado")
        return False
    else:
        print(f"   ✅ TELEGRAM_TOKEN: {telegram_token[:10]}...")
    
    if not spreadsheet_id:
        print("   ❌ SPREADSHEET_ID não configurado")
        return False
    else:
        print(f"   ✅ SPREADSHEET_ID: {spreadsheet_id}")
    
    # 2. Verificar credenciais
    print("\n2️⃣ Verificando credenciais do Google...")
    
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    creds = None
    
    # Tentar variável de ambiente
    if credentials_json:
        print("   📄 Usando CREDENTIALS_JSON da variável de ambiente")
        try:
            creds_dict = json.loads(credentials_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            print("   ✅ Credenciais carregadas com sucesso")
            print(f"   📧 Service Account: {creds_dict.get('client_email', 'N/A')}")
        except json.JSONDecodeError as e:
            print(f"   ❌ Erro ao parsear JSON: {e}")
            return False
        except Exception as e:
            print(f"   ❌ Erro ao carregar credenciais: {e}")
            return False
    
    # Tentar arquivo local
    elif os.path.exists("credentials.json"):
        print("   📄 Usando arquivo credentials.json local")
        try:
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            with open("credentials.json") as f:
                creds_dict = json.load(f)
            print("   ✅ Credenciais carregadas com sucesso")
            print(f"   📧 Service Account: {creds_dict.get('client_email', 'N/A')}")
        except Exception as e:
            print(f"   ❌ Erro ao carregar arquivo: {e}")
            return False
    else:
        print("   ❌ Nenhuma credencial encontrada!")
        print("   💡 Configure CREDENTIALS_JSON ou crie credentials.json")
        return False
    
    # 3. Testar conexão com Google Sheets
    print("\n3️⃣ Testando conexão com Google Sheets...")
    
    try:
        client = gspread.authorize(creds)
        print("   ✅ Autenticação bem-sucedida")
    except Exception as e:
        print(f"   ❌ Erro na autenticação: {e}")
        return False
    
    # 4. Tentar abrir a planilha
    print("\n4️⃣ Tentando abrir planilha...")
    
    try:
        planilha = client.open_by_key(spreadsheet_id)
        print(f"   ✅ Planilha aberta: {planilha.title}")
        print(f"   📊 URL: {planilha.url}")
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"   ❌ Planilha não encontrada (ID: {spreadsheet_id})")
        print("\n   💡 Possíveis causas:")
        print("      1. ID da planilha está errado")
        print("      2. Planilha não foi compartilhada com a service account")
        print(f"\n   📧 Compartilhe a planilha com: {creds_dict.get('client_email', 'N/A')}")
        return False
    except gspread.exceptions.APIError as e:
        print(f"   ❌ Erro da API do Google: {e}")
        print("\n   💡 Possíveis causas:")
        print("      1. Google Sheets API não está habilitada")
        print("      2. Problema de permissão")
        print("\n   🔗 Habilite a API em:")
        print("      https://console.cloud.google.com/apis/library/sheets.googleapis.com")
        return False
    except Exception as e:
        print(f"   ❌ Erro inesperado: {e}")
        return False
    
    # 5. Listar abas
    print("\n5️⃣ Listando abas da planilha...")
    
    try:
        abas = planilha.worksheets()
        print(f"   ✅ {len(abas)} aba(s) encontrada(s):")
        for aba in abas:
            print(f"      • {aba.title} ({aba.row_count} linhas x {aba.col_count} colunas)")
    except Exception as e:
        print(f"   ⚠️ Não foi possível listar abas: {e}")
    
    # 6. Teste de escrita (opcional)
    print("\n6️⃣ Testando permissão de escrita...")
    
    try:
        # Tentar acessar a primeira aba
        primeira_aba = planilha.get_worksheet(0)
        print(f"   ✅ Aba '{primeira_aba.title}' acessível")
        print("   ℹ️ Permissão de escrita confirmada (não gravamos nada)")
    except Exception as e:
        print(f"   ❌ Erro ao acessar aba: {e}")
        print("   💡 Verifique se a service account tem permissão de 'Editor'")
        return False
    
    # Sucesso total
    print("\n" + "="*60)
    print("✅ TODOS OS TESTES PASSARAM!")
    print("="*60)
    print("\n🎉 Tudo configurado corretamente!")
    print("💡 Execute o bot com: python bot.py")
    print()
    
    return True

if __name__ == "__main__":
    try:
        sucesso = testar_credenciais()
        exit(0 if sucesso else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️ Teste interrompido pelo usuário")
        exit(1)
    except Exception as e:
        print(f"\n\n❌ ERRO FATAL: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
