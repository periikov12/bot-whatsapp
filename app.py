from telethon import TelegramClient, events
from flask import Flask, request, jsonify
from flask_cors import CORS
import asyncio
import os
import threading
import uuid
import time
import re
from datetime import datetime
from queue import Queue
from PIL import Image

# ============================================
# CONFIGURACIÓN DE TELEGRAM
# ============================================
api_id = 33774512
api_hash = '72af30fe5dd18997fc50a0a53e544f0c'
CHAT_ID_NM = -1003732799535       # Chat para /nm y /c4
CHAT_ID_BILLETERAS = -1003771916049 # Chat para /billeteras

COMANDO_NM = '/nm'
COMANDO_C4 = '/c4'
COMANDO_BILLETERAS = '/billeteras' 

CARPETA_DESCARGAS = 'descargas'
os.makedirs(CARPETA_DESCARGAS, exist_ok=True)

client = TelegramClient('sesion_peliculas', api_id, api_hash)

solicitudes_nm = {}
cola_nm = Queue()
resultados_nm = {}

solicitudes_c4 = {}
cola_c4 = Queue()
resultados_c4 = {}

solicitudes_billeteras = {}
cola_billeteras = Queue()
resultados_billeteras = {}

buffer_billeteras = {}

MI_ID = None
USUARIO_MUSE_ID = 8132840073

_SC = str.maketrans({
    'ʀ': 'r', 'ᴇ': 'e', 'ᴜ': 'u', 'ᴛ': 't', 'ᴀ': 'a', 'ᴅ': 'd', 'ᴏ': 'o', 'ɪ': 'i',
    'ɴ': 'n', 'ꜰ': 'f', 'ᴄ': 'c', 'ʟ': 'l', 'ᴍ': 'm', 'ᴘ': 'p', 'ᴡ': 'w', 'ʏ': 'y',
    'ᴢ': 'z', 'ɢ': 'g', 'ʙ': 'b', 'ᴋ': 'k', 'ᴊ': 'j', 'ᴠ': 'v', 'ʜ': 'h', 'ꜱ': 's',
    'ǫ': 'o',
})

def normalizar(texto):
    return (texto or '').translate(_SC).lower()

def parsear_nm(texto):
    texto = (texto or '').replace('\\n', '\n')
    personas = []
    actual = None
    for raw in texto.split('\n'):
        ln = raw.strip()
        if not ln: continue
        if ln.startswith('**#SAKUMADATA') or ln.startswith('#SAKUMADATA'): continue
        if ln.startswith('➻'): continue
        if re.match(r'^ᴛᴏᴛᴀʟ\s*[:：]', ln, re.IGNORECASE): continue
        if 'ᴄʀᴇ́ᴅɪᴛᴏs' in ln or 'ᴄʀᴇᴅɪᴛᴏs' in ln: continue
        if 'ᴜsᴜᴀʀɪᴏ' in ln: continue
        if re.fullmatch(r'[-=_]{3,}', ln): continue
        if re.fullmatch(r'ʙᴜsǫᴜᴇᴅᴀ.*', ln): continue

        m_bloque = re.fullmatch(r'\[(\d+)\]', ln)
        if m_bloque:
            if actual: personas.append(actual)
            actual = {'dni': '', 'campos': []}
            continue

        m_dni_solo = re.fullmatch(r'[\*\`_\s]*(\d{8})[\*\`_\s]*', ln)
        if m_dni_solo:
            if actual and (actual.get('dni') or actual.get('campos')): personas.append(actual)
            actual = {'dni': m_dni_solo.group(1), 'campos': []}
            continue

        m_campo_dosp = re.match(r'^([^\s:：]+)\s*[:：]\s*(.+)$', ln)
        if m_campo_dosp and actual is not None:
            clave_raw = m_campo_dosp.group(1).strip()
            valor = m_campo_dosp.group(2).strip().strip('`*_ ')
            clave_norm = normalizar(clave_raw).strip().rstrip('.').strip()

            if clave_norm == 'dni' or 'dni' in clave_norm:
                actual['dni'] = re.sub(r'[^\d]', '', valor)
                continue
            actual['campos'].append(f"{clave_raw} : {valor}")
            continue

        if actual is not None:
            limpio = re.sub(r'^[\*\`_]+|[\*\`_]+$', '', ln).strip()
            if limpio: actual['campos'].append(limpio)

    if actual: personas.append(actual)
    personas = [p for p in personas if p.get('dni') and p['dni'].isdigit() and len(p['dni']) == 8]
    return personas

