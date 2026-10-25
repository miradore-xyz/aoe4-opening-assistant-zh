"""Edge（微软浏览器）在线语音的提前准备与本地缓存播放。

使用 edge-tts（开源在线朗读组件），无需登录或填写密钥。只有 prepare
（提前准备）会把指定文本发送给在线服务；say（播报）只播放本地 MP3
（压缩音频）缓存。服务联网可用性不由本软件保证，失败会明确通知。

在 Qt（窗口框架）主线程创建并调用本对象。prepare 返回任务编号，
progress（进度）和 prepared（完成）携带该编号，便于忽略旧任务。
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import threading
import unicodedata
import uuid

from PyQt5.QtCore import QObject, QUrl, pyqtSignal
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer


# 这三个语音编号已通过在线 list_voices（音色列表）实际核对。
CHINESE_VOICES = (
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 中文女声"),
    ("zh-CN-YunxiNeural", "云希 · 中文男声"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 中文女声"),
)
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
CACHE_VERSION = "edge-tts:24khz-48kbit-mono-mp3:1"


class OnlineSpeech(QObject):
    """后台联网生成，主线程播放；替换旧任务，不积压过期播报。"""

    progress = pyqtSignal(str, int, int)
    prepared = pyqtSignal(str)
    problem = pyqtSignal(str)
    started = pyqtSignal(str)

    def __init__(self, cache_dir, parent=None):
        super().__init__(parent)
        self.cache_dir = Path(cache_dir).resolve()
        self._lock = threading.Lock()
        self._closed = False
        self._active_job = ""
        self._pending_job = None
        self._loop = None
        self._task = None
        self._player = QMediaPlayer(self)
        self._player.stateChanged.connect(self._state_changed)
        self._player.mediaStatusChanged.connect(self._media_status_changed)
        self._player.error.connect(self._playback_error)
        self._play_text = ""
        self._play_announced = False
        self._thread = threading.Thread(
            target=self._worker, name="OnlineVoiceCacheWorker", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _text(text):
        return unicodedata.normalize("NFC", str(text)).strip()

    @classmethod
    def _texts(cls, texts):
        if isinstance(texts, str):
            texts = [texts]
        # 保持输入顺序，并对重复文本去重。
        return tuple(dict.fromkeys(cls._text(t) for t in texts if cls._text(t)))

    @staticmethod
    def _parameters(voice, rate):
        voice, rate = str(voice), int(rate)
        if voice not in dict(CHINESE_VOICES):
            raise ValueError("请选择晓晓、云希或晓伊中文语音。")
        if not -10 <= rate <= 10:
            raise ValueError("在线语音的语速应在 -10 至 10 之间。")
        return voice, rate

    def cache_path(self, text, voice=DEFAULT_VOICE, rate=0):
        """音色、语速和文本共同确定缓存；播放音量独立调整。"""
        voice, rate = self._parameters(voice, rate)
        material = json.dumps(
            [CACHE_VERSION, voice, rate, self._text(text)],
            ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return self.cache_dir / (hashlib.sha256(material).hexdigest() + ".mp3")

    @staticmethod
    def _valid_audio(path):
        """只接受完整提交的非空音频缓存，拒绝明显错误的返回内容。"""
        try:
            if path.stat().st_size < 128:
                return False
            with path.open("rb") as audio:
                header = audio.read(3)
            # MP3 帧同步头，或 ID3（音频标签）文件头。
            return header[:3] == b"ID3" or (
                len(header) >= 2 and header[0] == 0xff and header[1] & 0xe0 == 0xe0
            )
        except OSError:
            return False

    def ready_for(self, texts, voice=DEFAULT_VOICE, rate=0):
        """只检查本地缓存，不联网、不阻塞窗口等待服务。"""
        try:
            voice, rate = self._parameters(voice, rate)
            return not self._closed and all(
                self._valid_audio(self.cache_path(t, voice, rate))
                for t in self._texts(texts)
            )
        except (TypeError, ValueError):
            return False

    def prepare(self, texts, voice=DEFAULT_VOICE, rate=0):
        """在后台生成这些文本，返回新任务编号，并取消上次准备。"""
        try:
            voice, rate = self._parameters(voice, rate)
            texts = self._texts(texts)
        except (TypeError, ValueError) as error:
            self.problem.emit(str(error))
            return ""
        self.stop_playback()
        job_id = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                return ""
            self._active_job = job_id
            self._pending_job = (job_id, texts, voice, rate)
            loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._start_latest)
        return job_id

    def _current(self, job_id):
        with self._lock:
            return not self._closed and self._active_job == job_id

    def cancel_prepare(self):
        """取消联网准备，保留已经生成完成的缓存。"""
        with self._lock:
            if self._closed:
                return
            self._active_job = ""
            self._pending_job = None
            loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._cancel_task)

    def _cancel_task(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _start_latest(self):
        """仅在后台事件循环调用；未开始的中间任务直接被最新任务覆盖。"""
        with self._lock:
            if self._closed or self._pending_job is None:
                return
            job = self._pending_job
            self._pending_job = None
        self._cancel_task()
        self._task = asyncio.create_task(self._prepare_job(*job))

    async def _download_audio(self, text, voice, rate, path, job_id):
        import edge_tts
        # 沿用 -10 至 10 的界面范围，转换为 -50% 至 +50% 的在线语速。
        communicate = edge_tts.Communicate(
            text, voice=voice, rate=f"{rate * 5:+d}%",
            connect_timeout=10, receive_timeout=20,
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.stem + "." + job_id + ".tmp.mp3")
        try:
            with temporary.open("wb") as output:
                received = False
                async for chunk in communicate.stream():
                    if not self._current(job_id):
                        raise asyncio.CancelledError()
                    if chunk["type"] == "audio":
                        output.write(chunk["data"])
                        received = True
                output.flush()
                os.fsync(output.fileno())
            if not received or not self._valid_audio(temporary):
                raise ValueError("在线服务未返回有效音频。")
            if not self._current(job_id):
                raise asyncio.CancelledError()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    async def _prepare_job(self, job_id, texts, voice, rate):
        try:
            if not self._current(job_id):
                return
            self.progress.emit(job_id, 0, len(texts))
            for index, text in enumerate(texts):
                if not self._current(job_id):
                    return
                path = self.cache_path(text, voice, rate)
                if not self._valid_audio(path):
                    # 每段设置完整超时；失败即报告，不无限重试。
                    await asyncio.wait_for(
                        self._download_audio(text, voice, rate, path, job_id), timeout=45
                    )
                if self._current(job_id):
                    self.progress.emit(job_id, index + 1, len(texts))
            if self._current(job_id):
                self.prepared.emit(job_id)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            if self._current(job_id):
                self.problem.emit("在线语音准备超时，请检查网络后重试；已完成的缓存仍可使用。")
        except Exception as error:
            if self._current(job_id):
                status = getattr(error, "status", None)
                if status in (401, 403):
                    message = "在线语音服务拒绝了请求，请稍后重试或改用系统朗读。"
                elif status == 429:
                    message = "在线语音服务请求繁忙，请稍后重试。"
                elif isinstance(error, ImportError):
                    message = "在线语音运行组件缺失，请重新解压完整的软件包。"
                else:
                    message = "在线语音准备失败，请检查网络或稍后重试；已完成的缓存仍可使用。"
                self.problem.emit(message)

    def _worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            if self._closed:
                loop.close()
                return
            self._loop = loop
        try:
            loop.call_soon(self._start_latest)
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            with self._lock:
                self._loop = None

    def say(self, text, voice=DEFAULT_VOICE, volume=80, rate=0):
        """只播放已有本地音频；返回接受请求与否，缺缓存时明确提示。"""
        self.stop_playback()
        if self._closed:
            return False
        text = self._text(text)
        if not text:
            return False
        try:
            path = self.cache_path(text, voice, rate)
            volume = max(0, min(100, int(volume)))
        except (TypeError, ValueError) as error:
            self.problem.emit(str(error))
            return False
        if not self._valid_audio(path):
            self.problem.emit("这段在线播报尚未缓存，请先完成语音准备再开始计时。")
            return False
        if not volume:
            self.problem.emit("当前朗读音量为 0，请提高音量后再试听。")
            return False
        self._play_text = text
        self._player.setVolume(volume)
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path))))
        self._player.play()
        return self._player.error() == QMediaPlayer.NoError

    def _maybe_started(self):
        if (not self._closed and self._play_text and not self._play_announced
                and self._player.state() == QMediaPlayer.PlayingState
                and self._player.mediaStatus() in (
                    QMediaPlayer.LoadedMedia, QMediaPlayer.BufferingMedia,
                    QMediaPlayer.BufferedMedia,
                )):
            self._play_announced = True
            self.started.emit(self._play_text)

    def _state_changed(self, _state):
        self._maybe_started()

    def _media_status_changed(self, status):
        if status == QMediaPlayer.InvalidMedia and self._play_text:
            self._playback_error(QMediaPlayer.FormatError)
        else:
            self._maybe_started()

    def _playback_error(self, error):
        if error != QMediaPlayer.NoError and self._play_text and not self._closed:
            self.stop_playback()
            self.problem.emit("语音缓存播放失败，请检查系统输出设备、应用音量和 MP3 播放支持。")

    def stop_playback(self):
        """只停止当前声音，不取消后台准备。"""
        self._play_text = ""
        self._play_announced = False
        self._player.stop()
        self._player.setMedia(QMediaContent())

    def stop(self):
        """停止当前声音并取消旧准备任务。"""
        self.cancel_prepare()
        self.stop_playback()

    def close(self):
        """取消网络任务并立即返回，不让关窗等待网络超时。"""
        self.stop_playback()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._active_job = ""
            self._pending_job = None
            loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
