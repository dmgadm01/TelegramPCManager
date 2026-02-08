"""
TelegramPCManager - Telegram бот для управления компьютером
Автор: DmG
Python 3.14.3
"""

import asyncio
import subprocess
import webbrowser
import os
import io
import ctypes
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

import psutil

import config

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


_unauthorized_attempts = {}
_MAX_ATTEMPTS = 5

def is_authorized(user_id: int) -> bool:
    """Проверка авторизации пользователя"""
    return user_id in config.ALLOWED_USER_IDS


def log_unauthorized_attempt(user_id: int, username: str = None):
    """Логировать попытку несанкционированного доступа"""
    _unauthorized_attempts[user_id] = _unauthorized_attempts.get(user_id, 0) + 1
    if _unauthorized_attempts[user_id] <= _MAX_ATTEMPTS:
        print(f"⚠️ Попытка доступа: ID={user_id}, username={username}, попытка #{_unauthorized_attempts[user_id]}")


def is_blocked(user_id: int) -> bool:
    """Проверить, заблокирован ли пользователь (слишком много попыток)"""
    return _unauthorized_attempts.get(user_id, 0) > _MAX_ATTEMPTS


@dp.message.outer_middleware()
async def auth_middleware(handler, event, data):
    """Глобальная проверка авторизации для всех сообщений"""
    user_id = event.from_user.id
    
    if is_blocked(user_id):
        return
    
    if not is_authorized(user_id):
        log_unauthorized_attempt(user_id, event.from_user.username)
        return
    
    return await handler(event, data)


@dp.callback_query.outer_middleware()
async def auth_callback_middleware(handler, event, data):
    """Глобальная проверка авторизации для callback"""
    user_id = event.from_user.id
    
    if is_blocked(user_id):
        return
    
    if not is_authorized(user_id):
        log_unauthorized_attempt(user_id, event.from_user.username)
        await event.answer("⛔ Нет доступа", show_alert=True)
        return
    
    return await handler(event, data)




_cached_volume_level = 50
_volume_interface = None


def _get_volume_interface():
    """Получить кэшированный интерфейс громкости"""
    global _volume_interface
    if _volume_interface is None:
        try:
            from pycaw.pycaw import AudioUtilities
            devices = AudioUtilities.GetSpeakers()
            _volume_interface = devices.EndpointVolume
        except Exception as e:
            print(f"Volume interface error: {e}")
    return _volume_interface


def get_current_volume() -> int:
    """Получить текущий уровень громкости (0-100)"""
    global _cached_volume_level
    try:
        volume = _get_volume_interface()
        if volume:
            _cached_volume_level = int(volume.GetMasterVolumeLevelScalar() * 100)
    except Exception as e:
        print(f"Volume error: {e}")
    return _cached_volume_level


def set_volume(level: int):
    """Установить уровень громкости (0-100)"""
    global _cached_volume_level
    level = max(0, min(100, level))
    
    try:
        volume = _get_volume_interface()
        if volume:
            volume.SetMasterVolumeLevelScalar(level / 100, None)
            _cached_volume_level = level
    except Exception as e:
        print(f"Set volume error: {e}")


def toggle_mute():
    """Включить/выключить звук"""
    try:
        volume = _get_volume_interface()
        if volume:
            current_mute = volume.GetMute()
            volume.SetMute(not current_mute, None)
            return not current_mute
    except Exception:
        pass
    
    try:
        subprocess.run(['powershell', '-Command', 
            '(New-Object -ComObject WScript.Shell).SendKeys([char]173)'], 
            capture_output=True, timeout=5)
    except Exception:
        pass
    return True


def is_muted() -> bool:
    """Проверить, выключен ли звук"""
    try:
        volume = _get_volume_interface()
        if volume:
            return bool(volume.GetMute())
    except Exception:
        pass
    return False


def get_audio_devices() -> list:
    """Получить список аудио устройств вывода"""
    try:
        from pycaw.pycaw import AudioUtilities, EDataFlow, DEVICE_STATE
        import warnings
        warnings.filterwarnings("ignore")
        
        devices = []
        deviceEnumerator = AudioUtilities.GetDeviceEnumerator()
        if deviceEnumerator:
            collection = deviceEnumerator.EnumAudioEndpoints(0, DEVICE_STATE.ACTIVE.value)
            count = collection.GetCount()
            
            for i in range(count):
                try:
                    device = collection.Item(i)
                    if device:
                        device_id = device.GetId()
                        audio_dev = AudioUtilities.CreateDevice(device)
                        name = audio_dev.FriendlyName if audio_dev and audio_dev.FriendlyName else f"Device {i}"
                        
                        devices.append({
                            'id': device_id,
                            'name': name,
                            'index': i
                        })
                except Exception:
                    continue
        return devices
    except Exception as e:
        print(f"Error getting audio devices: {e}")
        return get_audio_devices_powershell()