def es_el_logo(ruta_imagen):
    try:
        with Image.open(ruta_imagen) as img:
            img = img.convert('RGB')
            img_pequena = img.resize((1, 1))
            r, g, b = img_pequena.getpixel((0, 0))
            if r > 80 and r > (g + 30) and r > (b + 30):
                return True
            return False
    except Exception as e:
        return False

# ============================================
# HANDLERS DE TELEGRAM
# ============================================
@client.on(events.MessageEdited(chats=[CHAT_ID_NM, CHAT_ID_BILLETERAS]))
@client.on(events.NewMessage(chats=[CHAT_ID_NM, CHAT_ID_BILLETERAS]))
async def handler_telegram(event):
    if event.sender_id == USUARIO_MUSE_ID:
        texto_usr = event.text or ''
        if texto_usr.startswith('/nm') or texto_usr.startswith('/c4') or texto_usr.startswith('/billeteras'):
            return
            
    texto = event.text or ''
    chat_id = event.chat_id
    
    if texto:
        print(f"📩 Msg de chat {chat_id}: {texto[:50]}...")

    # --- LÓGICA PARA BILLETERAS ---
    if chat_id == CHAT_ID_BILLETERAS:
        sol_pendiente = None
        for x in solicitudes_billeteras.values():
            if not x.get('completado') and (time.time() - datetime.fromisoformat(x['fecha']).timestamp()) <= 120:
                sol_pendiente = x
                break

        if sol_pendiente:
            if event.sender_id == USUARIO_MUSE_ID: return
            if 'Solicitud recibida' in texto or 'Créditos restantes' in texto: return

            mid = sol_pendiente['message_id']
            if mid not in buffer_billeteras:
                buffer_billeteras[mid] = {'texto': ''}
            
            if texto not in buffer_billeteras[mid]['texto']:
                buffer_billeteras[mid]['texto'] += texto + '\n'

            if 'ESTADO DE CUENTA' in buffer_billeteras[mid]['texto']:
                print("✅ Procesando BILLETERAS (Fin detectado)...")
                
                txt = buffer_billeteras[mid]['texto']
                
                match_titular = re.search(r'\*?Titular\*?\s*➾', txt)
                if match_titular:
                    txt = txt[match_titular.start():]
                
                match_estado = re.search(r'\*?\[⚡\]\s*ESTADO DE CUENTA\*?', txt)
                if match_estado:
                    txt = txt[:match_estado.start()]

                txt = txt.replace('*', '')
                txt = re.sub(r'\n{3,}', '\n\n', txt)
                txt = '\n'.join([line.strip() for line in txt.split('\n') if line.strip()]).strip()

                if not txt:
                    txt = "❌ No se pudieron obtener datos válidos."

                sol_pendiente['resultados'] = txt
                sol_pendiente['completado'] = True
                sol_pendiente['fecha_completado'] = datetime.now().isoformat()
                
                del buffer_billeteras[mid]
        return

    # --- LÓGICA PARA /NM Y /C4 (Chat Principal) ---
    if chat_id == CHAT_ID_NM:
        sol_nm = None
        for x in solicitudes_nm.values():
            if not x.get('completado') and (time.time() - datetime.fromisoformat(x['fecha']).timestamp()) <= 120:
                sol_nm = x
                break

        if sol_nm:
            if event.sender_id == USUARIO_MUSE_ID: return
            if 'ʙᴜsᴄᴀɴᴅᴏ' in texto or 'ᴘᴏʀ ꜰᴀᴠᴏʀ' in texto or 'sᴇʟᴇᴄᴄɪᴏɴᴀ' in texto:
                return
            
            if 'reniec' in texto.lower() or re.search(r'\d{8}', texto):
                personas = parsear_nm(texto)
                if personas:
                    print("✅ Detectado bloque NM.")
                    sol_nm['resultados'] = personas
                    sol_nm['completado'] = True
                    sol_nm['fecha_completado'] = datetime.now().isoformat()
            return

        sol_c4 = None
        for req in solicitudes_c4.values():
            if not req.get('completado') and req['numero'] in texto:
                sol_c4 = req
                break

        if sol_c4:
            if event.sender_id == USUARIO_MUSE_ID: return
            print(f"⚡ Respuesta C4 recibida.")
            if event.photo or event.document:
                if not any(f"c4_{event.id}_" in img for img in sol_c4['imagenes']):
                    try:
                        ruta = await event.download_media(file=CARPETA_DESCARGAS)
                        if ruta:
                            nombre_unico = f"c4_{event.id}_{uuid.uuid4().hex[:6]}.jpg"
                            ruta_unica = os.path.join(CARPETA_DESCARGAS, nombre_unico)
                            os.rename(ruta, ruta_unica)
                            if not es_el_logo(ruta_unica):
                                sol_c4['imagenes'].append(nombre_unico)
                                print(f"📸 Imagen C4 guardada.")
                            else:
                                os.remove(ruta_unica)
                    except Exception: pass

            sol_c4['ultima_actividad'] = time.time()
            
            async def finalizar_c4(req_ref):
                await asyncio.sleep(3.5)
                if not req_ref.get('completado') and (time.time() - req_ref.get('ultima_actividad', 0)) >= 3.0:
                    req_ref['completado'] = True
                    req_ref['fecha_completado'] = datetime.now().isoformat()
            asyncio.create_task(finalizar_c4(sol_c4))

