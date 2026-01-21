import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import pytesseract
from PIL import Image, ImageEnhance
import io
import asyncio
from pdf2image import convert_from_bytes
import zipfile
from dotenv import load_dotenv
import json

load_dotenv()

import platform

# Configurar Tesseract apenas no Windows
if platform.system() == 'Windows':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    # Configurar caminho do Poppler para processar PDFs
    os.environ['PATH'] += r';C:\poppler\Library\bin'

# ==================== CONFIGURAÇÕES ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Validar variáveis essenciais
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN não configurado no .env ou variáveis de ambiente")
if not SPREADSHEET_ID:
    raise ValueError("❌ SPREADSHEET_ID não configurado no .env ou variáveis de ambiente")

CNPJ_LDL = "60415819000141"
CNPJ_POP = "61081232000106"

CATEGORIAS_DESPESAS = [
    "Frete Internacional", "Frete Nacional", "Armazenagem", "Despachante",
    "AFRMM", "SISCOMEX", "ICMS", "Seguro", "Inspeção", "Certificação", "Outros"
]

user_data_temp = {}

# ==================== GOOGLE SHEETS - CONEXÃO MELHORADA ====================

def conectar_planilha():
    """
    Conecta ao Google Sheets usando Service Account.
    Prioriza variável de ambiente (produção) e fallback para arquivo local (dev).
    """
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        # Tentar carregar credenciais da variável de ambiente (PRODUÇÃO)
        credentials_json = os.getenv("CREDENTIALS_JSON")
        
        if credentials_json:
            print("[DEBUG] 🔐 Usando credenciais da variável de ambiente")
            try:
                creds_dict = json.loads(credentials_json)
                creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            except json.JSONDecodeError as e:
                raise ValueError(f"❌ CREDENTIALS_JSON inválido: {e}")
        else:
            # Fallback: arquivo local (DESENVOLVIMENTO)
            print("[DEBUG] 🔐 Usando credenciais do arquivo local")
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "❌ Arquivo credentials.json não encontrado e CREDENTIALS_JSON não configurado.\n"
                    "Configure a variável de ambiente CREDENTIALS_JSON ou crie o arquivo credentials.json"
                )
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        
        # Autorizar e conectar
        client = gspread.authorize(creds)
        planilha = client.open_by_key(SPREADSHEET_ID)
        
        print(f"[DEBUG] ✅ Conectado à planilha: {planilha.title}")
        return planilha
        
    except gspread.exceptions.SpreadsheetNotFound:
        raise ValueError(
            f"❌ Planilha não encontrada (ID: {SPREADSHEET_ID}).\n"
            "Verifique se:\n"
            "1. O ID está correto\n"
            "2. A planilha foi compartilhada com o email da Service Account"
        )
    except gspread.exceptions.APIError as e:
        raise ValueError(f"❌ Erro da API do Google: {e}")
    except Exception as e:
        raise ValueError(f"❌ Erro ao conectar planilha: {e}")

# ==================== FUNÇÕES AUXILIARES ====================

def normalizar_data(data_str):
    """
    Normaliza data para o formato DD/MM/YYYY
    Aceita formatos: DD/MM/YYYY, DD-MM-YY, DD-MM-YYYY, YYYY-MM-DD, etc
    """
    if not data_str:
        return datetime.now().strftime('%d/%m/%Y')
    
    data_str = str(data_str).strip()
    
    # Lista de formatos possíveis
    formatos = [
        '%d/%m/%Y',      # 15/10/2025
        '%d/%m/%y',      # 15/10/25
        '%d-%m-%Y',      # 15-10-2025
        '%d-%m-%y',      # 15-10-25 ou 25-09-19
        '%Y-%m-%d',      # 2025-10-15
        '%Y/%m/%d',      # 2025/10/15
        '%d.%m.%Y',      # 15.10.2025
        '%d.%m.%y',      # 15.10.25
        '%d %m %Y',      # 15 10 2025
        '%m/%d/%Y',      # 10/15/2025
        '%Y%m%d',        # 20251015
    ]
    
    for fmt in formatos:
        try:
            data_obj = datetime.strptime(data_str, fmt)
            
            # Se o ano for 2 dígitos, ajustar século
            if data_obj.year < 100:
                if data_obj.year <= 30:
                    data_obj = data_obj.replace(year=data_obj.year + 2000)
                else:
                    data_obj = data_obj.replace(year=data_obj.year + 1900)
            
            return data_obj.strftime('%d/%m/%Y')
        except ValueError:
            continue
    
    # Se nenhum formato funcionar, retorna data atual
    print(f"[AVISO] Formato de data não reconhecido: {data_str}. Usando data atual.")
    return datetime.now().strftime('%d/%m/%Y')

def converter_valor_para_float(valor_str):
    """
    Converte string de valor em float
    Aceita: "R$ 29,091.89", "29091.89", "29.091,89", etc
    """
    if not valor_str:
        return 0.0
    
    valor_str = str(valor_str).strip()
    
    # Remover "R$"
    valor_str = valor_str.replace('R$', '').strip()
    
    # Detectar qual é o separador decimal
    # Se tem vírgula E ponto, o último é decimal
    if ',' in valor_str and '.' in valor_str:
        if valor_str.rfind(',') > valor_str.rfind('.'):
            # Formato: 1.234,56 (brasileiro)
            valor_str = valor_str.replace('.', '').replace(',', '.')
        else:
            # Formato: 1,234.56 (americano)
            valor_str = valor_str.replace(',', '')
    elif ',' in valor_str:
        # Só tem vírgula: 1234,56 ou 1,56
        if valor_str.count(',') == 1:
            # Verificar se é separador de milhar ou decimal
            partes = valor_str.split(',')
            if len(partes[1]) == 2:  # Se tem 2 casas após vírgula, é decimal
                valor_str = valor_str.replace(',', '.')
            else:
                # É separador de milhar
                valor_str = valor_str.replace(',', '')
    
    try:
        return float(valor_str)
    except:
        print(f"[ERRO] Não consegui converter: {valor_str}")
        return 0.0