def get_audio_devices_powershell() -> list:
    """Получить устройства через PowerShell"""
    try:
        ps_script = '''
$devices = Get-CimInstance -Namespace root/cimv2 -ClassName Win32_SoundDevice | Where-Object {$_.Status -eq 'OK'}
$devices | ForEach-Object { $_.Name }
'''
        result = subprocess.run(['powershell', '-Command', ps_script], 
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            devices = []
            for i, name in enumerate(result.stdout.strip().split('\n')):
                if name.strip():
                    devices.append({
                        'id': str(i),
                        'name': name.strip(),
                        'index': i
                    })
            return devices
    except Exception:
        pass
    return []


def get_default_audio_device() -> str:
    """Получить имя текущего устройства по умолчанию"""
    try:
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        if speakers:
            return speakers.FriendlyName
    except Exception:
        pass
    return "Неизвестно"


def set_audio_device(device_id: str) -> bool:
    """Установить устройство вывода по умолчанию"""
    try:
        ps_script = f'''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig {{
    void Reserved1();
    void Reserved2();
    void Reserved3();
    void Reserved4();
    void Reserved5();
    void Reserved6();
    void Reserved7();
    void Reserved8();
    void Reserved9();
    void Reserved10();
    [PreserveSig]
    int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string deviceId, [MarshalAs(UnmanagedType.U4)] uint role);
}}

[ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
class PolicyConfigClient {{ }}

public class AudioSwitcher {{
    public static void SetDefault(string deviceId) {{
        IPolicyConfig config = (IPolicyConfig)new PolicyConfigClient();
        config.SetDefaultEndpoint(deviceId, 0); // eConsole
        config.SetDefaultEndpoint(deviceId, 1); // eMultimedia  
        config.SetDefaultEndpoint(deviceId, 2); // eCommunications
    }}
}}
"@
[AudioSwitcher]::SetDefault("{device_id}")
'''
        result = subprocess.run(['powershell', '-Command', ps_script], 
                               capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception as e:
        print(f"Error setting audio device: {e}")
        return False


def take_screenshot() -> bytes:
    """Сделать скриншот и вернуть как bytes"""
    import mss
    from mss.tools import to_png
    
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)
        return to_png(screenshot.rgb, screenshot.size)


def get_clipboard_image() -> bytes:
    """Получить изображение из буфера обмена, если есть"""
    try:
        from PIL import ImageGrab, Image
        import io
        
        clipboard_content = ImageGrab.grabclipboard()
        
        if clipboard_content is None:
            return None
        
        if isinstance(clipboard_content, list):
            for path in clipboard_content:
                if isinstance(path, str) and path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')):
                    try:
                        with Image.open(path) as img:
                            buffer = io.BytesIO()
                            img.save(buffer, format='PNG')
                            buffer.seek(0)
                            return buffer.read()
                    except Exception:
                        continue
            return None
        
        if hasattr(clipboard_content, 'save'):
            buffer = io.BytesIO()
            clipboard_content.save(buffer, format='PNG')
            buffer.seek(0)
            return buffer.read()
            
    except Exception as e:
        print(f"Clipboard image error: {e}")
    return None


def get_uptime() -> str:
    """Получить время работы системы"""
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time
    
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days} дн.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    if seconds > 0 or not parts:
        parts.append(f"{seconds} сек.")
    
    return " ".join(parts)


VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
KEYEVENTF_KEYUP = 0x0002


def press_media_key(vk_code: int):
    """Нажать медиа-клавишу"""
    ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def media_play_pause():
    """Play/Pause"""
    press_media_key(VK_MEDIA_PLAY_PAUSE)


def media_next():
    """Следующий трек"""
    press_media_key(VK_MEDIA_NEXT_TRACK)


def media_prev():
    """Предыдущий трек"""
    press_media_key(VK_MEDIA_PREV_TRACK)



def get_brightness() -> int:
    """Получить текущую яркость (0-100)"""
    try:
        import screen_brightness_control as sbc
        brightness = sbc.get_brightness()
        return brightness[0] if isinstance(brightness, list) else brightness
    except Exception:
        return -1


def set_brightness(level: int) -> bool:
    """Установить яркость (0-100)"""
    try:
        import screen_brightness_control as sbc
        level = max(0, min(100, level))
        sbc.set_brightness(level)
        return True
    except Exception:
        return False



_recording = False
_recording_data = []
_sample_rate = 44100


async def start_recording() -> bool:
    """Начать запись с микрофона"""
    global _recording, _recording_data
    
    if _recording:
        return False
    
    try:
        import sounddevice as sd
        _recording_data = []
        _recording = True
        
        def callback(indata, frames, time, status):
            if _recording:
                _recording_data.append(indata.copy())
        
        sd.default.samplerate = _sample_rate
        sd.default.channels = 1
        stream = sd.InputStream(callback=callback)
        stream.start()
        
        global _recording_stream
        _recording_stream = stream
        
        return True
    except Exception as e:
        print(f"Recording error: {e}")
        _recording = False
        return False


async def stop_recording() -> bytes:
    """Остановить запись и вернуть WAV файл"""
    global _recording, _recording_data, _recording_stream
    
    if not _recording:
        return None
    
    _recording = False
    
    try:
        import numpy as np
        import io
        import wave
        
        if _recording_stream:
            _recording_stream.stop()
            _recording_stream.close()
        
        if not _recording_data:
            return None
        
        audio_data = np.concatenate(_recording_data, axis=0)
        
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_sample_rate)
            audio_int16 = (audio_data * 32767).astype(np.int16)
            wav_file.writeframes(audio_int16.tobytes())
        
        wav_buffer.seek(0)
        return wav_buffer.read()
    except Exception as e:
        print(f"Stop recording error: {e}")
        return None
    finally:
        _recording_data = []