# ============================================
# COLAS
# ============================================
async def procesar_cola_nm():
    while True:
        item = await asyncio.get_event_loop().run_in_executor(None, cola_nm.get)
        request_id, query = item['request_id'], item['query']
        try:
            mensaje = await client.send_message(CHAT_ID_NM, f"{COMANDO_NM} {query}")
            resultados_nm[request_id] = {'ok': True, 'message_id': mensaje.id}
        except Exception as e:
            resultados_nm[request_id] = {'ok': False, 'error': str(e)}

async def procesar_cola_c4():
    while True:
        item = await asyncio.get_event_loop().run_in_executor(None, cola_c4.get)
        request_id, numero = item['request_id'], item['numero']
        try:
            mensaje = await client.send_message(CHAT_ID_NM, f"{COMANDO_C4} {numero}")
            resultados_c4[request_id] = {'ok': True, 'message_id': mensaje.id}
        except Exception as e:
            resultados_c4[request_id] = {'ok': False, 'error': str(e)}

async def procesar_cola_billeteras():
    while True:
        item = await asyncio.get_event_loop().run_in_executor(None, cola_billeteras.get)
        request_id, numero = item['request_id'], item['numero']
        try:
            mensaje = await client.send_message(CHAT_ID_BILLETERAS, f"{COMANDO_BILLETERAS} {numero}")
            resultados_billeteras[request_id] = {'ok': True, 'message_id': mensaje.id}
        except Exception as e:
            resultados_billeteras[request_id] = {'ok': False, 'error': str(e)}

# ============================================
# FLASK
# ============================================
app = Flask(__name__)
CORS(app)

@app.route('/nm', methods=['POST'])
def pedir_nm():
    data = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    if not query: return jsonify({'error': 'Consulta vacía'}), 400
    request_id = str(uuid.uuid4())
    cola_nm.put({'request_id': request_id, 'query': query})
    inicio = time.time()
    while time.time() - inicio < 20:
        if request_id in resultados_nm:
            r = resultados_nm.pop(request_id)
            if r['ok']:
                mid = r['message_id']
                solicitudes_nm[mid] = {'tipo': 'nm', 'completado': False, 'resultados': [], 'fecha': datetime.now().isoformat()}
                return jsonify({'success': True, 'message_id': mid})
            return jsonify({'error': r['error']}), 500
        time.sleep(0.5)
    return jsonify({'error': 'Timeout'}), 500