# ==================== OCR E PROCESSAMENTO ====================

def extrair_texto_imagem(image_bytes, is_pdf=False):
    try:
        if is_pdf:
            print("[DEBUG] Convertendo PDF...")
            poppler_path = r'C:\poppler\Library\bin' if platform.system() == 'Windows' else None
            images = convert_from_bytes(image_bytes, poppler_path=poppler_path, dpi=300)
            texto_completo = ""
            for i, image in enumerate(images):
                print(f"[DEBUG] Página {i+1}...")
                image = image.convert('L')
                enhancer = ImageEnhance.Contrast(image)
                image = enhancer.enhance(2.5)
                enhancer = ImageEnhance.Sharpness(image)
                image = enhancer.enhance(2.0)
                config = '--psm 6 --oem 3'
                texto = pytesseract.image_to_string(image, lang='por+eng', config=config)
                texto_completo += texto + "\n"
            print(f"[DEBUG] Texto PDF: {texto_completo[:200]}...")
            return texto_completo
        else:
            image = Image.open(io.BytesIO(image_bytes))
            image = image.convert('L')
            width, height = image.size
            if width < 1500 or height < 1500:
                image = image.resize((width*2, height*2), Image.LANCZOS)
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.5)
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(2.0)
            config = '--psm 6 --oem 3'
            texto = pytesseract.image_to_string(image, lang='por+eng', config=config)
            print(f"[DEBUG] Texto: {texto[:200]}...")
            return texto
    except Exception as e:
        print(f"[ERRO OCR] {e}")
        return ""

def extrair_valores_texto(texto):
    padroes = [
        r'(?:TOTAL|Total|VALUE|Value|VALOR|Valor|BRL)[:\s]+(?:BRL\s+)?(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})',
        r'R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})',
        r'(?:BRL|USD)\s+(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})',
        r'(\d{1,3}(?:\.\d{3})*,\d{2})',
        r'(\d{1,3}(?:,\d{3})*\.\d{2})'
    ]
    valores = []
    for padrao in padroes:
        valores.extend(re.findall(padrao, texto, re.IGNORECASE))
    
    valores_float = []
    for v in valores:
        try:
            if ',' in v and '.' in v:
                v_clean = v.replace('.', '').replace(',', '.') if v.rfind(',') > v.rfind('.') else v.replace(',', '')
            elif ',' in v:
                v_clean = v.replace('.', '').replace(',', '.') if len(v.split(',')[-1]) == 2 else v.replace(',', '')
            else:
                v_clean = v
            valor_float = float(v_clean)
            if valor_float > 0 and valor_float not in valores_float:
                valores_float.append(valor_float)
        except:
            continue
    return sorted(list(set(valores_float)), reverse=True)

def extrair_data(texto):
    for padrao in [r'(\d{2}[/.-]\d{2}[/.-]\d{4})', r'(\d{2}[/.-]\d{2}[/.-]\d{2})', r'(\d{4}[/.-]\d{2}[/.-]\d{2})']:
        match = re.search(padrao, texto)
        if match:
            data_encontrada = match.group(1)
            return normalizar_data(data_encontrada)
    return datetime.now().strftime('%d/%m/%Y')

def extrair_descricao(texto):
    texto_upper = texto.upper()
    if any(w in texto_upper for w in ['FRETE', 'FREIGHT', 'SHIPPING', 'OCEAN']):
        return 'Frete'
    elif any(w in texto_upper for w in ['ARMAZEN', 'STORAGE']):
        return 'Armazenagem'
    elif any(w in texto_upper for w in ['DESPACH', 'CUSTOMS']):
        return 'Despachante'
    elif 'AFRMM' in texto_upper:
        return 'AFRMM'
    elif 'SISCOMEX' in texto_upper:
        return 'SISCOMEX'
    return 'Despesa'

def extrair_dados_comprovante(texto):
    return {
        'valores': extrair_valores_texto(texto),
        'data': extrair_data(texto),
        'descricao': extrair_descricao(texto),
        'texto_completo': texto
    }

# ==================== PROCESSAMENTO XML ====================

