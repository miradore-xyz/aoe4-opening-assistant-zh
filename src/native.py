"""Windows（微软视窗系统）的全局快捷键与中文系统朗读。

hotkey=快捷键，speech=朗读，voice=声音，queue=队列。无需管理员权限。
"""
import ctypes
from ctypes import wintypes
import queue
import threading

from PyQt5.QtCore import QObject, pyqtSignal, QAbstractNativeEventFilter, Qt
from PyQt5.QtGui import QKeySequence


class GlobalHotkeys(QAbstractNativeEventFilter):
    """通过系统登记快捷键，不监控和保存其他键盘输入。"""
    def __init__(self, application, callback):
        super().__init__()
        self.callback = callback
        self.registered = {}
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        application.installNativeEventFilter(self)

    @staticmethod
    def parse(text):
        sequence = QKeySequence(text)
        if sequence.count() != 1 or not sequence[0]:
            raise ValueError("只支持单组组合键，例如 Ctrl+Alt+F7（控制键＋换档键＋功能键 F7）。")
        combined = sequence[0]
        modifiers = 0x4000  # MOD_NOREPEAT：长按不重复触发。
        for qt_flag, native_flag in [(Qt.AltModifier, 1), (Qt.ControlModifier, 2), (Qt.ShiftModifier, 4)]:
            if combined & int(qt_flag):
                modifiers |= native_flag
        if combined & int(Qt.MetaModifier):
            raise ValueError("请使用控制键、换档键或上档键，避免系统保留的徽标组合键。")
        key = combined & 0x01FFFFFF
        if int(Qt.Key_F1) <= key <= int(Qt.Key_F24):
            virtual = 0x70 + key - int(Qt.Key_F1)
        elif ord("A") <= key <= ord("Z") or ord("0") <= key <= ord("9"):
            if modifiers == 0x4000:
                raise ValueError("字母或数字快捷键需要搭配控制键、换档键或上档键。")
            virtual = key
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown,
                      Qt.Key_Home, Qt.Key_End, Qt.Key_Insert, Qt.Key_Delete, Qt.Key_Space):
            virtual = {Qt.Key_Left: 37, Qt.Key_Up: 38, Qt.Key_Right: 39, Qt.Key_Down: 40,
                       Qt.Key_PageUp: 33, Qt.Key_PageDown: 34, Qt.Key_Home: 36, Qt.Key_End: 35,
                       Qt.Key_Insert: 45, Qt.Key_Delete: 46, Qt.Key_Space: 32}[key]
        else:
            raise ValueError("请使用功能键，或组合字母、数字、方向键、翻页键。")
        return modifiers, virtual

    def apply(self, values):
        parsed = {}
        seen = set()
        for action, text in values.items():
            if not text.strip():
                continue
            pair = self.parse(text)
            if pair in seen:
                raise ValueError("不同功能不能使用相同快捷键。")
            seen.add(pair)
            parsed[action] = pair
        self.close()
        failures = []
        for index, (action, (modifiers, virtual)) in enumerate(parsed.items(), 0x6200):
            if self.user32.RegisterHotKey(None, index, modifiers, virtual):
                self.registered[index] = action
            else:
                failures.append(action)
        return failures

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0312 and msg.wParam in self.registered:  # WM_HOTKEY：系统快捷键消息。
            self.callback(self.registered[msg.wParam])
            return True, 0
        return False, 0

    def close(self):
        for index in self.registered:
            self.user32.UnregisterHotKey(None, index)
        self.registered.clear()


class Speech(QObject):
    """只使用已安装的中文 SAPI（系统语音接口）声音，缺失时不影响文字。"""
    voices_ready = pyqtSignal(list)
    problem = pyqtSignal(str)
    started = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.commands = queue.Queue()
        self.voices = []
        self.thread = threading.Thread(target=self.worker, daemon=True)

    def start(self):
        self.thread.start()

    def say(self, text, voice="", volume=80, rate=0):
        self.commands.put((text, voice, volume, rate))

    def stop(self):
        self.commands.put(("", "", 80, 0))

    def close(self):
        self.commands.put(None)
        if self.thread.is_alive():
            self.thread.join(timeout=1)

    def worker(self):
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            tokens = []
            for token in speaker.GetVoices():
                try:
                    languages = token.GetAttribute("Language").lower().split(";")
                    chinese = any(int(language, 16) & 0x3FF == 4 for language in languages)
                except Exception:
                    chinese = any(word in token.GetDescription().lower() for word in ("huihui", "chinese", "中文"))
                if chinese:
                    tokens.append(token)
            self.voices = [(token.Id, token.GetDescription()) for token in tokens]
            self.voices_ready.emit(self.voices)
            while True:
                # SAPI 在单线程 COM 环境里异步朗读；保持消息循环，避免等待下一条命令时阻断语音回调。
                pythoncom.PumpWaitingMessages()
                try:
                    item = self.commands.get(timeout=0.05)
                except queue.Empty:
                    continue
                if item is None:
                    speaker.Speak("", 3)
                    break
                text, voice_id, volume, rate = item
                # 合并队列，避免计时跳转后积压旧步骤。
                while not self.commands.empty():
                    newest = self.commands.get()
                    if newest is None:
                        speaker.Speak("", 3)
                        return
                    text, voice_id, volume, rate = newest
                if not tokens:
                    continue
                speaker.Voice = next((t for t in tokens if t.Id == voice_id), tokens[0])
                speaker.Volume = volume
                speaker.Rate = rate
                speaker.Speak(text, 3)  # 异步播报并清除旧播报，不阻塞窗口。
                if text:
                    self.started.emit(text)
        except Exception:
            self.problem.emit("系统中文朗读暂不可用；文字悬浮和快捷键仍可使用。")
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