def get_top_processes(count: int = 10) -> list:
    """Получить топ процессов по использованию CPU/RAM"""
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'cpu_percent']):
        try:
            info = proc.info
            if info['name'] and info['memory_percent']:
                processes.append({
                    'pid': info['pid'],
                    'name': info['name'][:20],
                    'memory': info['memory_percent'],
                    'cpu': info['cpu_percent'] or 0
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    processes.sort(key=lambda x: x['memory'], reverse=True)
    return processes[:count]


def kill_process_by_name(name: str) -> tuple:
    """Убить процесс по имени. Возврат (успех, сообщение)"""
    killed = 0
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and name.lower() in proc.info['name'].lower():
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    if killed > 0:
        return True, f"Завершено процессов: {killed}"
    return False, "Процесс не найден"


def kill_process_by_pid(pid: int) -> tuple:
    """Убить процесс по PID. Возврат (успех, сообщение)"""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        proc.kill()
        return True, f"Процесс {name} (PID: {pid}) завершён"
    except psutil.NoSuchProcess:
        return False, "Процесс не найден"
    except psutil.AccessDenied:
        return False, "Нет доступа (требуются права админа)"
    except Exception as e:
        return False, f"Ошибка: {e}"


def get_system_temps() -> str:
    """Попытка получить температуру (если доступно)"""
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            result = []
            for name, entries in temps.items():
                for entry in entries:
                    result.append(f"{entry.label or name}: {entry.current}°C")
            return "\n".join(result) if result else "Недоступно"
    except Exception:
        pass
    return "Недоступно на Windows"



def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура"""
    keyboard = [
        [KeyboardButton(text="🔊 Громкость"), KeyboardButton(text="🎵 Медиа")],
        [KeyboardButton(text="⏻ Питание"), KeyboardButton(text="💡 Яркость")],
        [KeyboardButton(text="📸 Скриншот"), KeyboardButton(text="📋 Буфер")],
        [KeyboardButton(text="📊 Система"), KeyboardButton(text="❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_system_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура мониторинга системы"""
    keyboard = [
        [
            InlineKeyboardButton(text="📋 Процессы", callback_data="sys_processes"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="sys_refresh"),
        ],
        [
            InlineKeyboardButton(text="💾 Диски", callback_data="sys_disks"),
            InlineKeyboardButton(text="🌡 Температура", callback_data="sys_temps"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_volume_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура управления громкостью"""
    keyboard = [
        [
            InlineKeyboardButton(text="🔇 Mute", callback_data="vol_mute"),
            InlineKeyboardButton(text="🔈 -10", callback_data="vol_minus10"),
            InlineKeyboardButton(text="🔊 +10", callback_data="vol_plus10"),
        ],
        [
            InlineKeyboardButton(text="0%", callback_data="vol_0"),
            InlineKeyboardButton(text="25%", callback_data="vol_25"),
            InlineKeyboardButton(text="50%", callback_data="vol_50"),
            InlineKeyboardButton(text="75%", callback_data="vol_75"),
            InlineKeyboardButton(text="100%", callback_data="vol_100"),
        ],
        [
            InlineKeyboardButton(text="🎧 Устройства", callback_data="vol_devices"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="vol_refresh"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_audio_devices_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора аудио устройств"""
    devices = get_audio_devices()
    current_device = get_default_audio_device()
    
    keyboard = []
    for device in devices:
        name = device['name']
        if len(name) > 30:
            name = name[:27] + "..."
        prefix = "✅ " if device['name'] in current_device or current_device in device['name'] else "🔊 "
        keyboard.append([
            InlineKeyboardButton(
                text=f"{prefix}{name}",
                callback_data=f"audio_{device['index']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="vol_back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_power_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура управления питанием"""
    keyboard = [
        [
            InlineKeyboardButton(text="⏻ Выключить", callback_data="power_shutdown"),
            InlineKeyboardButton(text="🔄 Перезагрузка", callback_data="power_restart"),
        ],
        [
            InlineKeyboardButton(text="😴 Сон", callback_data="power_sleep"),
            InlineKeyboardButton(text="🔒 Блокировка", callback_data="power_lock"),
        ],
        [
            InlineKeyboardButton(text="⏰ Таймеры", callback_data="power_timers"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="power_cancel"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_timer_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура таймеров выключения"""
    keyboard = [
        [
            InlineKeyboardButton(text="15 мин", callback_data="timer_900"),
            InlineKeyboardButton(text="30 мин", callback_data="timer_1800"),
            InlineKeyboardButton(text="1 час", callback_data="timer_3600"),
        ],
        [
            InlineKeyboardButton(text="1.5 часа", callback_data="timer_5400"),
            InlineKeyboardButton(text="2 часа", callback_data="timer_7200"),
            InlineKeyboardButton(text="3 часа", callback_data="timer_10800"),
        ],
        [
            InlineKeyboardButton(text="❌ Отменить", callback_data="timer_cancel"),
            InlineKeyboardButton(text="◀️ Назад", callback_data="timer_back"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_media_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура управления медиа"""
    keyboard = [
        [
            InlineKeyboardButton(text="⏮ Пред.", callback_data="media_prev"),
            InlineKeyboardButton(text="⏯ Play/Pause", callback_data="media_playpause"),
            InlineKeyboardButton(text="⏭ След.", callback_data="media_next"),
        ],
        [
            InlineKeyboardButton(text="🎤 Запись", callback_data="media_record"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_brightness_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура управления яркостью"""
    keyboard = [
        [
            InlineKeyboardButton(text="🔅 -20", callback_data="br_minus20"),
            InlineKeyboardButton(text="🔆 +20", callback_data="br_plus20"),
        ],
        [
            InlineKeyboardButton(text="25%", callback_data="br_25"),
            InlineKeyboardButton(text="50%", callback_data="br_50"),
            InlineKeyboardButton(text="75%", callback_data="br_75"),
            InlineKeyboardButton(text="100%", callback_data="br_100"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="br_refresh"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_record_keyboard(is_recording: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура записи микрофона"""
    if is_recording:
        keyboard = [[InlineKeyboardButton(text="⏹ Остановить запись", callback_data="rec_stop")]]
    else:
        keyboard = [[InlineKeyboardButton(text="🎤 Начать запись", callback_data="rec_start")]]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)



@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    if not is_authorized(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "🖥 Я бот для управления твоим компьютером.\n"
        "Выбери действие на клавиатуре ниже:",
        reply_markup=get_main_keyboard()
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    if not is_authorized(message.from_user.id):
        return
    
    help_text = """
📖 <b>Список команд:</b>

<b>🔊 Громкость:</b>
• Управление уровнем звука
• Mute/Unmute
• Выбор устройства вывода

<b>🎵 Медиа:</b>
• Play/Pause, след./пред. трек
• 🎤 Запись с микрофона

<b>⏻ Питание:</b>
• Выключение/Перезагрузка/Сон
• ⏰ Таймеры выключения

<b>💡 Яркость:</b>
• Регулировка яркости экрана

<b>📸 Скриншот:</b>
• Снимок экрана

<b>📋 Буфер обмена:</b>
• Отправь текст → скопируется на ПК
• Отправь фото → откроется на ПК
• /clipboard - получить текст с ПК

<b>� Файлы:</b>
• Отправь документ/видео/аудио → сохранится в Загрузки
• Опасные файлы (.exe, .bat и др.) заблокированы

<b>�📊 Система:</b>
• CPU/RAM/Диски с прогресс-барами
• 📋 Процессы - топ по памяти
• /kill [имя] - завершить процесс

<b>🔔 Уведомления:</b>
• Бот сообщит когда ПК включится
"""
    await message.answer(help_text, parse_mode=ParseMode.HTML)



@dp.message(F.text == "🔊 Громкость")
async def menu_volume(message: Message):
    """Меню громкости"""
    if not is_authorized(message.from_user.id):
        return
    
    current = get_current_volume()
    mute_status = " 🔇" if is_muted() else ""
    device_name = get_default_audio_device()
    if len(device_name) > 25:
        device_name = device_name[:22] + "..."
    await message.answer(
        f"🔊 <b>Управление громкостью</b>\n\n"
        f"Уровень: <b>{current}%</b>{mute_status}\n"
        f"Устройство: <b>{device_name}</b>",
        reply_markup=get_volume_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(F.text == "⏻ Питание")
async def menu_power(message: Message):
    """Меню питания"""
    if not is_authorized(message.from_user.id):
        return
    
    await message.answer(
        "⏻ <b>Управление питанием</b>\n\nВыберите действие:",
        reply_markup=get_power_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(F.text == "⏰ Таймеры")
async def menu_timers(message: Message):
    """Меню таймеров"""
    if not is_authorized(message.from_user.id):
        return
    
    await message.answer(
        "⏰ <b>Таймер выключения</b>\n\nВыберите время:",
        reply_markup=get_timer_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(F.text == "🌐 Браузер")
async def menu_browser(message: Message):
    """Меню браузера"""
    if not is_authorized(message.from_user.id):
        return
    
    await message.answer(
        "🌐 <b>Браузер</b>\n\n"
        "Доступные команды:\n"
        "• /open https://example.com - открыть ссылку\n"
        "• /youtube музыка - поиск на YouTube\n"
        "• /google как сделать - поиск в Google",
        parse_mode=ParseMode.HTML
    )


@dp.message(F.text == "📋 Буфер")
async def menu_clipboard(message: Message):
    """Меню буфера обмена"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        image_bytes = get_clipboard_image()
        if image_bytes:
            photo = BufferedInputFile(image_bytes, filename="clipboard.png")
            await message.answer_photo(photo, caption="📋 Изображение из буфера обмена")
            return
        
        import pyperclip
        text = pyperclip.paste()
        if text:
            if len(text) > 500:
                text = text[:500] + "..."
            await message.answer(
                f"📋 <b>Буфер обмена</b>\n\n"
                f"<code>{text}</code>\n\n"
                f"💡 Отправь текст — скопирую в буфер\n"
                f"📸 Отправь фото — открою на ПК",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer(
                "📋 <b>Буфер обмена пуст</b>\n\n"
                "💡 Отправь текст — скопирую в буфер\n"
                "📸 Отправь фото — открою на ПК",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.text == "📊 Система")
async def menu_status(message: Message):
    """Статус ПК"""
    if not is_authorized(message.from_user.id):
        return
    
    status_text = await get_system_status_text()
    await message.answer(status_text, parse_mode=ParseMode.HTML, reply_markup=get_system_keyboard())


async def get_system_status_text() -> str:
    """Получить текст статуса системы"""
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_freq = psutil.cpu_freq()
    cpu_freq_str = f" @ {cpu_freq.current:.0f} MHz" if cpu_freq else ""
    
    cpu_cores = psutil.cpu_count(logical=False)
    cpu_threads = psutil.cpu_count(logical=True)
    
    ram = psutil.virtual_memory()
    ram_used = ram.used / (1024 ** 3)
    ram_total = ram.total / (1024 ** 3)
    
    disk = psutil.disk_usage('C:')
    disk_used = disk.used / (1024 ** 3)
    disk_total = disk.total / (1024 ** 3)
    
    current_volume = get_current_volume()
    
    uptime_str = get_uptime()
    
    cpu_bar = get_progress_bar(cpu_percent)
    ram_bar = get_progress_bar(ram.percent)
    disk_bar = get_progress_bar(disk.percent)
    
    return f"""
📊 <b>Статус системы</b>

🖥 <b>CPU:</b> {cpu_percent}%{cpu_freq_str} ({cpu_cores}C/{cpu_threads}T)
{cpu_bar}

💾 <b>RAM:</b> {ram_used:.1f} / {ram_total:.1f} GB ({ram.percent}%)
{ram_bar}

💿 <b>Диск C:</b> {disk_used:.1f} / {disk_total:.1f} GB ({disk.percent}%)
{disk_bar}

🔊 <b>Громкость:</b> {current_volume}%
⏱ <b>Uptime:</b> {uptime_str}
"""


def get_progress_bar(percent: float, length: int = 10) -> str:
    """Создать прогресс-бар"""
    filled = int(percent / 100 * length)
    empty = length - filled
    return "▓" * filled + "░" * empty


@dp.message(F.text == "📸 Скриншот")
async def menu_screenshot(message: Message):
    """Сделать скриншот"""
    if not is_authorized(message.from_user.id):
        return
    
    await message.answer("📸 Делаю скриншот...")
    
    try:
        screenshot_bytes = take_screenshot()
        photo = BufferedInputFile(screenshot_bytes, filename="screenshot.png")
        await message.answer_photo(photo, caption="📸 Скриншот экрана")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.text == "🎵 Медиа")
async def menu_media(message: Message):
    """Меню управления медиа"""
    if not is_authorized(message.from_user.id):
        return
    
    await message.answer(
        "🎵 <b>Управление медиа</b>\n\nВыберите действие:",
        reply_markup=get_media_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.message(F.text == "💡 Яркость")
async def menu_brightness(message: Message):
    """Меню яркости экрана"""
    if not is_authorized(message.from_user.id):
        return
    
    brightness = get_brightness()
    if brightness >= 0:
        text = f"💡 <b>Яркость экрана</b>\n\nТекущая: <b>{brightness}%</b>"
    else:
        text = "💡 <b>Яркость экрана</b>\n\n⚠️ Регулировка недоступна на этом устройстве"
    
    await message.answer(text, reply_markup=get_brightness_keyboard(), parse_mode=ParseMode.HTML)


@dp.message(F.text == "❓ Помощь")
async def menu_help(message: Message):
    """Помощь"""
    await cmd_help(message)



@dp.callback_query(F.data.startswith("vol_"))
async def callback_volume(callback: CallbackQuery):
    """Обработка кнопок громкости"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("vol_", "")
    
    if action == "mute":
        muted = toggle_mute()
        status = "🔇 Звук выключен" if muted else "🔊 Звук включён"
        await callback.answer(status)
    elif action == "minus10":
        current = get_current_volume()
        set_volume(current - 10)
        await callback.answer(f"🔉 Громкость: {get_current_volume()}%")
    elif action == "plus10":
        current = get_current_volume()
        set_volume(current + 10)
        await callback.answer(f"🔊 Громкость: {get_current_volume()}%")
    elif action == "refresh":
        await callback.answer(f"🔊 Текущая громкость: {get_current_volume()}%")
    elif action == "devices":
        await callback.answer()
        current_device = get_default_audio_device()
        try:
            await callback.message.edit_text(
                f"🎧 <b>Выбор аудио устройства</b>\n\n"
                f"Текущее: <b>{current_device}</b>",
                reply_markup=get_audio_devices_keyboard(),
                parse_mode=ParseMode.HTML
            )
        except TelegramBadRequest:
            pass
        return
    elif action == "back":
        await callback.answer()
    elif action.isdigit():
        level = int(action)
        set_volume(level)
        await callback.answer(f"🔊 Громкость: {level}%")
    
    current = get_current_volume()
    mute_status = " 🔇" if is_muted() else ""
    device_name = get_default_audio_device()
    if len(device_name) > 25:
        device_name = device_name[:22] + "..."
    try:
        await callback.message.edit_text(
            f"🔊 <b>Управление громкостью</b>\n\n"
            f"Уровень: <b>{current}%</b>{mute_status}\n"
            f"Устройство: <b>{device_name}</b>",
            reply_markup=get_volume_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("audio_"))
async def callback_audio_device(callback: CallbackQuery):
    """Обработка выбора аудио устройства"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    device_index = int(callback.data.replace("audio_", ""))
    devices = get_audio_devices()
    
    if device_index < len(devices):
        device = devices[device_index]
        success = set_audio_device(device['id'])
        
        if success:
            global _volume_interface
            _volume_interface = None
            
            await callback.answer(f"✅ {device['name'][:30]}")
        else:
            await callback.answer("❌ Ошибка переключения")
    else:
        await callback.answer("❌ Устройство не найдено")
    
    current_device = get_default_audio_device()
    try:
        await callback.message.edit_text(
            f"🎧 <b>Выбор аудио устройства</b>\n\n"
            f"Текущее: <b>{current_device}</b>",
            reply_markup=get_audio_devices_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("power_"))
async def callback_power(callback: CallbackQuery):
    """Обработка кнопок питания"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("power_", "")
    
    CREATE_NO_WINDOW = 0x08000000
    
    if action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", "5"], creationflags=CREATE_NO_WINDOW)
        await callback.answer("⏻ ПК выключится через 5 секунд")
        await callback.message.edit_text("⏻ Компьютер выключается...")
    elif action == "restart":
        subprocess.run(["shutdown", "/r", "/t", "5"], creationflags=CREATE_NO_WINDOW)
        await callback.answer("🔄 ПК перезагрузится через 5 секунд")
        await callback.message.edit_text("🔄 Компьютер перезагружается...")
    elif action == "sleep":
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=CREATE_NO_WINDOW)
        await callback.answer("😴 ПК уходит в сон")
    elif action == "lock":
        ctypes.windll.user32.LockWorkStation()
        await callback.answer("🔒 ПК заблокирован")
    elif action == "cancel":
        subprocess.run(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)
        await callback.answer("✅ Выключение отменено")
        await callback.message.edit_text(
            "✅ Выключение/перезагрузка отменены",
            reply_markup=get_power_keyboard()
        )
    elif action == "timers":
        await callback.message.edit_text(
            "⏰ <b>Таймер выключения</b>\n\nВыберите время:",
            reply_markup=get_timer_keyboard(),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()


@dp.callback_query(F.data.startswith("timer_"))
async def callback_timer(callback: CallbackQuery):
    """Обработка кнопок таймера"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("timer_", "")
    
    CREATE_NO_WINDOW = 0x08000000
    
    if action == "cancel":
        subprocess.run(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)
        await callback.answer("✅ Таймер отменён")
        await callback.message.edit_text(
            "✅ Таймер выключения отменён",
            reply_markup=get_timer_keyboard()
        )
    elif action == "back":
        await callback.message.edit_text(
            "⏻ <b>Управление питанием</b>\n\nВыберите действие:",
            reply_markup=get_power_keyboard(),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()
    else:
        seconds = int(action)
        minutes = seconds // 60
        CREATE_NO_WINDOW = 0x08000000
        subprocess.run(["shutdown", "/s", "/t", str(seconds)], creationflags=CREATE_NO_WINDOW)
        await callback.answer(f"⏰ Таймер на {minutes} мин установлен")
        await callback.message.edit_text(
            f"⏰ ПК выключится через <b>{minutes} минут</b>",
            reply_markup=get_timer_keyboard(),
            parse_mode=ParseMode.HTML
        )


@dp.callback_query(F.data.startswith("media_"))
async def callback_media(callback: CallbackQuery):
    """Обработка кнопок медиа"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("media_", "")
    
    if action == "playpause":
        media_play_pause()
        await callback.answer("⏯ Play/Pause")
    elif action == "next":
        media_next()
        await callback.answer("⏭ Следующий трек")
    elif action == "prev":
        media_prev()
        await callback.answer("⏮ Предыдущий трек")
    elif action == "record":
        await callback.message.edit_text(
            "🎤 <b>Запись с микрофона</b>\n\nНажмите для начала записи:",
            reply_markup=get_record_keyboard(False),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()


@dp.callback_query(F.data.startswith("rec_"))
async def callback_record(callback: CallbackQuery):
    """Обработка кнопок записи микрофона"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("rec_", "")
    
    if action == "start":
        success = await start_recording()
        if success:
            await callback.message.edit_text(
                "🎤 <b>Запись идёт...</b>\n\n🔴 Нажмите для остановки:",
                reply_markup=get_record_keyboard(True),
                parse_mode=ParseMode.HTML
            )
            await callback.answer("🎤 Запись начата")
        else:
            await callback.answer("❌ Не удалось начать запись", show_alert=True)
    
    elif action == "stop":
        await callback.message.edit_text("⏳ Обработка записи...", parse_mode=ParseMode.HTML)
        await callback.answer()
        
        audio_data = await stop_recording()
        if audio_data:
            audio_file = BufferedInputFile(audio_data, filename="recording.wav")
            await callback.message.answer_audio(audio_file, caption="🎤 Запись с микрофона")
            await callback.message.delete()
        else:
            await callback.message.edit_text("❌ Ошибка записи", parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("br_"))
async def callback_brightness(callback: CallbackQuery):
    """Обработка кнопок яркости"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("br_", "")
    current = get_brightness()
    
    if action == "refresh":
        pass
    elif action == "minus20":
        set_brightness(current - 20)
    elif action == "plus20":
        set_brightness(current + 20)
    elif action in ["25", "50", "75", "100"]:
        set_brightness(int(action))
    
    new_brightness = get_brightness()
    if new_brightness >= 0:
        text = f"💡 <b>Яркость экрана</b>\n\nТекущая: <b>{new_brightness}%</b>"
    else:
        text = "💡 <b>Яркость экрана</b>\n\n⚠️ Недоступно"
    
    try:
        await callback.message.edit_text(text, reply_markup=get_brightness_keyboard(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass
    await callback.answer()


@dp.callback_query(F.data.startswith("sys_"))
async def callback_system(callback: CallbackQuery):
    """Обработка кнопок системного мониторинга"""
    if not is_authorized(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    action = callback.data.replace("sys_", "")
    
    if action == "refresh":
        status_text = await get_system_status_text()
        try:
            await callback.message.edit_text(
                status_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=get_system_keyboard()
            )
        except TelegramBadRequest:
            pass
        await callback.answer("🔄 Обновлено")
    
    elif action == "processes":
        processes = get_top_processes(10)
        if processes:
            text = "📋 <b>Топ процессов по памяти:</b>\n\n"
            for p in processes:
                text += f"<code>{p['pid']:>6}</code> | {p['name']:<20} | RAM: {p['memory']:.1f}%\n"
            text += "\n💡 <i>Для завершения: /kill имя или /killpid PID</i>"
        else:
            text = "❌ Не удалось получить список процессов"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="sys_processes")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sys_back")]
        ])
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
        await callback.answer()
    
    elif action == "disks":
        text = "💾 <b>Информация о дисках:</b>\n\n"
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                used_gb = usage.used / (1024 ** 3)
                total_gb = usage.total / (1024 ** 3)
                bar = get_progress_bar(usage.percent)
                text += f"<b>{partition.mountpoint}</b> ({partition.fstype})\n"
                text += f"{used_gb:.1f} / {total_gb:.1f} GB ({usage.percent}%)\n"
                text += f"{bar}\n\n"
            except Exception:
                continue
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sys_back")]
        ])
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
        await callback.answer()
    
    elif action == "temps":
        temps_text = get_system_temps()
        text = f"🌡 <b>Температура:</b>\n\n{temps_text}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sys_back")]
        ])
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
        await callback.answer()
    
    elif action == "back":
        status_text = await get_system_status_text()
        try:
            await callback.message.edit_text(
                status_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=get_system_keyboard()
            )
        except TelegramBadRequest:
            pass
        await callback.answer()



@dp.message(Command("volume"))
async def cmd_volume(message: Message):
    """Установить громкость: /volume 50"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Использование: /volume <0-100>")
        return
    
    try:
        level = int(args[1])
        set_volume(level)
        await message.answer(f"🔊 Громкость установлена: {get_current_volume()}%")
    except ValueError:
        await message.answer("❌ Укажите число от 0 до 100")


@dp.message(Command("shutdown"))
async def cmd_shutdown(message: Message):
    """Таймер выключения: /shutdown 3600"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Использование: /shutdown <секунды>\nПример: /shutdown 5400")
        return
    
    try:
        seconds = int(args[1])
        CREATE_NO_WINDOW = 0x08000000
        subprocess.run(["shutdown", "/s", "/t", str(seconds)], creationflags=CREATE_NO_WINDOW)
        minutes = seconds // 60
        await message.answer(f"⏰ Компьютер выключится через {minutes} минут ({seconds} сек)")
    except ValueError:
        await message.answer("❌ Укажите время в секундах")


@dp.message(Command("open"))
async def cmd_open(message: Message):
    """Открыть URL: /open https://google.com"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /open <url>")
        return
    
    url = args[1]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    
    webbrowser.open(url)
    await message.answer(f"🌐 Открываю: {url}")


@dp.message(Command("kill"))
async def cmd_kill(message: Message):
    """Завершить процесс по имени: /kill chrome"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /kill <имя_процесса>")
        return
    
    name = args[1]
    success, msg = kill_process_by_name(name)
    if success:
        await message.answer(f"✅ {msg}")
    else:
        await message.answer(f"❌ {msg}")


@dp.message(Command("killpid"))
async def cmd_killpid(message: Message):
    """Завершить процесс по PID: /killpid 1234"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Использование: /killpid <PID>")
        return
    
    try:
        pid = int(args[1])
        success, msg = kill_process_by_pid(pid)
        if success:
            await message.answer(f"✅ {msg}")
        else:
            await message.answer(f"❌ {msg}")
    except ValueError:
        await message.answer("❌ PID должен быть числом")


@dp.message(Command("youtube"))
async def cmd_youtube(message: Message):
    """Поиск на YouTube: /youtube music"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /youtube <запрос>")
        return
    
    query = args[1].replace(" ", "+")
    url = f"https://www.youtube.com/results?search_query={query}"
    webbrowser.open(url)
    await message.answer(f"🎬 Ищу на YouTube: {args[1]}")


@dp.message(Command("google"))
async def cmd_google(message: Message):
    """Поиск в Google: /google weather"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /google <запрос>")
        return
    
    query = args[1].replace(" ", "+")
    url = f"https://www.google.com/search?q={query}"
    webbrowser.open(url)
    await message.answer(f"🔍 Ищу в Google: {args[1]}")


@dp.message(Command("screenshot"))
async def cmd_screenshot(message: Message):
    """Сделать скриншот: /screenshot"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        screenshot_bytes = take_screenshot()
        photo = BufferedInputFile(screenshot_bytes, filename="screenshot.png")
        await message.answer_photo(photo, caption="📸 Скриншот экрана")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("cmd"))
async def cmd_execute(message: Message):
    """Выполнить команду: /cmd dir"""
    if not is_authorized(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Использование: /cmd [команда]")
        return
    
    command = args[1]
    
    dangerous_patterns = ['format', 'del /s', 'rd /s', 'rmdir /s', ':(){:|', 'rm -rf']
    if any(p in command.lower() for p in dangerous_patterns):
        await message.answer("⛔ Эта команда заблокирована из соображений безопасности")
        return
    
    try:
        CREATE_NO_WINDOW = 0x08000000
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            encoding='cp866',
            creationflags=CREATE_NO_WINDOW
        )
        output = result.stdout or result.stderr or "✅ Выполнено (нет вывода)"
        
        if len(output) > 4000:
            output = output[:4000] + "\n... (обрезано)"
        
        await message.answer(f"<pre>{output}</pre>", parse_mode=ParseMode.HTML)
    except subprocess.TimeoutExpired:
        await message.answer("⏰ Команда выполняется слишком долго")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("clipboard"))
async def cmd_clipboard(message: Message):
    """Получить текст из буфера обмена ПК"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        import pyperclip
        text = pyperclip.paste()
        if text:
            if len(text) > 4000:
                text = text[:4000] + "\n... (обрезано)"
            await message.answer(f"📋 <b>Буфер обмена:</b>\n\n<code>{text}</code>", parse_mode=ParseMode.HTML)
        else:
            await message.answer("📋 Буфер обмена пуст")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")



BLOCKED_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.com', '.scr', '.pif',
    '.msi', '.msp',
    '.vbs', '.vbe', '.js', '.jse', '.ws', '.wsf', '.wsc', '.wsh',
    '.ps1', '.psm1', '.psd1',
    '.reg',
    '.lnk',
    '.dll', '.sys',
    '.jar',
}


def is_file_allowed(filename: str) -> bool:
    """Проверить, разрешён ли файл для загрузки"""
    if not filename:
        return True
    ext = Path(filename).suffix.lower()
    return ext not in BLOCKED_EXTENSIONS


@dp.message(F.photo)
async def handle_photo(message: Message):
    """Получить фото → скачать и открыть на ПК"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"photo_{timestamp}.jpg"
        filepath = DOWNLOADS_DIR / filename
        
        await bot.download_file(file.file_path, filepath)
        
        os.startfile(str(filepath))
        
        await message.answer(f"📸 Фото сохранено и открыто:\n<code>{filepath}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.document)
async def handle_document(message: Message):
    """Получить документ → сохранить на ПК (без открытия)"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        doc = message.document
        filename = doc.file_name or f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        if not is_file_allowed(filename):
            ext = Path(filename).suffix
            await message.answer(f"⛔ Файлы с расширением <code>{ext}</code> заблокированы из соображений безопасности", parse_mode=ParseMode.HTML)
            return
        
        file = await bot.get_file(doc.file_id)
        filepath = DOWNLOADS_DIR / filename
        
        if filepath.exists():
            stem = filepath.stem
            suffix = filepath.suffix
            timestamp = datetime.now().strftime("%H%M%S")
            filepath = DOWNLOADS_DIR / f"{stem}_{timestamp}{suffix}"
        
        await bot.download_file(file.file_path, filepath)
        
        size_kb = doc.file_size / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
        
        await message.answer(
            f"📁 <b>Файл сохранён</b>\n\n"
            f"📄 <code>{filename}</code>\n"
            f"📦 Размер: {size_str}\n"
            f"📂 Папка: <code>{DOWNLOADS_DIR}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.video)
async def handle_video(message: Message):
    """Получить видео → сохранить на ПК"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        video = message.video
        file = await bot.get_file(video.file_id)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = video.file_name or f"video_{timestamp}.mp4"
        filepath = DOWNLOADS_DIR / filename
        
        await message.answer("⏳ Скачиваю видео...")
        await bot.download_file(file.file_path, filepath)
        
        size_mb = video.file_size / (1024 * 1024)
        
        await message.answer(
            f"🎬 <b>Видео сохранено</b>\n\n"
            f"📄 <code>{filename}</code>\n"
            f"📦 Размер: {size_mb:.1f} MB\n"
            f"📂 Папка: <code>{DOWNLOADS_DIR}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.audio)
async def handle_audio(message: Message):
    """Получить аудио → сохранить на ПК"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        audio = message.audio
        file = await bot.get_file(audio.file_id)
        
        filename = audio.file_name or f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
        filepath = DOWNLOADS_DIR / filename
        
        await bot.download_file(file.file_path, filepath)
        
        size_kb = audio.file_size / 1024
        
        await message.answer(
            f"🎵 <b>Аудио сохранено</b>\n\n"
            f"📄 <code>{filename}</code>\n"
            f"📦 Размер: {size_kb:.1f} KB\n"
            f"📂 Папка: <code>{DOWNLOADS_DIR}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.voice)
async def handle_voice(message: Message):
    """Получить голосовое сообщение → сохранить на ПК"""
    if not is_authorized(message.from_user.id):
        return
    
    try:
        voice = message.voice
        file = await bot.get_file(voice.file_id)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"voice_{timestamp}.ogg"
        filepath = DOWNLOADS_DIR / filename
        
        await bot.download_file(file.file_path, filepath)
        
        await message.answer(
            f"🎤 <b>Голосовое сохранено</b>\n\n"
            f"📄 <code>{filename}</code>\n"
            f"📂 Папка: <code>{DOWNLOADS_DIR}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.text)
async def handle_text(message: Message):
    """Любой текст (не команда) → копировать в буфер обмена"""
    if not is_authorized(message.from_user.id):
        return
    
    text = message.text
    if text.startswith('/'):
        return
    
    menu_buttons = ["🔊 Громкость", "🎵 Медиа", "⏻ Питание", "💡 Яркость", 
                    "📸 Скриншот", "📋 Буфер", "📊 Система", "❓ Помощь"]
    if text in menu_buttons:
        return
    
    try:
        import pyperclip
        pyperclip.copy(text)
        preview = text[:100] + "..." if len(text) > 100 else text
        await message.answer(f"📋 Скопировано в буфер:\n<code>{preview}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")



async def send_startup_notification():
    """Отправить уведомление о запуске ПК"""
    try:
        uptime = get_uptime()
        for user_id in config.ALLOWED_USER_IDS:
            try:
                await bot.send_message(
                    user_id,
                    f"🖥️ <b>ПК включён!</b>\n\n"
                    f"⏱ Uptime: {uptime}\n"
                    f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
    except Exception as e:
        print(f"Ошибка уведомления: {e}")


async def main():
    """Запуск бота"""
    print("🤖 Бот запущен!")
    print(f"📋 Разрешённые пользователи: {config.ALLOWED_USER_IDS}")
    
    await send_startup_notification()
    
    await dp.start_polling(
        bot,
        polling_timeout=30,
        allowed_updates=["message", "callback_query"]
    )


if __name__ == "__main__":
    asyncio.run(main())