def extrair_dados_xml(xml_content, pi_informada=None):
    try:
        root = ET.fromstring(xml_content)
        ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
        ide = root.find('.//nfe:ide', ns)
        emit = root.find('.//nfe:emit', ns)
        dest = root.find('.//nfe:dest', ns)
        total = root.find('.//nfe:total/nfe:ICMSTot', ns)
        
        dados = {
            'numero_nf': ide.find('nfe:nNF', ns).text if ide.find('nfe:nNF', ns) is not None else '',
            'data_emissao': normalizar_data(ide.find('nfe:dhEmi', ns).text) if ide.find('nfe:dhEmi', ns) is not None else datetime.now().strftime('%d/%m/%Y'),
            'natureza': ide.find('nfe:natOp', ns).text if ide.find('nfe:natOp', ns) is not None else '',
            'cnpj_emitente': emit.find('nfe:CNPJ', ns).text if emit.find('nfe:CNPJ', ns) is not None else '',
            'nome_emitente': emit.find('nfe:xNome', ns).text if emit.find('nfe:xNome', ns) is not None else '',
            'cnpj_destinatario': dest.find('nfe:CNPJ', ns).text if dest.find('nfe:CNPJ', ns) is not None else '',
            'nome_destinatario': dest.find('nfe:xNome', ns).text if dest.find('nfe:xNome', ns) is not None else '',
            'valor_produtos': float(total.find('nfe:vProd', ns).text) if total.find('nfe:vProd', ns) is not None else 0,
            'valor_nf': float(total.find('nfe:vNF', ns).text) if total.find('nfe:vNF', ns) is not None else 0,
            'icms': float(total.find('nfe:vICMS', ns).text) if total.find('nfe:vICMS', ns) is not None else 0,
            'ipi': float(total.find('nfe:vIPI', ns).text) if total.find('nfe:vIPI', ns) is not None else 0,
            'pis': float(total.find('nfe:vPIS', ns).text) if total.find('nfe:vPIS', ns) is not None else 0,
            'cofins': float(total.find('nfe:vCOFINS', ns).text) if total.find('nfe:vCOFINS', ns) is not None else 0,
            'pi': pi_informada or 'N/A'
        }
        
        try:
            dados['ii'] = float(root.find('.//nfe:II/nfe:vII', ns).text) if root.find('.//nfe:II/nfe:vII', ns) is not None else 0
        except:
            dados['ii'] = 0
        
        try:
            afrmm_total = 0
            for di in root.findall('.//nfe:DI', ns):
                afrmm_elem = di.find('nfe:vAFRMM', ns)
                if afrmm_elem is not None:
                    afrmm_total += float(afrmm_elem.text)
            dados['afrmm'] = afrmm_total
            print(f"[DEBUG] AFRMM TOTAL: R$ {afrmm_total}")
        except:
            dados['afrmm'] = 0
        
        try:
            siscomex = 0
            inf_cpl = root.find('.//nfe:infCpl', ns)
            if inf_cpl is not None and inf_cpl.text:
                texto = inf_cpl.text.upper()
                for padrao in [r'SISCOMEX\s+(?:FOI\s+DE\s+)?R?\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2})', r'TAXA\s+SISCOMEX\s+(?:FOI\s+DE\s+)?R?\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2})']:
                    match = re.search(padrao, texto)
                    if match:
                        siscomex = float(match.group(1).replace('.', '').replace(',', '.'))
                        print(f"[DEBUG] SISCOMEX: R$ {siscomex}")
                        break
            dados['siscomex'] = siscomex
        except:
            dados['siscomex'] = 0
        
        return dados
    except Exception as e:
        print(f"Erro XML: {e}")
        return None

def processar_zip_xmls(zip_bytes):
    try:
        print("[DEBUG] Processando ZIP...")
        zip_file = zipfile.ZipFile(io.BytesIO(zip_bytes))
        xmls_dados = []
        valor_total = 0
        quantidade_nfs = 0
        remessas_ignoradas = 0
        
        for file_name in zip_file.namelist():
            if file_name.lower().endswith('.xml'):
                print(f"[DEBUG] {file_name}...")
                xml_content = zip_file.read(file_name).decode('utf-8')
                dados = extrair_dados_xml(xml_content)
                
                if dados:
                    tipo = identificar_tipo_nota(dados)
                    if tipo == 'REMESSA':
                        print(f"[DEBUG] ⚠️ REMESSA - IGNORADO")
                        remessas_ignoradas += 1
                        continue
                    xmls_dados.append(dados)
                    valor_total += dados['valor_nf']
                    quantidade_nfs += 1
        
        print(f"[DEBUG] {quantidade_nfs} XMLs, R$ {valor_total:,.2f}")
        return {'xmls': xmls_dados, 'valor_total': valor_total, 'quantidade': quantidade_nfs, 'remessas_ignoradas': remessas_ignoradas}
    except Exception as e:
        print(f"[ERRO ZIP] {e}")
        return None

def identificar_tipo_nota(dados):
    natureza = dados['natureza'].upper()
    if 'REMESSA' in natureza:
        return 'REMESSA'
    if 'IMPORT' in natureza or 'ENTRADA' in natureza:
        return 'IMPORTACAO'
    if dados['cnpj_emitente'] == CNPJ_LDL and dados['cnpj_destinatario'] == CNPJ_POP:
        return 'LDL_PARA_POP'
    elif dados['cnpj_emitente'] == CNPJ_POP:
        return 'POP_PARA_CLIENTE'
    return 'DESCONHECIDO'

# ==================== GRAVAÇÃO NO SHEETS ====================

def gravar_xml_no_sheets(dados, tipo_nota):
    try:
        planilha = conectar_planilha()
        if tipo_nota == 'IMPORTACAO':
            aba = planilha.worksheet('Importacao')
            linha = [dados['pi'], dados['numero_nf'], dados['data_emissao'], dados['nome_emitente'], dados['cnpj_emitente'],
                    dados['valor_produtos'], dados['valor_nf'], dados['ii'], dados['ipi'], dados['pis'], dados['cofins'],
                    dados['icms'], dados.get('afrmm', 0), dados.get('siscomex', 0)]
            aba.append_row(linha)
            return True
        elif tipo_nota == 'LDL_PARA_POP':
            aba = planilha.worksheet('Saida_1')
            linha = [dados['pi'], dados['numero_nf'], dados['data_emissao'], dados['valor_nf'], dados['valor_nf'] * 0.004]
            aba.append_row(linha)
            return True
        elif tipo_nota == 'POP_PARA_CLIENTE':
            aba = planilha.worksheet('Saida_2')
            linha = [dados['pi'], dados['numero_nf'], dados['data_emissao'], dados['nome_destinatario'],
                    dados['cnpj_destinatario'], dados['valor_nf'], dados['natureza']]
            aba.append_row(linha)
            return True
        else:
            return False
    except Exception as e:
        print(f"Erro gravar: {e}")
        return False