@app.route('/nm_estado/<int:message_id>')
def ver_estado_nm(message_id):
    sol = solicitudes_nm.get(message_id)
    if not sol: return jsonify({'error': 'No encontrado'}), 404
    return jsonify(sol)

@app.route('/c4', methods=['POST'])
def pedir_c4():
    data = request.get_json(silent=True) or {}
    numero = (data.get('numero') or '').strip()
    if not numero: return jsonify({'error': 'Número vacío'}), 400
    request_id = str(uuid.uuid4())
    cola_c4.put({'request_id': request_id, 'numero': numero})
    inicio = time.time()
    while time.time() - inicio < 20:
        if request_id in resultados_c4:
            r = resultados_c4.pop(request_id)
            if r['ok']:
                mid = r['message_id']
                solicitudes_c4[mid] = {'tipo': 'c4', 'numero': numero, 'completado': False, 'imagenes': [], 'fecha': datetime.now().isoformat()}
                return jsonify({'success': True, 'message_id': mid})
            return jsonify({'error': r['error']}), 500
        time.sleep(0.5)
    return jsonify({'error': 'Timeout'}), 500

@app.route('/c4_estado/<int:message_id>')
def ver_estado_c4(message_id):
    sol = solicitudes_c4.get(message_id)
    if not sol: return jsonify({'error': 'No encontrado'}), 404
    imagenes_rutas = []
    for img in sol.get('imagenes', []):
        ruta_abs = os.path.abspath(os.path.join(CARPETA_DESCARGAS, img))
        if os.path.exists(ruta_abs): imagenes_rutas.append(ruta_abs)
    respuesta = sol.copy()
    respuesta['imagenes'] = imagenes_rutas
    return jsonify(respuesta)

@app.route('/billeteras', methods=['POST'])
def pedir_billeteras():
    data = request.get_json(silent=True) or {}
    numero = (data.get('numero') or '').strip()
    if not numero: return jsonify({'error': 'Número vacío'}), 400
    
    if not numero.isdigit() or len(numero) != 9: 
        return jsonify({'error': 'Número inválido (debe ser 9 dígitos)'}), 400
    
    request_id = str(uuid.uuid4())
    cola_billeteras.put({'request_id': request_id, 'numero': numero})
    inicio = time.time()
    while time.time() - inicio < 20:
        if request_id in resultados_billeteras:
            r = resultados_billeteras.pop(request_id)
            if r['ok']:
                mid = r['message_id']
                solicitudes_billeteras[mid] = {
                    'message_id': mid, 
                    'tipo': 'billeteras', 
                    'numero': numero, 
                    'completado': False, 
                    'resultados': '', 
                    'fecha': datetime.now().isoformat()
                }
                return jsonify({'success': True, 'message_id': mid})
            return jsonify({'error': r['error']}), 500
        time.sleep(0.5)
    return jsonify({'error': 'Timeout'}), 500

@app.route('/billeteras_estado/<int:message_id>')
def ver_estado_billeteras(message_id):
    sol = solicitudes_billeteras.get(message_id)
    if not sol: return jsonify({'error': 'No encontrado'}), 404
    return jsonify(sol)

# ============================================
# INICIO (ADAPTADO PARA RENDER)
# ============================================
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

async def main():
    global MI_ID
    await client.start()
    me = await client.get_me()
    MI_ID = me.id
    print(f"👤 Telegram conectado como: {me.first_name} (ID: {MI_ID})")
    asyncio.create_task(procesar_cola_nm())
    asyncio.create_task(procesar_cola_c4())
    asyncio.create_task(procesar_cola_billeteras())
    await client.run_until_disconnected()

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    with client:
        client.loop.run_until_complete(main())