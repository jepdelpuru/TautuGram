import asyncio
import requests
import os
import json
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError

# ================================
# CONFIGURACIÓN
# ================================
# Definir el directorio base absoluto (para Docker y Windows)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TELEGRAM_TOKEN = ''
TARGET_CHAT_ID = #Id group 1
SECOND_TARGET_CHAT_ID =   # id group 2
TARGET_MESSAGE_THREAD_ID = 21203
TAUTULLI_BASE_URL = 'http://192.168.0.143:8181/api/v2'
TAUTULLI_APIKEY = ''

# Usar rutas absolutas para los archivos de persistencia
LAST_MESSAGE_FILE = os.path.join(BASE_DIR, "last_sessions_message_ids.txt")
LAST_SESSION_FILE = os.path.join(BASE_DIR, "ultimo_dato_sesion.json")

# Valor máximo esperado de ancho de banda (en kbps) para construir la barra azul.
MAX_BANDWIDTH = 120000
# ================================

# Crear el bot
bot = Bot(token=TELEGRAM_TOKEN)

# -------------------------------
# Funciones para obtener datos de Tautulli
# -------------------------------
def get_activity():
    params = {
        'apikey': TAUTULLI_APIKEY,
        'cmd': 'get_activity'
    }
    try:
        response = requests.get(TAUTULLI_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error connecting to Tautulli: {e}")
        return None

def get_history(limit=10):
    params = {
        'apikey': TAUTULLI_APIKEY,
        'cmd': 'get_history',
        'length': limit
    }
    try:
        response = requests.get(TAUTULLI_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error connecting to Tautulli (history): {e}")
        return None

# -------------------------------
# Funciones de formato
# -------------------------------
def build_progress_bar(progress, bar_length=15, filled_icon="🟩"):
    filled_length = int(round(bar_length * progress / 100))
    bar = filled_icon * filled_length + "⬜" * (bar_length - filled_length)
    return f"{bar} {progress:.1f}%"

def format_time(seconds):
    seconds = float(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:04.1f}s"
    else:
        return f"{minutes}m {secs:04.1f}s"

def format_size(bytes_size):
    try:
        bytes_size = float(bytes_size)
    except (ValueError, TypeError):
        bytes_size = 0
    if bytes_size < 1024:
        return f"{bytes_size:.0f} B"
    elif bytes_size < 1024**2:
        return f"{bytes_size/1024:.1f} KB"
    elif bytes_size < 1024**3:
        return f"{bytes_size/1024**2:.1f} MB"
    else:
        return f"{bytes_size/1024**3:.1f} GB"

def format_datetime(timestamp):
    try:
        ts = int(timestamp)
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return "Fecha no disponible"

# -------------------------------
# Funciones para guardar y cargar los datos de las últimas 10 reproducciones
# -------------------------------
def guardar_datos_sesion(session):
    # Calcular el view_offset en segundos y solo almacenar si se ha visto al menos 60 segundos
    view_offset_sec = float(session.get('view_offset', 0)) / 1000
    if view_offset_sec < 60:
        return

    sesiones = []
    if os.path.exists(LAST_SESSION_FILE):
        try:
            with open(LAST_SESSION_FILE, "r") as f:
                sesiones = json.load(f)
                if not isinstance(sesiones, list):
                    sesiones = [sesiones]
        except Exception as e:
            print("Error al cargar datos previos:", e)
    
    current_title = session.get('full_title', 'Sin título')
    current_user = session.get('user', 'Desconocido')
    
    # Determinar si se realizó transcode o fue direct play
    transcode_decision = session.get('transcode_decision', 'direct play').lower()
    if transcode_decision != "direct play":
        resolution = session.get('stream_video_resolution', session.get('video_resolution', 'No especificada'))
        transcode_flag = True
    else:
        resolution = session.get('video_resolution', 'No especificada')
        transcode_flag = False

    actualizado = False
    # Buscar en toda la lista si ya existe un registro para este título y usuario
    for s in sesiones:
        if s.get("titulo") == current_title and s.get("usuario") == current_user:
            s["view_offset"] = view_offset_sec
            s["timestamp"] = int(datetime.now().timestamp())
            s["resolucion"] = resolution
            s["transcode"] = transcode_flag
            actualizado = True
            break

    # Si no se encontró, se crea un nuevo registro
    if not actualizado:
        duration = float(session.get('duration', 1)) / 1000  # duración total en segundos
        datos = {
            "titulo": current_title,
            "usuario": current_user,
            "view_offset": view_offset_sec,
            "duration": duration,
            "file_size": session.get('file_size', '0'),
            "biblioteca": session.get('library_name', 'Desconocida'),
            "dispositivo": session.get('device', 'No especificado'),
            "player": session.get('player', 'No especificado'),
            "resolucion": resolution,
            "transcode": transcode_flag,
            "timestamp": int(datetime.now().timestamp())
        }
        sesiones.insert(0, datos)

    # Limitar la lista a 10 elementos
    sesiones = sesiones[:10]
    try:
        with open(LAST_SESSION_FILE, "w") as f:
            json.dump(sesiones, f)
    except Exception as e:
        print("Error al guardar los datos de la sesión:", e)

def cargar_datos_sesiones():
    if os.path.exists(LAST_SESSION_FILE):
        try:
            with open(LAST_SESSION_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                else:
                    print("Formato inesperado en datos de sesión")
                    return []
        except Exception as e:
            print("Error al cargar los datos de la sesión:", e)
    return []

# -------------------------------
# Función para construir el mensaje de sesiones o historial
# -------------------------------
def build_sessions_message():
    activity = get_activity()
    if activity is None:
        return "No se puede conectar a Tautulli. Esperando..."
    
    sessions = activity.get('response', {}).get('data', {}).get('sessions', [])
    
    if sessions:
        # Guardar cada sesión activa en el historial (solo si se ha visto >= 60 s)
        for session in sessions:
            guardar_datos_sesion(session)
        
        messages = []
        for idx, session in enumerate(sessions, start=1):
            title = session.get('full_title', 'Sin título')
            user = session.get('user', 'Desconocido')
            progress = float(session.get('progress_percent', 0))
            view_offset = float(session.get('view_offset', 0)) / 1000
            duration = float(session.get('duration', 1)) / 1000

            playback_bar = build_progress_bar(progress, bar_length=15, filled_icon="🟩")
            time_str = f"{format_time(view_offset)} / {format_time(duration)}"
            
            bandwidth_raw = session.get('bandwidth', 0)
            try:
                bandwidth_value = float(bandwidth_raw) if bandwidth_raw != "" else 0
            except (ValueError, TypeError):
                bandwidth_value = 0
            bandwidth_percentage = (bandwidth_value / MAX_BANDWIDTH) * 100
            if bandwidth_percentage > 100:
                bandwidth_percentage = 100
            bandwidth_bar = build_progress_bar(bandwidth_percentage, bar_length=15, filled_icon="🟦")
            
            transcode_decision = session.get('transcode_decision', 'direct play').lower()
            status_icon = "⚠️" if transcode_decision != "direct play" else "✅"
            
            session_message = (
                f"*Sesión {idx}:*\n"
                f"🎬 *Reproduciendo:* `{title}`\n"
                f"👤 *Usuario:* `{user}`\n"
                f"⏱️ *Tiempo:* `{time_str}`\n"
                f"📊 *Progreso reproducción:*\n{playback_bar}\n"
                f"📶 *Ancho de banda:* `{bandwidth_value} kbps`\n{bandwidth_bar}\n"
            )
            
            if transcode_decision != "direct play":
                transcode_progress = float(session.get('transcode_progress', 0))
                transcode_bar = build_progress_bar(transcode_progress, bar_length=15, filled_icon="🟧")
                transcode_speed = session.get('transcode_speed', 'N/A')
                session_message += f"🍊 *Progreso transcodificación:*\n{transcode_bar}\n"
                session_message += f"⚡ *Velocidad transcodificación:* `{transcode_speed}`\n"
            
            file_size = session.get('file_size', '0')
            file_size_str = format_size(file_size)
            session_message += f"📦 *Tamaño del archivo:* `{file_size_str}`\n"
            
            library_name = session.get('library_name', 'Desconocida')
            device = session.get('device', 'No especificado')
            player = session.get('player', 'No especificado')
            if transcode_decision != "direct play" and session.get('stream_video_resolution'):
                video_resolution = session.get('stream_video_resolution', 'No especificada')
            else:
                video_resolution = session.get('video_resolution', 'No especificada')
            session_message += (
                f"\n🏛️ *Biblioteca:* `{library_name}`\n"
                f"💻 *Dispositivo:* `{device}`\n"
                f"🎥 *Player:* `{player}`\n"
                f"{status_icon} *Resolución:* `{video_resolution}`\n"
            )
            messages.append(session_message)
        return "\n".join(messages)
    else:
        # Si no hay sesiones activas, usamos las últimas sesiones almacenadas
        # Si no hay sesiones activas, usamos las últimas sesiones almacenadas
        stored_sessions = cargar_datos_sesiones()
        if stored_sessions:
            messages = []
            for datos in reversed(stored_sessions):
                timestamp = datos.get('timestamp')
                if timestamp:
                    # Formatear fecha y hora como "dd/mm/yyyy HH:MM"
                    fecha_hora = datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
                else:
                    fecha_hora = "??/??/???? ??:??"
                tiempo_str = f"{format_time(datos['view_offset'])} / {format_time(datos['duration'])}"
                # Calcular el porcentaje de progreso basado en view_offset y duración total
                progress = (datos['view_offset'] / datos['duration'] * 100) if datos['duration'] > 0 else 0
                playback_bar = build_progress_bar(progress, bar_length=15, filled_icon="🟩")
                status_icon = "⚠️" if datos.get('transcode') else "✅"
                session_message = (
                    f"*{fecha_hora} Reproducción:*\n"
                    f"🎬 *Reproduciendo:* `{datos['titulo']}`\n"
                    f"👤 *Usuario:* `{datos['usuario']}`\n"
                    f"⏱️ *Tiempo:* `{tiempo_str}`\n"
                    f"{playback_bar}\n"
                    f"📦 *Tamaño del archivo:* `{format_size(datos['file_size'])}`\n"
                    f"🏛️ *Biblioteca:* `{datos['biblioteca']}`\n"
                    f"💻 *Dispositivo:* `{datos['dispositivo']}`\n"
                    f"🎥 *Player:* `{datos['player']}`\n"
                    f"{status_icon} *Resolución:* `{datos['resolucion']}`\n"
                )
                messages.append(session_message)
            return "\n".join(messages)

        else:
            # Como respaldo, se utiliza el historial de Tautulli
            history = get_history(limit=10)
            if history is None:
                return "No se puede conectar a Tautulli. Esperando..."
            history_sessions = history.get('response', {}).get('data', {}).get('data', [])
            if not history_sessions:
                return "No hay reproducciones en curso ni historial reciente."
            
            history_sessions = list(reversed(history_sessions))
            messages = ["*Últimas 10 reproducciones:*"]
            for idx, session in enumerate(history_sessions, start=1):
                title = session.get('full_title', 'Sin título')
                user = session.get('user', 'Desconocido')
                watched_at_raw = session.get('watched_at', session.get('date', None))
                if watched_at_raw:
                    watched_at = format_datetime(watched_at_raw)
                else:
                    watched_at = "Fecha no disponible"
                view_offset = float(session.get('view_offset', 0))
                duration = float(session.get('duration', 1))
                time_str = f"{format_time(view_offset)} / {format_time(duration)}"
                
                session_message = (
                    f"*Reproducción {idx}:*\n"
                    f"🎬 *Título:* `{title}`\n"
                    f"👤 *Usuario:* `{user}`\n"
                    f"🗓 *Fecha:* `{watched_at}`\n"
                    f"⏱️ *Tiempo visto:* `{time_str}`\n"
                )
                messages.append(session_message)
            return "\n".join(messages)

# -------------------------------
# Funciones para almacenar/recuperar los IDs de los mensajes
# -------------------------------
def load_last_message_ids():
    if os.path.exists(LAST_MESSAGE_FILE):
        try:
            with open(LAST_MESSAGE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print("Error al leer los IDs de mensajes:", e)
    return None

def save_last_message_ids(msg1, msg2):
    try:
        with open(LAST_MESSAGE_FILE, "w") as f:
            json.dump({"msg1": msg1, "msg2": msg2}, f)
    except Exception as e:
        print("Error al guardar los IDs de mensajes:", e)

# -------------------------------
# Tarea asíncrona para actualizar los mensajes
# -------------------------------
async def update_sessions_message_task():
    previous_text = build_sessions_message()
    # Intentar cargar los IDs previos
    msg_ids = load_last_message_ids()
    if msg_ids:
        msg1_id = msg_ids.get("msg1")
        msg2_id = msg_ids.get("msg2")
    else:
        msg1_id = None
        msg2_id = None

    # Si no se encontraron IDs, se envían nuevos mensajes
    if msg1_id is None:
        msg1 = await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=previous_text,
            parse_mode='Markdown',
            message_thread_id=TARGET_MESSAGE_THREAD_ID
        )
        msg1_id = msg1.message_id
    if msg2_id is None:
        msg2 = await bot.send_message(
            chat_id=SECOND_TARGET_CHAT_ID,
            text=previous_text,
            parse_mode='Markdown'
        )
        msg2_id = msg2.message_id

    save_last_message_ids(msg1_id, msg2_id)

    while True:
        new_text = build_sessions_message()
        if new_text != previous_text:
            try:
                await bot.edit_message_text(
                    chat_id=TARGET_CHAT_ID,
                    message_id=msg1_id,
                    text=new_text,
                    parse_mode='Markdown'
                )
            except TelegramError as e:
                if "message to edit not found" in str(e).lower():
                    msg1 = await bot.send_message(
                        chat_id=TARGET_CHAT_ID,
                        text=new_text,
                        parse_mode='Markdown',
                        message_thread_id=TARGET_MESSAGE_THREAD_ID
                    )
                    msg1_id = msg1.message_id
                else:
                    print("Error al actualizar el primer mensaje:", e)

            try:
                await bot.edit_message_text(
                    chat_id=SECOND_TARGET_CHAT_ID,
                    message_id=msg2_id,
                    text=new_text,
                    parse_mode='Markdown'
                )
            except TelegramError as e:
                if "message to edit not found" in str(e).lower():
                    msg2 = await bot.send_message(
                        chat_id=SECOND_TARGET_CHAT_ID,
                        text=new_text,
                        parse_mode='Markdown'
                    )
                    msg2_id = msg2.message_id
                else:
                    print("Error al actualizar el segundo mensaje:", e)

            previous_text = new_text
            save_last_message_ids(msg1_id, msg2_id)
        await asyncio.sleep(15)

# -------------------------------
# Función principal
# -------------------------------
async def main():
    await update_sessions_message_task()

if __name__ == '__main__':
    asyncio.run(main())