def gravar_zip_consolidado_no_sheets(pi, zip_resultado, tipo_nota):
    try:
        planilha = conectar_planilha()
        primeiro = zip_resultado['xmls'][0]
        if tipo_nota == 'POP_PARA_CLIENTE':
            aba = planilha.worksheet('Saida_2')
            linha = [pi, f"ZIP com {zip_resultado['quantidade']} NFs", primeiro['data_emissao'],
                    primeiro['nome_destinatario'], primeiro['cnpj_destinatario'], zip_resultado['valor_total'], primeiro['natureza']]
        else:
            return False
        aba.append_row(linha)
        return True
    except Exception as e:
        print(f"Erro ZIP sheets: {e}")
        return False

def gravar_despesa_no_sheets(pi, categoria, valor, data, descricao, observacao=""):
    try:
        planilha = conectar_planilha()
        try:
            aba = planilha.worksheet('outras_despesas')
        except:
            aba = planilha.add_worksheet(title='outras_despesas', rows=1000, cols=10)
            aba.append_row(['PI', 'Data', 'Categoria', 'Valor', 'Descrição', 'Observação'])
        aba.append_row([pi, data, categoria, valor, descricao, observacao])
        return True
    except Exception as e:
        print(f"Erro despesa: {e}")
        return False

# ==================== VERIFICAÇÕES DE DUPLICATA ====================

def extrair_pi_da_mensagem(texto):
    if not texto:
        return None
    texto_upper = texto.upper()
    for padrao in [r'PI[:\s]*([A-Z0-9]+)', r'PROCESSO[:\s]*([A-Z0-9]+)', r'([A-Z]{4}\d{7})']:
        match = re.search(padrao, texto_upper)
        if match:
            return match.group(1)
    return None

def verificar_xml_duplicado(numero_nf, cnpj_emitente, valor_nf):
    """Verifica se um XML já foi lançado"""
    try:
        planilha = conectar_planilha()
        
        for nome_aba in ['Importacao', 'Saida_1', 'Saida_2']:
            try:
                aba = planilha.worksheet(nome_aba)
                todas_linhas = aba.get_all_values()
                
                for i, linha in enumerate(todas_linhas[1:], start=2):
                    if not linha or len(linha) < 2:
                        continue
                    
                    nf_planilha = str(linha[1]).strip() if len(linha) > 1 else ''
                    
                    if nf_planilha == str(numero_nf).strip():
                        return {
                            'duplicada': True,
                            'aba': nome_aba,
                            'detalhes': {
                                'numero_nf': numero_nf,
                                'linha': i,
                                'data': linha[2] if len(linha) > 2 else 'N/A'
                            }
                        }
            except:
                continue
        
        return {'duplicada': False}
    except Exception as e:
        print(f"[ERRO verificar_xml] {e}")
        return {'duplicada': False}

def verificar_valor_duplicado_pi(pi, valor, data, categoria=None, dias_margem=100):
    """Verifica se existe valor similar na mesma PI"""
    try:
        data = normalizar_data(data)
        print(f"[DEBUG] Data normalizada: {data}")
        print(f"[DEBUG] Valor recebido: {valor} (tipo: {type(valor)})")
        
        planilha = conectar_planilha()
        
        try:
            aba = planilha.worksheet('outras_despesas')
        except:
            print("[DEBUG] Aba 'outras_despesas' não existe ainda")
            return {'duplicada': False}
        
        todas_linhas = aba.get_all_values()
        
        if len(todas_linhas) <= 1:
            print("[DEBUG] Aba vazia, sem duplicatas")
            return {'duplicada': False}
        
        try:
            data_obj = datetime.strptime(data, '%d/%m/%Y')
            print(f"[DEBUG] Data convertida: {data_obj}")
        except Exception as e:
            print(f"[DEBUG] Erro ao converter data: {e}")
            data_obj = datetime.now()
        
        data_inicio = data_obj - timedelta(days=dias_margem)
        data_fim = data_obj + timedelta(days=dias_margem)
        
        print(f"[DEBUG] Procurando duplicatas entre {data_inicio.date()} e {data_fim.date()}")
        print(f"[DEBUG] PI: {pi}, Valor: {valor}, Categoria: {categoria}")
        
        duplicatas_encontradas = []
        
        for i, linha in enumerate(todas_linhas[1:], start=2):
            if not linha or len(linha) < 4:
                continue
            
            try:
                pi_planilha = str(linha[0]).strip()
                data_planilha_str = normalizar_data(str(linha[1]).strip())
                categoria_planilha = str(linha[2]).strip() if len(linha) > 2 else ''
                valor_planilha_str = str(linha[3]).strip() if len(linha) > 3 else ''
                
                print(f"[DEBUG] Linha {i}: PI={pi_planilha}, Data={data_planilha_str}, Val={valor_planilha_str}")
                
                if pi_planilha != str(pi).strip():
                    continue
                
                print(f"[DEBUG] ✓ PI coincide!")
                
                valor_planilha = converter_valor_para_float(valor_planilha_str)
                print(f"[DEBUG] Valor convertido: {valor_planilha}")
                
                try:
                    data_planilha_obj = datetime.strptime(data_planilha_str, '%d/%m/%Y')
                except Exception as e:
                    print(f"[DEBUG] Erro ao converter data da planilha: {e}")
                    continue
                
                if not (data_inicio <= data_planilha_obj <= data_fim):
                    print(f"[DEBUG] Data fora do intervalo")
                    continue
                
                print(f"[DEBUG] ✓ Data dentro do intervalo!")
                
                if valor > 0:
                    diferenca_percentual = abs(valor - valor_planilha) / valor * 100
                else:
                    diferenca_percentual = 0
                
                print(f"[DEBUG] Diferença: {diferenca_percentual:.2f}%")
                
                if diferenca_percentual <= 1:
                    print(f"[DEBUG] ⚠️ DUPLICATA ENCONTRADA!")
                    duplicatas_encontradas.append({
                        'linha': i,
                        'data': data_planilha_str,
                        'categoria': categoria_planilha,
                        'valor': valor_planilha,
                        'diferenca': diferenca_percentual
                    })
            
            except Exception as e:
                print(f"[DEBUG] Erro ao processar linha {i}: {e}")
                continue
        
        if duplicatas_encontradas:
            print(f"[DEBUG] {len(duplicatas_encontradas)} duplicata(s) encontrada(s)!")
            return {
                'duplicada': True,
                'detalhes': duplicatas_encontradas[0],
                'quantidade': len(duplicatas_encontradas)
            }
        
        print(f"[DEBUG] Nenhuma duplicata encontrada")
        return {'duplicada': False}
    
    except Exception as e:
        print(f"[ERRO verificar_valor] {e}")
        import traceback
        traceback.print_exc()
        return {'duplicada': False}

# ==================== HANDLERS DO TELEGRAM ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot de Controle de Importação POP+\n\n"
        "📋 O que posso processar:\n"
        "✅ XMLs de Notas Fiscais (com AFRMM e SISCOMEX)\n"
        "✅ ZIP com múltiplos XMLs (soma tudo)\n"
        "✅ Comprovantes (imagem/PDF)\n\n"
        "📝 Como usar:\n"
        "1. Envie: PI: YWXS2025115\n"
        "2. Anexe o documento\n"
        "3. Confirme antes de gravar"
    )

async def processar_xml_ou_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        pi = extrair_pi_da_mensagem(update.message.caption) if update.message.caption else None
        file = await update.message.document.get_file()
        file_bytes = await file.download_as_bytearray()
        file_name = update.message.document.file_name
        
        if file_name.lower().endswith('.zip'):
            print("[DEBUG] ZIP detectado")
            msg = await update.message.reply_text("📦 Processando ZIP...")
            zip_resultado = processar_zip_xmls(file_bytes)
            
            if not zip_resultado or zip_resultado['quantidade'] == 0:
                await msg.edit_text("❌ Erro no ZIP ou nenhum XML de venda.")
                return
            
            if not zip_resultado['xmls']:
                await msg.edit_text("❌ Só tinha REMESSAs no ZIP.")
                return
            
            tipo_nota = identificar_tipo_nota(zip_resultado['xmls'][0])
            if tipo_nota == 'DESCONHECIDO':
                await msg.edit_text("⚠️ Tipo não identificado.")
                return
            
            tipo_map = {'IMPORTACAO': '📦 Importação', 'LDL_PARA_POP': '🔄 LDL→POP+', 'POP_PARA_CLIENTE': '💰 POP+→Cliente'}
            mensagem_remessas = f"\n⚠️ {zip_resultado['remessas_ignoradas']} REMESSAs ignoradas" if zip_resultado.get('remessas_ignoradas', 0) > 0 else ""
            
            user_data_temp[user_id] = {'tipo': 'zip', 'zip_resultado': zip_resultado, 'tipo_nota': tipo_nota, 'pi': pi}
            
            if not pi:
                keyboard = [[InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
                await msg.edit_text(
                    f"📦 ZIP processado!\n\n"
                    f"Tipo: {tipo_map[tipo_nota]}\n"
                    f"XMLs: {zip_resultado['quantidade']}\n"
                    f"Valor: R$ {zip_resultado['valor_total']:,.2f}{mensagem_remessas}\n\n"
                    f"📝 Informe a PI:\nDigite: PI: YWXS2025115",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_zip")], [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
            await msg.edit_text(
                f"📦 ZIP processado!\n\nTipo: {tipo_map[tipo_nota]}\nXMLs: {zip_resultado['quantidade']}\n"
                f"Valor: R$ {zip_resultado['valor_total']:,.2f}\nPI: {pi}{mensagem_remessas}\n\n"
                f"⚠️ Será UMA linha. Confirma?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            print("[DEBUG] XML único")
            xml_content = file_bytes.decode('utf-8')
            dados = extrair_dados_xml(xml_content, pi)
            
            if not dados:
                await update.message.reply_text("❌ Erro no XML.")
                return
            
            tipo_nota = identificar_tipo_nota(dados)
            if tipo_nota == 'REMESSA':
                await update.message.reply_text("⚠️ REMESSA não é contabilizada.")
                return
            if tipo_nota == 'DESCONHECIDO':
                await update.message.reply_text("⚠️ Tipo não identificado.")
                return
            
            user_data_temp[user_id] = {'tipo': 'xml', 'dados': dados, 'tipo_nota': tipo_nota}
            
            if not dados['pi'] or dados['pi'] == 'N/A':
                keyboard = [[InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
                await update.message.reply_text(
                    f"📄 XML processado!\n\nNF: {dados['numero_nf']}\nValor: R$ {dados['valor_nf']:,.2f}\n\n"
                    f"📝 Informe a PI:\nDigite: PI: YWXS2025115",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            tipo_map = {'IMPORTACAO': '📦 Importação', 'LDL_PARA_POP': '🔄 LDL→POP+', 'POP_PARA_CLIENTE': '💰 POP+→Cliente'}
            keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_xml")], [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
            await update.message.reply_text(
                f"📄 XML processado!\n\nTipo: {tipo_map[tipo_nota]}\nNF: {dados['numero_nf']}\n"
                f"Valor: R$ {dados['valor_nf']:,.2f}\nPI: {dados['pi']}\n\nConfirma?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        print(f"[ERRO] {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)}")

async def callback_confirmar_xml(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    dados_temp = user_data_temp[user_id]
    
    verificacao = verificar_xml_duplicado(
        dados_temp['dados']['numero_nf'],
        dados_temp['dados']['cnpj_emitente'],
        dados_temp['dados']['valor_nf']
    )
    
    if verificacao['duplicada']:
        keyboard = [
            [InlineKeyboardButton("✅ Adicionar mesmo assim", callback_data="forcar_xml")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]
        ]
        await query.edit_message_text(
            f"⚠️ POSSÍVEL DUPLICATA!\n\n"
            f"NF: {verificacao['detalhes']['numero_nf']}\n"
            f"Data anterior: {verificacao['detalhes']['data']}\n"
            f"Aba: {verificacao['aba']}\n\n"
            f"Deseja adicionar mesmo assim?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        user_data_temp[user_id]['verificacao_duplicata'] = True
        return
    
    sucesso = gravar_xml_no_sheets(dados_temp['dados'], dados_temp['tipo_nota'])
    
    if sucesso:
        await query.edit_message_text(f"✅ XML gravado!\n\nNF: {dados_temp['dados']['numero_nf']}\nPI: {dados_temp['dados']['pi']}\n\n📊 Planilha atualizada!")
    else:
        await query.edit_message_text("❌ Erro ao gravar.")
    
    del user_data_temp[user_id]

async def callback_forcar_xml(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    dados_temp = user_data_temp[user_id]
    sucesso = gravar_xml_no_sheets(dados_temp['dados'], dados_temp['tipo_nota'])
    
    if sucesso:
        await query.edit_message_text(
            f"⚠️ XML gravado (com duplicata detectada)!\n\n"
            f"NF: {dados_temp['dados']['numero_nf']}\n"
            f"PI: {dados_temp['dados']['pi']}\n\n"
            f"⚠️ Verifique depois!"
        )
    else:
        await query.edit_message_text("❌ Erro ao gravar.")
    
    del user_data_temp[user_id]

async def callback_confirmar_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    dados_temp = user_data_temp[user_id]
    pi = dados_temp['pi']
    
    if not pi:
        await query.edit_message_text("⚠️ PI não informada! Envie novamente com: PI: YWXS2025115")
        del user_data_temp[user_id]
        return
    
    sucesso = gravar_zip_consolidado_no_sheets(pi, dados_temp['zip_resultado'], dados_temp['tipo_nota'])
    
    if sucesso:
        await query.edit_message_text(
            f"✅ ZIP gravado!\n\nXMLs: {dados_temp['zip_resultado']['quantidade']}\n"
            f"Valor: R$ {dados_temp['zip_resultado']['valor_total']:,.2f}\nPI: {pi}\n\n📊 UMA linha na planilha!"
        )
    else:
        await query.edit_message_text("❌ Erro ao gravar.")
    
    del user_data_temp[user_id]

async def callback_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id in user_data_temp:
        del user_data_temp[user_id]
    await query.edit_message_text("❌ Cancelado.")

async def processar_imagem_ou_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        pi = extrair_pi_da_mensagem(update.message.caption) if update.message.caption else None
        
        is_pdf = False
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
        else:
            file = await update.message.document.get_file()
            if update.message.document.mime_type == 'application/pdf':
                is_pdf = True
        
        file_bytes = await file.download_as_bytearray()
        msg = await update.message.reply_text("🔍 Processando...")
        
        texto = extrair_texto_imagem(file_bytes, is_pdf)
        
        if not texto or len(texto) < 10:
            await msg.edit_text("❌ Não consegui extrair texto.")
            return
        
        dados = extrair_dados_comprovante(texto)
        
        if not dados['valores']:
            await msg.edit_text("⚠️ Nenhum valor encontrado.\nUse: /despesa PI 1234.56 Descrição")
            return
        
        user_data_temp[user_id] = {'tipo': 'comprovante', 'valores': dados['valores'], 'data': dados['data'], 
                                     'descricao': dados['descricao'], 'pi': pi, 'texto': texto[:500]}
        
        valores_str = "\n".join([f"• R$ {v:,.2f}" for v in dados['valores'][:5]])
        keyboard = []
        for i in range(0, len(CATEGORIAS_DESPESAS), 2):
            row = []
            for j in range(2):
                if i + j < len(CATEGORIAS_DESPESAS):
                    row.append(InlineKeyboardButton(CATEGORIAS_DESPESAS[i+j], callback_data=f"cat_{i+j}"))
            keyboard.append(row)
        
        await msg.edit_text(
            f"📄 Processado!\n\nValores:\n{valores_str}\n\nData: {dados['data']}\n"
            f"Tipo: {dados['descricao']}\nPI: {pi or 'Não informada'}\n\n👇 Categoria:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        print(f"[ERRO] {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)}")

async def callback_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    cat_index = int(query.data.split('_')[1])
    categoria = CATEGORIAS_DESPESAS[cat_index]
    user_data_temp[user_id]['categoria'] = categoria
    
    if categoria == "Outros":
        keyboard = [[InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
        await query.edit_message_text(f"📝 'Outros' selecionado\n\nDigite descrição:\n(ex: Taxa alfandegária)", reply_markup=InlineKeyboardMarkup(keyboard))
        user_data_temp[user_id]['aguardando_descricao_outros'] = True
        return
    
    dados = user_data_temp[user_id]
    if not dados.get('pi'):
        keyboard = [[InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
        await query.edit_message_text(f"✅ Categoria: {categoria}\n\n📝 Informe PI:\nDigite: PI: YWXS2025115", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if len(dados['valores']) > 1:
        keyboard = []
        for i, v in enumerate(dados['valores']):
            keyboard.append([InlineKeyboardButton(f"R$ {v:,.2f}", callback_data=f"val_{i}")])
        
        keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")])
        keyboard.append([InlineKeyboardButton("📝 Digitar outro valor", callback_data="digitar_valor")])
        
        valores_encontrados = f"\n✅ {len(dados['valores'])} valores encontrados:\n"
        await query.edit_text(
            f"✅ {categoria}\nPI: {dados['pi']}\n{valores_encontrados}\n💰 Qual valor?", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        valor = dados['valores'][0]
        categoria_exibir = f"Outros - {dados.get('categoria_personalizada', '')}" if categoria == "Outros" else categoria
        keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_despesa")], [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
        dados['valor_final'] = valor
        await query.edit_message_text(
            f"📋 RESUMO\n\nPI: {dados['pi']}\nCategoria: {categoria_exibir}\n"
            f"Valor: R$ {valor:,.2f}\nData: {dados['data']}\n\nConfirma?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def callback_digitar_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    user_data_temp[user_id]['aguardando_valor_manual'] = True
    keyboard = [[InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
    await query.edit_message_text(
        "💰 Digite o valor (ex: 1234.56 ou 1234,56):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def callback_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    val_index = int(query.data.split('_')[1])
    dados = user_data_temp[user_id]
    valor = dados['valores'][val_index]
    dados['valor_final'] = valor
    
    categoria = dados.get('categoria_personalizada', dados['descricao']) if dados['categoria'] == "Outros" else dados['categoria']
    keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_despesa")], [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
    await query.edit_message_text(
        f"📋 RESUMO\n\nPI: {dados['pi']}\nCategoria: {categoria}\n"
        f"Valor: R$ {valor:,.2f}\nData: {dados['data']}\n\nConfirma?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def callback_confirmar_despesa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    print(f"[DEBUG] callback_confirmar_despesa acionado para user {user_id}")
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    dados = user_data_temp[user_id]
    
    print(f"[DEBUG] Dados: {dados}")
    print(f"[DEBUG] Verificando duplicata para: PI={dados['pi']}, Valor={dados['valor_final']}, Data={dados['data']}")
    
    data_normalizada = normalizar_data(dados['data'])
    print(f"[DEBUG] Data normalizada: {data_normalizada}")
    
    verificacao = verificar_valor_duplicado_pi(
        dados['pi'],
        dados['valor_final'],
        data_normalizada,
        dados['categoria']
    )
    
    print(f"[DEBUG] Resultado: {verificacao}")
    
    if verificacao['duplicada']:
        print(f"[DEBUG] DUPLICATA ENCONTRADA!")
        keyboard = [
            [InlineKeyboardButton("✅ Adicionar mesmo assim", callback_data="forcar_despesa")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]
        ]
        
        detalhes = verificacao['detalhes']
        await query.edit_message_text(
            f"⚠️ POSSÍVEL DUPLICATA!\n\n"
            f"PI: {dados['pi']}\n"
            f"Valor: R$ {dados['valor_final']:,.2f}\n"
            f"Categoria: {dados['categoria']}\n\n"
            f"Já existe lançamento similar:\n"
            f"Data: {detalhes['data']}\n"
            f"Valor: R$ {detalhes['valor']:,.2f}\n"
            f"Categoria: {detalhes['categoria']}\n"
            f"Diferença: {detalhes['diferenca']:.2f}%\n\n"
            f"Deseja adicionar mesmo assim?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    print(f"[DEBUG] Sem duplicata, gravando...")
    
    if dados['categoria'] == "Outros":
        descricao = dados.get('categoria_personalizada', dados['descricao'])
    else:
        descricao = dados['categoria']
    
    sucesso = gravar_despesa_no_sheets(dados['pi'], dados['categoria'], dados['valor_final'], data_normalizada, descricao)
    
    if sucesso:
        await query.edit_message_text(f"✅ Despesa gravada!\n\nPI: {dados['pi']}\nValor: R$ {dados['valor_final']:,.2f}\n\n📊 Planilha atualizada!")
    else:
        await query.edit_message_text("❌ Erro ao gravar.")
    
    del user_data_temp[user_id]

async def callback_forcar_despesa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if user_id not in user_data_temp:
        await query.edit_message_text("❌ Dados expirados.")
        return
    
    dados = user_data_temp[user_id]
    
    if dados['categoria'] == "Outros":
        descricao = dados.get('categoria_personalizada', dados['descricao'])
    else:
        descricao = dados['categoria']
    
    sucesso = gravar_despesa_no_sheets(dados['pi'], dados['categoria'], dados['valor_final'], dados['data'], descricao)
    
    if sucesso:
        await query.edit_message_text(
            f"⚠️ Despesa gravada (com duplicata detectada)!\n\n"
            f"PI: {dados['pi']}\nValor: R$ {dados['valor_final']:,.2f}\n\n"
            f"⚠️ Verifique depois!"
        )
    else:
        await query.edit_message_text("❌ Erro ao gravar.")
    
    del user_data_temp[user_id]

async def processar_mensagem_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data_temp:
        return
    
    texto = update.message.text.strip()
    dados = user_data_temp[user_id]
    
    if dados.get('aguardando_valor_manual'):
        try:
            valor = float(texto.replace(',', '.'))
            dados['valor_final'] = valor
            dados['aguardando_valor_manual'] = False
            
            categoria = dados.get('categoria_personalizada', dados['descricao']) if dados['categoria'] == "Outros" else dados['categoria']
            keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_despesa")], [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]]
            await update.message.reply_text(
                f"📋 RESUMO\n\nPI: {dados['pi']}\nCategoria: {categoria}\n"
                f"Valor: R$ {valor:,.2f}\nData: {dados['data']}\n\nConfirma?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        except:
            await update.message.reply_text("⚠️ Valor inválido. Use: 1234.56 ou 1234,56")
            return
    
    if dados.get('aguardando_descricao_outros'):
        dados['categoria_personalizada'] = texto
        dados['aguardando_descricao_outros'] = False
        if not dados.get('pi'):
            await update.message.reply_text(f"✅ Descrição: {texto}\n\n📝 Informe PI:\nDigite: PI: YWXS2025115")
            return
        await update.message.reply_text("✅ Descrição salva! Agora escolha o valor (use botões acima).")
        return
    
    pi = extrair_pi_da_mensagem(texto)
    if pi:
        dados['pi'] = pi
        
        keyboard = []
        for i in range(0, len(CATEGORIAS_DESPESAS), 2):
            row = []
            for j in range(2):
                if i + j < len(CATEGORIAS_DESPESAS):
                    row.append(InlineKeyboardButton(CATEGORIAS_DESPESAS[i+j], callback_data=f"cat_{i+j}"))
            keyboard.append(row)
        
        await update.message.reply_text(
            f"✅ PI definida: {pi}\n\n👇 Agora selecione a categoria:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text("⚠️ PI não identificada. Use: PI: YWXS2025115")

async def comando_despesa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("❌ Uso: /despesa PI VALOR Descrição\nEx: /despesa YWXS2025115 1234.56 Frete")
        return
    
    try:
        pi = context.args[0].upper()
        valor = float(context.args[1].replace(',', '.'))
        descricao = ' '.join(context.args[2:])
        data = datetime.now().strftime('%d/%m/%Y')
        categoria = "Outros"
        
        for cat in CATEGORIAS_DESPESAS[:-1]:
            if cat.upper() in descricao.upper():
                categoria = cat
                break
        
        if gravar_despesa_no_sheets(pi, categoria, valor, data, descricao):
            await update.message.reply_text(f"✅ Despesa lançada!\n\nPI: {pi}\nValor: R$ {valor:,.2f}\nCategoria: {categoria}\n\n📊 Planilha atualizada!")
        else:
            await update.message.reply_text("❌ Erro ao gravar.")
    except:
        await update.message.reply_text("❌ Valor inválido. Use números.")

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        planilha = conectar_planilha()
        await update.message.reply_text(
            f"📊 Status\n\n✅ Bot online\n✅ Google Sheets: {planilha.title}\n✅ OCR OK\n✅ ZIP OK\n\n"
            "Comandos:\n/start - Instruções\n/despesa - Lançar manual\n/info - Status"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao conectar: {str(e)}")

# ==================== MAIN ====================

def main():
    PORT = int(os.environ.get("PORT", 10000))
    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")

    if not WEBHOOK_URL:
        raise RuntimeError("❌ RENDER_EXTERNAL_URL não definida")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(False)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("despesa", comando_despesa))

    app.add_handler(MessageHandler(
        filters.Document.FileExtension("xml") | filters.Document.FileExtension("zip"),
        processar_xml_ou_zip
    ))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF,
        processar_imagem_ou_pdf
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem_texto))

    app.add_handler(CallbackQueryHandler(callback_confirmar_xml, pattern="^confirmar_xml$"))
    app.add_handler(CallbackQueryHandler(callback_forcar_xml, pattern="^forcar_xml$"))
    app.add_handler(CallbackQueryHandler(callback_confirmar_zip, pattern="^confirmar_zip$"))
    app.add_handler(CallbackQueryHandler(callback_confirmar_despesa, pattern="^confirmar_despesa$"))
    app.add_handler(CallbackQueryHandler(callback_forcar_despesa, pattern="^forcar_despesa$"))
    app.add_handler(CallbackQueryHandler(callback_cancelar, pattern="^cancelar$"))
    app.add_handler(CallbackQueryHandler(callback_categoria, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(callback_valor, pattern="^val_"))
    app.add_handler(CallbackQueryHandler(callback_digitar_valor, pattern="^digitar_valor$"))

    print("🤖 Bot iniciado em modo WEBHOOK")
    print(f"🌐 URL pública: {WEBHOOK_URL}")
    print(f"🔗 Registrando webhook em: {WEBHOOK_URL}/{TELEGRAM_TOKEN}")

    # Testar conexão ao iniciar
    try:
        planilha = conectar_planilha()
        print(f"✅ Conectado à planilha: {planilha.title}")
    except Exception as e:
        print(f"❌ ERRO ao conectar planilha: {e}")
        print("⚠️ Verifique as credenciais e o ID da planilha")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == '__main__':
    main()
